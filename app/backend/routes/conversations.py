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


class ConversationRename(BaseModel):
    title: str


class ConversationScopeUpdate(BaseModel):
    """Per-conversation video scope (issue #279).

    ``video_ids`` is the set of video ids the conversation should draw from.
    None or an empty list clears the scope (search the whole library).
    """

    video_ids: list[str] | None = None


@router.get("/conversations")
async def list_conversations(current_user: dict[str, Any] = Depends(get_current_user)):
    return await repository.list_conversations(user_id=str(current_user["id"]))


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Create a new empty conversation. Body is optional; defaults to title='New Conversation'."""
    title = body.title if body else "New Conversation"
    return await repository.create_conversation(
        user_id=str(current_user["id"]),
        title=title,
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


@router.patch("/conversations/{conv_id}/scope")
async def update_conversation_scope(
    conv_id: str,
    body: ConversationScopeUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Set or clear the videos a conversation is scoped to (issue #279).

    An empty list (or null) clears the scope so retrieval searches the whole
    library again. A non-null list is validated against the library — unknown
    ids are rejected with 422 to avoid the silent "scope matches nothing" trap.
    Owner-scoped: a conversation that doesn't exist or isn't owned returns 404
    (no existence leak), matching the rename handler's semantics.
    """
    user_id = str(current_user["id"])

    # Normalize [] → None: a cleared scope is identical to never-scoped.
    video_ids = body.video_ids or None

    if video_ids is not None:
        all_videos = await repository.list_videos()
        known_ids = {v["id"] for v in all_videos if v.get("id")}
        unknown = [vid for vid in video_ids if vid not in known_ids]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown video id(s): {', '.join(unknown)}",
            )

    updated = await repository.update_conversation_scope(conv_id, user_id, video_ids)
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await repository.get_conversation(conv_id, user_id=user_id)


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
