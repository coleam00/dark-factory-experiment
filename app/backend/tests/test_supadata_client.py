"""
Tests for the Supadata client and /ingest/from-url endpoint.

Verifies:
  - URL parsing (valid/invalid YouTube URLs)
  - Supadata client retry/normalize behaviour via respx mocking
  - Full /ingest/from-url pipeline with mocked dependencies
  - Proper HTTP status codes for error cases (400, 429 → 503, 500, 502)
"""

from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from backend.auth.dependencies import get_current_user
from backend.ingest.supadata_client import SupadataClient, SupadataError
from backend.ingest.youtube_url import parse_youtube_url
from backend.main import app

# ---------------------------------------------------------------------------
# Auth + cache fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def bypass_auth():
    """Satisfy the auth gate with a stub user."""
    app.dependency_overrides[get_current_user] = lambda: {"id": "test-user", "email": "t@t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


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
# SupadataClient unit tests (via respx mocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_fetch_transcript_happy_path():
    """Supadata returns 200 → response is normalized correctly."""
    happy_response = {
        "video": {
            "title": "Test Video",
            "description": "A test description.",
            "transcript": [
                {"text": "Hello world.", "start_seconds": 0.0},
                {"text": "This is a test.", "start_seconds": 3.5},
            ],
        }
    }

    with respx.mock:
        respx.get("https://api.supadata.io/v1/youtube/transcript").mock(
            return_value=Response(200, json=happy_response),
        )

        client = SupadataClient()
        result = await client.fetch_transcript(
            "https://www.youtube.com/watch?v=abc123",
            lang="en",
        )
        await client.close()

    assert result["title"] == "Test Video"
    assert result["description"] == "A test description."
    assert "Hello world." in result["transcript"]
    assert result["segments"][0]["text"] == "Hello world."
    assert result["segments"][0]["start_seconds"] == 0.0


@pytest.mark.asyncio
async def test_client_fetch_transcript_rate_limit_429():
    """429 from Supadata → SupadataError with 503 after retries exhausted."""
    with respx.mock:
        respx.get("https://api.supadata.io/v1/youtube/transcript").mock(
            return_value=Response(
                429,
                headers={"retry-after": "1"},
                json={"error": "rate limited"},
            ),
        )

        client = SupadataClient()
        with pytest.raises(SupadataError, match="rate-limited"):
            await client.fetch_transcript(
                "https://www.youtube.com/watch?v=abc123",
                lang="en",
            )
        await client.close()


@pytest.mark.asyncio
async def test_client_fetch_transcript_500_without_lang_retries():
    """500 without lang → retries with lang='en' → succeeds."""
    # First call (no lang or wrong lang) returns 500, second call with lang="en" succeeds
    happy_response = {
        "video": {
            "title": "Test Video",
            "description": "Description",
            "transcript": [{"text": "Hello.", "start_seconds": 0.0}],
        }
    }

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        params = request.url.params
        lang_param = params.get("lang", "")
        if lang_param != "en":
            return Response(500, json={"error": "server error"})
        return Response(200, json=happy_response)

    with respx.mock:
        route = respx.get("https://api.supadata.io/v1/youtube/transcript")
        route.side_effect = side_effect

        client = SupadataClient()
        result = await client.fetch_transcript(
            "https://www.youtube.com/watch?v=abc123",
            lang="en",
        )
        await client.close()

    assert result["title"] == "Test Video"


# ---------------------------------------------------------------------------
# /ingest/from-url endpoint integration tests
# ---------------------------------------------------------------------------


async def test_ingest_from_url_happy_path():
    """Full pipeline: URL parsed → Supadata called → video+chunks created."""
    mock_video = {
        "id": "v-from-url-1",
        "title": "Supadata Title",
        "description": "Supadata Desc",
        "url": "https://www.youtube.com/watch?v=abc123",
        "transcript": "Hello world. This is a test.",
    }
    mock_embedding = [[0.1, 0.2, 0.3]]

    supadata_response = {
        "video": {
            "title": "Supadata Title",
            "description": "Supadata Desc",
            "transcript": [
                {"text": "Hello world.", "start_seconds": 0.0},
                {"text": "This is a test.", "start_seconds": 3.5},
            ],
        }
    }

    with (
        respx.mock,
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ) as mock_create_video,
        patch(
            "backend.routes.ingest.chunk_video",
            return_value=["chunk 1"],
        ) as mock_chunk,
        patch(
            "backend.routes.ingest.embed_batch",
            return_value=mock_embedding,
        ) as mock_embed,
        patch(
            "backend.routes.ingest.repository.create_chunk",
            new_callable=AsyncMock,
        ) as mock_create_chunk,
        patch("backend.routes.ingest.retriever.invalidate_cache") as mock_invalidate,
    ):
        respx.get("https://api.supadata.io/v1/youtube/transcript").mock(
            return_value=Response(200, json=supadata_response),
        )

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

        mock_create_video.assert_awaited_once()
        mock_chunk.assert_called_once()
        mock_embed.assert_called_once()
        mock_create_chunk.assert_awaited_once()
        mock_invalidate.assert_called_once()


async def test_ingest_from_url_invalid_url_returns_400():
    """Non-YouTube URL → 400 Bad Request."""
    with (
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
        ),
        patch("backend.routes.ingest.chunk_video", return_value=[]),
    ):
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


async def test_ingest_from_url_empty_chunks_returns_stored_no_chunks():
    """Supadata returns a video but chunker returns 0 chunks → status stored_no_chunks."""
    mock_video = {
        "id": "v-empty-1",
        "title": "Empty Video",
        "description": "No content",
        "url": "https://www.youtube.com/watch?v=empty",
        "transcript": "",
    }

    supadata_response = {
        "video": {
            "title": "Empty Video",
            "description": "No content",
            "transcript": [],
        }
    }

    with (
        respx.mock,
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch(
            "backend.routes.ingest.chunk_video",
            return_value=[],  # empty transcript → 0 chunks
        ),
    ):
        respx.get("https://api.supadata.io/v1/youtube/transcript").mock(
            return_value=Response(200, json=supadata_response),
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=empty"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stored_no_chunks"
        assert data["chunks_created"] == 0
