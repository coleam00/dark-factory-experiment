"""Tests for backend.rag.tools — four retrieval tools and their executors."""

from __future__ import annotations

import json

import pytest

from backend.llm.openrouter import build_system_prompt
from backend.rag import tools as tools_module
from backend.rag.tools import (
    GET_VIDEO_TRANSCRIPT_TOOL,
    KEYWORD_SEARCH_TOOL,
    SEARCH_VIDEOS_TOOL,
    SEMANTIC_SEARCH_TOOL,
    TOOL_SCHEMAS,
    _format_search_results,
    _format_transcript,
    execute_get_video_transcript,
    execute_search_hybrid,
    execute_search_keyword,
    execute_search_semantic,
    execute_tool,
    serialize_tool_result,
)

# --- Tool schemas ----------------------------------------------------------


@pytest.mark.parametrize(
    "schema,name",
    [
        (SEARCH_VIDEOS_TOOL, "search_videos"),
        (KEYWORD_SEARCH_TOOL, "keyword_search_videos"),
        (SEMANTIC_SEARCH_TOOL, "semantic_search_videos"),
        (GET_VIDEO_TRANSCRIPT_TOOL, "get_video_transcript"),
    ],
)
def test_tool_schemas_are_openai_function_format(schema, name) -> None:
    assert schema["type"] == "function"
    assert schema["function"]["name"] == name
    assert "description" in schema["function"]
    assert "parameters" in schema["function"]
    assert schema in TOOL_SCHEMAS


# --- Argument validation (shared across search tools) ---------------------


@pytest.mark.parametrize(
    "executor",
    [execute_search_hybrid, execute_search_keyword, execute_search_semantic],
)
@pytest.mark.asyncio
async def test_search_tools_require_non_empty_query(executor) -> None:
    assert (await executor({}))["ok"] is False
    assert (await executor({"query": "   "}))["ok"] is False
    assert (await executor("{not valid json"))["ok"] is False


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error() -> None:
    result = await execute_tool("not_a_real_tool", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"].lower()


# --- Search executors (happy paths, dependencies mocked) -------------------


_FAKE_CHUNKS = [
    {
        "chunk_id": "c1",
        "content": "First chunk text.",
        "video_id": "v1",
        "video_title": "How RAG Works",
        "video_url": "https://youtu.be/abc",
        "start_seconds": 0.0,
        "end_seconds": 30.0,
        "snippet": "First",
    },
]


@pytest.mark.asyncio
async def test_execute_search_hybrid_happy_path(monkeypatch) -> None:
    async def fake_retrieve(_q, _emb, top_k=5):
        assert top_k == 10
        return _FAKE_CHUNKS

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_hybrid(json.dumps({"query": "rag pipelines"}))
    assert result["ok"] is True
    assert "How RAG Works" in result["text"]
    assert "at 00:00" in result["text"]
    assert result["chunks"] == _FAKE_CHUNKS


@pytest.mark.asyncio
async def test_execute_search_keyword_hydrates_raw_chunks(monkeypatch) -> None:
    async def fake_keyword(_q, top_k=10, language="english"):
        return [
            {
                "id": "c1",
                "video_id": "v1",
                "content": "hello",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "hello",
            }
        ]

    async def fake_get_video(_v):
        return {"id": "v1", "title": "Kw Video", "url": "https://youtu.be/k"}

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)

    result = await execute_search_keyword({"query": "hello"})
    assert result["ok"] is True
    assert "Kw Video" in result["text"]
    assert result["chunks"][0]["chunk_id"] == "c1"
    assert result["chunks"][0]["video_title"] == "Kw Video"


