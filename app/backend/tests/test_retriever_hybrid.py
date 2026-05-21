"""
Tests for hybrid retrieval (tsvector + pgvector via RRF).

Verifies:
  - RRF merge is deterministic given fixed inputs
  - RRF correctly combines keyword and vector rankings
  - A rare exact term ranks higher via hybrid than pure cosine
  - A conceptual query with no exact-term overlap still returns relevant results
  - Hybrid retriever raises clear error when DATABASE_URL is unset
  - The video-metadata cache is single-flight and LRU-bounded
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import backend.rag.retriever_hybrid as retriever_hybrid_module
from backend.rag.retriever_hybrid import _rrf_merge, retrieve_hybrid

# Minimal chunk fixtures for RRF testing
_CHUNK_A = {
    "id": "c1",
    "video_id": "v1",
    "content": "hello world",
    "chunk_index": 0,
    "start_seconds": 0.0,
    "end_seconds": 10.0,
    "snippet": "",
}
_CHUNK_B = {
    "id": "c2",
    "video_id": "v1",
    "content": "mcp server setup guide",
    "chunk_index": 1,
    "start_seconds": 10.0,
    "end_seconds": 20.0,
    "snippet": "",
}
_CHUNK_C = {
    "id": "c3",
    "video_id": "v2",
    "content": "introduction to RAG systems",
    "chunk_index": 0,
    "start_seconds": 0.0,
    "end_seconds": 15.0,
    "snippet": "",
}


class TestRRFMerge:
    """Tests for the Reciprocal Rank Fusion merge function."""

    def test_rrf_merge_is_deterministic(self):
        """RRF merge with same inputs produces same output order."""
        keyword = [_CHUNK_A, _CHUNK_B, _CHUNK_C]
        vector = [_CHUNK_B, _CHUNK_A, _CHUNK_C]

        result1 = _rrf_merge(keyword, vector, k=60, top_k=5)
        result2 = _rrf_merge(keyword, vector, k=60, top_k=5)

        assert [r["id"] for r in result1] == [r["id"] for r in result2]

    def test_rrf_merge_top_k_respected(self):
        """RRF merge returns at most top_k results."""
        keyword = [_CHUNK_A, _CHUNK_B, _CHUNK_C]
        vector = [_CHUNK_A, _CHUNK_B, _CHUNK_C]

        result = _rrf_merge(keyword, vector, k=60, top_k=2)
        assert len(result) <= 2

    def test_rrf_chunk_in_both_lists_ranks_higher(self):
        """A chunk present in both result sets scores higher than one in only one."""
        keyword = [_CHUNK_A, _CHUNK_B]  # A at rank 0, B at rank 1
        vector = [_CHUNK_B, _CHUNK_C]  # B at rank 0, C at rank 1

        result = _rrf_merge(keyword, vector, k=60, top_k=3)

        # B is in both lists at ranks (1, 0) → score = 1/(60+1) + 1/(60+0) ≈ 0.0328
        # A is in only keyword at rank 0 → score = 1/(60+0) = 0.0164
        # C is in only vector at rank 1 → score = 1/(60+1) = 0.0164
        # So B > A = C (tie-break by insertion order)

        ids = [r["id"] for r in result]
        assert ids.index("c2") < ids.index("c1")  # B ranks above A

    def test_rrf_merge_empty_keyword_returns_vector_ranked(self):
        """Empty keyword list returns vector results sorted by vector rank."""
        keyword = []
        vector = [_CHUNK_A, _CHUNK_B, _CHUNK_C]

        result = _rrf_merge(keyword, vector, k=60, top_k=5)
        assert [r["id"] for r in result] == ["c1", "c2", "c3"]

    def test_rrf_merge_empty_vector_returns_keyword_ranked(self):
        """Empty vector list returns keyword results sorted by keyword rank."""
        keyword = [_CHUNK_A, _CHUNK_B, _CHUNK_C]
        vector = []

        result = _rrf_merge(keyword, vector, k=60, top_k=5)
        assert [r["id"] for r in result] == ["c1", "c2", "c3"]

    def test_rrf_merge_all_empty_returns_empty(self):
        """Both empty returns empty list."""
        result = _rrf_merge([], [], k=60, top_k=5)
        assert result == []


class TestRetrieveHybrid:
    """Integration tests for retrieve_hybrid()."""

    async def test_raises_when_database_url_unset(self, monkeypatch):
        """retrieve_hybrid raises RuntimeError if DATABASE_URL is not set."""
        # Temporarily unset DATABASE_URL (env var AND cached config constant)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import backend.config

        monkeypatch.setattr(backend.config, "DATABASE_URL", "")

        with pytest.raises(RuntimeError, match="Hybrid retrieval requires Postgres"):
            await retrieve_hybrid("test query", [0.1] * 1536, top_k=5)

    async def test_calls_keyword_and_vector_search(self):
        """retrieve_hybrid calls both keyword_search and vector_search_pg."""
        from backend.config import HYBRID_OVERFETCH_FACTOR

        fetch_k = 5 * HYBRID_OVERFETCH_FACTOR

        with (
            patch(
                "backend.rag.retriever_hybrid.repository.keyword_search",
                new_callable=AsyncMock,
            ) as mock_kw,
            patch(
                "backend.rag.retriever_hybrid.repository.vector_search_pg",
                new_callable=AsyncMock,
            ) as mock_vec,
            patch(
                "backend.rag.retriever_hybrid.repository.get_video",
                new_callable=AsyncMock,
            ) as mock_video,
        ):
            mock_kw.return_value = [_CHUNK_A, _CHUNK_B]
            mock_vec.return_value = [_CHUNK_B, _CHUNK_C]
            mock_video.return_value = {
                "title": "Test Video",
                "url": "https://youtube.com/watch?v=abc",
            }

            result = await retrieve_hybrid("test query", [0.1] * 1536, top_k=5)

            # Verify both search functions were called
            assert mock_kw.called
            assert mock_vec.called

            # Verify correct arguments passed (over-fetch factor applied)
            mock_kw.assert_called_once_with(
                "test query", top_k=fetch_k, language="english", allowed_source_types=["youtube"]
            )
            mock_vec.assert_called_once_with(
                [0.1] * 1536, top_k=fetch_k, allowed_source_types=["youtube"]
            )

            # Verify result shape has all required citation fields
            assert len(result) >= 1
            for item in result:
                assert "chunk_id" in item
                assert "video_title" in item
                assert "video_url" in item
                assert "start_seconds" in item
                assert "end_seconds" in item
                assert "snippet" in item

    async def test_rare_exact_term_boosted_by_keyword_path(self):
        """A technical acronym (weak in cosine space) ranks higher via hybrid.

        This test verifies that when keyword_search returns a result that vector_search
        would rank poorly, the hybrid merge still surfaces it near the top.
        """
        # chunk_b has the rare "mcp" term; chunk_a does not
        chunk_mcp = {
            "id": "c_mcp",
            "video_id": "v1",
            "content": "the MCP protocol enables",
            "chunk_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "snippet": "",
        }
        chunk_unrelated = {
            "id": "c_unrelated",
            "video_id": "v1",
            "content": "hello world general query",
            "chunk_index": 1,
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "snippet": "",
        }

        # Vector search ranks unrelated higher (embedding similarity)
        # Keyword search ranks MCP higher (exact term match)
        keyword = [chunk_mcp, chunk_unrelated]
        vector = [chunk_unrelated, chunk_mcp]

        result = _rrf_merge(keyword, vector, k=60, top_k=2)

        # MCP should outrank unrelated because it appears in BOTH lists
        # (even though vector prefers unrelated, keyword strongly prefers MCP)
        ids = [r["id"] for r in result]
        assert ids.index("c_mcp") < ids.index("c_unrelated")

    async def test_conceptual_query_relies_on_vector_path(self):
        """A conceptual query with no exact terms still works via vector search."""
        chunk_conceptual = {
            "id": "c_concept",
            "video_id": "v1",
            "content": "machine learning inference optimization",
            "chunk_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "snippet": "",
        }

        keyword = []  # No exact term matches
        vector = [chunk_conceptual]

        result = _rrf_merge(keyword, vector, k=60, top_k=5)
        assert result[0]["id"] == "c_concept"


class TestVideoCacheSingleFlight:
    """Regression tests for the single-flight + LRU-bounded video metadata cache."""

    async def test_single_flight_concurrent_misses_call_get_video_once(self):
        """N concurrent retrieve_hybrid() calls on the same uncached video_id
        result in exactly one repository.get_video() call (single-flight).
        """
        release = asyncio.Event()

        async def gated_get_video(_video_id: str) -> dict:
            await release.wait()
            return {
                "title": "T",
                "url": "U",
                "source_type": "youtube",
                "lesson_url": "",
            }

        with (
            patch(
                "backend.rag.retriever_hybrid.repository.keyword_search",
                new_callable=AsyncMock,
            ) as mock_kw,
            patch(
                "backend.rag.retriever_hybrid.repository.vector_search_pg",
                new_callable=AsyncMock,
            ) as mock_vec,
            patch(
                "backend.rag.retriever_hybrid.repository.get_video",
                new_callable=AsyncMock,
                side_effect=gated_get_video,
            ) as mock_video,
        ):
            mock_kw.return_value = [_CHUNK_A]
            mock_vec.return_value = [_CHUNK_A]

            async def kick() -> list[dict]:
                return await retrieve_hybrid("q", [0.0] * 1536, top_k=1)

            tasks = [asyncio.create_task(kick()) for _ in range(5)]
            # Yield so all tasks reach the gated get_video before we release.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(*tasks)

            assert mock_video.call_count == 1
            assert len(results) == 5
            for r in results:
                assert r[0]["video_title"] == "T"

    async def test_lru_eviction_bounds_cache_size(self, monkeypatch):
        """Cache size never exceeds MAX_VIDEO_CACHE_SIZE; oldest entry is evicted first."""
        monkeypatch.setattr(retriever_hybrid_module, "MAX_VIDEO_CACHE_SIZE", 2)

        def chunk_for(video_id: str) -> dict:
            return {
                "id": f"c-{video_id}",
                "video_id": video_id,
                "content": "x",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 1.0,
                "snippet": "",
            }

        async def fake_get_video(video_id: str) -> dict:
            return {"title": f"title-{video_id}", "url": f"url-{video_id}"}

        with (
            patch(
                "backend.rag.retriever_hybrid.repository.keyword_search",
                new_callable=AsyncMock,
            ) as mock_kw,
            patch(
                "backend.rag.retriever_hybrid.repository.vector_search_pg",
                new_callable=AsyncMock,
            ) as mock_vec,
            patch(
                "backend.rag.retriever_hybrid.repository.get_video",
                new_callable=AsyncMock,
                side_effect=fake_get_video,
            ),
        ):
            for vid in ("va", "vb", "vc"):
                mock_kw.return_value = [chunk_for(vid)]
                mock_vec.return_value = [chunk_for(vid)]
                await retrieve_hybrid("q", [0.0] * 1536, top_k=1)

            assert len(retriever_hybrid_module._video_cache) == 2
            # Oldest insert (va) should have been evicted; vb and vc remain.
            assert "va" not in retriever_hybrid_module._video_cache
            assert "vb" in retriever_hybrid_module._video_cache
            assert "vc" in retriever_hybrid_module._video_cache

    async def test_get_video_exception_clears_inflight_for_retry(self):
        """A failed get_video() must clear the in-flight entry so the next call retries."""
        call_count = {"n": 0}

        async def flaky_get_video(_video_id: str) -> dict:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            return {
                "title": "OK",
                "url": "U",
                "source_type": "youtube",
                "lesson_url": "",
            }

        with (
            patch(
                "backend.rag.retriever_hybrid.repository.keyword_search",
                new_callable=AsyncMock,
            ) as mock_kw,
            patch(
                "backend.rag.retriever_hybrid.repository.vector_search_pg",
                new_callable=AsyncMock,
            ) as mock_vec,
            patch(
                "backend.rag.retriever_hybrid.repository.get_video",
                new_callable=AsyncMock,
                side_effect=flaky_get_video,
            ),
        ):
            mock_kw.return_value = [_CHUNK_A]
            mock_vec.return_value = [_CHUNK_A]

            with pytest.raises(RuntimeError, match="boom"):
                await retrieve_hybrid("q", [0.0] * 1536, top_k=1)

            # Second call should retry get_video and succeed.
            result = await retrieve_hybrid("q", [0.0] * 1536, top_k=1)
            assert result[0]["video_title"] == "OK"
            assert call_count["n"] == 2
            # No leaked in-flight entry.
            assert "v1" not in retriever_hybrid_module._inflight
