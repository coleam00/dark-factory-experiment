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


def _normalize_video_ids(video_ids: list[str] | None) -> list[str]:
    """Strip falsy entries and dedupe preserving order."""
    if not video_ids:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for vid in video_ids:
        if not vid or not isinstance(vid, str):
            continue
        vid = vid.strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        normalized.append(vid)
    return normalized


async def _validate_video_ids_exist(video_ids: list[str]) -> None:
    """Raise 422 if any id is not in the video library."""
    known = {v["id"] for v in await repository.list_videos()}
    unknown = [vid for vid in video_ids if vid not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown video ids: {', '.join(unknown)}",
        )


class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    video_ids: list[str] | None = None


class ConversationRename(BaseModel):
    title: str


class ScopeSet(BaseModel):
    video_ids: list[str]

    @field_validator("video_ids")
    @classmethod
    def at_least_one_video(cls, v: list[str]) -> list[str]:
        if not _normalize_video_ids(v):
            raise ValueError("video_ids must contain at least one non-empty id")
        return v


@router.get("/conversations")
async def list_conversations(current_user: dict[str, Any] = Depends(get_current_user)):
    return await repository.list_conversations(user_id=str(current_user["id"]))


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Create a new empty conversation. Body is optional; defaults to
    title='New Conversation'. An optional `video_ids` list scopes the
    conversation to those videos (issue #279); empty/absent means unscoped.
    """
    title = body.title if body else "New Conversation"
    scoped = _normalize_video_ids(body.video_ids if body else None)
    if scoped:
        await _validate_video_ids_exist(scoped)
    return await repository.create_conversation(
        user_id=str(current_user["id"]),
        title=title,
        scoped_video_ids=scoped or None,
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


@router.post("/conversations/{conv_id}/scope")
async def set_conversation_scope(
    conv_id: str,
    body: ScopeSet,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Scope a still-unscoped conversation to a set of videos (issue #279).

    Write-once: the scope can only be set while `scoped_video_ids` is NULL.
    409 if a scope was already set; 404 on missing/foreign conversations
    (existence non-leak preserved).
    """
    user_id = str(current_user["id"])
    video_ids = _normalize_video_ids(body.video_ids)
    await _validate_video_ids_exist(video_ids)
    updated = await repository.set_conversation_scope(conv_id, user_id=user_id, video_ids=video_ids)
    if not updated:
        # Disambiguate: not found / foreign → 404; found-with-scope → 409.
        conv = await repository.get_conversation(conv_id, user_id=user_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        raise HTTPException(status_code=409, detail="Conversation scope already set")
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    return conv


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


@router.get("/videos")
async def list_videos():
    return await repository.list_videos()
