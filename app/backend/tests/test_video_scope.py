"""Tests for conversation-level video scoping (issue #279).

Verifies that:
- create_conversation accepts video_ids
- keyword_search and vector_search_pg filter by allowed_video_ids
- retrieve_hybrid threads video_id_whitelist to both searches
- Tool executors respect video_id_whitelist
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.db import repository
from backend.rag import tools as tools_module
from backend.rag.retriever_hybrid import retrieve_hybrid
from backend.rag.tools import (
    execute_search_hybrid,
    execute_search_keyword,
    execute_search_semantic,
    execute_tool,
)


# ---------------------------------------------------------------------------
# Repository-level filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_search_filters_by_allowed_video_ids():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        await repository.keyword_search(
            "hello", top_k=5, allowed_video_ids=["v1", "v2"]
        )

    call_args = mock_conn.fetch.call_args[0]
    sql = call_args[0]
    assert "video_id = ANY($4::text[])" in sql
    assert call_args[4] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_vector_search_pg_filters_by_allowed_video_ids():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        await repository.vector_search_pg(
            [0.1] * 1536, top_k=5, allowed_video_ids=["v1"]
        )

    call_args = mock_conn.fetch.call_args[0]
    sql = call_args[0]
    assert "video_id = ANY($4::text[])" in sql
    assert call_args[4] == ["v1"]


@pytest.mark.asyncio
async def test_keyword_search_allows_null_allowed_video_ids():
    """When allowed_video_ids is None, no video filter is applied."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        await repository.keyword_search("hello", top_k=5, allowed_video_ids=None)

    call_args = mock_conn.fetch.call_args[0]
    assert call_args[4] is None


@pytest.mark.asyncio
async def test_vector_search_pg_allows_null_allowed_video_ids():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        await repository.vector_search_pg([0.1] * 1536, top_k=5, allowed_video_ids=None)

    call_args = mock_conn.fetch.call_args[0]
    assert call_args[4] is None


# ---------------------------------------------------------------------------
# create_conversation with video_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_with_video_ids():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        result = await repository.create_conversation(
            user_id="u1", title="Scoped Chat", video_ids=["v1", "v2"]
        )

    assert result["video_ids"] == ["v1", "v2"]
    call_args = mock_conn.execute.call_args[0]
    assert "video_ids" in call_args[0]
    assert call_args[4] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# Tool-level filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_search_hybrid_respects_video_id_whitelist(monkeypatch):
    async def fake_retrieve(_q, _emb, top_k=5, is_member=False, video_id_whitelist=None):
        assert video_id_whitelist == {"v1"}
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_hybrid(
        {"query": "rag"}, video_id_whitelist={"v1"}
    )
    assert result["ok"] is True
    assert "No relevant chunks found" in result["text"]


@pytest.mark.asyncio
async def test_execute_search_keyword_respects_video_id_whitelist(monkeypatch):
    async def fake_keyword(_q, top_k=10, language="english", allowed_source_types=None, allowed_video_ids=None):
        assert sorted(allowed_video_ids) == ["v1", "v2"]
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)

    result = await execute_search_keyword(
        {"query": "test"}, video_id_whitelist={"v1", "v2"}
    )
    assert result["ok"] is True
    assert "No relevant chunks found" in result["text"]


@pytest.mark.asyncio
async def test_execute_search_semantic_respects_video_id_whitelist(monkeypatch):
    async def fake_vector(_emb, top_k=10, allowed_source_types=None, allowed_video_ids=None):
        assert allowed_video_ids == ["v1"]
        return []

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_semantic(
        {"query": "concept"}, video_id_whitelist={"v1"}
    )
    assert result["ok"] is True
    assert "No relevant chunks found" in result["text"]


@pytest.mark.asyncio
async def test_execute_tool_threads_video_id_whitelist_to_all_searches(monkeypatch):
    calls = []

    async def fake_hybrid(raw, embedding_cache=None, is_member=False, video_id_whitelist=None):
        calls.append(("hybrid", video_id_whitelist))
        return {"ok": True, "text": "hybrid result", "chunks": []}

    async def fake_keyword(raw, is_member=False, video_id_whitelist=None):
        calls.append(("keyword", video_id_whitelist))
        return {"ok": True, "text": "keyword result", "chunks": []}

    async def fake_semantic(raw, embedding_cache=None, is_member=False, video_id_whitelist=None):
        calls.append(("semantic", video_id_whitelist))
        return {"ok": True, "text": "semantic result", "chunks": []}

    async def fake_transcript(raw, video_id_whitelist=None, is_member=False):
        calls.append(("transcript", video_id_whitelist))
        return {"ok": True, "text": "transcript result", "chunks": []}

    monkeypatch.setattr(tools_module, "execute_search_hybrid", fake_hybrid)
    monkeypatch.setattr(tools_module, "execute_search_keyword", fake_keyword)
    monkeypatch.setattr(tools_module, "execute_search_semantic", fake_semantic)
    monkeypatch.setattr(tools_module, "execute_get_video_transcript", fake_transcript)

    whitelist = {"v1", "v2"}
    await execute_tool("search_videos", {"query": "x"}, video_id_whitelist=whitelist)
    await execute_tool("keyword_search_videos", {"query": "x"}, video_id_whitelist=whitelist)
    await execute_tool("semantic_search_videos", {"query": "x"}, video_id_whitelist=whitelist)
    await execute_tool("get_video_transcript", {"video_id": "v1"}, video_id_whitelist=whitelist)

    assert calls == [
        ("hybrid", whitelist),
        ("keyword", whitelist),
        ("semantic", whitelist),
        ("transcript", whitelist),
    ]


# ---------------------------------------------------------------------------
# retrieve_hybrid threads video_id_whitelist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_hybrid_threads_video_id_whitelist(monkeypatch):
    keyword_spy = []
    vector_spy = []

    async def fake_keyword(*args, **kwargs):
        keyword_spy.append(kwargs)
        return []

    async def fake_vector(*args, **kwargs):
        vector_spy.append(kwargs)
        return []

    from backend.db import repository as repo

    monkeypatch.setattr(repo, "keyword_search", fake_keyword)
    monkeypatch.setattr(repo, "vector_search_pg", fake_vector)

    await retrieve_hybrid(
        "query",
        [0.1] * 1536,
        top_k=5,
        video_id_whitelist={"v1", "v2"},
    )

    assert sorted(keyword_spy[0]["allowed_video_ids"]) == ["v1", "v2"]
    assert sorted(vector_spy[0]["allowed_video_ids"]) == ["v1", "v2"]
