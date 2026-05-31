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
    # Optional video scope (issue #279). None/absent = search the whole library;
    # a list restricts this conversation's retrieval to those video ids.
    scoped_video_ids: list[str] | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationScope(BaseModel):
    # None clears the scope (search everything); a list restricts retrieval to
    # the given video ids. An empty list is allowed but yields no results.
    scoped_video_ids: list[str] | None = None


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
    scoped_video_ids = body.scoped_video_ids if body else None
    return await repository.create_conversation(
        user_id=str(current_user["id"]),
        title=title,
        scoped_video_ids=scoped_video_ids,
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
    body: ConversationScope,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Set or clear the video scope for a conversation (issue #279).

    Once set, the assistant's answers and citations in this conversation only
    draw from the chosen videos. Pass `scoped_video_ids: null` to clear the
    scope and go back to searching the whole library.
    """
    user_id = str(current_user["id"])
    updated = await repository.update_conversation_scope(
        conv_id, user_id=user_id, scoped_video_ids=body.scoped_video_ids
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await repository.get_conversation(conv_id, user_id=user_id)


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
