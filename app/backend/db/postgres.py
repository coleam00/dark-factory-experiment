"""
Async Postgres connection pool and schema bootstrap.

Used for new tables landing in Postgres (starting with `users`). Existing chat
tables remain in SQLite for now — see CLAUDE.md "Planned migration: Postgres".

The pool is a module-level singleton created in the FastAPI lifespan handler;
routes and repos fetch it via `get_pg_pool()`.
"""

from __future__ import annotations

import logging

import asyncpg

from backend.config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


USERS_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS users_email_idx ON users (email);

-- Drop the placeholder counter columns from #51. The sliding-window audit
-- table below supersedes them; see issue #52.
ALTER TABLE users DROP COLUMN IF EXISTS daily_message_count;
ALTER TABLE users DROP COLUMN IF EXISTS rate_window_start;

-- Sliding-window audit trail for the 25 msg/user/24h cap (MISSION §10 #1).
-- One row per streamed message. Retained for audit; never pruned actively.
CREATE TABLE IF NOT EXISTS user_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_messages_user_id_created_at_idx
    ON user_messages (user_id, created_at DESC);
"""


async def init_pg_pool() -> asyncpg.Pool:
    """Create the asyncpg pool if not already created. Idempotent."""
    global _pool
    if _pool is not None:
        return _pool
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set; cannot initialise Postgres pool. "
            "Auth-required features are disabled in this environment."
        )
    logger.info("Connecting to Postgres…")
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=10,
    )
    logger.info("Postgres pool ready.")
    return _pool


async def close_pg_pool() -> None:
    """Close the pool on shutdown. Idempotent."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pg_pool() -> asyncpg.Pool:
    """Return the live pool. Raises if `init_pg_pool` was not called."""
    if _pool is None:
        raise RuntimeError(
            "Postgres pool is not initialised. Call init_pg_pool() in the "
            "FastAPI lifespan before using any Postgres-backed repository."
        )
    return _pool


async def init_users_schema() -> None:
    """Run the users-table migration. Idempotent via CREATE ... IF NOT EXISTS."""
    pool = await init_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute(USERS_SCHEMA)
    logger.info("Users schema ready.")
