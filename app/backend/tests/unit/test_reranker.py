"""Unit tests for rag.reranker — all OpenRouter calls are monkeypatched."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _make_chunks(n: int) -> list[dict]:
    return [
        {
            "chunk_id": f"c{i}",
            "content": f"chunk content {i}",
            "video_title": f"Video {i}",
            "video_id": f"vid{i}",
            "score": float(n - i),  # RRF puts chunk 0 first
        }
        for i in range(n)
    ]


def _mock_response(indices: list[int]) -> SimpleNamespace:
    msg = SimpleNamespace(content=json.dumps(indices))
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _mock_response_raw(raw: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=raw)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


async def test_rerank_happy_path(monkeypatch):
    """Reranker re-orders chunks per the LLM response."""
    from backend.rag import reranker

    chunks = _make_chunks(5)
    # LLM says chunk 3 is most relevant, then 1, then 0, then 4, then 2
    mock_create = AsyncMock(return_value=_mock_response([3, 1, 0, 4, 2]))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=3)

    assert [c["chunk_id"] for c in result] == ["c3", "c1", "c0"]
    mock_create.assert_awaited_once()


async def test_rerank_fallback_on_invalid_json(monkeypatch):
    """Returns original order (truncated to top_n) when LLM returns garbage."""
    from backend.rag import reranker

    chunks = _make_chunks(4)
    mock_create = AsyncMock(return_value=_mock_response_raw("not valid json"))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=2)

    assert [c["chunk_id"] for c in result] == ["c0", "c1"]


async def test_rerank_fallback_on_api_error(monkeypatch):
    """Returns original order when the LLM call raises an exception."""
    from openai import APIConnectionError

    from backend.rag import reranker

    chunks = _make_chunks(3)
    mock_create = AsyncMock(side_effect=APIConnectionError(request=MagicMock()))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=3)

    assert [c["chunk_id"] for c in result] == ["c0", "c1", "c2"]


async def test_rerank_empty_chunks(monkeypatch):
    """Returns empty list immediately without calling LLM."""
    from backend.rag import reranker

    mock_client = MagicMock()
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", [], top_n=5)

    assert result == []
    mock_client.chat.completions.create.assert_not_called()


async def test_rerank_fewer_chunks_than_top_n(monkeypatch):
    """Returns all chunks when fewer than top_n available, in LLM-reranked order."""
    from backend.rag import reranker

    chunks = _make_chunks(2)
    mock_create = AsyncMock(return_value=_mock_response([1, 0]))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=5)

    assert len(result) == 2
    assert [c["chunk_id"] for c in result] == ["c1", "c0"]


async def test_rerank_out_of_range_indices_ignored(monkeypatch):
    """Out-of-range indices from LLM are silently dropped."""
    from backend.rag import reranker

    chunks = _make_chunks(3)
    # LLM returns index 99 (out of range) and valid indices 2, 0
    mock_create = AsyncMock(return_value=_mock_response([99, 2, 0]))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=3)

    # Valid indices [2, 0], then remainder [1]
    assert [c["chunk_id"] for c in result] == ["c2", "c0", "c1"]


async def test_rerank_single_chunk_skips_llm(monkeypatch):
    """Single-chunk input returns immediately without calling LLM."""
    from backend.rag import reranker

    chunks = _make_chunks(1)
    mock_client = MagicMock()
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=5)

    assert result == chunks
    mock_client.chat.completions.create.assert_not_called()


async def test_rerank_all_out_of_range_falls_back(monkeypatch):
    """Falls back to original order when LLM returns only out-of-range indices."""
    from backend.rag import reranker

    chunks = _make_chunks(3)
    mock_create = AsyncMock(return_value=_mock_response([99, 100, 200]))
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    monkeypatch.setattr(reranker, "_async_client", mock_client)

    result = await reranker.rerank_chunks("test query", chunks, top_n=3)

    assert [c["chunk_id"] for c in result] == ["c0", "c1", "c2"]