@pytest.mark.asyncio
async def test_execute_search_semantic_embeds_and_hydrates(monkeypatch) -> None:
    async def fake_vector(_emb, top_k=10):
        return [
            {
                "id": "c2",
                "video_id": "v2",
                "content": "semantic hit",
                "chunk_index": 0,
                "start_seconds": 42.0,
                "end_seconds": 60.0,
                "snippet": "semantic",
            }
        ]

    async def fake_get_video(_v):
        return {"id": "v2", "title": "Sem Video", "url": "https://youtu.be/s"}

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_semantic({"query": "some concept"})
    assert result["ok"] is True
    assert "Sem Video" in result["text"]
    assert "at 00:42" in result["text"]
    assert result["chunks"][0]["chunk_id"] == "c2"


@pytest.mark.asyncio
async def test_search_empty_results_returns_canned_message(monkeypatch) -> None:
    async def fake_keyword(_q, top_k=10, language="english"):
        return []

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)

    result = await execute_search_keyword({"query": "nothing matches"})
    assert result["ok"] is True
    assert "No relevant chunks found" in result["text"]
    assert result["chunks"] == []


# --- Per-video diversity cap ----------------------------------------------


def test_apply_per_video_cap_limits_same_video() -> None:
    from backend.rag.tools import _apply_per_video_cap

    chunks = [{"video_id": "v1", "chunk_id": f"c{i}"} for i in range(10)]
    assert len(_apply_per_video_cap(chunks, max_per_video=3)) == 3


def test_apply_per_video_cap_preserves_order_and_multi_video() -> None:
    from backend.rag.tools import _apply_per_video_cap

    chunks = [
        {"video_id": "v1", "chunk_id": "c1"},
        {"video_id": "v2", "chunk_id": "c2"},
        {"video_id": "v1", "chunk_id": "c3"},
        {"video_id": "v1", "chunk_id": "c4"},
        {"video_id": "v1", "chunk_id": "c5"},
        {"video_id": "v2", "chunk_id": "c6"},
    ]
    # v1 gets c1 + c3 (2 max), then c4/c5 are dropped; v2 gets c2 + c6
    kept = _apply_per_video_cap(chunks, max_per_video=2)
    assert [c["chunk_id"] for c in kept] == ["c1", "c2", "c3", "c6"]


def test_apply_per_video_cap_large_value_is_noop() -> None:
    from backend.rag.tools import _apply_per_video_cap

    chunks = [{"video_id": "v1", "chunk_id": f"c{i}"} for i in range(5)]
    assert _apply_per_video_cap(chunks, max_per_video=999) == chunks


def test_apply_per_video_cap_zero_is_noop() -> None:
    from backend.rag.tools import _apply_per_video_cap

    chunks = [{"video_id": "v1", "chunk_id": f"c{i}"} for i in range(5)]
    assert _apply_per_video_cap(chunks, max_per_video=0) == chunks


def test_apply_per_video_cap_missing_video_id_passes_through() -> None:
    from backend.rag.tools import _apply_per_video_cap

    # Chunks without a video_id should not be grouped or capped.
    chunks = [
        {"chunk_id": "c1"},  # no video_id
        {"chunk_id": "c2"},  # no video_id
        {"video_id": "v1", "chunk_id": "c3"},
        {"video_id": "v1", "chunk_id": "c4"},
    ]
    result = _apply_per_video_cap(chunks, max_per_video=1)
    # Both no-id chunks pass through; v1 is capped at 1 → c4 dropped.
    assert [c["chunk_id"] for c in result] == ["c1", "c2", "c3"]


# --- Executor-level per-video-cap enforcement tests ------------------------


