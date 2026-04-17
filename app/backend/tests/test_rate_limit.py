"""
Tests for the 25 msg/user/24h cap (issue #52, MISSION §10 invariant #1).

Two layers:

1. **Unit tests** against `rate_limit.check_and_record` with the
   `user_messages_repo` functions stubbed to an in-memory store. Verifies
   the counting logic, the insert-on-pass behaviour, and the
   `RateLimitExceeded` / `reset_at` computation without needing Postgres.

2. **Integration tests** against the live FastAPI app (`httpx.AsyncClient` +
   ASGITransport) for the route wiring: the 429 body shape on cap hit and
   the extended `/api/auth/me` counter fields. These tests monkeypatch the
   rate-limit helpers directly — Postgres is never touched.

Tests for the "partial stream still records" property live here too: the
audit row is inserted inside `check_and_record` BEFORE the stream starts,
so if the client aborts mid-stream, the row is already committed.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

# Point DB_PATH at a throw-away sqlite file before any backend import.
_tmp_dir = tempfile.mkdtemp(prefix="dynachat-rate-test-")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "chat.db")
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from backend import rate_limit  # noqa: E402
from backend.db.schema import init_db  # noqa: E402

# `message_store` and the `patch_rate_limit` autouse stub live in conftest.py
# so every test in this suite (including /me auth tests) gets the same
# in-memory rate-limiter without needing a live Postgres connection.


# ---------------------------------------------------------------------------
# Unit tests — rate_limit.check_and_record
# ---------------------------------------------------------------------------


async def test_check_and_record_allows_when_under_cap(message_store):
    user_id = str(uuid4())
    # 24 existing messages — one more should be allowed (takes count to 25).
    now = datetime.now(UTC)
    message_store[user_id] = [now - timedelta(minutes=i) for i in range(24)]

    await rate_limit.check_and_record(user_id)

    assert len(message_store[user_id]) == 25


async def test_check_and_record_raises_at_cap(message_store):
    user_id = str(uuid4())
    now = datetime.now(UTC)
    # 25 messages already — next send must hit 429.
    message_store[user_id] = [now - timedelta(minutes=i) for i in range(25)]

    with pytest.raises(rate_limit.RateLimitExceeded) as exc_info:
        await rate_limit.check_and_record(user_id)

    # reset_at = oldest + 24h (oldest is the 24-min-ago row? No: the 24th row
    # was `now - timedelta(minutes=24)`. But earlier we went from i=0 to 24,
    # so the oldest is now - 24min.)
    expected_oldest = now - timedelta(minutes=24)
    expected_reset = expected_oldest + timedelta(hours=rate_limit.WINDOW_HOURS)
    # Allow ~5s of drift for test wall-clock sampling.
    assert abs((exc_info.value.reset_at - expected_reset).total_seconds()) < 5
    # Store unchanged on failure — insert didn't happen.
    assert len(message_store[user_id]) == 25


async def test_check_and_record_ignores_messages_outside_window(message_store):
    user_id = str(uuid4())
    now = datetime.now(UTC)
    # 30 messages, all >24h old — the sliding window sees zero.
    message_store[user_id] = [now - timedelta(hours=25, minutes=i) for i in range(30)]

    await rate_limit.check_and_record(user_id)

    # 30 stale + 1 new row inserted by check_and_record. Still under cap in-window.
    assert len(message_store[user_id]) == 31


async def test_get_status_reports_zero_for_new_user(message_store):
    user_id = str(uuid4())
    status = await rate_limit.get_status(user_id)
    assert status.used == 0
    assert status.remaining == rate_limit.DAILY_MESSAGE_CAP
    assert status.resets_at is None


async def test_get_status_computes_resets_at_from_oldest_in_window(message_store):
    user_id = str(uuid4())
    now = datetime.now(UTC)
    oldest = now - timedelta(hours=3)
    message_store[user_id] = [oldest, now - timedelta(hours=1), now]

    status = await rate_limit.get_status(user_id)

    assert status.used == 3
    assert status.remaining == rate_limit.DAILY_MESSAGE_CAP - 3
    assert status.resets_at is not None
    expected = oldest + timedelta(hours=rate_limit.WINDOW_HOURS)
    assert abs((status.resets_at - expected).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Integration fixtures (mirror test_auth.py — users in memory, pg lifespan no-op)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def fresh_sqlite_schema():
    db_path = Path(os.environ["DB_PATH"])
    if db_path.exists():
        db_path.unlink()
    await init_db()
    yield


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str) -> dict[str, Any]:
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
        if not u:
            return None
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def update_last_login(user_id: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["last_login_at"] = "now"

    from backend.auth import dependencies as auth_deps
    from backend.db import users_repo
    from backend.routes import auth as auth_route

    monkeypatch.setattr(users_repo, "create_user", create_user)
    monkeypatch.setattr(users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(users_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(users_repo, "update_last_login", update_last_login)
    monkeypatch.setattr(auth_deps.users_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(auth_route.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_route.users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", update_last_login)
    return store


@pytest.fixture(autouse=True)
def stub_pg_lifecycle(monkeypatch):
    from backend.db import postgres as pg

    async def noop():
        return None

    monkeypatch.setattr(pg, "init_users_schema", noop)
    monkeypatch.setattr(pg, "close_pg_pool", noop)


async def _client() -> AsyncClient:
    from backend.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _signup(email: str = "alice@example.com", password: str = "password123") -> AsyncClient:
    c = await _client()
    r = await c.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return c


# ---------------------------------------------------------------------------
# /api/auth/me counter fields
# ---------------------------------------------------------------------------


async def test_me_includes_counter_fields_zero_usage(fake_users_repo):
    client = await _signup()
    try:
        r = await client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["messages_used_today"] == 0
        assert body["messages_remaining_today"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["rate_window_resets_at"] is None
    finally:
        await client.aclose()


async def test_me_reflects_usage_after_sending(fake_users_repo, message_store):
    client = await _signup("counter-user@example.com")
    try:
        # Simulate 7 messages already sent by this user in the window.
        uid = next(iter(fake_users_repo))
        now = datetime.now(UTC)
        message_store[uid] = [now - timedelta(minutes=i) for i in range(7)]

        r = await client.get("/api/auth/me")
        body = r.json()
        assert body["messages_used_today"] == 7
        assert body["messages_remaining_today"] == rate_limit.DAILY_MESSAGE_CAP - 7
        assert body["rate_window_resets_at"] is not None
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# POST /api/conversations/{id}/messages — 429 on cap hit
# ---------------------------------------------------------------------------


async def test_post_message_returns_429_when_over_cap(fake_users_repo, message_store):
    client = await _signup("over-cap@example.com")
    try:
        # Create a conversation for the user so the ownership check passes.
        r_conv = await client.post("/api/conversations", json={"title": "t"})
        assert r_conv.status_code == 201
        conv_id = r_conv.json()["id"]

        # Seed 25 prior messages → next send must 429.
        uid = next(iter(fake_users_repo))
        now = datetime.now(UTC)
        message_store[uid] = [now - timedelta(minutes=i) for i in range(25)]

        r = await client.post(f"/api/conversations/{conv_id}/messages", json={"content": "hi"})
        assert r.status_code == 429, r.text
        body = r.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["window_hours"] == rate_limit.WINDOW_HOURS
        # reset_at must be ISO, parse-able, and roughly oldest+24h.
        reset_dt = datetime.fromisoformat(body["reset_at"])
        expected_oldest = now - timedelta(minutes=24)
        expected_reset = expected_oldest + timedelta(hours=rate_limit.WINDOW_HOURS)
        assert abs((reset_dt - expected_reset).total_seconds()) < 5

        # Store unchanged — the 429 path does NOT record a new row.
        assert len(message_store[uid]) == 25
    finally:
        await client.aclose()


async def test_rate_limit_429_does_not_persist_user_message(fake_users_repo, message_store):
    """When the cap is hit, we must reject BEFORE writing the user's content
    to the messages table — otherwise the chat history would show ghost user
    messages with no assistant reply."""
    from backend.db import repository

    client = await _signup("no-ghost@example.com")
    try:
        r_conv = await client.post("/api/conversations", json={"title": "t"})
        conv_id = r_conv.json()["id"]
        uid = next(iter(fake_users_repo))

        now = datetime.now(UTC)
        message_store[uid] = [now - timedelta(minutes=i) for i in range(25)]

        r = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "this must never land in the DB"},
        )
        assert r.status_code == 429

        persisted = await repository.list_messages(conv_id, user_id=uid)
        assert persisted == []
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Structural invariants — the cap must not be env-configurable
# ---------------------------------------------------------------------------


def test_cap_is_hardcoded_25():
    """MISSION §10 #1: the value is fixed. No env var may change it."""
    assert rate_limit.DAILY_MESSAGE_CAP == 25
    assert rate_limit.WINDOW_HOURS == 24


def test_cap_does_not_read_from_environment():
    """Grep the module source for os.environ / getenv — must be absent."""
    import inspect

    src = inspect.getsource(rate_limit)
    assert "os.environ" not in src, "rate_limit.py must not read env vars"
    assert "getenv" not in src, "rate_limit.py must not read env vars"
