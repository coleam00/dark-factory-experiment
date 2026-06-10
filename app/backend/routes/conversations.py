"""Conversation management routes.

All handlers are user-scoped (MISSION §10 #3). A conversation that exists but
belongs to another user returns 404, not 403 — don't leak existence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from backend.auth.dependencies import get_current_user
from backend.db import repository

router = APIRouter()


def _clean_video_scope(scope: list[str] | None) -> list[str] | None:
    """Strip entries, drop empties, dedupe preserving order, reject empty list."""
    if scope is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in scope:
        entry = entry.strip()
        if entry and entry not in seen:
            seen.add(entry)
            cleaned.append(entry)
    return cleaned if cleaned else None


class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    video_scope: list[str] | None = None

    @field_validator("video_scope", mode="before")
    @classmethod
    def _validate_scope(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not isinstance(v, list):
            raise ValueError("video_scope must be a list")
        if isinstance(v, list) and len(v) == 0:
            raise ValueError("video_scope must not be an empty list")
        cleaned = _clean_video_scope(v)
        if cleaned is not None and len(cleaned) > 100:
            raise ValueError("video_scope may not contain more than 100 entries")
        return cleaned


class ConversationScopeSet(BaseModel):
    video_ids: list[str]

    @field_validator("video_ids", mode="before")
    @classmethod
    def _validate_ids(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("video_ids must be a list")
        seen: set[str] = set()
        cleaned: list[str] = []
        for entry in v:
            if not isinstance(entry, str):
                raise ValueError("each video_id must be a string")
            entry = entry.strip()
            if entry and entry not in seen:
                seen.add(entry)
                cleaned.append(entry)
        if not cleaned:
            raise ValueError("video_ids must contain at least one valid video id")
        if len(cleaned) > 100:
            raise ValueError("video_ids may not contain more than 100 entries")
        return cleaned


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
    video_scope = body.video_scope if body else None

    if video_scope is not None:
        all_videos = await repository.list_videos()
        valid_ids = {v["id"] for v in all_videos if v.get("id")}
        unknown = [vid for vid in video_scope if vid not in valid_ids]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown video ids in scope: {unknown}",
            )

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


@router.patch("/conversations/{conv_id}/scope")
async def set_conversation_scope(
    conv_id: str,
    body: ConversationScopeSet,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Set the video scope on an unscoped conversation. Set-once by design."""
    user_id = str(current_user["id"])
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("video_scope") is not None:
        raise HTTPException(status_code=409, detail="Conversation scope is already set")

    all_videos = await repository.list_videos()
    valid_ids = {v["id"] for v in all_videos if v.get("id")}
    unknown = [vid for vid in body.video_ids if vid not in valid_ids]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown video ids in scope: {unknown}",
        )

    updated = await repository.set_conversation_video_scope(
        conv_id, user_id=user_id, video_scope=body.video_ids
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Conversation scope is already set")

    conv = await repository.get_conversation(conv_id, user_id=user_id)
    return conv


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


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
