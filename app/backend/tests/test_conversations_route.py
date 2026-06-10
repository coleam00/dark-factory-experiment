"""
Route-level tests for per-conversation video scope (issue #279).

The create_conversation handler is a plain async function, so we call it
directly with a fake current_user and a mocked repository boundary — this
exercises the request model + handler wiring without standing up the full
ASGI/auth/DB stack (which the broader cross-user suite in
test_conversation_scoping.py still requires and currently skips).
"""

from __future__ import annotations

import pytest

from backend.routes import conversations as conv_route
from backend.routes.conversations import ConversationCreate, create_conversation


def test_model_accepts_scoped_video_ids():
    body = ConversationCreate(title="T", scoped_video_ids=["v1", "v2"])
    assert body.scoped_video_ids == ["v1", "v2"]


def test_model_defaults_scope_to_none():
    body = ConversationCreate()
    assert body.scoped_video_ids is None
    assert body.title == "New Conversation"


@pytest.mark.asyncio
async def test_handler_passes_scope_to_repo(monkeypatch):
    captured: dict = {}

    async def fake_create(*, user_id, title, scoped_video_ids=None):
        captured.update(user_id=user_id, title=title, scoped_video_ids=scoped_video_ids)
        return {
            "id": "c1",
            "user_id": user_id,
            "title": title,
            "scoped_video_ids": scoped_video_ids,
        }

    monkeypatch.setattr(conv_route.repository, "create_conversation", fake_create)

    body = ConversationCreate(title="Scoped", scoped_video_ids=["v1", "v2"])
    result = await create_conversation(body=body, current_user={"id": "u1"})

    assert captured["scoped_video_ids"] == ["v1", "v2"]
    assert captured["user_id"] == "u1"
    assert result["scoped_video_ids"] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_handler_omitted_scope_is_none(monkeypatch):
    captured: dict = {}

    async def fake_create(*, user_id, title, scoped_video_ids=None):
        captured["scoped_video_ids"] = scoped_video_ids
        return {"id": "c1", "user_id": user_id, "title": title, "scoped_video_ids": None}

    monkeypatch.setattr(conv_route.repository, "create_conversation", fake_create)

    # No body at all → handler must default scope to None (search everything).
    await create_conversation(body=None, current_user={"id": "u1"})
    assert captured["scoped_video_ids"] is None
