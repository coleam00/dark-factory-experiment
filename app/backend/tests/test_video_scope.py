"""Tests for conversation video scoping (issue #279).

Covers:
- retrieve_hybrid forwards allowed_video_ids to repository searches
- Tool executors thread allowed_video_ids down to repository/retriever
- execute_get_video_transcript scope guard
- Route-level validation and ownership guards
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app

# ---------------------------------------------------------------------------
# Spy helper (from test_retriever_member_filter.py)
# ---------------------------------------------------------------------------


class _Spy:
    def __init__(self, return_rows: list[dict[str, Any]] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.return_rows = return_rows or []

    async def __call__(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.return_rows


# ---------------------------------------------------------------------------
# retrieve_hybrid forwards allowed_video_ids
# ---------------------------------------------------------------------------


async def test_retrieve_hybrid_forwards_allowed_video_ids(monkeypatch: pytest.MonkeyPatch):
    keyword_spy = _Spy()
    vector_spy = _Spy()

    from backend.db import repository

    monkeypatch.setattr(repository, "keyword_search", keyword_spy)
    monkeypatch.setattr(repository, "vector_search_pg", vector_spy)

    from backend.rag.retriever_hybrid import retrieve_hybrid

    await retrieve_hybrid(
        query_text="agent patterns",
        query_embedding=[0.1] * 1536,
        top_k=5,
        is_member=False,
        allowed_video_ids=["v1", "v2"],
    )

    assert keyword_spy.calls, "keyword_search not called"
    assert vector_spy.calls, "vector_search_pg not called"
    assert keyword_spy.calls[0]["kwargs"]["allowed_video_ids"] == ["v1", "v2"]
    assert vector_spy.calls[0]["kwargs"]["allowed_video_ids"] == ["v1", "v2"]


async def test_retrieve_hybrid_default_none_when_omitted(monkeypatch: pytest.MonkeyPatch):
    keyword_spy = _Spy()
    vector_spy = _Spy()

    from backend.db import repository

    monkeypatch.setattr(repository, "keyword_search", keyword_spy)
    monkeypatch.setattr(repository, "vector_search_pg", vector_spy)

    from backend.rag.retriever_hybrid import retrieve_hybrid

    await retrieve_hybrid(
        query_text="anything",
        query_embedding=[0.0] * 1536,
        top_k=5,
    )

    assert keyword_spy.calls[0]["kwargs"]["allowed_video_ids"] is None
    assert vector_spy.calls[0]["kwargs"]["allowed_video_ids"] is None


# ---------------------------------------------------------------------------
# Tool executors thread allowed_video_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_search_hybrid_threads_allowed_video_ids(monkeypatch) -> None:
    from backend.rag.tools import execute_search_hybrid

    async def fake_retrieve(_q, _emb, top_k=5, is_member=False, allowed_video_ids=None):
        return [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "T",
                "video_url": "u",
                "source_type": "youtube",
                "lesson_url": "",
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "s",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)
    monkeypatch.setattr("backend.config.RETRIEVAL_MAX_PER_VIDEO", 999)
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0)

    result = await execute_search_hybrid({"query": "test"}, allowed_video_ids=["v1"])
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_execute_search_keyword_threads_allowed_video_ids(monkeypatch) -> None:
    from backend.rag import tools as tools_module
    from backend.rag.tools import execute_search_keyword

    async def fake_keyword(
        _q, top_k=10, language="english", allowed_source_types=None, allowed_video_ids=None
    ):
        assert allowed_video_ids == ["v1"]
        return [
            {
                "id": "c1",
                "video_id": "v1",
                "content": "x",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "s",
            }
        ]

    async def fake_get_video(_v):
        return {"id": "v1", "title": "T", "url": "u"}

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr("backend.config.RETRIEVAL_MAX_PER_VIDEO", 999)
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0)

    result = await execute_search_keyword({"query": "test"}, allowed_video_ids=["v1"])
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_execute_search_semantic_threads_allowed_video_ids(monkeypatch) -> None:
    from backend.rag import tools as tools_module
    from backend.rag.tools import execute_search_semantic

    async def fake_vector(_emb, top_k=10, allowed_source_types=None, allowed_video_ids=None):
        assert allowed_video_ids == ["v1"]
        return [
            {
                "id": "c1",
                "video_id": "v1",
                "content": "x",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "s",
            }
        ]

    async def fake_get_video(_v):
        return {"id": "v1", "title": "T", "url": "u"}

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)
    monkeypatch.setattr("backend.config.RETRIEVAL_MAX_PER_VIDEO", 999)
    monkeypatch.setattr("backend.config.RETRIEVAL_EXPANSION_WINDOW", 0)

    result = await execute_search_semantic({"query": "test"}, allowed_video_ids=["v1"])
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_execute_tool_dispatcher_forwards_allowed_video_ids(monkeypatch) -> None:
    from backend.rag import tools as tools_module
    from backend.rag.tools import execute_tool

    calls = []

    async def fake_hybrid(raw, embedding_cache=None, is_member=False, allowed_video_ids=None):
        calls.append(("hybrid", allowed_video_ids))
        return {"ok": True, "text": "", "chunks": []}

    async def fake_keyword(raw, is_member=False, allowed_video_ids=None):
        calls.append(("keyword", allowed_video_ids))
        return {"ok": True, "text": "", "chunks": []}

    async def fake_semantic(raw, embedding_cache=None, is_member=False, allowed_video_ids=None):
        calls.append(("semantic", allowed_video_ids))
        return {"ok": True, "text": "", "chunks": []}

    async def fake_transcript(
        raw, video_id_whitelist=None, is_member=False, allowed_video_ids=None
    ):
        calls.append(("transcript", allowed_video_ids))
        return {"ok": True, "text": "", "chunks": []}

    monkeypatch.setattr(tools_module, "execute_search_hybrid", fake_hybrid)
    monkeypatch.setattr(tools_module, "execute_search_keyword", fake_keyword)
    monkeypatch.setattr(tools_module, "execute_search_semantic", fake_semantic)
    monkeypatch.setattr(tools_module, "execute_get_video_transcript", fake_transcript)

    await execute_tool("search_videos", {}, allowed_video_ids=["v1"])
    await execute_tool("keyword_search_videos", {}, allowed_video_ids=["v1"])
    await execute_tool("semantic_search_videos", {}, allowed_video_ids=["v1"])
    await execute_tool("get_video_transcript", {}, allowed_video_ids=["v1"])

    assert calls == [
        ("hybrid", ["v1"]),
        ("keyword", ["v1"]),
        ("semantic", ["v1"]),
        ("transcript", ["v1"]),
    ]


# ---------------------------------------------------------------------------
# execute_get_video_transcript scope guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcript_scope_guard_rejects_out_of_scope() -> None:
    from backend.rag.tools import execute_get_video_transcript

    result = await execute_get_video_transcript(
        {"video_id": "v-out"},
        video_id_whitelist={"v-out"},
        allowed_video_ids=["v-in"],
    )
    assert result["ok"] is False
    assert "video scope" in result["error"].lower()


@pytest.mark.asyncio
async def test_transcript_scope_guard_allows_in_scope(monkeypatch) -> None:
    from backend.rag import tools as tools_module
    from backend.rag.tools import execute_get_video_transcript

    async def fake_get_video(_v):
        return {"id": "v-in", "title": "T", "url": "u"}

    async def fake_list(_v):
        return [
            {
                "id": "c1",
                "content": "x",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "s",
            }
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_list)

    result = await execute_get_video_transcript(
        {"video_id": "v-in"},
        video_id_whitelist={"v-in"},
        allowed_video_ids=["v-in"],
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_transcript_scope_guard_none_allows_any_in_whitelist(monkeypatch) -> None:
    from backend.rag import tools as tools_module
    from backend.rag.tools import execute_get_video_transcript

    async def fake_get_video(_v):
        return {"id": "v-any", "title": "T", "url": "u"}

    async def fake_list(_v):
        return [
            {
                "id": "c1",
                "content": "x",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "s",
            }
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_list)

    result = await execute_get_video_transcript(
        {"video_id": "v-any"},
        video_id_whitelist={"v-any"},
        allowed_video_ids=None,
    )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


async def test_post_conversation_with_valid_video_scope():
    test_user_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    created = {
        "id": str(uuid4()),
        "user_id": test_user_id,
        "title": "New Conversation",
        "video_scope": ["v1"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    async def mock_create_conversation(*, user_id, title="New Conversation", video_scope=None):
        assert video_scope == ["v1"]
        return created

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.db.repository.create_conversation", mock_create_conversation),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/conversations",
                json={"title": "Test", "video_scope": ["v1"]},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 201
    assert response.json()["video_scope"] == ["v1"]


async def test_post_conversation_unknown_video_id_returns_422():
    test_user_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.list_videos", mock_list_videos),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/conversations",
                json={"title": "Test", "video_scope": ["v-unknown"]},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 422


async def test_post_conversation_empty_scope_returns_422():
    test_user_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_list_videos():
        return []

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.list_videos", mock_list_videos),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/conversations",
                json={"title": "Test", "video_scope": []},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 422


async def test_patch_scope_on_unscoped_conversation_succeeds():
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "video_scope": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    call_count = {"n": 0}

    async def mock_get_conversation_switch(conv_id, user_id):
        if conv_id == test_conv_id:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "id": test_conv_id,
                    "user_id": test_user_id,
                    "title": "Test",
                    "video_scope": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "video_scope": ["v1"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_set_scope(conv_id, user_id, video_scope):
        assert video_scope == ["v1"]
        return True

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation_switch),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.db.repository.set_conversation_video_scope", mock_set_scope),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/conversations/{test_conv_id}/scope",
                json={"video_ids": ["v1"]},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 200
    assert response.json()["video_scope"] == ["v1"]


async def test_patch_scope_on_already_scoped_conversation_returns_409():
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "video_scope": ["v1"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.list_videos", mock_list_videos),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/conversations/{test_conv_id}/scope",
                json={"video_ids": ["v2"]},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 409


async def test_patch_scope_on_other_users_conversation_returns_404():
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        # The conversation exists but belongs to another user
        if conv_id == test_conv_id:
            return None
        return None

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.list_videos", mock_list_videos),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/api/conversations/{test_conv_id}/scope",
                json={"video_ids": ["v1"]},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Message route threads scope into execute_tool
# ---------------------------------------------------------------------------


async def test_message_route_passes_scope_to_execute_tool():
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "video_scope": ["v1", "v2"],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_create_message(**kwargs):
        return {"id": str(uuid4()), **kwargs}

    async def mock_list_messages(conv_id, user_id):
        return []

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps('hello')}\n\n"
        if final_text_out is not None:
            final_text_out.append("hello")
        yield "data: [DONE]\n\n"

    tool_calls = []

    async def mock_execute_tool(
        name,
        raw_args,
        video_id_whitelist=None,
        embedding_cache=None,
        is_member=False,
        allowed_video_ids=None,
    ):
        tool_calls.append((name, allowed_video_ids))
        return {"ok": True, "text": "context", "chunks": []}

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.create_message", mock_create_message),
        patch("backend.db.repository.list_messages", mock_list_messages),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.routes.messages.stream_chat", mock_stream_chat),
        patch("backend.routes.messages.execute_tool", mock_execute_tool),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{test_conv_id}/messages",
                json={"content": "test question"},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 200
    assert tool_calls, "execute_tool was not called"
    assert tool_calls[0] == ("search_videos", ["v1", "v2"])


async def test_message_route_passes_none_when_scope_is_null():
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "video_scope": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_create_message(**kwargs):
        return {"id": str(uuid4()), **kwargs}

    async def mock_list_messages(conv_id, user_id):
        return []

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps('hello')}\n\n"
        if final_text_out is not None:
            final_text_out.append("hello")
        yield "data: [DONE]\n\n"

    tool_calls = []

    async def mock_execute_tool(
        name,
        raw_args,
        video_id_whitelist=None,
        embedding_cache=None,
        is_member=False,
        allowed_video_ids=None,
    ):
        tool_calls.append((name, allowed_video_ids))
        return {"ok": True, "text": "context", "chunks": []}

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.create_message", mock_create_message),
        patch("backend.db.repository.list_messages", mock_list_messages),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.routes.messages.stream_chat", mock_stream_chat),
        patch("backend.routes.messages.execute_tool", mock_execute_tool),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{test_conv_id}/messages",
                json={"content": "test question"},
                headers={"Cookie": f"session={valid_token}"},
            )

    assert response.status_code == 200
    assert tool_calls, "execute_tool was not called"
    assert tool_calls[0] == ("search_videos", None)
