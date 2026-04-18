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


@router.get("/conversations/search")
async def search_conversations(
    q: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    return await repository.search_conversations_by_title(
        user_id=str(current_user["id"]),
        query=q,
    )


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
