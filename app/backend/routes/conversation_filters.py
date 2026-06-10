"""Conversation filter route.

Lives in its own module — deliberately separate from `routes/conversations.py`,
which is a protected (human-authored) path — so the date/video filter endpoint
can be added without editing that file. The handler is user-scoped
(MISSION §10 #3): it only ever queries the authenticated caller's own
conversations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from backend.auth.dependencies import get_current_user
from backend.db import repository

router = APIRouter()


def _coerce_utc(dt: datetime | None) -> datetime | None:
    """Treat a naive datetime as UTC.

    asyncpg compares TIMESTAMPTZ columns against tz-aware datetimes; a naive
    value would raise at bind time. The frontend always sends Z-suffixed ISO
    strings, but coerce defensively so the API never 500s on naive input.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("/conversations/filter")
async def filter_conversations(
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    video_id: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Filter the caller's conversations by title text, ``updated_at`` range, and
    cited video. All params are optional and AND-combined; results come back
    newest-first (``updated_at DESC``), matching the default conversation list.

    Must be registered BEFORE ``/conversations/{conv_id}`` or FastAPI routes
    "filter" to the path-parameter handler and returns 404.
    """
    return await repository.filter_conversations(
        user_id=str(current_user["id"]),
        query=q,
        date_from=_coerce_utc(date_from),
        date_to=_coerce_utc(date_to),
        video_id=video_id,
    )
