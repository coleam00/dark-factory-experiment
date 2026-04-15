"""
Tests for the module-level embedding cache in retriever.py.

Verifies:
  - cache is populated on first retrieve()
  - cache is reused on subsequent retrieve() calls (no extra DB reads)
  - invalidate_cache() clears the cache and forces a fresh DB load
"""

from unittest.mock import AsyncMock, patch

import backend.rag.retriever as retriever_module
from backend.rag.retriever import invalidate_cache, retrieve

# Minimal chunk fixture — must include "embedding" key so NumPy matrix build works.
_CHUNK = [
    {
        "id": "c1",
        "video_id": "v1",
        "content": "hello world",
        "embedding": [0.1, 0.2, 0.3],
        "chunk_index": 0,
    }
]


async def test_cache_is_populated_on_first_retrieve():
    """retrieve() loads from DB on first call and populates the module-level cache."""
    retriever_module._cache = None  # ensure clean state

    with patch("backend.rag.retriever.repository.list_chunks", new_callable=AsyncMock) as mock_lc:
        with patch("backend.rag.retriever.repository.get_video", new_callable=AsyncMock) as mock_gv:
            mock_lc.return_value = _CHUNK
            mock_gv.return_value = {"title": "Test Video"}

            await retrieve([0.1, 0.2, 0.3])

    assert mock_lc.call_count == 1
    assert retriever_module._cache is not None


async def test_cache_is_reused_on_second_retrieve():
    """retrieve() called twice hits the DB only once."""
    retriever_module._cache = None  # ensure clean state

    with patch("backend.rag.retriever.repository.list_chunks", new_callable=AsyncMock) as mock_lc:
        with patch("backend.rag.retriever.repository.get_video", new_callable=AsyncMock) as mock_gv:
            mock_lc.return_value = _CHUNK
            mock_gv.return_value = {"title": "Test Video"}

            await retrieve([0.1, 0.2, 0.3])
            await retrieve([0.1, 0.2, 0.3])

    assert mock_lc.call_count == 1


async def test_invalidate_cache_clears_cache_and_forces_reload():
    """invalidate_cache() sets _cache to None; next retrieve() re-fetches from DB."""
    retriever_module._cache = None  # ensure clean state

    with patch("backend.rag.retriever.repository.list_chunks", new_callable=AsyncMock) as mock_lc:
        with patch("backend.rag.retriever.repository.get_video", new_callable=AsyncMock) as mock_gv:
            mock_lc.return_value = _CHUNK
            mock_gv.return_value = {"title": "Test Video"}

            # First call populates the cache
            await retrieve([0.1, 0.2, 0.3])
            assert retriever_module._cache is not None

            # Invalidate — cache should be None again
            invalidate_cache()
            assert retriever_module._cache is None

            # Second call must hit DB again
            await retrieve([0.1, 0.2, 0.3])

    assert mock_lc.call_count == 2
