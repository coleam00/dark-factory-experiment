"""
Users repository — all raw SQL for the `users` Postgres table lives here.

Mirrors the aiosqlite repository.py pattern: no ORM, parameterised queries
via asyncpg's `$1`/`$2` placeholders, one function per operation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from backend.db.postgres import get_pg_pool


async def create_user(
    email: str,
    password_hash: str,
    *,
    conn: asyncpg.Connection | None = None,
) -> dict[str, Any]:
    """Insert a new user. Raises asyncpg.UniqueViolationError on duplicate email.

    If `conn` is supplied the caller owns the transaction (used by the signup
    handler so the user insert and the rate-limit audit row commit together
    under the same advisory lock). Otherwise grab a pool connection.
    """

    async def _insert(c: asyncpg.Connection) -> dict[str, Any]:
        row = await c.fetchrow(
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

    if conn is not None:
        return await _insert(conn)

    pool = get_pg_pool()
    async with pool.acquire() as new_conn:
        return await _insert(new_conn)


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Fetch user by email (case-insensitive via CITEXT). Returns None if missing."""
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, password_hash, created_at, last_login_at,
                   is_member, member_verified_at
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
            SELECT id, email, created_at, last_login_at,
                   is_member, member_verified_at
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


async def set_member_status(
    user_id: UUID | str,
    *,
    is_member: bool,
    conn: asyncpg.Connection | None = None,
) -> None:
    """Set is_member and stamp member_verified_at = now().

    Called from the auth routes after each Circle verification (signup, login,
    or /me refresh). Optional `conn` lets the caller run this inside an
    in-flight transaction so the user insert and the membership stamp commit
    atomically.
    """
    uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id

    async def _do(c: asyncpg.Connection) -> None:
        await c.execute(
            """
            UPDATE users
            SET is_member = $2, member_verified_at = now()
            WHERE id = $1
            """,
            uid,
            is_member,
        )

    if conn is not None:
        await _do(conn)
        return
    pool = get_pg_pool()
    async with pool.acquire() as new_conn:
        await _do(new_conn)
