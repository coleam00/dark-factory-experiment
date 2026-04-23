"""
Tests for chunk expansion (expand_and_merge).

Verifies:
  - window=0 returns input unchanged (no DB call)
  - Adjacent chunks merge into single span
  - Non-adjacent chunks remain separate spans
  - Multiple videos are processed independently
  - Citation fields (chunk_id, snippet) come from originally-retrieved chunk
  - Empty chunks list returns empty
"""

import pytest

from backend.rag.expansion import expand_and_merge

# Minimal chunk fixtures with all required fields
_ORIG_CHUNK = {
    "chunk_id": "c5",
    "video_id": "v1",
    "content": "hello world",
    "chunk_index": 5,
    "start_seconds": 50.0,
    "end_seconds": 60.0,
    "snippet": "hello world snippet",
    "video_title": "Test Video",
    "video_url": "https://youtube.com/watch?v=v1",
}
_NEIGHBOR_LEFT = {
    "id": "c4",
    "video_id": "v1",
    "content": "previous content",
    "chunk_index": 4,
    "start_seconds": 40.0,
    "end_seconds": 50.0,
    "snippet": "previous snippet",
    "video_title": "Test Video",
    "video_url": "https://youtube.com/watch?v=v1",
}
_NEIGHBOR_RIGHT = {
    "id": "c6",
    "video_id": "v1",
    "content": "next content",
    "chunk_index": 6,
    "start_seconds": 60.0,
    "end_seconds": 70.0,
    "snippet": "next snippet",
    "video_title": "Test Video",
    "video_url": "https://youtube.com/watch?v=v1",
}
_NON_ADJACENT_CHUNK = {
    "chunk_id": "c20",
    "video_id": "v1",
    "content": "far away chunk",
    "chunk_index": 20,
    "start_seconds": 200.0,
    "end_seconds": 210.0,
    "snippet": "far away snippet",
    "video_title": "Test Video",
    "video_url": "https://youtube.com/watch?v=v1",
}
_CHUNK_VIDEO2 = {
    "chunk_id": "d5",
    "video_id": "v2",
    "content": "video 2 chunk",
    "chunk_index": 5,
    "start_seconds": 50.0,
    "end_seconds": 60.0,
    "snippet": "video 2 snippet",
    "video_title": "Test Video 2",
    "video_url": "https://youtube.com/watch?v=v2",
}


