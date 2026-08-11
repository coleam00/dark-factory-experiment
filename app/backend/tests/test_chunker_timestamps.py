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


# Text long enough to force HybridChunker to split one segment into several
# sub-chunks (~7k chars against a 512-token limit). The existing tests in this
# file all use short segments that yield exactly one sub-chunk, so before this
# the `len(sub_chunks) > 1` distribution branch had no coverage at all.
_SPLITTABLE_TEXT = " ".join(
    f"Sentence number {i} explains an idea about retrieval augmented generation in some detail."
    for i in range(80)
)


class TestMultiSubChunkTimestampDistribution:
    """Issue #245 — the timestamp-distribution branch, which was untested.

    The issue asked for zero-duration segments to stop producing all-equal
    timestamps. That is not achievable: a segment where end == start has no
    span to distribute, and inventing one would fabricate deep-link targets.
    What IS a real defect is a segment where end < start, where the old
    unconditional division produced timestamps that ran backwards.
    """

    def test_splittable_text_actually_splits(self) -> None:
        """Guard the guard: if this stops splitting, the tests below go vacuous."""
        chunks, _ = chunk_video_timestamped(
            [{"start": 10.0, "end": 110.0, "text": _SPLITTABLE_TEXT}]
        )
        assert len(chunks) > 1

    def test_normal_duration_still_distributes_evenly(self) -> None:
        """The behaviour that must NOT change. Previously unguarded."""
        chunks, _ = chunk_video_timestamped(
            [{"start": 10.0, "end": 110.0, "text": _SPLITTABLE_TEXT}]
        )
        n = len(chunks)
        step = 100.0 / n

        for i, c in enumerate(chunks):
            assert c["start_seconds"] == pytest.approx(10.0 + i * step)
            assert c["end_seconds"] == pytest.approx(10.0 + (i + 1) * step)

        assert chunks[0]["start_seconds"] == pytest.approx(10.0)
        assert chunks[-1]["end_seconds"] == pytest.approx(110.0)

    def test_negative_duration_gets_no_fabricated_distribution(self) -> None:
        """end < start must not be divided into descending windows.

        The malformed span itself is passed through (garbage in, garbage out —
        nothing upstream produces this today). What the guard prevents is the
        old behaviour of dividing a negative duration into a series of
        progressively *earlier* windows: 90->70, 70->50, 50->30. Those look
        like real, distinct, precise timestamps and are entirely invented.
        """
        chunks, _ = chunk_video_timestamped(
            [{"start": 90.0, "end": 30.0, "text": _SPLITTABLE_TEXT}]
        )
        assert len(chunks) > 1

        # Every sub-chunk keeps the segment's own bounds, so no chunk claims a
        # window the segment never described.
        for c in chunks:
            assert c["start_seconds"] == 90.0
            assert c["end_seconds"] == 30.0

        starts = [c["start_seconds"] for c in chunks]
        assert len(set(starts)) == 1, f"distribution fabricated distinct starts: {starts}"

    def test_zero_duration_stays_on_the_segment_boundary(self) -> None:
        """No fabricated spread when there is no duration to spread."""
        chunks, _ = chunk_video_timestamped(
            [{"start": 42.0, "end": 42.0, "text": _SPLITTABLE_TEXT}]
        )
        assert len(chunks) > 1
        for c in chunks:
            assert c["start_seconds"] == 42.0
            assert c["end_seconds"] == 42.0
