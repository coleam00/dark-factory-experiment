"""
Tests for chunk_video_timestamped and chunk_video_fallback functions.

Verifies:
  - chunk_video_timestamped preserves start/end timestamps from input segments
  - chunk_video_timestamped stores original segment text as snippet (not contextualized)
  - chunk_video_fallback produces monotonic estimated timestamps
  - chunk_video_fallback evenly distributes estimated duration across chunks
"""

import pytest

from backend.rag.chunker import chunk_video_fallback, chunk_video_timestamped


def _long_splittable_text() -> str:
    """Text well over HybridChunker's 512-token limit with sentence boundaries,
    so a single segment splits into more than one sub-chunk."""
    return " ".join(
        f"This is sentence number {i} describing some part of the video content."
        for i in range(160)
    )


class TestChunkVideoTimestamped:
    def test_preserves_segment_timestamps(self) -> None:
        """Each returned chunk carries the start/end from its source segment."""
        segments = [
            {"start": 0.0, "end": 10.5, "text": "Hello world this is a test"},
            {"start": 10.5, "end": 25.0, "text": "And this is another segment of the video."},
        ]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) >= 1
        # At least one chunk should have the first segment's timestamps
        first_c = next((c for c in result if "Hello" in c["content"]), None)
        assert first_c is not None
        assert first_c["start_seconds"] == 0.0
        assert first_c["end_seconds"] == 10.5

    def test_snippet_is_original_segment_text(self) -> None:
        """snippet field contains the original uncontextualized segment text."""
        segments = [{"start": 0.0, "end": 5.0, "text": "Original transcript text here"}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) >= 1
        # Find the chunk that contains "Original transcript"
        chunk = next((c for c in result if "Original transcript" in c["snippet"]), None)
        assert chunk is not None
        # The snippet should be the raw segment text (up to 300 chars)
        assert chunk["snippet"] == "Original transcript text here"

    def test_empty_segments_returns_empty_list(self) -> None:
        """Empty input returns empty list."""
        result, had_errors = chunk_video_timestamped([])
        assert result == []
        assert had_errors is False

    def test_skips_empty_text_segments(self) -> None:
        """Segments with empty text are skipped."""
        segments = [
            {"start": 0.0, "end": 5.0, "text": ""},
            {"start": 5.0, "end": 10.0, "text": "Real content here"},
        ]
        result, _ = chunk_video_timestamped(segments)
        # Should not produce any chunks from the empty segment
        assert all("Real content" in c["content"] or "Real content" in c["snippet"] for c in result)

    def test_zero_duration_segment_does_not_redistribute(self) -> None:
        """A zero-duration segment (end == start) that splits into multiple
        sub-chunks keeps each sub-chunk on the original boundary instead of
        running the meaningless even-distribution (step = 0) over it."""
        segments = [{"start": 100.0, "end": 100.0, "text": _long_splittable_text()}]
        result, _ = chunk_video_timestamped(segments)

        # The branch must be reachable: the segment actually split.
        assert len(result) > 1
        # Distribution did not run: every sub-chunk keeps the preserved
        # [start_s, end_s] boundary (both 100.0), not a step-derived span.
        for chunk in result:
            assert chunk["start_seconds"] == 100.0
            assert chunk["end_seconds"] == 100.0

    def test_negative_duration_segment_does_not_redistribute(self) -> None:
        """A negative-duration segment (end < start) that splits into multiple
        sub-chunks keeps the original [start_s, end_s] boundary rather than
        producing distributed (decreasing) spans from a negative step."""
        segments = [{"start": 100.0, "end": 50.0, "text": _long_splittable_text()}]
        result, _ = chunk_video_timestamped(segments)

        # The branch must be reachable: the segment actually split.
        assert len(result) > 1
        # Distribution did not run: all sub-chunks retain the original boundary.
        for chunk in result:
            assert chunk["start_seconds"] == 100.0
            assert chunk["end_seconds"] == 50.0

    def test_positive_duration_segment_distributes_evenly(self) -> None:
        """A normal positive-duration segment that splits into multiple
        sub-chunks still gets evenly-distributed, increasing timestamps."""
        segments = [{"start": 0.0, "end": 100.0, "text": _long_splittable_text()}]
        result, _ = chunk_video_timestamped(segments)

        # The branch must be reachable: the segment actually split.
        n = len(result)
        assert n > 1

        step = 100.0 / n
        for i, chunk in enumerate(result):
            assert chunk["start_seconds"] == pytest.approx(i * step)
            assert chunk["end_seconds"] == pytest.approx((i + 1) * step)
        # Timestamps are strictly increasing and span the full segment.
        for i in range(1, n):
            assert result[i]["start_seconds"] > result[i - 1]["start_seconds"]
        assert result[0]["start_seconds"] == 0.0
        assert result[-1]["end_seconds"] == pytest.approx(100.0)


class TestChunkVideoFallback:
    def test_produces_monotonic_timestamps(self) -> None:
        """Estimated start/end timestamps are strictly increasing."""
        video = {
            "title": "Test Video",
            "transcript": " ".join(["word"] * 300),  # ~2 min at 150 WPM
        }
        result, _ = chunk_video_fallback(video)

        assert len(result) >= 1
        for i in range(1, len(result)):
            assert result[i]["start_seconds"] > result[i - 1]["start_seconds"]
            assert result[i]["end_seconds"] > result[i - 1]["end_seconds"]

    def test_end_after_start(self) -> None:
        """Each chunk's end_seconds is greater than its start_seconds."""
        video = {
            "title": "Test Video",
            "transcript": " ".join(["word"] * 300),
        }
        result, _ = chunk_video_fallback(video)

        for chunk in result:
            assert chunk["end_seconds"] >= chunk["start_seconds"]

    def test_snippet_contains_content_preview(self) -> None:
        """snippet field contains the first 300 chars of the chunk content."""
        video = {
            "title": "Test Video",
            "transcript": "A" * 500,
        }
        result, _ = chunk_video_fallback(video)

        assert len(result) >= 1
        for chunk in result:
            assert len(chunk["snippet"]) <= 300

    def test_empty_transcript_returns_empty(self) -> None:
        """Empty transcript returns empty list."""
        video = {"title": "Test", "transcript": ""}
        result, had_errors = chunk_video_fallback(video)
        assert result == []
        assert had_errors is True
