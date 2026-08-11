"""
Regression tests for issue #228 — POST /api/ingest/from-url left an orphan
video row behind when the embeddings call failed.

The sibling handler (POST /api/ingest) already deleted the row it had just
created; the from-url handler did not, so a transient embeddings outage left a
video in the library with zero chunks: it showed up in the catalog and the
video list, and answered nothing.

The delete must target the id returned by create_video. It must never look the
row up by URL — chunks cascade on delete, and get_video_by_youtube_id matches
with `url LIKE '%id%'`, so a substring collision would take out a different
video and every chunk belonging to it.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_admin, get_current_user
from backend.main import app

VIDEO_ID = "the-row-we-just-created"
OTHER_VIDEO_ID = "a-different-video-that-must-survive"


@pytest.fixture(autouse=True)
def bypass_auth():
    stub_user = {"id": "test-user", "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.dependency_overrides[get_current_admin] = lambda: stub_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_admin, None)


def _fetch_result():
    """What fetch_video_for_ingest returns for a healthy YouTube URL."""
    return {
        "title": "A Video",
        "description": "desc",
        "transcript": "some transcript text that is long enough to chunk",
        "segments": [{"start": 0.0, "end": 12.0, "text": "some transcript text"}],
    }


@pytest.mark.asyncio
async def test_from_url_deletes_orphan_video_when_embedding_fails():
    """A 502 from the embeddings API must not leave the video row behind."""
    with (
        patch(
            "backend.routes.ingest.fetch_video_for_ingest",
            new_callable=AsyncMock,
            return_value=_fetch_result(),
        ),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value={"id": VIDEO_ID},
        ),
        patch(
            "backend.routes.ingest.repository.delete_video",
            new_callable=AsyncMock,
        ) as mock_delete,
        patch(
            "backend.routes.ingest.embed_batch",
            side_effect=RuntimeError("embeddings provider is down"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=abc12345678"},
            )

    assert response.status_code == 502
    mock_delete.assert_awaited_once()

    # The id passed to delete_video must be exactly the one create_video
    # returned — positionally or by keyword, but that id and no other.
    args, kwargs = mock_delete.await_args
    passed = kwargs.get("video_id", args[0] if args else None)
    assert passed == VIDEO_ID
    assert passed != OTHER_VIDEO_ID


@pytest.mark.asyncio
async def test_from_url_does_not_delete_anything_on_success():
    """The cleanup must be confined to the failure path."""
    with (
        patch(
            "backend.routes.ingest.fetch_video_for_ingest",
            new_callable=AsyncMock,
            return_value=_fetch_result(),
        ),
        patch(
            "backend.routes.ingest.repository.create_video",
            new_callable=AsyncMock,
            return_value={"id": VIDEO_ID},
        ),
        patch(
            "backend.routes.ingest.repository.delete_video",
            new_callable=AsyncMock,
        ) as mock_delete,
        patch(
            "backend.routes.ingest.repository.create_chunk",
            new_callable=AsyncMock,
        ),
        patch("backend.routes.ingest.embed_batch", return_value=[[0.1] * 1536]),
        patch("backend.rag.retriever_hybrid.invalidate_cache"),
        patch("backend.rag.catalog.invalidate_catalog"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/ingest/from-url",
                json={"url": "https://www.youtube.com/watch?v=abc12345678"},
            )

    assert response.status_code == 200
    mock_delete.assert_not_awaited()
