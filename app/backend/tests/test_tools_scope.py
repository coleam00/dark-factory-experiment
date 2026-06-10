"""
Tests for per-conversation video scope (issue #279) at the tool layer.

execute_tool must forward video_id_filter to every search executor (and on to
the retrieval functions), while the transcript tool relies on the narrowed
video_id_whitelist instead. All dependencies are mocked — no live DB.
"""

from __future__ import annotations

import json

import pytest

from backend.rag import tools as tools_module
from backend.rag.tools import execute_get_video_transcript, execute_tool


@pytest.mark.asyncio
async def test_execute_tool_forwards_filter_to_hybrid(monkeypatch):
    captured: dict = {}

    async def fake_retrieve(_q, _emb, top_k=5, is_member=False, video_id_filter=None):
        captured["video_id_filter"] = video_id_filter
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_tool(
        "search_videos",
        json.dumps({"query": "anything"}),
        video_id_filter=["v1", "v2"],
    )
    assert captured["video_id_filter"] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_execute_tool_forwards_filter_to_keyword(monkeypatch):
    captured: dict = {}

    async def fake_keyword(
        _q, top_k=10, language="english", allowed_source_types=None, video_id_filter=None
    ):
        captured["video_id_filter"] = video_id_filter
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)

    await execute_tool(
        "keyword_search_videos",
        json.dumps({"query": "anything"}),
        video_id_filter=["v9"],
    )
    assert captured["video_id_filter"] == ["v9"]


@pytest.mark.asyncio
async def test_execute_tool_forwards_filter_to_semantic(monkeypatch):
    captured: dict = {}

    async def fake_vector(_emb, top_k=10, allowed_source_types=None, video_id_filter=None):
        captured["video_id_filter"] = video_id_filter
        return []

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_tool(
        "semantic_search_videos",
        json.dumps({"query": "anything"}),
        video_id_filter=["v3"],
    )
    assert captured["video_id_filter"] == ["v3"]


@pytest.mark.asyncio
async def test_unscoped_passes_none_filter(monkeypatch):
    captured: dict = {"sentinel": True}

    async def fake_retrieve(_q, _emb, top_k=5, is_member=False, video_id_filter="UNSET"):
        captured["video_id_filter"] = video_id_filter
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    # No video_id_filter passed → None reaches retrieval (search whole library).
    await execute_tool("search_videos", json.dumps({"query": "anything"}))
    assert captured["video_id_filter"] is None


@pytest.mark.asyncio
async def test_transcript_rejects_out_of_scope_video():
    """When the whitelist is narrowed to the scoped set, a transcript request
    for an out-of-scope (but otherwise valid) video is rejected."""
    result = await execute_get_video_transcript(
        {"video_id": "out-of-scope"},
        video_id_whitelist={"in-scope-1", "in-scope-2"},
    )
    assert result["ok"] is False
    assert "library" in result["error"].lower()