@pytest.mark.asyncio
async def test_execute_search_hybrid_respects_per_video_cap(monkeypatch) -> None:
    """Cap must be enforced end-to-end inside execute_search_hybrid."""
    # 6 chunks all from the same video; default RETRIEVAL_MAX_PER_VIDEO is 3.
    many_chunks = [
        {
            "chunk_id": f"c{i}",
            "content": "text",
            "video_id": "v1",
            "video_title": "T",
            "video_url": "u",
            "start_seconds": float(i),
            "end_seconds": float(i + 1),
            "snippet": "x",
            "score": 0.9,
        }
        for i in range(6)
    ]

    async def fake_retrieve(_q, _emb, top_k=10):
        return many_chunks

    monkeypatch.setattr("backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_hybrid({"query": "broad question"})
    assert result["ok"] is True
    v1_chunks = [c for c in result["chunks"] if c["video_id"] == "v1"]
    assert len(v1_chunks) <= 3  # RETRIEVAL_MAX_PER_VIDEO default


@pytest.mark.asyncio
async def test_execute_search_keyword_respects_per_video_cap(monkeypatch) -> None:
    """Cap must be enforced end-to-end inside execute_search_keyword."""

    async def fake_keyword(_q, top_k=10, language="english"):
        return [
            {
                "id": f"c{i}",
                "video_id": "v1",
                "content": "text",
                "chunk_index": i,
                "start_seconds": float(i),
                "end_seconds": float(i + 1),
                "snippet": "x",
            }
            for i in range(6)
        ]

    async def fake_get_video(_v):
        return {"id": "v1", "title": "Kw Video", "url": "https://youtu.be/k"}

    monkeypatch.setattr(tools_module.repository, "keyword_search", fake_keyword)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)

    result = await execute_search_keyword({"query": "broad question"})
    assert result["ok"] is True
    v1_chunks = [c for c in result["chunks"] if c["video_id"] == "v1"]
    assert len(v1_chunks) <= 3  # RETRIEVAL_MAX_PER_VIDEO default


@pytest.mark.asyncio
async def test_execute_search_semantic_respects_per_video_cap(monkeypatch) -> None:
    """Cap must be enforced end-to-end inside execute_search_semantic."""

    async def fake_vector(_emb, top_k=10):
        return [
            {
                "id": f"c{i}",
                "video_id": "v1",
                "content": "text",
                "chunk_index": i,
                "start_seconds": float(i),
                "end_seconds": float(i + 1),
                "snippet": "x",
            }
            for i in range(6)
        ]

    async def fake_get_video(_v):
        return {"id": "v1", "title": "Sem Video", "url": "https://youtu.be/s"}

    monkeypatch.setattr(tools_module.repository, "vector_search_pg", fake_vector)
    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr("backend.rag.embeddings.embed_text", lambda _s: [0.0] * 1536)

    result = await execute_search_semantic({"query": "broad question"})
    assert result["ok"] is True
    v1_chunks = [c for c in result["chunks"] if c["video_id"] == "v1"]
    assert len(v1_chunks) <= 3  # RETRIEVAL_MAX_PER_VIDEO default


# --- Transcript size cap ---------------------------------------------------


def test_format_transcript_truncates_when_over_cap() -> None:
    from backend.rag.tools import _format_transcript

    video = {"title": "Long Video"}
    chunks = [{"start_seconds": float(i * 30), "content": "x" * 100} for i in range(50)]
    text = _format_transcript(video, chunks, max_chars=500)
    assert len(text) <= 800  # cap + truncation marker
    assert "truncated" in text


def test_format_transcript_no_cap_returns_all() -> None:
    from backend.rag.tools import _format_transcript

    video = {"title": "Short"}
    chunks = [{"start_seconds": 0.0, "content": "hi"}]
    text = _format_transcript(video, chunks, max_chars=None)
    assert "truncated" not in text
    assert "hi" in text


# --- Chunk shape normalization --------------------------------------------


def test_normalize_chunk_shape_drops_score() -> None:
    from backend.rag.tools import _normalize_chunk_shape

    chunk = {
        "chunk_id": "c1",
        "content": "x",
        "video_id": "v1",
        "video_title": "T",
        "video_url": "u",
        "start_seconds": 1.0,
        "end_seconds": 2.0,
        "snippet": "x",
        "score": 0.9,  # hybrid-only field
    }
    normalized = _normalize_chunk_shape(chunk)
    assert "score" not in normalized
    assert normalized["chunk_id"] == "c1"


