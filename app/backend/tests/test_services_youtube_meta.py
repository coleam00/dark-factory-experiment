"""
Tests for YouTube metadata helpers in services/youtube_meta.py.

Verifies:
  - get_video_description returns real description when API key is set
  - get_video_description falls back to og:description when no API key is set
  - get_video_description returns None gracefully on API errors
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestGetVideoDescription:
    """Tests for get_video_description()."""

    @pytest.mark.asyncio
    async def test_returns_description_when_api_key_set(self):
        """YouTube API returns description → return it."""
        mock_response = {
            "items": [
                {
                    "snippet": {
                        "description": "This is the real video description with chapters and links."
                    }
                }
            ]
        }

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200

                def json(self):
                    return mock_response

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result == "This is the real video description with chapters and links."

    @pytest.mark.asyncio
    async def test_returns_none_when_api_returns_empty_description(self):
        """YouTube API returns empty description string → return None."""
        mock_response = {"items": [{"snippet": {"description": ""}}]}

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200

                def json(self):
                    return mock_response

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_403_invalid_key(self):
        """YouTube API returns 403 (invalid key) → return None (log warning)."""

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 403

                @property
                def text(self):
                    return "API key not valid"

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "bad-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_429_quota_exceeded(self):
        """YouTube API returns 429 (quota exceeded) → return None (log warning)."""

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 429

                @property
                def text(self):
                    return "Quota exceeded"

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_500_internal_error(self):
        """YouTube API returns 500 (internal server error) → return None (log warning)."""

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 500

                @property
                def text(self):
                    return "Internal server error"

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_timeout(self):
        """Network timeout → return None (log warning)."""

        async def fake_get(*args, **kwargs):
            raise TimeoutError("Connection timed out")

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("abc123xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_to_og_description_when_no_api_key(self):
        """YOUTUBE_API_KEY is empty → fall back to og:description scrape."""
        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            # Simulate og:description scrape returning a description
            instance.get = AsyncMock(
                return_value=Mock(
                    status_code=200,
                    text='<meta property="og:description" content="Test description">',
                ),
            )

            from backend.services.youtube_meta import get_video_description

            await get_video_description("abc123xyz")

        # Key behavior: og:description fallback path was used (get was called)
        instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_items_list_is_empty(self):
        """Video doesn't exist or is private → API returns empty items list → return None."""
        mock_response = {"items": []}

        async def fake_get(*args, **kwargs):
            class FakeResp:
                status_code = 200

                def json(self):
                    return mock_response

            return FakeResp()

        with (
            patch(
                "backend.config.YOUTUBE_API_KEY",
                "test-api-key",
            ),
            patch("httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=fake_get)

            from backend.services.youtube_meta import get_video_description

            result = await get_video_description("privvideo123")

        assert result is None
