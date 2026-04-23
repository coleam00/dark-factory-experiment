"""Tests for selection helpers (apply_per_video_cap)."""

import pytest

from backend.rag.selection import apply_per_video_cap


class TestApplyPerVideoCap:
    """Tests for the per-video cap function."""

    def test_all_same_video_capped(self):
        """10 chunks from same video, max_per_video=3 → returns 3."""
        chunks = [
            {"video_id": "v1", "chunk_id": f"c{i}", "content": f"content {i}"}
            for i in range(10)
        ]
        result = apply_per_video_cap(chunks, max_per_video=3)
        assert len(result) == 3
        assert [c["chunk_id"] for c in result] == ["c0", "c1", "c2"]

    def test_multi_video_unchanged(self):
        """10 chunks across 5 videos, max_per_video=3 → all 10 returned."""
        chunks = [
            {"video_id": f"v{i % 5}", "chunk_id": f"c{i}", "content": f"content {i}"}
            for i in range(10)
        ]
        result = apply_per_video_cap(chunks, max_per_video=3)
        assert len(result) == 10

    def test_order_preserved(self):
        """Relative ranking order of kept chunks matches input order."""
        chunks = [
            {"video_id": "v1", "chunk_id": "c0"},
            {"video_id": "v2", "chunk_id": "c1"},
            {"video_id": "v1", "chunk_id": "c2"},
            {"video_id": "v3", "chunk_id": "c3"},
            {"video_id": "v1", "chunk_id": "c4"},
            {"video_id": "v2", "chunk_id": "c5"},
        ]
        result = apply_per_video_cap(chunks, max_per_video=2)
        # c0, c1 kept (first of v1, v2); c2 kept (second of v1); c3 kept (first of v3)
        # c4 skipped (third of v1); c5 kept (second of v2)
        assert [c["chunk_id"] for c in result] == ["c0", "c1", "c2", "c3", "c5"]

    def test_empty_input(self):
        """Empty list → empty list."""
        result = apply_per_video_cap([], max_per_video=3)
        assert result == []

    def test_single_chunk(self):
        """1 chunk → 1 chunk."""
        chunks = [{"video_id": "v1", "chunk_id": "c0", "content": "hello"}]
        result = apply_per_video_cap(chunks, max_per_video=3)
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c0"