"""Regression tests for the in-memory embedding cache in retriever.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.rag import retriever


FAKE_CHUNKS = [
    {
        "id": "chunk-1",
        "video_id": "vid-1",
        "content": "Hello world",
        "embedding": [1.0, 0.0],
        "chunk_index": 0,
    },
    {
        "id": "chunk-2",
        "video_id": "vid-1",
        "content": "Goodbye world",
        "embedding": [0.0, 1.0],
        "chunk_index": 1,
    },
]


@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure the module-level cache is clean before and after each test."""
    retriever.invalidate_embedding_cache()
    yield
    retriever.invalidate_embedding_cache()


async def test_cache_populated_on_first_retrieve():
    """retrieve() loads from DB on first call and populates the cache."""
    mock_get_video = AsyncMock(return_value={"title": "Test Video"})
    with (
        patch("backend.rag.retriever.repository.list_chunks", new=AsyncMock(return_value=FAKE_CHUNKS)),
        patch("backend.rag.retriever.repository.get_video", new=mock_get_video),
    ):
        await retriever.retrieve([1.0, 0.0], k=1)

    assert retriever._cached_chunks is not None
    assert retriever._cached_matrix is not None
    assert len(retriever._cached_chunks) == 2


async def test_cache_not_reloaded_on_second_retrieve():
    """retrieve() does NOT call list_chunks() again on second call (cache hit)."""
    mock_list_chunks = AsyncMock(return_value=FAKE_CHUNKS)
    mock_get_video = AsyncMock(return_value={"title": "Test Video"})
    with (
        patch("backend.rag.retriever.repository.list_chunks", new=mock_list_chunks),
        patch("backend.rag.retriever.repository.get_video", new=mock_get_video),
    ):
        await retriever.retrieve([1.0, 0.0], k=1)
        await retriever.retrieve([1.0, 0.0], k=1)

    assert mock_list_chunks.call_count == 1


async def test_cache_reloaded_after_invalidation():
    """After invalidate_embedding_cache(), retrieve() calls list_chunks() again."""
    mock_list_chunks = AsyncMock(return_value=FAKE_CHUNKS)
    mock_get_video = AsyncMock(return_value={"title": "Test Video"})
    with (
        patch("backend.rag.retriever.repository.list_chunks", new=mock_list_chunks),
        patch("backend.rag.retriever.repository.get_video", new=mock_get_video),
    ):
        await retriever.retrieve([1.0, 0.0], k=1)
        retriever.invalidate_embedding_cache()
        await retriever.retrieve([1.0, 0.0], k=1)

    assert mock_list_chunks.call_count == 2
