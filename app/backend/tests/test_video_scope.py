"""Tests for per-conversation video scoping (issue #279).

Covers the three layers of the scope path:
  1. rag/tools.py — `scope_video_ids` is threaded from `execute_tool` down to
     the repository / retriever calls, and `get_video_transcript` rejects
     out-of-scope ids.
  2. routes/conversations.py — PATCH /conversations/{id}/scope validation
     (set, clear, all-unknown → 400, cross-user → 404).
  3. routes/messages.py — the conversation row's `scoped_video_ids` reaches
     `execute_tool` on every turn (and unscoped conversations pass None).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_user
from backend.db import repository
from backend.main import app
from backend.rag import retriever_hybrid as retriever_hybrid_module
from backend.rag import tools as tools_module
from backend.rag.tools import (
    execute_get_video_transcript,
    execute_tool,
)

# ---------------------------------------------------------------------------
# 1. Tool layer — scope threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_hybrid_forwards_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_retrieve(query, embedding, top_k=5, is_member=False, scope_video_ids=None):
        captured["scope_video_ids"] = scope_video_ids
        return []

    monkeypatch.setattr(retriever_hybrid_module, "retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_tool(
        "search_videos", json.dumps({"query": "q"}), scope_video_ids=["v1", "v2"]
    )
    assert result["ok"] is True
    assert captured["scope_video_ids"] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_execute_tool_hybrid_unscoped_passes_none(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_retrieve(query, embedding, top_k=5, is_member=False, scope_video_ids=None):
        captured["scope_video_ids"] = scope_video_ids
        return []

    monkeypatch.setattr(retriever_hybrid_module, "retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_tool("search_videos", json.dumps({"query": "q"}))
    assert captured["scope_video_ids"] is None


@pytest.mark.asyncio
async def test_execute_tool_keyword_forwards_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_keyword(
        query, top_k=10, language="english", allowed_source_types=None, video_ids=None
    ):
        captured["video_ids"] = video_ids
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)

    result = await execute_tool(
        "keyword_search_videos", json.dumps({"query": "q"}), scope_video_ids=["v1"]
    )
    assert result["ok"] is True
    assert captured["video_ids"] == ["v1"]


@pytest.mark.asyncio
async def test_execute_tool_semantic_forwards_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_vector(embedding, top_k=10, allowed_source_types=None, video_ids=None):
        captured["video_ids"] = video_ids
        return []

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_tool(
        "semantic_search_videos", json.dumps({"query": "q"}), scope_video_ids=["v9"]
    )
    assert result["ok"] is True
    assert captured["video_ids"] == ["v9"]


@pytest.mark.asyncio
async def test_retrieve_hybrid_forwards_scope_to_both_searches(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_keyword(
        query, top_k=10, language="english", allowed_source_types=None, video_ids=None
    ):
        captured["keyword_video_ids"] = video_ids
        return []

    async def fake_vector(embedding, top_k=10, allowed_source_types=None, video_ids=None):
        captured["vector_video_ids"] = video_ids
        return []

    monkeypatch.setattr(repository, "keyword_search", fake_keyword)
    monkeypatch.setattr(repository, "vector_search_pg", fake_vector)

    result = await retriever_hybrid_module.retrieve_hybrid(
        "q", [0.0] * 1536, top_k=5, scope_video_ids=["v1", "v2"]
    )
    assert result == []
    assert captured["keyword_video_ids"] == ["v1", "v2"]
    assert captured["vector_video_ids"] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# 1b. Transcript tool — scope enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcript_rejects_in_library_but_out_of_scope_video() -> None:
    result = await execute_get_video_transcript(
        {"video_id": "v2"},
        video_id_whitelist={"v1", "v2"},  # v2 IS in the library...
        scope_video_ids=["v1"],  # ...but outside the conversation scope
    )
    assert result["ok"] is False
    assert "selected videos" in result["error"]


@pytest.mark.asyncio
async def test_transcript_allows_in_scope_video(monkeypatch) -> None:
    async def fake_get_video(_v):
        return {"id": "v1", "title": "In Scope", "url": "https://youtu.be/x"}

    async def fake_chunks(_v):
        return [
            {
                "id": "c1",
                "content": "hello world",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "snippet": "hello",
            }
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_chunks)

    result = await execute_get_video_transcript(
        {"video_id": "v1"},
        video_id_whitelist={"v1", "v2"},
        scope_video_ids=["v1"],
    )
    assert result["ok"] is True
    assert "In Scope" in result["text"]


@pytest.mark.asyncio
async def test_transcript_unscoped_does_not_block(monkeypatch) -> None:
    async def fake_get_video(_v):
        return {"id": "v2", "title": "Any Video", "url": "https://youtu.be/y"}

    async def fake_chunks(_v):
        return [
            {
                "id": "c1",
                "content": "text",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "snippet": "t",
            }
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_chunks)

    result = await execute_get_video_transcript(
        {"video_id": "v2"},
        video_id_whitelist={"v1", "v2"},
        scope_video_ids=None,
    )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# 2. PATCH /api/conversations/{id}/scope
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user():
    stub = {"id": str(uuid4()), "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


_KNOWN_VIDEOS = [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]


@pytest.mark.asyncio
async def test_patch_scope_valid_subset_returns_conversation(auth_user) -> None:
    conv_id = str(uuid4())
    conv_row = {"id": conv_id, "title": "T", "scoped_video_ids": ["v2", "v1"]}
    with (
        patch(
            "backend.routes.conversations.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_KNOWN_VIDEOS,
        ),
        patch(
            "backend.routes.conversations.repository.update_conversation_scope",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update,
        patch(
            "backend.routes.conversations.repository.get_conversation",
            new_callable=AsyncMock,
            return_value=conv_row,
        ),
    ):
        async with _client() as client:
            # Duplicates and unknown ids are dropped; order preserved.
            r = await client.patch(
                f"/api/conversations/{conv_id}/scope",
                json={"video_ids": ["v2", "v1", "v2", "nope"]},
            )
    assert r.status_code == 200
    assert r.json()["scoped_video_ids"] == ["v2", "v1"]
    mock_update.assert_awaited_once_with(conv_id, user_id=auth_user["id"], video_ids=["v2", "v1"])


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"video_ids": None}, {"video_ids": []}, {}])
async def test_patch_scope_null_or_empty_clears(auth_user, body) -> None:
    conv_id = str(uuid4())
    conv_row = {"id": conv_id, "title": "T", "scoped_video_ids": None}
    with (
        patch(
            "backend.routes.conversations.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_KNOWN_VIDEOS,
        ) as mock_list,
        patch(
            "backend.routes.conversations.repository.update_conversation_scope",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update,
        patch(
            "backend.routes.conversations.repository.get_conversation",
            new_callable=AsyncMock,
            return_value=conv_row,
        ),
    ):
        async with _client() as client:
            r = await client.patch(f"/api/conversations/{conv_id}/scope", json=body)
    assert r.status_code == 200
    assert r.json()["scoped_video_ids"] is None
    mock_update.assert_awaited_once_with(conv_id, user_id=auth_user["id"], video_ids=None)
    # Clearing must not need the video catalog at all.
    mock_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_scope_all_unknown_ids_returns_400(auth_user) -> None:
    conv_id = str(uuid4())
    with (
        patch(
            "backend.routes.conversations.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_KNOWN_VIDEOS,
        ),
        patch(
            "backend.routes.conversations.repository.update_conversation_scope",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        async with _client() as client:
            r = await client.patch(
                f"/api/conversations/{conv_id}/scope",
                json={"video_ids": ["ghost-1", "ghost-2"]},
            )
    assert r.status_code == 400
    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_scope_other_users_conversation_returns_404(auth_user) -> None:
    conv_id = str(uuid4())
    with (
        patch(
            "backend.routes.conversations.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_KNOWN_VIDEOS,
        ),
        patch(
            "backend.routes.conversations.repository.update_conversation_scope",
            new_callable=AsyncMock,
            return_value=False,  # owner check failed → no row updated
        ),
    ):
        async with _client() as client:
            r = await client.patch(
                f"/api/conversations/{conv_id}/scope", json={"video_ids": ["v1"]}
            )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. routes/messages.py — conversation scope reaches execute_tool
# ---------------------------------------------------------------------------


async def _run_message_turn(conv_row: dict[str, Any]) -> AsyncMock:
    """Drive the message route with a fake stream that issues one tool call;
    return the patched execute_tool mock for kwarg inspection."""

    async def fake_stream(*args: Any, **kwargs: Any):
        executor = kwargs.get("tool_executor")
        if executor is not None:
            await executor("search_videos", json.dumps({"query": "q"}))
        final_text_out = kwargs.get("final_text_out")
        if final_text_out is not None:
            final_text_out.append("Answer.")
        yield f"data: {json.dumps('Answer.')}\n\n"
        yield "data: [DONE]\n\n"

    user = {"id": conv_row["user_id"], "email": "t@t"}
    with (
        patch(
            "backend.routes.messages.repository.get_conversation",
            new_callable=AsyncMock,
            return_value=conv_row,
        ),
        patch(
            "backend.routes.messages.repository.create_message",
            new_callable=AsyncMock,
            return_value={"id": str(uuid4())},
        ),
        patch(
            "backend.routes.messages.repository.list_messages",
            new_callable=AsyncMock,
            return_value=[{"role": "user", "content": "hi"}],
        ),
        patch(
            "backend.routes.messages.repository.list_videos",
            new_callable=AsyncMock,
            return_value=[{"id": "v1"}, {"id": "v2"}],
        ),
        patch(
            "backend.routes.messages.rate_limit.check_and_record",
            new_callable=AsyncMock,
        ),
        patch("backend.routes.messages.stream_chat", side_effect=fake_stream),
        patch(
            "backend.routes.messages._maybe_set_conversation_title",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.routes.messages.execute_tool",
            new_callable=AsyncMock,
            return_value={"ok": True, "text": "results", "chunks": []},
        ) as mock_execute,
    ):
        from backend.routes.messages import MessageCreate, create_message

        resp = await create_message(
            conv_id=conv_row["id"],
            body=MessageCreate(content="hi"),
            current_user=user,
        )
        async for _ in resp.body_iterator:
            pass
    return mock_execute


@pytest.mark.asyncio
async def test_message_turn_passes_conversation_scope_to_execute_tool() -> None:
    conv = {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "title": "Scoped",
        "scoped_video_ids": ["v1"],
    }
    mock_execute = await _run_message_turn(conv)
    mock_execute.assert_awaited_once()
    assert mock_execute.call_args.kwargs["scope_video_ids"] == ["v1"]


@pytest.mark.asyncio
async def test_message_turn_unscoped_conversation_passes_none() -> None:
    conv = {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "title": "Unscoped",
        "scoped_video_ids": None,
    }
    mock_execute = await _run_message_turn(conv)
    mock_execute.assert_awaited_once()
    assert mock_execute.call_args.kwargs["scope_video_ids"] is None
