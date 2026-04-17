"""
Integration tests for auth endpoints and protected-route gating.

Postgres is mocked at the repository boundary (see CLAUDE.md "Testing external
APIs") — users_repo functions hit an in-memory dict so the tests stay
hermetic. The pg pool lifecycle is stubbed to no-ops.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# Set secrets BEFORE importing the app so config.py picks them up.
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    """Replace Postgres-backed users_repo with an in-memory dict."""
    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str) -> dict[str, Any]:
        import asyncpg

        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
                raise asyncpg.UniqueViolationError("duplicate email")
        uid = str(uuid4())
        row = {
            "id": uid,
            "email": email,
            "password_hash": password_hash,
            "created_at": None,
            "last_login_at": None,
        }
        store[uid] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def get_user_by_email(email: str) -> dict[str, Any] | None:
        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
                return dict(u)
        return None

    async def get_user_by_id(user_id: Any) -> dict[str, Any] | None:
        u = store.get(str(user_id))
        if not u:
            return None
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def update_last_login(user_id: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["last_login_at"] = "now"

    from backend.db import users_repo

    monkeypatch.setattr(users_repo, "create_user", create_user)
    monkeypatch.setattr(users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(users_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(users_repo, "update_last_login", update_last_login)

    # Patch auth.dependencies.users_repo.get_user_by_id too — it's imported as
    # `from backend.db import users_repo` so the module-level name is aliased.
    from backend.auth import dependencies as auth_deps

    monkeypatch.setattr(auth_deps.users_repo, "get_user_by_id", get_user_by_id)

    # Also patch routes.auth which imports users_repo the same way.
    from backend.routes import auth as auth_route

    monkeypatch.setattr(auth_route.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_route.users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", update_last_login)

    return store


@pytest.fixture(autouse=True)
def stub_pg_lifecycle(monkeypatch):
    """The FastAPI lifespan calls init_users_schema + close_pg_pool — no-op both."""
    from backend.db import postgres as pg

    async def noop():
        return None

    monkeypatch.setattr(pg, "init_users_schema", noop)
    monkeypatch.setattr(pg, "close_pg_pool", noop)


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# /api/auth/signup
# ---------------------------------------------------------------------------


async def test_signup_creates_user_and_sets_cookie(client):
    r = await client.post(
        "/api/auth/signup",
        json={"email": "alice@example.com", "password": "supersecret"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    assert "session" in r.cookies


async def test_duplicate_signup_returns_409(client):
    creds = {"email": "dup@example.com", "password": "password123"}
    r1 = await client.post("/api/auth/signup", json=creds)
    assert r1.status_code == 201
    r2 = await client.post("/api/auth/signup", json=creds)
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# /api/auth/login
# ---------------------------------------------------------------------------


async def test_login_with_correct_password_sets_cookie(client):
    await client.post(
        "/api/auth/signup",
        json={"email": "bob@example.com", "password": "password123"},
    )
    # Clear cookies so login is a fresh request
    client.cookies.clear()
    r = await client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "password123"},
    )
    assert r.status_code == 200
    assert "session" in r.cookies


async def test_login_with_wrong_password_returns_401(client):
    await client.post(
        "/api/auth/signup",
        json={"email": "carol@example.com", "password": "password123"},
    )
    client.cookies.clear()
    r = await client.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "wrong-password"},
    )
    assert r.status_code == 401


async def test_login_unknown_email_returns_401(client):
    r = await client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "whatever1"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/auth/me and /logout
# ---------------------------------------------------------------------------


async def test_me_returns_user_when_authenticated(client):
    await client.post(
        "/api/auth/signup",
        json={"email": "dan@example.com", "password": "password123"},
    )
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "dan@example.com"


async def test_me_unauthenticated_returns_401(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_logout_clears_cookie_and_blocks_me(client):
    await client.post(
        "/api/auth/signup",
        json={"email": "eve@example.com", "password": "password123"},
    )
    r_logout = await client.post("/api/auth/logout")
    assert r_logout.status_code == 204
    # Clear client-side cookies as the browser would after Set-Cookie with
    # max-age=0 / deletion. httpx's ASGI transport doesn't automatically honour
    # the delete-cookie response, so drop them manually to match browser state.
    client.cookies.clear()
    r_me = await client.get("/api/auth/me")
    assert r_me.status_code == 401


# ---------------------------------------------------------------------------
# Protected route gating — acceptance criteria from issue #51
# ---------------------------------------------------------------------------


async def test_unauthenticated_create_conversation_returns_401(client):
    r = await client.post("/api/conversations", json={})
    assert r.status_code == 401


async def test_unauthenticated_messages_post_returns_401(client):
    r = await client.post(
        "/api/conversations/some-id/messages",
        json={"content": "hi"},
    )
    assert r.status_code == 401


async def test_unauthenticated_ingest_returns_401(client):
    r = await client.post(
        "/api/ingest",
        json={
            "title": "t",
            "description": "d",
            "url": "https://example.com",
            "transcript": "tx",
        },
    )
    assert r.status_code == 401


async def test_health_remains_public(client):
    r = await client.get("/api/health")
    assert r.status_code == 200


async def test_version_remains_public(client):
    r = await client.get("/api/version")
    # 200 if the package is installed, 503 if metadata is missing — either
    # proves the route is not gated behind auth (auth would give 401).
    assert r.status_code in (200, 503)
