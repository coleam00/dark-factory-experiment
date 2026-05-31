"""
Tests for POST /api/conversations/{conv_id}/messages/{message_id}/regenerate (issue #280).

Verifies:
  - Happy path: latest assistant message regenerates with new SSE stream
  - Usage counted: successful regenerate adds one rate-limit audit row
  - Rate-limited: pre-seed 25 rows → 429, old answer preserved
  - Ownership: wrong user → 404
  - Guard rails: non-latest, user message, no preceding user turn → 400
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def memory_store():
    """In-memory store for conversations and messages."""
    return {
        "conversations": {},
        "messages": {},
    }


@pytest.fixture(autouse=True)
def patch_repository(monkeypatch, memory_store):
    """Monkeypatch repository functions to use in-memory stores."""
    from backend.db import repository as repo_mod

    async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict:
        conv_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        conv = {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }
        memory_store["conversations"][conv_id] = conv
        return conv

    async def get_conversation(conv_id: str, user_id: str) -> dict | None:
        conv = memory_store["conversations"].get(conv_id)
        if conv and conv["user_id"] == user_id:
            return dict(conv)
        return None

    async def create_message(
        *,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
    ) -> dict | None:
        conv = memory_store["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return None
        msg_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        msg = {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "sources": sources,
            "created_at": now,
        }
        memory_store["messages"].setdefault(conversation_id, []).append(msg)
        return msg

    async def list_messages(conversation_id: str, user_id: str) -> list[dict]:
        conv = memory_store["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return []
        msgs = memory_store["messages"].get(conversation_id, [])
        return [dict(m) for m in msgs]

    async def delete_message(message_id: str, conversation_id: str, user_id: str) -> bool:
        conv = memory_store["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return False
        msgs = memory_store["messages"].get(conversation_id, [])
        for idx, m in enumerate(msgs):
            if m["id"] == message_id:
                msgs.pop(idx)
                return True
        return False

    async def touch_conversation(conv_id: str, user_id: str) -> None:
        pass

    monkeypatch.setattr(repo_mod, "create_conversation", create_conversation)
    monkeypatch.setattr(repo_mod, "get_conversation", get_conversation)
    monkeypatch.setattr(repo_mod, "create_message", create_message)
    monkeypatch.setattr(repo_mod, "list_messages", list_messages)
    monkeypatch.setattr(repo_mod, "delete_message", delete_message)
    monkeypatch.setattr(repo_mod, "touch_conversation", touch_conversation)


@pytest.fixture(autouse=True)
def override_current_user(monkeypatch):
    """Always authenticate as a fixed test user."""
    from backend.auth.dependencies import get_current_user
    from backend.main import app

    test_user = {
        "id": "test-user-id",
        "email": "test@example.com",
        "is_member": False,
    }

    async def fake_get_current_user():
        return test_user

    app.dependency_overrides[get_current_user] = fake_get_current_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


async def _seed_conversation(memory_store):
    """Seed a conversation with a user message and an assistant message."""
    from backend.db import repository as repo_mod

    conv = await repo_mod.create_conversation(user_id="test-user-id", title="Test")
    conv_id = conv["id"]

    user_msg = await repo_mod.create_message(
        conversation_id=conv_id,
        user_id="test-user-id",
        role="user",
        content="Hello",
    )
    assistant_msg = await repo_mod.create_message(
        conversation_id=conv_id,
        user_id="test-user-id",
        role="assistant",
        content="Hi there",
    )
    return conv_id, user_msg["id"], assistant_msg["id"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_regenerate_happy_path(client, memory_store):
    conv_id, _user_msg_id, assistant_msg_id = await _seed_conversation(memory_store)

    async def fake_stream_chat(*args, **kwargs):
        tool_executor = kwargs.get("tool_executor")
        if tool_executor:
            await tool_executor("search_videos", '{"query": "test"}')
        yield 'data: "Regenerated"\n\n'
        yield 'data: [DONE]\n\n'

    fake_chunk = {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Test",
        "video_url": "https://example.com",
        "start_seconds": 0,
        "end_seconds": 10,
        "snippet": "s",
    }

    with (
        patch("backend.routes.messages.stream_chat", fake_stream_chat),
        patch(
            "backend.routes.messages.execute_tool",
            new=AsyncMock(return_value={"ok": True, "chunks": [fake_chunk]}),
        ),
    ):
        r = await client.post(
            f"/api/conversations/{conv_id}/messages/{assistant_msg_id}/regenerate"
        )

    assert r.status_code == 200
    text = r.text
    assert 'data: "Regenerated"' in text
    assert "event: sources" in text
    assert "data: [DONE]" in text

    # Old assistant message should be gone; user + new assistant remain
    msgs = memory_store["messages"][conv_id]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Regenerated"
    assert msgs[1]["sources"] is not None


# ---------------------------------------------------------------------------
# Usage counted
# ---------------------------------------------------------------------------


async def test_regenerate_counts_toward_rate_limit(client, memory_store, message_store):
    conv_id, _user_msg_id, assistant_msg_id = await _seed_conversation(memory_store)

    async def fake_stream_chat(*args, **kwargs):
        yield 'data: "OK"\n\n'
        yield 'data: [DONE]\n\n'

    with patch("backend.routes.messages.stream_chat", fake_stream_chat):
        r = await client.post(
            f"/api/conversations/{conv_id}/messages/{assistant_msg_id}/regenerate"
        )

    assert r.status_code == 200
    assert len(message_store["test-user-id"]) == 1


# ---------------------------------------------------------------------------
# Rate-limited
# ---------------------------------------------------------------------------


async def test_regenerate_returns_429_when_over_cap(client, memory_store, message_store):
    conv_id, _user_msg_id, assistant_msg_id = await _seed_conversation(memory_store)

    now = datetime.now(UTC)
    message_store["test-user-id"] = [now - timedelta(minutes=i) for i in range(25)]

    r = await client.post(
        f"/api/conversations/{conv_id}/messages/{assistant_msg_id}/regenerate"
    )

    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["limit"] == 25

    # Old answer preserved
    msgs = memory_store["messages"][conv_id]
    assert any(m["id"] == assistant_msg_id for m in msgs)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_regenerate_404_for_other_users_conversation(client, memory_store):
    conv_id, _user_msg_id, assistant_msg_id = await _seed_conversation(memory_store)

    # Change conversation owner
    memory_store["conversations"][conv_id]["user_id"] = "other-user"

    r = await client.post(
        f"/api/conversations/{conv_id}/messages/{assistant_msg_id}/regenerate"
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


async def test_regenerate_400_for_non_latest_message(client, memory_store):
    conv_id, _user_msg_id, assistant_msg_id = await _seed_conversation(memory_store)

    from backend.db import repository as repo_mod

    await repo_mod.create_message(
        conversation_id=conv_id,
        user_id="test-user-id",
        role="assistant",
        content="Second",
    )

    r = await client.post(
        f"/api/conversations/{conv_id}/messages/{assistant_msg_id}/regenerate"
    )
    assert r.status_code == 400


async def test_regenerate_400_for_user_message(client, memory_store):
    conv_id, user_msg_id, _assistant_msg_id = await _seed_conversation(memory_store)

    r = await client.post(
        f"/api/conversations/{conv_id}/messages/{user_msg_id}/regenerate"
    )
    assert r.status_code == 400


async def test_regenerate_400_when_no_preceding_user_message(client, memory_store):
    from backend.db import repository as repo_mod

    conv = await repo_mod.create_conversation(user_id="test-user-id", title="Empty")
    conv_id = conv["id"]

    assistant_msg = await repo_mod.create_message(
        conversation_id=conv_id,
        user_id="test-user-id",
        role="assistant",
        content="Lonely",
    )

    r = await client.post(
        f"/api/conversations/{conv_id}/messages/{assistant_msg['id']}/regenerate"
    )
    assert r.status_code == 400


async def test_regenerate_404_for_empty_conversation(client):
    r = await client.post("/api/conversations/nonexistent/messages/msg-id/regenerate")
    assert r.status_code == 404
