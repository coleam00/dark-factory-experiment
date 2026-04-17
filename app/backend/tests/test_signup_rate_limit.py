"""
Real-Postgres integration tests for signup rate-limiting (issue #54).

Unlike `test_auth.py` (which stubs users_repo in memory), this file runs
against an actual Postgres instance because the limiter's correctness depends
on asyncpg's `pg_advisory_xact_lock` semantics and on the `signup_attempts`
table's index-backed sliding-window queries.

Run pre-reqs (throw-away container, started by the developer before pytest):

    docker run -d --name dynachat-signup-test -p 127.0.0.1:5435:5432 \
      -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test \
      postgres:16-alpine

The backend's ASGI lifespan runs `init_users_schema` + `init_signup_attempts_schema`
against this container. Each test truncates both tables before running so
counts are deterministic.
"""

from __future__ import annotations

import inspect
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

# Point DATABASE_URL + DB_PATH at throw-away storage BEFORE any backend imports.
os.environ["DATABASE_URL"] = "postgresql://test:test@127.0.0.1:5435/test"
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
_tmp_dir = tempfile.mkdtemp(prefix="dynachat-signup-test-")
os.environ["DB_PATH"] = str(Path(_tmp_dir) / "chat.db")

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from backend import signup_rate_limit  # noqa: E402

DSN = os.environ["DATABASE_URL"]


# ---------------------------------------------------------------------------
# Fixture overrides — disable the permissive conftest stubs so the real
# `signup_rate_limit` module runs against real Postgres.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_signup_rate_limit():
    """Override conftest stub — exercise the real limiter."""
    yield


@pytest.fixture(autouse=True)
async def patch_pg_pool(monkeypatch):
    """Override conftest stub — create a real backend pool against the test DB.

    ASGITransport does not run the FastAPI lifespan, so we bring the pool up
    (and the two schemas) ourselves. The backend's DATABASE_URL was captured
    at `backend.config` import time (which happened from conftest) — we patch
    the module-level constant in `postgres.py` to point at the test DB before
    creating the pool.
    """
    from backend.db import postgres as pg

    monkeypatch.setattr(pg, "DATABASE_URL", DSN)
    # Ensure a clean pool bound to this test's event loop — a stale pool from a
    # previous test is bound to the previous asyncio loop and raises on use.
    await pg.close_pg_pool()
    await pg.init_pg_pool()
    await pg.init_users_schema()
    await pg.init_signup_attempts_schema()
    yield
    await pg.close_pg_pool()


# ---------------------------------------------------------------------------
# Per-test table truncation + direct asyncpg conn for seeding/asserting
# ---------------------------------------------------------------------------


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Connection]:
    """Standalone asyncpg connection for seeding rows and asserting counts.

    Independent of the backend's pool so it survives ASGI lifespan teardown.
    """
    # Ensure schemas exist (idempotent — harmless if backend already ran it).
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext;")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email CITEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_login_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS signup_attempts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ip INET NOT NULL,
                email_attempted CITEXT,
                outcome TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute("TRUNCATE signup_attempts, users CASCADE;")
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# ASGI client — triggers the real lifespan so the backend pool is initialised.
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _signup(
    client: AsyncClient,
    email: str,
    password: str = "password123",
) -> Any:
    return await client.post("/api/auth/signup", json={"email": email, "password": password})


def _with_ip(monkeypatch, ip: str) -> None:
    """Force the route's `_client_ip` helper to return a specific IP.

    ASGITransport's synthetic `request.client.host` is `testclient` — patching
    at the helper level is cleaner than rigging proxy headers."""
    from backend.routes import auth as auth_route

    monkeypatch.setattr(auth_route, "_client_ip", lambda _req: ip)


async def _count(db: asyncpg.Connection, outcome: str | None = None) -> int:
    if outcome:
        return int(
            await db.fetchval("SELECT count(*) FROM signup_attempts WHERE outcome = $1", outcome)
        )
    return int(await db.fetchval("SELECT count(*) FROM signup_attempts"))


# ---------------------------------------------------------------------------
# Happy path + basic per-IP
# ---------------------------------------------------------------------------


async def test_first_signup_records_accepted_and_returns_201(db, client, monkeypatch):
    _with_ip(monkeypatch, "10.0.0.1")
    r = await _signup(client, "alice@example.com")
    assert r.status_code == 201, r.text
    assert await _count(db, "accepted") == 1
    assert await _count(db) == 1


async def test_second_signup_same_ip_within_hour_returns_429_ip_scope(db, client, monkeypatch):
    _with_ip(monkeypatch, "10.0.0.2")
    r1 = await _signup(client, "first@example.com")
    assert r1.status_code == 201
    r2 = await _signup(client, "second@example.com")
    assert r2.status_code == 429, r2.text
    body = r2.json()
    assert body["error"] == "signup_rate_limited"
    assert body["scope"] == "ip"
    assert isinstance(body["message"], str) and body["message"]
    assert await _count(db, "accepted") == 1
    assert await _count(db, "ip_limited") == 1


