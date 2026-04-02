"""
Tests for the RAG retriever module.

Covers:
- _cosine_similarity_batch (pure function, no mocking needed)
- retrieve() default k (config.RETRIEVAL_TOP_K)
- retrieve() empty DB early-return
- retrieve() when k > available chunks
- retrieve() results are sorted by score descending
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

import numpy as np

from backend import config
from backend.rag.retriever import retrieve, _cosine_similarity_batch


# ---------------------------------------------------------------------------
# _cosine_similarity_batch — pure function, no I/O
# ---------------------------------------------------------------------------

def test_cosine_similarity_batch_returns_correct_shape():
    query = np.array([1.0, 0.0], dtype=np.float32)
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]], dtype=np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert scores.shape == (3,)
    assert scores[0] == pytest.approx(1.0, abs=1e-5)
    assert scores[1] == pytest.approx(0.0, abs=1e-5)
    assert scores[2] == pytest.approx(-1.0, abs=1e-5)


def test_cosine_similarity_batch_zero_query_returns_zeros():
    query = np.zeros(3, dtype=np.float32)
    matrix = np.ones((5, 3), dtype=np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert (scores == 0.0).all()


def test_cosine_similarity_batch_identical_vectors():
    query = np.array([0.6, 0.8], dtype=np.float32)
    matrix = np.array([[0.6, 0.8]], dtype=np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert scores[0] == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------------------
# retrieve() — async, requires mocking repository
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_chunks():
    """10 chunks with 2-D embeddings for easy manipulation."""
    return [
        {
            "id": f"chunk-{i}",
            "content": f"Content {i}",
            "video_id": "vid-1",
            "embedding": [float(i), 0.0],
        }
        for i in range(10)
    ]


@pytest.mark.asyncio
async def test_retrieve_returns_config_top_k_results_by_default(fake_chunks):
    """Default k should come from config.RETRIEVAL_TOP_K, not a hardcoded value."""
    with patch("backend.rag.retriever.repository") as mock_repo:
        mock_repo.list_chunks = AsyncMock(return_value=fake_chunks)
        mock_repo.get_video = AsyncMock(return_value={"title": "Test Video"})

        results = await retrieve([1.0, 0.0])  # no explicit k — uses default

    assert len(results) == config.RETRIEVAL_TOP_K


@pytest.mark.asyncio
async def test_retrieve_empty_db_returns_empty_list():
    """If no chunks exist, retrieve() should return [] without error."""
    with patch("backend.rag.retriever.repository") as mock_repo:
        mock_repo.list_chunks = AsyncMock(return_value=[])

        results = await retrieve([1.0, 0.0])

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_k_larger_than_chunks_returns_all(fake_chunks):
    """When k > number of available chunks, return all chunks without error."""
    with patch("backend.rag.retriever.repository") as mock_repo:
        mock_repo.list_chunks = AsyncMock(return_value=fake_chunks[:3])
        mock_repo.get_video = AsyncMock(return_value={"title": "T"})

        results = await retrieve([1.0, 0.0], k=100)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_retrieve_results_sorted_by_score_descending(fake_chunks):
    """Results must be sorted by cosine similarity, highest first."""
    with patch("backend.rag.retriever.repository") as mock_repo:
        mock_repo.list_chunks = AsyncMock(return_value=fake_chunks)
        mock_repo.get_video = AsyncMock(return_value={"title": "T"})

        results = await retrieve([1.0, 0.0], k=5)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_retrieve_result_contains_expected_fields(fake_chunks):
    """Each result dict must contain the required fields."""
    with patch("backend.rag.retriever.repository") as mock_repo:
        mock_repo.list_chunks = AsyncMock(return_value=fake_chunks[:2])
        mock_repo.get_video = AsyncMock(return_value={"title": "My Video"})

        results = await retrieve([1.0, 0.0], k=1)

    assert len(results) == 1
    result = results[0]
    assert "chunk_id" in result
    assert "content" in result
    assert "video_id" in result
    assert "video_title" in result
    assert "score" in result
    assert isinstance(result["score"], float)
