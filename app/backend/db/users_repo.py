"""
Users repository — all raw SQL for the `users` Postgres table lives here.

Mirrors the aiosqlite repository.py pattern: no ORM, parameterised queries
via asyncpg's `$1`/`$2` placeholders, one function per operation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from backend.db.postgres import get_pg_pool


async def create_user(email: str, password_hash: str) -> dict[str, Any]:
    """Insert a new user. Raises asyncpg.UniqueViolationError on duplicate email."""
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, password_hash)
            VALUES ($1, $2)
            RETURNING id, email, created_at, last_login_at
            """,
            email,
            password_hash,
        )
    assert row is not None  # RETURNING always yields a row on successful INSERT
    return dict(row)


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Fetch user by email (case-insensitive via CITEXT). Returns None if missing."""
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, password_hash, created_at, last_login_at
            FROM users
            WHERE email = $1
            """,
            email,
        )
    return dict(row) if row else None


async def get_user_by_id(user_id: UUID | str) -> dict[str, Any] | None:
    """Fetch user by UUID. Returns None if missing."""
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, created_at, last_login_at
            FROM users
            WHERE id = $1
            """,
            UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id,
        )
    return dict(row) if row else None


async def update_last_login(user_id: UUID | str) -> None:
    """Stamp last_login_at = now() for a successful login."""
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_login_at = now() WHERE id = $1",
            UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id,
        )
