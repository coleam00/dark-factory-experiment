"""Conversation video reference routes.

Exposes which videos are cited in a user's conversations so the frontend
can filter the sidebar history by video.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.auth.dependencies import get_current_user
from backend.db import repository

router = APIRouter()


@router.get("/conversation-videos")
async def list_conversation_videos(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    return await repository.list_conversation_video_refs(user_id=str(current_user["id"]))
