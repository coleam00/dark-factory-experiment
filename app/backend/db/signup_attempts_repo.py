"""
signup_attempts repository — audit-table access for signup rate-limiting.

Backed by the Postgres `signup_attempts` table created in `postgres.py`. One
row per signup attempt (accepted, duplicate, or rate-limited). The sliding
windows are `count(*) WHERE created_at > now() - interval '...'`, filtered
by IP (for the per-IP window) or not (for the global window).

All functions take a live asyncpg Connection so the caller controls the
transaction — `signup_rate_limit.check` needs count+insert atomicity under a
pg_advisory_xact_lock, same pattern as `user_messages_repo.py`.
"""

from __future__ import annotations

import asyncpg


async def insert_attempt(
    conn: asyncpg.Connection,
    ip: str,
    email_attempted: str | None,
    outcome: str,
) -> None:
    """Append one row to the signup_attempts audit table."""
    await conn.execute(
        """
        INSERT INTO signup_attempts (ip, email_attempted, outcome)
        VALUES ($1::inet, $2, $3)
        """,
        ip,
        email_attempted,
        outcome,
    )


async def count_for_ip_in_window(conn: asyncpg.Connection, ip: str, window_seconds: int) -> int:
    """Count `accepted` signups from `ip` in the last `window_seconds` seconds.

    Per-IP limit counts only successful signups — a typo'd password shouldn't
    lock the user out of their household for an hour.
    """
    count = await conn.fetchval(
        f"""
        SELECT count(*) FROM signup_attempts
        WHERE ip = $1::inet
          AND outcome = 'accepted'
          AND created_at > now() - interval '{int(window_seconds)} seconds'
        """,
        ip,
    )
    return int(count or 0)


async def count_global_in_window(conn: asyncpg.Connection, window_seconds: int) -> int:
    """Count all signup attempts globally in the last `window_seconds`.

    Excludes `invalid` (Pydantic 400s — never written today, reserved). Counts
    `accepted`, `duplicate`, `ip_limited`, `global_limited` so an attacker
    cycling through IPs still trips the global cap.
    """
    count = await conn.fetchval(
        f"""
        SELECT count(*) FROM signup_attempts
        WHERE outcome <> 'invalid'
          AND created_at > now() - interval '{int(window_seconds)} seconds'
        """,
    )
    return int(count or 0)
