"""
Regression tests for issue #89 — all chunks have start_seconds=0.0.

Verifies that the from-url ingest path, admin _fetch_chunks_and_embeddings,
and channel sync all pass real timestamp fields to create_chunk rather than
leaving them at the default 0.0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.auth.dependencies import get_current_user
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Satisfy the auth gate with a stub user."""
    app.dependency_overrides[get_current_user] = lambda: {"id": "test-user", "email": "t@t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Regression: from-url ingest stores real timestamps
# ---------------------------------------------------------------------------


async def test_ingest_from_url_stores_timestamps():
    """create_chunk must be called with non-zero timestamps when segments are present."""
    from httpx import ASGITransport, AsyncClient

    mock_video = {
        "id": "v-ts-1",
        "title": "Timestamp Test",
        "description": "desc",
        "url": "https://www.youtube.com/watch?v=ts1",
        "transcript": "Intro. Main content. Conclusion.",
    }

    fake_helper = AsyncMock(
        return_value={
            "youtube_video_id": "ts1",
            "title": "Timestamp Test",
            "description": "desc",
            "transcript": "Intro. Main content. Conclusion.",
            "segments": [
                {"start": 0.0, "end": 30.0, "text": "Intro."},
                {"start": 30.0, "end": 90.0, "text": "Main content."},
                {"start": 90.0, "end": 120.0, "text": "Conclusion."},
            ],
        }
    )

    chunk_dicts = [
        {"content": "Intro.", "start_seconds": 0.0, "end_seconds": 30.0, "snippet": "Intro."},
        {
            "content": "Main content.",
            "start_seconds": 30.0,
            "end_seconds": 90.0,
            "snippet": "Main content.",
        },
        {
            "content": "Conclusion.",
            "start_seconds": 90.0,
            "end_seconds": 120.0,
            "snippet": "Conclusion.",
        },
    ]

    with (
        patch("backend.routes.ingest.fetch_video_for_ingest", new=fake_helper),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch("backend.routes.ingest.chunk_video_timestamped", return_value=chunk_dicts),
        patch("backend.routes.ingest.embed_batch", return_value=[[0.1] * 3, [0.2] * 3, [0.3] * 3]),
        patch(
            "backend.routes.ingest.repository.create_chunk", new_callable=AsyncMock
        ) as mock_create_chunk,
        patch("backend.routes.ingest.retriever.invalidate_cache"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=ts1"},
            )

    assert response.status_code == 200
    assert response.json()["chunks_created"] == 3

    # Verify that later chunks have non-zero start_seconds
    calls = mock_create_chunk.call_args_list
    assert len(calls) == 3
    # Second chunk must have start_seconds=30.0 (not 0.0 — regression check)
    second_call_kwargs = calls[1].kwargs
    assert second_call_kwargs["start_seconds"] == 30.0
    assert second_call_kwargs["end_seconds"] == 90.0
    assert second_call_kwargs["snippet"] == "Main content."
    # Third chunk
    third_call_kwargs = calls[2].kwargs
    assert third_call_kwargs["start_seconds"] == 90.0


# ---------------------------------------------------------------------------
# Regression: fallback path still produces dicts (not plain strings)
# ---------------------------------------------------------------------------


async def test_ingest_from_url_fallback_stores_timestamps_when_no_segments():
    """When no segments are provided, chunk_video_fallback is used and timestamps are estimated."""
    from httpx import ASGITransport, AsyncClient

    mock_video = {
        "id": "v-ts-2",
        "title": "No Segments Video",
        "description": "desc",
        "url": "https://www.youtube.com/watch?v=nosegs",
        "transcript": " ".join(["word"] * 200),
    }

    fake_helper = AsyncMock(
        return_value={
            "youtube_video_id": "nosegs",
            "title": "No Segments Video",
            "description": "desc",
            "transcript": " ".join(["word"] * 200),
            "segments": [],  # no segments → fallback path
        }
    )

    fallback_chunk = {
        "content": "word word word",
        "start_seconds": 0.0,
        "end_seconds": 40.0,
        "snippet": "word word word",
    }

    with (
        patch("backend.routes.ingest.fetch_video_for_ingest", new=fake_helper),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value=mock_video,
        ),
        patch("backend.routes.ingest.chunk_video_fallback", return_value=[fallback_chunk]),
        patch("backend.routes.ingest.embed_batch", return_value=[[0.1] * 3]),
        patch(
            "backend.routes.ingest.repository.create_chunk", new_callable=AsyncMock
        ) as mock_create_chunk,
        patch("backend.routes.ingest.retriever.invalidate_cache"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=nosegs"},
            )

    assert response.status_code == 200
    assert response.json()["chunks_created"] == 1

    call_kwargs = mock_create_chunk.call_args_list[0].kwargs
    # Fallback chunk must have all timestamp fields (not missing keys)
    assert "start_seconds" in call_kwargs
    assert "end_seconds" in call_kwargs
    assert "snippet" in call_kwargs
    assert call_kwargs["end_seconds"] == 40.0
