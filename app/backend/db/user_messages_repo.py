"""
user_messages repository — audit-table access for the 25 msg/user/24h cap.

Backed by the Postgres `user_messages` table created in `postgres.py`. One row
per streamed message; the sliding-window counter is `count(*) WHERE created_at
> now() - interval 'N hours'`.

All three functions accept a live asyncpg Connection so the caller controls
the transaction (critical for the count+insert race in `rate_limit.py`). A
module-level pool helper would work for the `/me` counter read, but taking a
connection keeps the call shape identical across callers.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg


def _as_uuid(user_id: UUID | str) -> UUID:
    return user_id if isinstance(user_id, UUID) else UUID(str(user_id))


async def insert_message_row(conn: asyncpg.Connection, user_id: UUID | str) -> None:
    """Append one row to the audit table for `user_id`. Timestamp = now()."""
    await conn.execute(
        "INSERT INTO user_messages (user_id) VALUES ($1)",
        _as_uuid(user_id),
    )


async def count_messages_in_window(
    conn: asyncpg.Connection, user_id: UUID | str, hours: int = 24
) -> int:
    """Count rows for `user_id` in the last `hours` hours (default 24)."""
    count = await conn.fetchval(
        f"""
        SELECT count(*) FROM user_messages
        WHERE user_id = $1
          AND created_at > now() - interval '{int(hours)} hours'
        """,
        _as_uuid(user_id),
    )
    return int(count or 0)


async def oldest_message_in_window_created_at(
    conn: asyncpg.Connection, user_id: UUID | str, hours: int = 24
) -> datetime | None:
    """Return `min(created_at)` for rows in the current sliding window, or None."""
    row = await conn.fetchval(
        f"""
        SELECT min(created_at) FROM user_messages
        WHERE user_id = $1
          AND created_at > now() - interval '{int(hours)} hours'
        """,
        _as_uuid(user_id),
    )
    if row is None:
        return None
    assert isinstance(row, datetime)
    return row
