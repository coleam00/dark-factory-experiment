"""
Tests for the unified video-ingest helper and /api/ingest/from-url endpoint.

Verifies:
  - URL parsing (valid/invalid YouTube URLs)
  - fetch_video_for_ingest normalizes SDK responses (string + list content)
  - fetch_video_for_ingest falls back on oEmbed title failures
  - Full /api/ingest/from-url pipeline with mocked dependencies
  - 400 for invalid URLs, 503 for SupadataError
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_user
from backend.ingest.youtube_url import parse_youtube_url
from backend.main import app
from backend.services.video_ingest import fetch_video_for_ingest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def bypass_auth():
    """Satisfy the auth gate with a stub user."""
    app.dependency_overrides[get_current_user] = lambda: {"id": "test-user", "email": "t@t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def mock_oembed_title():
    """Stub the oEmbed title lookup so tests don't hit YouTube."""
    with patch(
        "backend.services.video_ingest.get_video_title",
        new=AsyncMock(return_value="Fake oEmbed Title"),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_video_description():
    """Stub the YouTube Data API description lookup so tests don't hit the API."""
    with patch(
        "backend.services.video_ingest.get_video_description",
        new=AsyncMock(return_value=None),
    ):
        yield


# ---------------------------------------------------------------------------
# URL parsing unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected_id",
    [
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://youtube.com/watch?v=abc123", "abc123"),
        ("https://www.youtube.com/watch?v=abc123&list=PLxxxx", "abc123"),
        ("https://youtu.be/abc123", "abc123"),
        ("https://youtu.be/abc123?t=42", "abc123"),
        ("https://www.youtube.com/shorts/abc123", "abc123"),
        ("https://www.youtube.com/shorts/abc123?feature=share", "abc123"),
    ],
)
def test_parse_youtube_url_valid(url, expected_id):
    result = parse_youtube_url(url)
    assert result.video_id == expected_id
    assert result.url == url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/search?q=cats",
        "https://example.com/video",
        "https://youtube.com/",
        "https://youtube.com/watch",
        "https://youtu.be/",
        "",
        "not-a-url",
    ],
)
def test_parse_youtube_url_invalid(url):
    with pytest.raises(ValueError, match="Invalid or unrecognized YouTube URL"):
        parse_youtube_url(url)


# ---------------------------------------------------------------------------
# fetch_video_for_ingest unit tests (mock the Supadata SDK directly)
# ---------------------------------------------------------------------------


def _mock_transcript_list(parts: list[tuple[str, int, int]]):
    """Build a SimpleNamespace that mimics the SDK's Transcript object with list content."""
    chunks = [
        SimpleNamespace(text=text, offset=offset, duration=duration, lang="en")
        for (text, offset, duration) in parts
    ]
    return SimpleNamespace(content=chunks, lang="en", availableLangs=["en"])


def _mock_transcript_string(text: str):
    """Build a Transcript-like object with string content (SDK text mode)."""
    return SimpleNamespace(content=text, lang="en", availableLangs=["en"])


@pytest.mark.asyncio
async def test_fetch_video_for_ingest_list_content():
    """List-mode SDK response → segments with ms→s conversion + joined transcript."""
    mock_result = _mock_transcript_list(
        [
            ("Hello world.", 0, 3000),
            ("This is a test.", 3500, 2500),
        ]
    )
    fake_client = SimpleNamespace(transcript=lambda **kwargs: mock_result)

    with patch("backend.services.video_ingest._get_client", return_value=fake_client):
        data = await fetch_video_for_ingest(
            "https://www.youtube.com/watch?v=abc123",
            lang="en",
        )

    assert data["youtube_video_id"] == "abc123"
    assert data["title"] == "Fake oEmbed Title"
    assert "Hello world." in data["transcript"]
    assert "This is a test." in data["transcript"]
    assert data["segments"][0] == {"start": 0.0, "end": 3.0, "text": "Hello world."}
    assert data["segments"][1]["start"] == 3.5
    assert data["segments"][1]["end"] == 6.0


@pytest.mark.asyncio
async def test_fetch_video_for_ingest_string_content():
    """String-mode SDK response → transcript populated, segments empty."""
    mock_result = _mock_transcript_string("The whole transcript as one string.")
    fake_client = SimpleNamespace(transcript=lambda **kwargs: mock_result)

    with patch("backend.services.video_ingest._get_client", return_value=fake_client):
        data = await fetch_video_for_ingest(
            "https://www.youtube.com/watch?v=abc123",
            lang="en",
        )

    assert data["transcript"] == "The whole transcript as one string."
    assert data["segments"] == []
    assert data["youtube_video_id"] == "abc123"


@pytest.mark.asyncio
async def test_fetch_video_for_ingest_fallback_title_when_oembed_missing():
    """oEmbed returning None → title falls back to 'Video <id>'."""
    mock_result = _mock_transcript_string("Anything")
    fake_client = SimpleNamespace(transcript=lambda **kwargs: mock_result)

    with (
        patch("backend.services.video_ingest._get_client", return_value=fake_client),
        patch(
            "backend.services.video_ingest.get_video_title",
            new=AsyncMock(return_value=None),
        ),
    ):
        data = await fetch_video_for_ingest("https://www.youtube.com/watch?v=abc123")

    assert data["title"] == "Video abc123"


