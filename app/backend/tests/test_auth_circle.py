"""End-to-end-ish auth tests covering Circle membership flow.

Reuses the in-memory fake_users_repo + stub_pg_lifecycle fixtures from
test_auth.py via conftest's auto-discovery; we override `circle.verify_paid_member`
per test to exercise the Member / non-Member / Circle-down branches.

Acceptance criteria from issue #147 covered:
- (3) signup with email NOT in Circle → user created, is_member=False on /me
- (4) Circle outage during login → login still succeeds, /me reflects False
- (8) returning user logs in → is_member updated based on current Circle state
- /me opportunistic refresh after MEMBERSHIP_REFRESH_SECONDS (mocked to 0 here)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# Standard secrets BEFORE app import.
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


# Reuse the fixtures from test_auth.py (pytest discovers them via conftest auto-loading
# of files in the same directory tree). To make this file self-contained without
# importing the fixture from a sibling test module, we redeclare the slim subset
# we need here.


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    from uuid import uuid4

    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str, **kwargs: Any) -> dict[str, Any]:
        import asyncpg

        for u in store.values():
            if str(u["email"]).lower() == email.lower():
                raise asyncpg.UniqueViolationError("duplicate email")
        uid = str(uuid4())
        row = {
            "id": uid,
            "email": email,
            "password_hash": password_hash,
            "created_at": None,
            "last_login_at": None,
            "is_member": False,
            "member_verified_at": None,
        }
        store[uid] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def get_user_by_email(email: str) -> dict[str, Any] | None:
        for u in store.values():
            if str(u["email"]).lower() == email.lower():
                return dict(u)
        return None

    async def get_user_by_id(user_id: Any) -> dict[str, Any] | None:
        u = store.get(str(user_id))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def update_last_login(user_id: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["last_login_at"] = "now"

    async def set_member_status(user_id: Any, *, is_member: bool, **kwargs: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["is_member"] = is_member
            u["member_verified_at"] = datetime.now(UTC)

    from backend.auth import dependencies as auth_deps
    from backend.db import users_repo
    from backend.routes import auth as auth_route

    for repo, fn_name, fn in [
        (users_repo, "create_user", create_user),
        (users_repo, "get_user_by_email", get_user_by_email),
        (users_repo, "get_user_by_id", get_user_by_id),
        (users_repo, "update_last_login", update_last_login),
        (users_repo, "set_member_status", set_member_status),
        (auth_deps.users_repo, "get_user_by_id", get_user_by_id),
        (auth_route.users_repo, "create_user", create_user),
        (auth_route.users_repo, "get_user_by_email", get_user_by_email),
        (auth_route.users_repo, "update_last_login", update_last_login),
        (auth_route.users_repo, "set_member_status", set_member_status),
    ]:
        monkeypatch.setattr(repo, fn_name, fn)
    return store


@pytest.fixture(autouse=True)
def stub_pg_lifecycle(monkeypatch):
    from backend.db import postgres as pg

    async def noop():
        return None

    monkeypatch.setattr(pg, "close_pg_pool", noop)


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


def _set_circle(monkeypatch, return_value: bool, fail: bool = False):
    """Patch circle.verify_paid_member. fail=True simulates Circle being down
    (the production verify_paid_member never raises, so we just return False
    here too)."""

    async def fake(email: str) -> bool:
        return False if fail else return_value

    from backend.integrations import circle as circle_module
    from backend.routes import auth as auth_route

    monkeypatch.setattr(circle_module, "verify_paid_member", fake)
    monkeypatch.setattr(auth_route.circle, "verify_paid_member", fake)


# ---------------------------------------------------------------------------
# Signup paths
# ---------------------------------------------------------------------------


async def test_signup_non_member_lands_with_is_member_false(client, monkeypatch):
    _set_circle(monkeypatch, return_value=False)
    r = await client.post(
        "/api/auth/signup",
        json={"email": "free@example.com", "password": "supersecret"},
    )
    assert r.status_code == 201

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "free@example.com"
    assert body["is_member"] is False


async def test_signup_paid_member_flagged(client, monkeypatch):
    _set_circle(monkeypatch, return_value=True)
    r = await client.post(
        "/api/auth/signup",
        json={"email": "paid@example.com", "password": "supersecret"},
    )
    assert r.status_code == 201

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_member"] is True


# ---------------------------------------------------------------------------
# Login paths
# ---------------------------------------------------------------------------


async def test_login_circle_down_succeeds_with_non_member(client, monkeypatch):
    """Acceptance #4: Circle outage during login → user falls back to non-member."""
    # Signup with Circle UP → user is a member.
    _set_circle(monkeypatch, return_value=True)
    await client.post(
        "/api/auth/signup",
        json={"email": "paid@example.com", "password": "supersecret"},
    )

    # Now Circle is "down" — verify_paid_member returns False (fail-closed).
    _set_circle(monkeypatch, return_value=False, fail=True)
    r = await client.post(
        "/api/auth/login",
        json={"email": "paid@example.com", "password": "supersecret"},
    )
    assert r.status_code == 200

    me = await client.get("/api/auth/me")
    # /me will see member_verified_at as just-now, so it skips the refresh and
    # returns the freshly stored False.
    assert me.json()["is_member"] is False


async def test_me_refreshes_after_staleness_window(client, monkeypatch, fake_users_repo):
    """Acceptance #4 follow-up: after Circle recovers, /me re-verifies and flips."""
    # Signup with Circle DOWN → user lands as non-member.
    _set_circle(monkeypatch, return_value=False)
    r = await client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "password": "supersecret"},
    )
    assert r.status_code == 201

    # Make verified_at look stale.
    user = next(iter(fake_users_repo.values()))
    user["member_verified_at"] = datetime.now(UTC) - timedelta(hours=2)

    # Bring Circle back up — paid this time.
    _set_circle(monkeypatch, return_value=True)

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_member"] is True
    # And the row now has a fresh stamp.
    assert (datetime.now(UTC) - user["member_verified_at"]).total_seconds() < 5


async def test_me_skips_refresh_when_fresh(client, monkeypatch, fake_users_repo):
    """If verified_at is fresh, /me must NOT re-call Circle."""
    _set_circle(monkeypatch, return_value=False)
    await client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "password": "supersecret"},
    )

    # Switch Circle to True. /me should NOT pick this up because verified_at is fresh.
    call_count = {"n": 0}

    async def counting(email: str) -> bool:
        call_count["n"] += 1
        return True

    from backend.integrations import circle as circle_module
    from backend.routes import auth as auth_route

    monkeypatch.setattr(circle_module, "verify_paid_member", counting)
    monkeypatch.setattr(auth_route.circle, "verify_paid_member", counting)

    me = await client.get("/api/auth/me")
    assert me.json()["is_member"] is False
    assert call_count["n"] == 0
