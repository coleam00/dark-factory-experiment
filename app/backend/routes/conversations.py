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


class ConversationScope(BaseModel):
    """Body for PATCH /conversations/{conv_id}/scope (issue #279).

    None or [] clears the scope (conversation searches the whole library).
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
async def set_conversation_scope(
    conv_id: str,
    body: ConversationScope,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Set or clear the conversation's video scope (issue #279).

    A non-empty selection restricts retrieval and citations in this
    conversation to the chosen videos; None/[] clears the scope back to
    whole-library search. Unknown video ids are dropped; a selection that
    resolves to zero known videos is rejected (400) so the user never
    silently ends up with an empty library.
    """
    user_id = str(current_user["id"])
    ids = body.video_ids or None
    if ids is not None:
        known = {v["id"] for v in await repository.list_videos()}
        # Dedup (preserving order) and drop ids not in the library.
        ids = [vid for vid in dict.fromkeys(ids) if vid in known]
        if not ids:
            raise HTTPException(status_code=400, detail="No valid videos in scope selection")
    updated = await repository.update_conversation_scope(conv_id, user_id=user_id, video_ids=ids)
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await repository.get_conversation(conv_id, user_id=user_id)


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
