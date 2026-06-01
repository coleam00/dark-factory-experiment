"""Conversation management routes.

All handlers are user-scoped (MISSION §10 #3). A conversation that exists but
belongs to another user returns 404, not 403 — don't leak existence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.db import repository

router = APIRouter()


class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    # Optional video scope (issue #279): pin the conversation to a subset of
    # videos so the assistant only answers from them. Omitted/empty = unscoped.
    video_scope: list[str] | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationScopeUpdate(BaseModel):
    # The video ids this conversation should be scoped to. An empty list clears
    # the scope and restores whole-library search.
    video_ids: list[str]


def _normalize_scope(video_ids: list[str] | None) -> list[str] | None:
    """Trim, drop blanks, de-dupe (order-preserving). Empty → None (unscoped)."""
    if not video_ids:
        return None
    seen: list[str] = []
    for v in video_ids:
        if isinstance(v, str):
            trimmed = v.strip()
            if trimmed and trimmed not in seen:
                seen.append(trimmed)
    return seen or None


@router.get("/conversations")
async def list_conversations(current_user: dict[str, Any] = Depends(get_current_user)):
    return await repository.list_conversations(user_id=str(current_user["id"]))


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Create a new empty conversation. Body is optional; defaults to title='New Conversation'.

    An optional ``video_scope`` pins the conversation to a subset of videos
    (issue #279); omitted or empty leaves it unscoped (whole-library search).
    """
    title = body.title if body else "New Conversation"
    video_scope = _normalize_scope(body.video_scope) if body else None
    return await repository.create_conversation(
        user_id=str(current_user["id"]),
        title=title,
        video_scope=video_scope,
    )


@router.get("/conversations/search")
async def search_conversations(
    q: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Title-contains search. Must be declared BEFORE /conversations/{conv_id}
    or FastAPI routes "search" to the path-parameter handler and returns 404."""
    return await repository.search_conversations_by_title(
        user_id=str(current_user["id"]),
        query=q,
    )


@router.get("/conversations/{conv_id}")
async def get_conversation(
    conv_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user["id"])
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await repository.list_messages(conv_id, user_id=user_id)
    return {**conv, "messages": messages}


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    deleted = await repository.delete_conversation(conv_id, user_id=str(current_user["id"]))
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str,
    body: ConversationRename,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    updated = await repository.update_conversation_title(
        conv_id, user_id=str(current_user["id"]), title=body.title
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = await repository.get_conversation(conv_id, user_id=str(current_user["id"]))
    return conv


@router.put("/conversations/{conv_id}/scope")
async def set_conversation_scope(
    conv_id: str,
    body: ConversationScopeUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Scope a conversation to a subset of videos (issue #279).

    Pass the video ids to restrict the assistant to; pass an empty list to
    clear the scope and search the whole library again. Owner-scoped — a
    mismatched conversation returns 404 (no existence leak), exactly like the
    other conversation mutations here.
    """
    user_id = str(current_user["id"])
    scope = _normalize_scope(body.video_ids)
    updated = await repository.update_conversation_scope(
        conv_id, user_id=user_id, video_scope=scope
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await repository.get_conversation(conv_id, user_id=user_id)


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
