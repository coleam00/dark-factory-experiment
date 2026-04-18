"""
Tests for IngestRequest segments validator.

Verifies:
  - Valid segments pass validation
  - Missing required keys raise ValueError
  - Wrong types for start/end/text raise ValueError
  - None segments returns None
"""

import pytest
from pydantic import ValidationError

from backend.routes.ingest import IngestRequest


class TestIngestRequestSegments:
    def test_valid_segments_passes(self) -> None:
        """Valid segments with all required keys pass validation."""
        req = IngestRequest(
            title="Test Video",
            description="A test video description",
            url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]  # type: ignore[arg-type]
            transcript="Full transcript text here",
            segments=[{"start": 0.0, "end": 10.0, "text": "Hello world"}],
        )
        assert req.segments is not None
        assert len(req.segments) == 1
        assert req.segments[0]["start"] == 0.0
        assert req.segments[0]["end"] == 10.0
        assert req.segments[0]["text"] == "Hello world"

    def test_none_segments_is_none(self) -> None:
        """No segments provided returns None."""
        req = IngestRequest(
            title="Test Video",
            description="A test video description",
            url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
            transcript="Full transcript text here",
        )
        assert req.segments is None

    def test_missing_start_key_raises(self) -> None:
        """Segment missing 'start' key raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"end": 10.0, "text": "Hello"}],
            )
        assert "start" in str(exc_info.value)

    def test_missing_end_key_raises(self) -> None:
        """Segment missing 'end' key raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"start": 0.0, "text": "Hello"}],
            )
        assert "end" in str(exc_info.value)

    def test_missing_text_key_raises(self) -> None:
        """Segment missing 'text' key raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"start": 0.0, "end": 10.0}],
            )
        assert "text" in str(exc_info.value)

    def test_non_dict_segment_raises(self) -> None:
        """Non-dict segment raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=["not a dict"],  # type: ignore[list-item]
            )
        assert "dict" in str(exc_info.value).lower()

    def test_start_as_string_raises(self) -> None:
        """Segment with start as string raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"start": "abc", "end": 10.0, "text": "Hello"}],
            )
        assert "start" in str(exc_info.value)

    def test_end_as_string_raises(self) -> None:
        """Segment with end as string raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"start": 0.0, "end": "xyz", "text": "Hello"}],
            )
        assert "end" in str(exc_info.value)

    def test_text_as_number_raises(self) -> None:
        """Segment with text as number raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            IngestRequest(
                title="Test Video",
                description="A test video description",
                url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
                transcript="Full transcript text here",
                segments=[{"start": 0.0, "end": 10.0, "text": 123}],
            )
        assert "text" in str(exc_info.value)

    def test_start_as_int_passes(self) -> None:
        """Segment with start as int (not float) passes validation."""
        req = IngestRequest(
            title="Test Video",
            description="A test video description",
            url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
            transcript="Full transcript text here",
            segments=[{"start": 0, "end": 10, "text": "Hello world"}],
        )
        assert req.segments[0]["start"] == 0  # type: ignore[index]
        assert req.segments[0]["end"] == 10  # type: ignore[index]

    def test_multiple_segments_passes(self) -> None:
        """Multiple valid segments all pass validation."""
        req = IngestRequest(
            title="Test Video",
            description="A test video description",
            url="https://youtube.com/watch?v=abc123",  # type: ignore[arg-type]
            transcript="Full transcript text here",
            segments=[
                {"start": 0.0, "end": 5.0, "text": "First segment"},
                {"start": 5.0, "end": 10.0, "text": "Second segment"},
                {"start": 10.0, "end": 15.0, "text": "Third segment"},
            ],
        )
        assert len(req.segments) == 3  # type: ignore[arg-type]
