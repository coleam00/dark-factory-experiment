"""
Tests for POST /api/conversations/{conv_id}/messages/regenerate (issue #280).

Verifies that regenerating the last assistant message:
  - requires ownership and a valid assistant-as-last-message history
  - runs the same rate-limit gate as a normal send (so a 429 leaves the
    old answer untouched and consumes no quota)
  - deletes the old assistant message, reuses the existing user question,
    streams a fresh response with new sources, and persists the replacement
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend import rate_limit
from backend.auth.dependencies import get_current_user
from backend.main import app


@pytest.fixture
def bypass_auth():
    """Satisfy the auth dependency for the regenerate route."""
    stub = {"id": str(uuid4()), "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


def _history(user_question: str = "hi", old_answer: str = "old answer") -> list[dict]:
    return [
        {
            "id": "msg-user-1",
            "conversation_id": "conv-1",
            "role": "user",
            "content": user_question,
            "created_at": "",
        },
        {
            "id": "msg-assistant-1",
            "conversation_id": "conv-1",
            "role": "assistant",
            "content": old_answer,
            "created_at": "",
            "sources": None,
        },
    ]


def _fake_conv(user_id: str) -> dict:
    return {"id": "conv-1", "user_id": user_id, "title": "Test Chat"}


async def _read_sse(response) -> str:
    """Consume an httpx streaming response into a single string."""
    chunks = []
    async for chunk in response.aiter_text():
        chunks.append(chunk)
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Validation / scoping
# ---------------------------------------------------------------------------


async def test_regenerate_returns_404_when_conversation_missing(bypass_auth, monkeypatch):
    from backend.db import repository

    async def fake_get_conv(c, **kwargs):
        return None

    async def fake_list(c, **kwargs):
        return _history()

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)

    client = await _client()
    try:
        r = await client.post("/api/conversations/conv-1/messages/regenerate")
        assert r.status_code == 404
    finally:
        await client.aclose()


async def test_regenerate_returns_409_when_conversation_empty(bypass_auth, monkeypatch):
    from backend.db import repository

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(bypass_auth["id"])

    async def fake_list(c, **kwargs):
        return []

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)

    client = await _client()
    try:
        r = await client.post("/api/conversations/conv-1/messages/regenerate")
        assert r.status_code == 409
        assert "No assistant response to regenerate" in r.json()["detail"]
    finally:
        await client.aclose()


async def test_regenerate_returns_409_when_last_message_is_user(bypass_auth, monkeypatch):
    from backend.db import repository

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(bypass_auth["id"])

    async def fake_list(c, **kwargs):
        return [
            {
                "id": "msg-user-1",
                "conversation_id": "conv-1",
                "role": "user",
                "content": "hi",
                "created_at": "",
            }
        ]

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)

    client = await _client()
    try:
        r = await client.post("/api/conversations/conv-1/messages/regenerate")
        assert r.status_code == 409
    finally:
        await client.aclose()


async def test_regenerate_returns_409_when_no_user_precedes_assistant(bypass_auth, monkeypatch):
    from backend.db import repository

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(bypass_auth["id"])

    async def fake_list(c, **kwargs):
        return [
            {
                "id": "msg-assistant-1",
                "conversation_id": "conv-1",
                "role": "assistant",
                "content": "orphan",
                "created_at": "",
                "sources": None,
            }
        ]

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)

    client = await _client()
    try:
        r = await client.post("/api/conversations/conv-1/messages/regenerate")
        assert r.status_code == 409
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_regenerate_deletes_old_answer_and_streams_new_one(bypass_auth, monkeypatch):
    from backend.db import repository

    uid = bypass_auth["id"]
    history = _history()
    deleted_ids: list[str] = []
    created_calls: list[dict] = []

    async def fake_delete(message_id: str, conversation_id: str, user_id: str) -> bool:
        deleted_ids.append(message_id)
        return True

    async def fake_create(*, conversation_id, user_id, role, content, sources=None):
        created_calls.append(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "sources": sources,
            }
        )
        return {"id": "msg-assistant-2"}

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(uid)

    async def fake_list(c, **kwargs):
        return list(history)

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)
    monkeypatch.setattr(repository, "delete_message", fake_delete)
    monkeypatch.setattr(repository, "create_message", fake_create)

    new_sources = [
        {
            "chunk_id": "c-new",
            "video_id": "v-new",
            "video_title": "New Video",
            "video_url": "https://youtube.com/watch?v=new",
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "snippet": "new snippet",
        }
    ]

    async def fake_execute_tool(name: str, raw_args: str, **kwargs):
        return {"ok": True, "chunks": list(new_sources)}

    async def fake_list_videos():
        return []

    monkeypatch.setattr("backend.routes.messages.execute_tool", fake_execute_tool)
    monkeypatch.setattr(repository, "list_videos", fake_list_videos)
    monkeypatch.setattr("backend.routes.messages.LLM_TOOLS_ENABLED", True)

    stream_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_stream(*args, **kwargs):
        stream_calls.append((args, kwargs))
        tool_executor = kwargs.get("tool_executor")
        if tool_executor:
            await tool_executor("search_videos", '{"query":"x"}')
        for token in ("New", " answer"):
            yield f"data: {json.dumps(token)}\n\n"
        yield "data: [DONE]\n\n"

    with patch("backend.routes.messages.stream_chat", side_effect=fake_stream):
        client = await _client()
        try:
            r = await client.post("/api/conversations/conv-1/messages/regenerate")
            assert r.status_code == 200, r.text
            body = await _read_sse(r)
            assert 'data: "New"' in body
            assert 'data: " answer"' in body
            assert "event: sources" in body
            assert "New Video" in body
            assert "data: [DONE]" in body
        finally:
            await client.aclose()

    assert deleted_ids == ["msg-assistant-1"]

    assert len(stream_calls) == 1
    args, _ = stream_calls[0]
    llm_messages = args[0]
    assert llm_messages == [{"role": "user", "content": "hi"}]

    assert len(created_calls) == 1
    assert created_calls[0]["role"] == "assistant"
    assert created_calls[0]["content"] == "New answer"
    persisted_sources = created_calls[0]["sources"]
    assert persisted_sources is not None
    assert len(persisted_sources) == 1
    assert persisted_sources[0]["video_title"] == "New Video"


# ---------------------------------------------------------------------------
# Rate-limit behavior
# ---------------------------------------------------------------------------


async def test_regenerate_returns_429_at_cap_without_deleting(
    bypass_auth, message_store, monkeypatch
):
    from backend.db import repository

    uid = bypass_auth["id"]
    deleted: list[str] = []

    async def fake_delete(message_id: str, conversation_id: str, user_id: str) -> bool:
        deleted.append(message_id)
        return True

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(uid)

    async def fake_list(c, **kwargs):
        return _history()

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)
    monkeypatch.setattr(repository, "delete_message", fake_delete)

    now = datetime.now(UTC)
    message_store[uid] = [now - timedelta(minutes=i) for i in range(25)]

    client = await _client()
    try:
        r = await client.post("/api/conversations/conv-1/messages/regenerate")
        assert r.status_code == 429, r.text
        body = r.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
    finally:
        await client.aclose()

    assert deleted == []
    assert len(message_store[uid]) == 25


async def test_regenerate_consumes_one_quota_on_success(bypass_auth, message_store, monkeypatch):
    from backend.db import repository

    uid = bypass_auth["id"]

    async def fake_delete(*args, **kwargs) -> bool:
        return True

    async def fake_create(*args, **kwargs):
        return {"id": "msg-assistant-2"}

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(uid)

    async def fake_list(c, **kwargs):
        return _history()

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)
    monkeypatch.setattr(repository, "delete_message", fake_delete)
    monkeypatch.setattr(repository, "create_message", fake_create)

    async def fake_stream(*args, **kwargs):
        yield f"data: {json.dumps('ok')}\n\n"
        yield "data: [DONE]\n\n"

    with patch("backend.routes.messages.stream_chat", side_effect=fake_stream):
        client = await _client()
        try:
            r = await client.post("/api/conversations/conv-1/messages/regenerate")
            assert r.status_code == 200, r.text
            await _read_sse(r)
        finally:
            await client.aclose()

    assert len(message_store[uid]) == 1


# ---------------------------------------------------------------------------
# LLM history shape
# ---------------------------------------------------------------------------


async def test_regenerate_feeds_history_minus_deleted_assistant(bypass_auth, monkeypatch):
    from backend.db import repository

    uid = bypass_auth["id"]
    history = [
        {"id": "u1", "role": "user", "content": "first", "created_at": ""},
        {
            "id": "a1",
            "role": "assistant",
            "content": "first answer",
            "created_at": "",
            "sources": None,
        },
        {"id": "u2", "role": "user", "content": "second", "created_at": ""},
        {
            "id": "a2",
            "role": "assistant",
            "content": "second answer",
            "created_at": "",
            "sources": None,
        },
    ]

    async def fake_delete(*args, **kwargs) -> bool:
        return True

    async def fake_get_conv(c, **kwargs):
        return _fake_conv(uid)

    async def fake_list(c, **kwargs):
        return list(history)

    async def fake_create(*args, **kwargs):
        return {"id": "new"}

    monkeypatch.setattr(repository, "get_conversation", fake_get_conv)
    monkeypatch.setattr(repository, "list_messages", fake_list)
    monkeypatch.setattr(repository, "delete_message", fake_delete)
    monkeypatch.setattr(repository, "create_message", fake_create)

    stream_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_stream(*args, **kwargs):
        stream_calls.append((args, kwargs))
        yield f"data: {json.dumps('x')}\n\n"
        yield "data: [DONE]\n\n"

    with patch("backend.routes.messages.stream_chat", side_effect=fake_stream):
        client = await _client()
        try:
            r = await client.post("/api/conversations/conv-1/messages/regenerate")
            assert r.status_code == 200
            await _read_sse(r)
        finally:
            await client.aclose()

    assert len(stream_calls) == 1
    args, _ = stream_calls[0]
    llm_messages = args[0]
    assert llm_messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second"},
    ]
    assert not any(
        m["role"] == "assistant" and m["content"] == "second answer" for m in llm_messages
    )
