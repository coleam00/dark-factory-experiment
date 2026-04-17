"""
Rate-limiter for the 25 messages / user / 24 hours cap (MISSION §10 invariant #1).

The cap value is **hardcoded** and **not configurable**. No env var, no
DATABASE override, no admin bypass. Any PR that tries to raise, lower,
remove, or make this value configurable must be rejected at triage — see
FACTORY_RULES.md §2.7 and the protected-paths list in CLAUDE.md.

Counting strategy: sliding-window `count(*)` over `user_messages` in the last
24 hours. Enforcement happens in `POST /api/conversations/{id}/messages`
BEFORE the SSE stream starts; the audit row is inserted at that moment, so
partial streams (client aborts) still count against the user's quota.

Race-safety: wrap the count + insert in a single transaction holding a
per-user `pg_advisory_xact_lock`. This serialises concurrent sends from the
same user so two parallel requests can never both see count=24 and both
insert (which would let 26 messages through). The lock is released on
transaction end; it has zero impact on other users because `hashtext(uuid)`
gives a per-user key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from backend.db import user_messages_repo
from backend.db.postgres import get_pg_pool

# The cap. Do not change this value in code — it is governed by MISSION §10 #1.
DAILY_MESSAGE_CAP: int = 25
WINDOW_HOURS: int = 24


@dataclass(frozen=True)
class RateLimitStatus:
    """Snapshot of a user's rate-limit state — for the /me counter."""

    used: int
    remaining: int
    resets_at: datetime | None  # None when used == 0 (nothing to reset)


class RateLimitExceeded(Exception):
    """Raised by `check_and_record` when the user is over the cap."""

    def __init__(self, reset_at: datetime):
        self.reset_at = reset_at
        super().__init__(
            f"Rate limit of {DAILY_MESSAGE_CAP} messages/{WINDOW_HOURS}h exceeded; "
            f"resets at {reset_at.isoformat()}"
        )


def _as_uuid(user_id: UUID | str) -> UUID:
    return user_id if isinstance(user_id, UUID) else UUID(str(user_id))


def _advisory_lock_key(user_id: UUID) -> int:
    """Stable signed 64-bit key for `pg_advisory_xact_lock` derived from the UUID.

    `pg_advisory_xact_lock(int8)` wants a bigint; we XOR the two 64-bit halves
    of the UUID so collisions are astronomically unlikely.
    """
    hi = user_id.int >> 64
    lo = user_id.int & ((1 << 64) - 1)
    key = hi ^ lo
    # Fold unsigned → signed 64-bit (Postgres bigint is signed).
    if key >= 1 << 63:
        key -= 1 << 64
    return key


async def check_and_record(user_id: UUID | str) -> None:
    """Atomically verify the user is under the cap and record a new message row.

    Raises `RateLimitExceeded` with a reset_at timestamp if the user would
    exceed the cap. On success, the audit row is already committed.
    """
    uid = _as_uuid(user_id)
    pool = get_pg_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Serialise per-user sends — no two concurrent requests can both
        # pass the count check and both insert.
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _advisory_lock_key(uid))
        count = await user_messages_repo.count_messages_in_window(conn, uid, hours=WINDOW_HOURS)
        if count >= DAILY_MESSAGE_CAP:
            oldest = await user_messages_repo.oldest_message_in_window_created_at(
                conn, uid, hours=WINDOW_HOURS
            )
            # `oldest` is guaranteed non-None when count >= cap > 0.
            assert oldest is not None
            reset_at = oldest + timedelta(hours=WINDOW_HOURS)
            raise RateLimitExceeded(reset_at=reset_at)
        await user_messages_repo.insert_message_row(conn, uid)


async def get_status(user_id: UUID | str) -> RateLimitStatus:
    """Read-only view of the user's current quota state. Used by /api/auth/me."""
    uid = _as_uuid(user_id)
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        used = await user_messages_repo.count_messages_in_window(conn, uid, hours=WINDOW_HOURS)
        remaining = max(0, DAILY_MESSAGE_CAP - used)
        resets_at: datetime | None = None
        if used > 0:
            oldest = await user_messages_repo.oldest_message_in_window_created_at(
                conn, uid, hours=WINDOW_HOURS
            )
            if oldest is not None:
                resets_at = oldest + timedelta(hours=WINDOW_HOURS)
    return RateLimitStatus(used=used, remaining=remaining, resets_at=resets_at)