# --- Embedding memoization -------------------------------------------------


@pytest.mark.asyncio
async def test_embed_query_uses_cache(monkeypatch) -> None:
    from backend.rag.tools import _embed_query

    calls = {"n": 0}

    def fake_embed(_query):
        calls["n"] += 1
        return [0.0] * 1536

    monkeypatch.setattr("backend.rag.embeddings.embed_text", fake_embed)
    cache: dict[str, list[float]] = {}

    await _embed_query("same query", cache)
    await _embed_query("same query", cache)
    await _embed_query("different query", cache)

    assert calls["n"] == 2  # cache hit on the second "same query" call


# --- Transcript tool -------------------------------------------------------


@pytest.mark.asyncio
async def test_transcript_missing_or_empty_video_id() -> None:
    assert (await execute_get_video_transcript({}))["ok"] is False
    assert (await execute_get_video_transcript({"video_id": "   "}))["ok"] is False
    assert (await execute_get_video_transcript({"video_id": 123}))["ok"] is False


@pytest.mark.asyncio
async def test_transcript_whitelist_rejects_unknown_id() -> None:
    result = await execute_get_video_transcript(
        {"video_id": "hallucinated"}, video_id_whitelist={"real-1"}
    )
    assert result["ok"] is False
    assert "library" in result["error"].lower()


@pytest.mark.asyncio
async def test_transcript_happy_path_returns_text_and_chunks(monkeypatch) -> None:
    async def fake_get_video(_v):
        return {"id": "v1", "title": "How RAG Works", "url": "https://youtu.be/abc"}

    async def fake_list(_v):
        return [
            {
                "id": "c1",
                "content": "First.",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 30.0,
                "snippet": "First",
            },
            {
                "id": "c2",
                "content": "Second.",
                "chunk_index": 1,
                "start_seconds": 30.0,
                "end_seconds": 65.0,
                "snippet": "Second",
            },
        ]

    monkeypatch.setattr(tools_module.repository, "get_video", fake_get_video)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", fake_list)

    result = await execute_tool(
        "get_video_transcript",
        json.dumps({"video_id": "v1"}),
        video_id_whitelist={"v1"},
    )
    assert result["ok"] is True
    assert "How RAG Works" in result["text"]
    assert "[00:00]" in result["text"]
    assert "[00:30]" in result["text"]
    assert len(result["chunks"]) == 2
    assert result["chunks"][0]["chunk_id"] == "c1"
    assert result["chunks"][0]["video_title"] == "How RAG Works"


# --- Formatting / serialization -------------------------------------------


def test_search_results_formatter_renders_timestamps_and_title() -> None:
    text = _format_search_results(_FAKE_CHUNKS)
    assert "How RAG Works" in text
    assert "at 00:00" in text


def test_transcript_formatter_handles_60_minute_edge() -> None:
    text = _format_transcript(
        {"title": "Demo"},
        [{"start_seconds": 3600.0, "content": "x"}],
    )
    assert "[60:00]" in text


def test_serialize_ok_returns_text() -> None:
    assert serialize_tool_result({"ok": True, "text": "hello"}) == "hello"


def test_serialize_error_returns_error_line() -> None:
    payload = serialize_tool_result({"ok": False, "error": "boom"})
    assert payload.startswith("Error:") and "boom" in payload


def test_serialize_malformed_returns_generic_error() -> None:
    assert serialize_tool_result({}).startswith("Error:")


# --- System prompt tool guidance ------------------------------------------


def test_prompt_includes_all_tools_when_cap_positive() -> None:
    prompt = build_system_prompt(max_tool_calls=6)
    for name in (
        "search_videos",
        "keyword_search_videos",
        "semantic_search_videos",
        "get_video_transcript",
    ):
        assert name in prompt


def test_prompt_omits_tool_guidance_when_cap_zero() -> None:
    prompt = build_system_prompt(max_tool_calls=0)
    assert "search_videos" not in prompt
