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
    video_ids: list[str] | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationUpdate(BaseModel):
    title: str | None = None
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
    video_ids = body.video_ids if body else None
    return await repository.create_conversation(
        user_id=str(current_user["id"]),
        title=title,
        video_ids=video_ids,
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
async def update_conversation(
    conv_id: str,
    body: ConversationUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user["id"])
    if body.title is not None:
        updated = await repository.update_conversation_title(
            conv_id, user_id=user_id, title=body.title
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Conversation not found")
    if body.video_ids is not None:
        updated = await repository.update_conversation_video_ids(
            conv_id, user_id=user_id, video_ids=body.video_ids or None
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Conversation not found")
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    return conv


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
