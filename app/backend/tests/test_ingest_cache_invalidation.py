"""
Tests for the cache-invalidation side effect of POST /api/ingest.

Verifies:
  - invalidate_cache() is called exactly once after a successful ingest
  - invalidate_cache() is NOT called on the empty-chunk early-return path
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_admin, get_current_user
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Ingest tests focus on cache behavior; satisfy the auth gate with a stub user.

    /api/ingest is admin-gated, so both get_current_user and get_current_admin
    must be overridden — otherwise the admin check returns 403.
    """
    stub_user = {"id": "test-user", "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.dependency_overrides[get_current_admin] = lambda: stub_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_admin, None)


async def test_ingest_calls_invalidate_cache_after_chunks_stored():
    """invalidate_cache() must be called once after all chunks are stored."""
    mock_video = {
        "id": "v1",
        "title": "T",
        "description": "D",
        "url": "http://x.com",
        "transcript": "hi",
    }
    mock_embedding = [[0.1, 0.2, 0.3]]

    with (
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch(
            "backend.routes.ingest.chunk_video_fallback",
            return_value=(
                [
                    {
                        "content": "chunk 1",
                        "start_seconds": 0.0,
                        "end_seconds": 10.0,
                        "snippet": "chunk 1",
                    }
                ],
                False,
            ),
        ),
        patch("backend.routes.ingest.embed_batch", return_value=mock_embedding),
        patch("backend.routes.ingest.repository.create_chunk", new_callable=AsyncMock),
        patch("backend.routes.ingest.retriever_hybrid.invalidate_cache") as mock_invalidate,
        patch("backend.routes.ingest.catalog.invalidate_catalog") as mock_cat_invalidate,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/ingest",
                json={
                    "title": "T",
                    "description": "D",
                    "url": "http://example.com/watch?v=abc",
                    "transcript": "hi",
                },
            )

        assert response.status_code == 200
        mock_invalidate.assert_called_once()
        mock_cat_invalidate.assert_called_once()


async def test_ingest_does_not_call_invalidate_cache_on_empty_chunks():
    """invalidate_cache() must NOT be called when the chunker returns no chunks."""
    mock_video = {
        "id": "v1",
        "title": "T",
        "description": "D",
        "url": "http://x.com",
        "transcript": "hi",
    }

    with (
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch("backend.routes.ingest.chunk_video_fallback", return_value=([], True)),
        patch("backend.routes.ingest.retriever_hybrid.invalidate_cache") as mock_invalidate,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/ingest",
                json={
                    "title": "T",
                    "description": "D",
                    "url": "http://example.com/watch?v=abc",
                    "transcript": "hi",
                },
            )

        assert response.status_code == 200
        assert response.json()["status"] == "stored_no_chunks"
        mock_invalidate.assert_not_called()
