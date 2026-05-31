"""Share-link routes for issue #278.

Two routers are exposed:
- share_router: owner-scoped create/revoke (auth required).
- public_share_router: unauthenticated read-only GET (no auth).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.db import repository

share_router = APIRouter()
public_share_router = APIRouter()


class ShareLinkResponse(BaseModel):
    token: str
    url_path: str


class SharedMessage(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None = None


class SharedConversation(BaseModel):
    title: str
    messages: list[SharedMessage]


@share_router.post("/conversations/{conversation_id}/share", response_model=ShareLinkResponse)
async def create_share_link(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Mint or rotate a share token for a conversation. Only the owner can call this."""
    token = secrets.token_urlsafe(32)
    ok = await repository.set_conversation_share_token(
        conversation_id, str(current_user["id"]), token
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"token": token, "url_path": f"/share/{token}"}


@share_router.delete("/conversations/{conversation_id}/share", status_code=204)
async def revoke_share_link(
    conversation_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Revoke a share token. Only the owner can call this."""
    ok = await repository.clear_conversation_share_token(
        conversation_id, str(current_user["id"])
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")


@public_share_router.get("/share/{token}", response_model=SharedConversation)
async def get_shared_conversation(token: str):
    """Public read-only view of a shared conversation. No auth required."""
    conv = await repository.get_conversation_by_share_token(token)
    if conv is None:
        raise HTTPException(status_code=404, detail="Share link not found")
    messages = await repository.list_messages_for_share_token(token)
    return {
        "title": conv["title"],
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "sources": m.get("sources"),
            }
            for m in messages
        ],
    }