async def test_different_ip_not_blocked_by_other_ips_cap(db, client, monkeypatch):
    _with_ip(monkeypatch, "10.0.0.10")
    assert (await _signup(client, "a@example.com")).status_code == 201

    _with_ip(monkeypatch, "10.0.0.11")
    r = await _signup(client, "b@example.com")
    assert r.status_code == 201, r.text
    assert await _count(db, "accepted") == 2


async def test_duplicate_email_records_duplicate_and_returns_409(db, client, monkeypatch):
    _with_ip(monkeypatch, "10.0.0.20")
    assert (await _signup(client, "dup@example.com")).status_code == 201
    # Different IP so the per-IP check passes and we reach the uniqueness check.
    _with_ip(monkeypatch, "10.0.0.21")
    r = await _signup(client, "dup@example.com")
    assert r.status_code == 409, r.text
    assert await _count(db, "accepted") == 1
    assert await _count(db, "duplicate") == 1


# ---------------------------------------------------------------------------
# Global cap
# ---------------------------------------------------------------------------


async def test_global_cap_25_per_10min_returns_429_global_scope(db, client, monkeypatch):
    for i in range(signup_rate_limit.GLOBAL_LIMIT):
        await db.execute(
            """
            INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
            VALUES ($1::inet, $2, 'accepted', now() - interval '30 seconds')
            """,
            f"10.1.0.{i}",
            f"seed{i}@example.com",
        )

    _with_ip(monkeypatch, "10.1.1.99")
    r = await _signup(client, "fresh@example.com")
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["scope"] == "global"
    assert await _count(db, "accepted") == signup_rate_limit.GLOBAL_LIMIT
    assert await _count(db, "global_limited") == 1


async def test_global_window_rolls_off_after_10_min(db, client, monkeypatch):
    for i in range(signup_rate_limit.GLOBAL_LIMIT):
        await db.execute(
            """
            INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
            VALUES ($1::inet, $2, 'accepted', now() - interval '11 minutes')
            """,
            f"10.2.0.{i}",
            f"old{i}@example.com",
        )
    _with_ip(monkeypatch, "10.2.1.99")
    r = await _signup(client, "new@example.com")
    assert r.status_code == 201, r.text


async def test_per_ip_window_rolls_off_after_1h(db, client, monkeypatch):
    await db.execute(
        """
        INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
        VALUES ('10.3.0.1'::inet, 'ancient@example.com', 'accepted',
                now() - interval '61 minutes')
        """
    )
    _with_ip(monkeypatch, "10.3.0.1")
    r = await _signup(client, "reborn@example.com")
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Precedence + ordering
# ---------------------------------------------------------------------------


async def test_ip_precedence_over_global(db, client, monkeypatch):
    target_ip = "10.4.0.99"
    await db.execute(
        """
        INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
        VALUES ($1::inet, 'target@example.com', 'accepted',
                now() - interval '30 seconds')
        """,
        target_ip,
    )
    for i in range(signup_rate_limit.GLOBAL_LIMIT - 1):
        await db.execute(
            """
            INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
            VALUES ($1::inet, $2, 'accepted', now() - interval '30 seconds')
            """,
            f"10.4.1.{i}",
            f"filler{i}@example.com",
        )
    _with_ip(monkeypatch, target_ip)
    r = await _signup(client, "new@example.com")
    assert r.status_code == 429
    assert r.json()["scope"] == "ip"


async def test_429_ip_does_not_call_bcrypt(db, client, monkeypatch):
    """Rate-limit check must run before password hashing — attackers shouldn't
    be able to burn server CPU via repeated 429'd requests."""
    await db.execute(
        """
        INSERT INTO signup_attempts (ip, email_attempted, outcome, created_at)
        VALUES ('10.5.0.1'::inet, 'prior@example.com', 'accepted', now())
        """
    )

    called = {"hashed": False}

    def tripwire(_password: str) -> str:
        called["hashed"] = True
        raise AssertionError("bcrypt was called on a rate-limited request")

    from backend.auth import password as password_module
    from backend.routes import auth as auth_route

    monkeypatch.setattr(password_module, "hash_password", tripwire)
    monkeypatch.setattr(auth_route, "hash_password", tripwire)

    _with_ip(monkeypatch, "10.5.0.1")
    r = await _signup(client, "late@example.com")
    assert r.status_code == 429
    assert called["hashed"] is False


# ---------------------------------------------------------------------------
# Structural invariants — constants cannot be env-configurable
# ---------------------------------------------------------------------------


def test_constants_are_hardcoded_exact_values():
    assert signup_rate_limit.PER_IP_WINDOW_SECONDS == 3600
    assert signup_rate_limit.PER_IP_LIMIT == 1
    assert signup_rate_limit.GLOBAL_WINDOW_SECONDS == 600
    assert signup_rate_limit.GLOBAL_LIMIT == 25


def test_module_does_not_read_environment():
    src = inspect.getsource(signup_rate_limit)
    assert "os.environ" not in src, "signup_rate_limit.py must not read env vars"
    assert "getenv" not in src, "signup_rate_limit.py must not read env vars"


_ = uuid4  # kept for ad-hoc use; silences unused-import lint
