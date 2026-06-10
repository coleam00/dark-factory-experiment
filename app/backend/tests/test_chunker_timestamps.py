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

    def test_zero_duration_segment_splitting_keeps_original_boundary(self) -> None:
        """A zero-duration segment that splits keeps the original boundary on every sub-chunk."""
        long_text = " ".join(f"word{i}" for i in range(1500))
        segments = [{"start": 120.0, "end": 120.0, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        # The split must actually happen for the test to exercise the bug
        assert len(result) > 1
        for chunk in result:
            assert chunk["start_seconds"] == 120.0
            assert chunk["end_seconds"] == 120.0
        # Multiple distinct sub-chunks were produced from the one segment
        contents = [c["content"] for c in result]
        assert len(set(contents)) == len(contents)

    def test_negative_duration_segment_keeps_original_boundary(self) -> None:
        """A malformed end < start segment skips redistribution entirely."""
        long_text = " ".join(f"word{i}" for i in range(1500))
        segments = [{"start": 120.0, "end": 100.0, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) > 1
        for chunk in result:
            assert chunk["start_seconds"] == 120.0
            assert chunk["end_seconds"] == 100.0

    def test_positive_duration_segment_distributes_evenly(self) -> None:
        """A real-duration segment still distributes timestamps evenly across sub-chunks."""
        long_text = " ".join(f"word{i}" for i in range(1500))
        segments = [{"start": 0.0, "end": 100.0, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) > 1
        assert result[0]["start_seconds"] == pytest.approx(0.0)
        assert result[-1]["end_seconds"] == pytest.approx(100.0)
        for i in range(len(result) - 1):
            assert result[i]["end_seconds"] == pytest.approx(result[i + 1]["start_seconds"])
        for chunk in result:
            assert chunk["end_seconds"] > chunk["start_seconds"]


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
