"""
Direct unit tests for backend.services.supadata.

Tests get_transcript() normalization and error handling in isolation,
independent of the Alembic-pending test_channel_sync.py skip.
Uses unittest.mock.patch to replace _get_client().
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from supadata import SupadataError

from backend.services.supadata import get_transcript


class MockTranscriptChunk:
    """Mimics the TranscriptChunk segment returned by the Supadata SDK."""

    def __init__(self, text: str, offset: float = 0.0, duration: float = 1.0, lang: str = "en"):
        self.text = text
        self.offset = offset
        self.duration = duration
        self.lang = lang


class MockTranscriptResultString:
    """Mock result where content is a plain string."""

    def __init__(self, text: str):
        self.content = text


class MockTranscriptResultList:
    """Mock result where content is a list of TranscriptChunk segments."""

    def __init__(self, chunks: list[MockTranscriptChunk]):
        self.content = chunks


class MockTranscriptResultNone:
    """Mock result where content is None (no transcript available)."""

    def __init__(self):
        self.content = None


# ---------------------------------------------------------------------------
# Tests: get_transcript — string content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transcript_returns_string_content():
    """When SDK returns content as a plain string, it is returned as-is."""
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultString(
        "Hello world, this is a transcript."
    )

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result == "Hello world, this is a transcript."
    mock_client.transcript.assert_called_once_with(
        url="https://youtube.com/watch?v=dQw4w9WgXcQ", lang="en"
    )


@pytest.mark.asyncio
async def test_get_transcript_empty_string_returns_none():
    """When SDK returns an empty string content, get_transcript returns None."""
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultString("")

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


@pytest.mark.asyncio
async def test_get_transcript_whitespace_only_string_returned_as_is():
    """When SDK returns whitespace-only string, it is returned as-is (not stripped to None).

    The string case does NOT strip whitespace — only the list-of-chunks path does.
    This matches the existing normalization behavior in supadata.py.
    """
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultString("   \n\t  ")

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result == "   \n\t  "  # returned as-is, not None


# ---------------------------------------------------------------------------
# Tests: get_transcript — list of chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transcript_handles_list_of_chunks():
    """When SDK returns content as a list of TranscriptChunk, they are joined."""
    chunks = [
        MockTranscriptChunk("First segment."),
        MockTranscriptChunk("Second segment."),
        MockTranscriptChunk("Third segment."),
    ]
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultList(chunks)

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result == "First segment. Second segment. Third segment."


@pytest.mark.asyncio
async def test_get_transcript_list_chunks_empty_text_included_as_empty():
    """Empty .text chunks produce double spaces when joined (not silently dropped).

    The join uses " ".join(), so empty strings produce consecutive spaces.
    This matches the actual normalization behavior in supadata.py.
    """
    chunks = [
        MockTranscriptChunk("Valid segment."),
        MockTranscriptChunk(""),  # empty, contributes to double space
        MockTranscriptChunk("Another valid."),
    ]
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultList(chunks)

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    # Note: double space because "" joins to "Valid segment.  Another valid."
    assert result == "Valid segment.  Another valid."


@pytest.mark.asyncio
async def test_get_transcript_list_chunks_all_empty_returns_none():
    """When all chunks have empty .text, get_transcript returns None."""
    chunks = [
        MockTranscriptChunk(""),
        MockTranscriptChunk("  "),
    ]
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultList(chunks)

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


# ---------------------------------------------------------------------------
# Tests: get_transcript — None / falsy content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transcript_result_is_none_returns_none():
    """When result itself is falsy (None), get_transcript returns None."""
    mock_client = MagicMock()
    mock_client.transcript.return_value = None

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


@pytest.mark.asyncio
async def test_get_transcript_content_is_none_returns_none():
    """When result.content is None, get_transcript returns None."""
    mock_client = MagicMock()
    mock_client.transcript.return_value = MockTranscriptResultNone()

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


# ---------------------------------------------------------------------------
# Tests: get_transcript — SDK errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transcript_429_triggers_backoff_and_succeeds():
    """Supadata 429 triggers exponential backoff retry and succeeds on second attempt."""
    call_count = 0

    def transcript_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            exc = SupadataError(error="rate_limited", message="Rate limit", details="")
            exc.status = 429
            raise exc
        return MockTranscriptResultString("Retry succeeded.")

    mock_client = MagicMock()
    mock_client.transcript.side_effect = transcript_side_effect

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert call_count == 2
    assert result == "Retry succeeded."


@pytest.mark.asyncio
async def test_get_transcript_429_all_retries_exhausted_raises():
    """After 3 consecutive 429s, SupadataError is raised."""

    def always_429(*args, **kwargs):
        exc = SupadataError(error="rate_limited", message="Rate limit", details="")
        exc.status = 429
        raise exc

    mock_client = MagicMock()
    mock_client.transcript.side_effect = always_429

    with (
        patch("backend.services.supadata._get_client", return_value=mock_client),
        pytest.raises(SupadataError) as exc_info,
    ):
        await get_transcript("dQw4w9WgXcQ", lang="en")

    assert exc_info.value.status == 429


@pytest.mark.asyncio
async def test_get_transcript_404_returns_none():
    """Supadata 404 (video unavailable) returns None, not an exception."""

    def not_found(*args, **kwargs):
        exc = SupadataError(error="not_found", message="Transcript not found", details="")
        exc.status = 404
        raise exc

    mock_client = MagicMock()
    mock_client.transcript.side_effect = not_found

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


@pytest.mark.asyncio
async def test_get_transcript_400_returns_none():
    """Supadata 400 (bad request) returns None, not an exception."""

    def bad_request(*args, **kwargs):
        exc = SupadataError(error="bad_request", message="Bad request", details="")
        exc.status = 400
        raise exc

    mock_client = MagicMock()
    mock_client.transcript.side_effect = bad_request

    with patch("backend.services.supadata._get_client", return_value=mock_client):
        result = await get_transcript("dQw4w9WgXcQ", lang="en")

    assert result is None


@pytest.mark.asyncio
async def test_get_transcript_other_error_raises():
    """Supadata errors other than 429/404/400 are raised."""

    def server_error(*args, **kwargs):
        exc = SupadataError(error="server_error", message="Internal error", details="")
        exc.status = 500
        raise exc

    mock_client = MagicMock()
    mock_client.transcript.side_effect = server_error

    with (
        patch("backend.services.supadata._get_client", return_value=mock_client),
        pytest.raises(SupadataError) as exc_info,
    ):
        await get_transcript("dQw4w9WgXcQ", lang="en")

    assert exc_info.value.status == 500


@pytest.mark.asyncio
async def test_get_transcript_network_error_raises_wrapped():
    """Network errors (TimeoutError, OSError) are wrapped in SupadataError."""
    mock_client = MagicMock()
    mock_client.transcript.side_effect = TimeoutError("connection timed out")

    with (
        patch("backend.services.supadata._get_client", return_value=mock_client),
        pytest.raises(SupadataError) as exc_info,
    ):
        await get_transcript("dQw4w9WgXcQ", lang="en")

    assert "network_error" in exc_info.value.error


@pytest.mark.asyncio
async def test_get_transcript_oserror_network_error_raises_wrapped():
    """OSError (network refused) is wrapped in SupadataError with network_error tag."""
    mock_client = MagicMock()
    mock_client.transcript.side_effect = OSError("connection refused")

    with (
        patch("backend.services.supadata._get_client", return_value=mock_client),
        pytest.raises(SupadataError) as exc_info,
    ):
        await get_transcript("dQw4w9WgXcQ", lang="en")

    assert "network_error" in exc_info.value.error