@pytest.mark.asyncio
async def test_fetch_video_for_ingest_uses_real_description():
    """get_video_description returning a real string → description is the real string."""
    mock_result = _mock_transcript_string("Anything")
    fake_client = SimpleNamespace(transcript=lambda **kwargs: mock_result)

    with (
        patch("backend.services.video_ingest._get_client", return_value=fake_client),
        patch(
            "backend.services.video_ingest.get_video_description",
            new=AsyncMock(return_value="This video covers topics like chapter 1, chapter 2, and more."),
        ),
    ):
        data = await fetch_video_for_ingest("https://www.youtube.com/watch?v=abc123")

    assert data["description"] == "This video covers topics like chapter 1, chapter 2, and more."


@pytest.mark.asyncio
async def test_fetch_video_for_ingest_fallback_description_when_api_unavailable():
    """get_video_description returning None → description falls back to placeholder."""
    mock_result = _mock_transcript_string("Anything")
    fake_client = SimpleNamespace(transcript=lambda **kwargs: mock_result)

    with (
        patch("backend.services.video_ingest._get_client", return_value=fake_client),
        patch(
            "backend.services.video_ingest.get_video_description",
            new=AsyncMock(return_value=None),
        ),
    ):
        data = await fetch_video_for_ingest("https://www.youtube.com/watch?v=abc123")

    assert data["description"] == "Ingested from https://www.youtube.com/watch?v=abc123"


# ---------------------------------------------------------------------------
# /api/ingest/from-url integration tests
# ---------------------------------------------------------------------------


async def test_ingest_from_url_happy_path():
    """Full pipeline: URL parsed → helper called → video+chunks created."""
    mock_video = {
        "id": "v-from-url-1",
        "title": "Fake oEmbed Title",
        "description": "Ingested from https://www.youtube.com/watch?v=abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "transcript": "Hello world. This is a test.",
    }

    fake_helper = AsyncMock(
        return_value={
            "youtube_video_id": "abc123",
            "title": "Fake oEmbed Title",
            "description": "Ingested from https://www.youtube.com/watch?v=abc123",
            "transcript": "Hello world. This is a test.",
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "Hello world."},
                {"start": 3.5, "end": 6.0, "text": "This is a test."},
            ],
        }
    )

    chunk_dict = {
        "content": "chunk 1",
        "start_seconds": 0.0,
        "end_seconds": 3.0,
        "snippet": "chunk 1",
    }

    with (
        patch(
            "backend.routes.ingest.fetch_video_for_ingest",
            new=fake_helper,
        ),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ) as mock_create_video,
        patch(
            "backend.routes.ingest.chunk_video_timestamped",
            return_value=([chunk_dict], False),
        ) as mock_chunk,
        patch(
            "backend.routes.ingest.embed_batch",
            return_value=[[0.1, 0.2, 0.3]],
        ) as mock_embed,
        patch(
            "backend.routes.ingest.repository.create_chunk",
            new_callable=AsyncMock,
        ) as mock_create_chunk,
        patch("backend.routes.ingest.retriever.invalidate_cache") as mock_invalidate,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=abc123"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["video_id"] == "v-from-url-1"
    assert data["chunks_created"] == 1
    assert data["status"] == "ok"

    fake_helper.assert_awaited_once()
    mock_create_video.assert_awaited_once()
    mock_chunk.assert_called_once()
    mock_embed.assert_called_once()
    mock_create_chunk.assert_awaited_once()
    mock_invalidate.assert_called_once()


async def test_ingest_from_url_invalid_url_returns_400():
    """Non-YouTube URL → 400 Bad Request."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        response = await ac.post(
            "/api/ingest/from-url",
            json={"url": "https://example.com/not-youtube"},
        )

    assert response.status_code == 400
    assert "Invalid or unrecognized YouTube URL" in response.json()["detail"]


async def test_ingest_from_url_supadata_error_returns_503():
    """fetch_video_for_ingest raising SupadataError → 503."""
    from supadata import SupadataError

    with patch(
        "backend.routes.ingest.fetch_video_for_ingest",
        new=AsyncMock(side_effect=SupadataError(error="rate_limited", message="429", details="")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=abc123"},
            )

    assert response.status_code == 503
    assert "Transcript fetch failed" in response.json()["detail"]


async def test_ingest_from_url_empty_chunks_returns_stored_no_chunks():
    """Helper returns a video but chunker returns 0 chunks → status stored_no_chunks."""
    mock_video = {
        "id": "v-empty-1",
        "title": "Empty Video",
        "description": "No content",
        "url": "https://www.youtube.com/watch?v=empty123",
        "transcript": "",
    }

    fake_helper = AsyncMock(
        return_value={
            "youtube_video_id": "empty123",
            "title": "Empty Video",
            "description": "No content",
            "transcript": "",
            "segments": [],
        }
    )

    with (
        patch(
            "backend.routes.ingest.fetch_video_for_ingest",
            new=fake_helper,
        ),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch(
            "backend.routes.ingest.chunk_video_fallback",
            return_value=([], True),  # empty transcript → 0 chunks
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=empty123"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stored_no_chunks"
    assert data["chunks_created"] == 0
