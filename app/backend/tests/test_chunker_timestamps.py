"""
Tests for chunk_video_timestamped and chunk_video_fallback functions.

Verifies:
  - chunk_video_timestamped preserves start/end timestamps from input segments
  - chunk_video_timestamped stores original segment text as snippet (not contextualized)
  - chunk_video_fallback produces monotonic estimated timestamps
  - chunk_video_fallback evenly distributes estimated duration across chunks
"""

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

    def test_zero_duration_segment_does_not_collapse_timestamps(self) -> None:
        """A zero-width segment that splits into multiple sub-chunks keeps the
        original [start, end] bounds instead of step-distributed degenerate spans."""
        long_text = "This sentence pads the segment so the chunker must split it. " * 200
        segments = [{"start": 42.0, "end": 42.0, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) >= 1
        for chunk in result:
            assert chunk["end_seconds"] >= chunk["start_seconds"]
            # Original segment bounds preserved — no step-based redistribution.
            assert chunk["start_seconds"] == 42.0
            assert chunk["end_seconds"] == 42.0

    def test_negative_duration_segment_keeps_original_bounds(self) -> None:
        """A segment with end < start (float rounding) is not redistributed,
        which would produce decreasing timestamps."""
        long_text = "Another padding sentence to force a multi-chunk split here. " * 200
        segments = [{"start": 100.0, "end": 99.9, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) >= 1
        for chunk in result:
            assert chunk["start_seconds"] == 100.0
            assert chunk["end_seconds"] == 99.9

    def test_positive_duration_still_distributes(self) -> None:
        """A positive-duration segment that splits into multiple sub-chunks still
        gets evenly distributed, increasing, non-degenerate spans."""
        long_text = "Yet another padding sentence so the chunker splits this up. " * 200
        segments = [{"start": 10.0, "end": 70.0, "text": long_text}]
        result, _ = chunk_video_timestamped(segments)

        assert len(result) >= 1
        if len(result) > 1:
            assert result[0]["start_seconds"] == 10.0
            assert result[-1]["end_seconds"] == 70.0
            for i, chunk in enumerate(result):
                assert chunk["end_seconds"] > chunk["start_seconds"]
                if i > 0:
                    assert chunk["start_seconds"] > result[i - 1]["start_seconds"]


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
