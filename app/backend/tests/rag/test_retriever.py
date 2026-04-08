"""Tests for the cosine similarity retriever."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

import numpy as np

from backend.rag.retriever import retrieve, _cosine_similarity_batch
from backend import config


# ---------------------------------------------------------------------------
# _cosine_similarity_batch unit tests
# ---------------------------------------------------------------------------


def test_cosine_similarity_batch_basic():
    query = np.array([1.0, 0.0], dtype=np.float32)
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert pytest.approx(scores[0], abs=1e-6) == 1.0
    assert pytest.approx(scores[1], abs=1e-6) == 0.0


def test_cosine_similarity_batch_zero_query_norm():
    """Zero-norm query should return all-zero similarities without error."""
    query = np.zeros(3, dtype=np.float32)
    matrix = np.random.rand(5, 3).astype(np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert np.all(scores == 0.0)


def test_cosine_similarity_batch_zero_row_norm():
    """Zero-norm rows in matrix should not cause division-by-zero."""
    query = np.array([1.0, 0.0], dtype=np.float32)
    matrix = np.array([[0.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    scores = _cosine_similarity_batch(query, matrix)
    assert np.isfinite(scores).all()


# ---------------------------------------------------------------------------
# retrieve() integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_uses_config_top_k():
    """retrieve() default k should come from config, not a hardcoded value."""
    fake_chunks = [
        {"id": f"c{i}", "embedding": [float(i), 0.0], "content": f"chunk {i}", "video_id": "v1"}
        for i in range(1, config.RETRIEVAL_TOP_K + 3)  # more chunks than top-k
    ]
    fake_video = {"title": "Test Video"}

    with patch("backend.rag.retriever.repository.list_chunks", AsyncMock(return_value=fake_chunks)), \
         patch("backend.rag.retriever.repository.get_video", AsyncMock(return_value=fake_video)):
        results = await retrieve(query_embedding=[1.0, 0.0])  # k defaults to config value

    assert len(results) == config.RETRIEVAL_TOP_K


@pytest.mark.asyncio
async def test_retrieve_empty_db():
    """retrieve() should return [] when no chunks exist."""
    with patch("backend.rag.retriever.repository.list_chunks", AsyncMock(return_value=[])):
        results = await retrieve(query_embedding=[1.0, 0.0])
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_results_sorted_by_score_descending():
    """retrieve() results should be ordered by cosine similarity, highest first."""
    fake_chunks = [
        {"id": "low",  "embedding": [0.0, 1.0], "content": "low chunk", "video_id": "v1"},
        {"id": "high", "embedding": [1.0, 0.0], "content": "high chunk", "video_id": "v1"},
    ]
    fake_video = {"title": "Test Video"}

    with patch("backend.rag.retriever.repository.list_chunks", AsyncMock(return_value=fake_chunks)), \
         patch("backend.rag.retriever.repository.get_video", AsyncMock(return_value=fake_video)):
        results = await retrieve(query_embedding=[1.0, 0.0], k=2)

    assert results[0]["chunk_id"] == "high"
    assert results[1]["chunk_id"] == "low"
    assert results[0]["score"] >= results[1]["score"]


# ---------------------------------------------------------------------------
# Config guard
# ---------------------------------------------------------------------------


def test_retrieval_top_k_is_positive():
    """RETRIEVAL_TOP_K must be a positive integer to avoid silent empty results."""
    assert isinstance(config.RETRIEVAL_TOP_K, int)
    assert config.RETRIEVAL_TOP_K > 0
