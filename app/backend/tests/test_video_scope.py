"""Tests for per-conversation video scope (issue #279).

A conversation can be pinned to a subset of videos so the assistant's answers
and citations only come from them. The scope is threaded from the conversation
row → messages route → tool executor → search/transcript layer. These tests
cover every link below the route (the route layer is exercised via the
``_normalize_scope`` helper plus the repository signature).

If no scope is set, retrieval must behave exactly as before — search the whole
library — so the search/transcript layers must call their dependencies with the
pre-#279 signature (no ``allowed_video_ids`` kwarg) when the scope is empty.
"""

from __future__ import annotations

import inspect
import json

import pytest

from backend.rag import tools as tools_module
from backend.rag.tools import (
    execute_get_video_transcript,
    execute_search_hybrid,
    execute_search_keyword,
    execute_search_semantic,
    execute_tool,
)
from backend.routes.conversations import _normalize_scope

# ---------------------------------------------------------------------------
# Route helper: scope normalization
# ---------------------------------------------------------------------------


def test_normalize_scope_none_and_empty_are_unscoped() -> None:
    assert _normalize_scope(None) is None
    assert _normalize_scope([]) is None
    # All-blank input collapses to unscoped, not an empty-but-present scope.
    assert _normalize_scope(["", "   "]) is None


def test_normalize_scope_trims_dedupes_preserves_order() -> None:
    assert _normalize_scope([" v1 ", "v2", "v1", "  ", "v3"]) == ["v1", "v2", "v3"]


# ---------------------------------------------------------------------------
# Repository signature invariant
# ---------------------------------------------------------------------------


def test_search_functions_accept_allowed_video_ids() -> None:
    """The two SQL search functions must expose allowed_video_ids so the scope
    can reach the WHERE clause. Structural guard against a silent removal."""
    from backend.db import repository

    for name in ("keyword_search", "vector_search_pg"):
        params = inspect.signature(getattr(repository, name)).parameters
        assert "allowed_video_ids" in params, f"repository.{name} must take allowed_video_ids"

    # update_conversation_scope must be owner-scoped (takes user_id).
    scope_params = inspect.signature(repository.update_conversation_scope).parameters
    assert "user_id" in scope_params


# ---------------------------------------------------------------------------
# Search executors forward the scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_forwards_scope_to_retriever(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retrieve(_q, _emb, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_search_hybrid({"query": "q"}, allowed_video_ids=["v1", "v2"])
    assert captured.get("allowed_video_ids") == ["v1", "v2"]


@pytest.mark.asyncio
async def test_hybrid_omits_scope_kwarg_when_unscoped(monkeypatch) -> None:
    """When unscoped, retrieve_hybrid must be called WITHOUT allowed_video_ids
    so the pre-#279 signature (and its test fakes) keep working."""
    captured: dict = {}

    async def fake_retrieve(_q, _emb, top_k=10, is_member=False):
        # Deliberately does NOT accept allowed_video_ids — this would TypeError
        # if the executor passed it on the unscoped path.
        captured["top_k"] = top_k
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_hybrid({"query": "q"})  # no scope
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_keyword_forwards_scope_to_repository(monkeypatch) -> None:
    captured: dict = {}

    async def fake_keyword(_q, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)

    await execute_search_keyword({"query": "q"}, allowed_video_ids=["v9"])
    assert captured.get("allowed_video_ids") == ["v9"]


@pytest.mark.asyncio
async def test_semantic_forwards_scope_to_repository(monkeypatch) -> None:
    captured: dict = {}

    async def fake_vector(_emb, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_search_semantic({"query": "q"}, allowed_video_ids=["v9"])
    assert captured.get("allowed_video_ids") == ["v9"]


@pytest.mark.asyncio
async def test_unscoped_keyword_does_not_pass_scope_kwarg(monkeypatch) -> None:
    """Backward-compat: the unscoped path must not pass allowed_video_ids, so a
    fake with the old signature still works."""

    async def fake_keyword(_q, top_k=10, language="english", allowed_source_types=None):
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)
    result = await execute_search_keyword({"query": "q"})  # no scope
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Transcript tool scope guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcript_rejects_out_of_scope_video() -> None:
    result = await execute_get_video_transcript({"video_id": "v-out"}, allowed_video_ids=["v-in"])
    assert result["ok"] is False
    assert "scope" in result["error"].lower()


@pytest.mark.asyncio
async def test_transcript_allows_in_scope_video(monkeypatch) -> None:
    async def fake_get_video(_v):
        return {"id": "v-in", "title": "In Scope", "url": "https://youtu.be/x"}

    async def fake_list(_v):
        return [
            {
                "id": "c1",
                "content": "hello",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "snippet": "hello",
            }
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_list)

    result = await execute_get_video_transcript(
        {"video_id": "v-in"}, video_id_whitelist={"v-in"}, allowed_video_ids=["v-in"]
    )
    assert result["ok"] is True
    assert "In Scope" in result["text"]


# ---------------------------------------------------------------------------
# Dispatcher threads the scope through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_threads_scope_to_search(monkeypatch) -> None:
    captured: dict = {}

    async def fake_retrieve(_q, _emb, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    await execute_tool("search_videos", json.dumps({"query": "q"}), allowed_video_ids=["v1"])
    assert captured.get("allowed_video_ids") == ["v1"]


@pytest.mark.asyncio
async def test_execute_tool_threads_scope_to_transcript_guard() -> None:
    result = await execute_tool(
        "get_video_transcript",
        json.dumps({"video_id": "v-out"}),
        allowed_video_ids=["v-in"],
    )
    assert result["ok"] is False
    assert "scope" in result["error"].lower()