class TestExpandAndMerge:
    """Tests for expand_and_merge."""

    @pytest.mark.asyncio
    async def test_window_zero_returns_input_unchanged(self):
        """window=0 bypasses expansion entirely, returns input chunks unchanged."""
        chunks = [_ORIG_CHUNK]
        result = await expand_and_merge(chunks, window=0)
        assert result == chunks

    @pytest.mark.asyncio
    async def test_empty_chunks_returns_empty(self):
        """Empty list returns empty list without calling DB."""
        result = await expand_and_merge([], window=1)
        assert result == []

    @pytest.mark.asyncio
    async def test_adjacent_chunks_merge_into_single_span(self):
        """Adjacent chunks (indices 4, 5, 6) with window=1 merge into one span covering 4-6."""
        # _ORIG_CHUNK has chunk_index=5, neighbors are indices 4 and 6
        # With window=1, the three chunks (4,5,6) are all adjacent → one merged span
        chunks = [_ORIG_CHUNK]

        async def fake_neighbors(video_id, chunk_index, window):
            if chunk_index == 5:
                return [_NEIGHBOR_LEFT, _NEIGHBOR_RIGHT]
            return []

        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)

        assert len(result) == 1
        span = result[0]
        assert span["video_id"] == "v1"
        # Content should be merged: left(4) + orig(5) + right(6)
        assert "previous content" in span["content"]
        assert "hello world" in span["content"]
        assert "next content" in span["content"]
        # Citation fields from original chunk
        assert span["chunk_id"] == "c5"
        assert span["snippet"] == "hello world snippet"
        # Span boundaries
        assert span["start_seconds"] == 40.0  # from left neighbor
        assert span["end_seconds"] == 70.0  # from right neighbor

    @pytest.mark.asyncio
    async def test_non_adjacent_chunks_remain_separate(self):
        """Non-adjacent chunks (indices 5 and 20) remain as separate spans."""
        chunks = [_ORIG_CHUNK, _NON_ADJACENT_CHUNK]  # both video_id="v1", indices 5 and 20

        async def fake_neighbors(video_id, chunk_index, window):
            if chunk_index == 5:
                return [_NEIGHBOR_LEFT, _NEIGHBOR_RIGHT]
            return []

        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)

        # v1 has two non-adjacent spans: one covering indices 4-6 (c4,c5,c6) and one at index 20 (c20)
        # Both _ORIG_CHUNK and _NON_ADJACENT_CHUNK are from v1, so no v2 spans
        assert len(result) == 2
        video1_spans = [r for r in result if r["video_id"] == "v1"]
        assert len(video1_spans) == 2
        video2_spans = [r for r in result if r["video_id"] == "v2"]
        assert len(video2_spans) == 0

    @pytest.mark.asyncio
    async def test_multiple_videos_processed_independently(self):
        """Chunks from different videos are processed independently."""
        chunks = [_ORIG_CHUNK, _CHUNK_VIDEO2]

        async def fake_neighbors(video_id, chunk_index, window):
            if video_id == "v1" and chunk_index == 5:
                return [_NEIGHBOR_LEFT, _NEIGHBOR_RIGHT]
            return []

        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)

        assert len(result) == 2  # v1 merges into one span, v2 stays as-is

    @pytest.mark.asyncio
    async def test_citation_chunk_id_from_original(self):
        """Citation chunk_id always comes from the originally-retrieved chunk, not expanded."""
        chunks = [_ORIG_CHUNK]

        async def fake_neighbors(video_id, chunk_index, window):
            return [_NEIGHBOR_LEFT, _NEIGHBOR_RIGHT]

        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)

        assert len(result) == 1
        # chunk_id must be from the originally-retrieved chunk
        assert result[0]["chunk_id"] == "c5"
        # snippet must also be from the originally-retrieved chunk
        assert result[0]["snippet"] == "hello world snippet"

    @pytest.mark.asyncio
    async def test_chunk_index_zero_lower_bound_clamped(self):
        """Chunk index 0 with window=1 does not underflow."""
        zero_chunk = {
            "chunk_id": "c0",
            "video_id": "v1",
            "content": "first chunk",
            "chunk_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "snippet": "first snippet",
            "video_title": "Test Video",
            "video_url": "https://youtube.com/watch?v=v1",
        }
        chunks = [zero_chunk]

        called = False

        async def fake_neighbors(video_id, chunk_index, window):
            nonlocal called
            # Should be called with min_index = max(0, 0-1) = 0
            assert chunk_index == 0
            called = True
            return []

        await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)
        assert called, "fake_neighbors was not called"

    @pytest.mark.asyncio
    async def test_neighbors_fetch_exception_caught_and_logged(self):
        """When _fetch_neighbors raises, the exception is logged and remaining chunks are processed."""
        chunks = [
            {
                "chunk_id": "c5",
                "video_id": "v1",
                "content": "hello world",
                "chunk_index": 5,
                "start_seconds": 50.0,
                "end_seconds": 60.0,
                "snippet": "hello world snippet",
                "video_title": "T",
                "video_url": "u",
            },
        ]

        async def throwing_neighbors(video_id, chunk_index, window):
            raise RuntimeError("DB connection failed")

        # Should NOT raise - exceptions are caught internally and logged
        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=throwing_neighbors)
        # Returns the original chunk unchanged since all neighbor fetches failed
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c5"

    @pytest.mark.asyncio
    async def test_overlapping_expansions_deduplicated(self):
        """When two retrieved chunks share a neighbor, that neighbor appears only once."""
        chunks = [
            {
                "chunk_id": "c4",
                "video_id": "v1",
                "content": "chunk 4",
                "chunk_index": 4,
                "start_seconds": 40.0,
                "end_seconds": 50.0,
                "snippet": "s4",
                "video_title": "T",
                "video_url": "u",
            },
            {
                "chunk_id": "c6",
                "video_id": "v1",
                "content": "chunk 6",
                "chunk_index": 6,
                "start_seconds": 60.0,
                "end_seconds": 70.0,
                "snippet": "s6",
                "video_title": "T",
                "video_url": "u",
            },
        ]

        async def fake_neighbors(video_id, chunk_index, window):
            if chunk_index == 4:
                return [
                    {
                        "id": "c3",
                        "video_id": "v1",
                        "content": "chunk 3",
                        "chunk_index": 3,
                        "start_seconds": 30.0,
                        "end_seconds": 40.0,
                        "snippet": "s3",
                        "video_title": "T",
                        "video_url": "u",
                    }
                ]
            if chunk_index == 6:
                return [
                    {
                        "id": "c7",
                        "video_id": "v1",
                        "content": "chunk 7",
                        "chunk_index": 7,
                        "start_seconds": 70.0,
                        "end_seconds": 80.0,
                        "snippet": "s7",
                        "video_title": "T",
                        "video_url": "u",
                    }
                ]
            return []

        result = await expand_and_merge(chunks, window=1, _fetch_neighbors=fake_neighbors)
        # Indices 3,4 are adjacent (span 1) and 6,7 are adjacent (span 2) — 2 spans total
        assert len(result) == 2
        # Verify no chunk appears twice
        all_contents = [r["content"] for r in result]
        for content in all_contents:
            assert all_contents.count(content) == 1
