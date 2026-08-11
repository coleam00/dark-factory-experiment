"""
Regression tests for issue #295 — channel sync counted skipped videos as new.

`videos_new` was incremented in the "already ingested, skipping" branch, so on
a routine nightly sync it equalled the whole channel. The run status is

    status = "failed" if videos_error > 0 and videos_new == 0 else "completed"

so an inflated `videos_new` made the `failed` arm unreachable in practice: a
sync where every new video errored still reported "completed".

These tests deliberately exercise the skip branch, because that branch is also
the only application-level guard against re-ingesting the corpus. `videos.url`
has no UNIQUE constraint, so if the skip ever stops firing the nightly sync
duplicates every video and every chunk, and reverting the code does not undo
it.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.auth.dependencies import get_current_admin, get_current_user
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """/api/channels/sync is admin-gated; both dependencies must be overridden."""
    stub_user = {"id": "test-user", "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.dependency_overrides[get_current_admin] = lambda: stub_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_admin, None)


EXISTING = {
    "id": "existing-video-uuid",
    "title": "Already in DB",
    "description": "desc",
    "url": "https://www.youtube.com/watch?v=existing1",
    "transcript": "old",
}


class _MockChannelVideosResult:
    def __init__(self, ids: list[str]):
        self.video_ids = ids
        self.short_ids: list[str] = []
        self.live_ids: list[str] = []


def _channel_patches(video_ids: list[str]):
    """The mock stack shared by both tests."""
    return (
        patch(
            "backend.routes.channels.repo.create_sync_run",
            new_callable=AsyncMock,
            return_value={"id": "run-1"},
        ),
        patch(
            "backend.routes.channels.repo.create_sync_video",
            new_callable=AsyncMock,
            return_value={"id": "sv-1"},
        ),
        patch("backend.routes.channels.repo.update_sync_video_status", new_callable=AsyncMock),
        patch("backend.rag.retriever_hybrid.invalidate_cache"),
        patch("backend.rag.catalog.invalidate_catalog"),
    )


@pytest.mark.asyncio
async def test_skipped_videos_are_not_counted_as_new():
    """A sync where everything already exists reports 0 new, N skipped."""
    from httpx import ASGITransport, AsyncClient

    p1, p2, p3, p4, p5 = _channel_patches(["existing1", "existing2"])
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            new_callable=AsyncMock,
            return_value=EXISTING,
        ),
        patch("backend.routes.channels.repo.update_sync_run", new_callable=AsyncMock) as mock_upd,
        patch("backend.routes.channels.fetch_video_for_ingest", new_callable=AsyncMock) as mock_f,
        patch("backend.services.supadata._get_client") as mock_get_client,
    ):
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = lambda *a, **k: _MockChannelVideosResult(
            ["existing1", "existing2"]
        )
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/channels/sync")

    assert response.status_code == 200
    body = response.json()

    assert body["videos_new"] == 0, "skips must not inflate videos_new"
    assert body["videos_skipped"] == 2
    assert body["videos_error"] == 0
    assert body["status"] == "completed"

    # The idempotency guard must still hold: nothing was re-fetched.
    mock_f.assert_not_called()

    # And the persisted run agrees with the response.
    assert mock_upd.await_args.kwargs["videos_new"] == 0


@pytest.mark.asyncio
async def test_run_with_only_errors_is_reported_failed():
    """The bug this unmasks: an all-error sync used to report 'completed'.

    One video already exists (skipped), one is new and fails to fetch. Under
    the old counting, videos_new was 1 from the skip, so `videos_new == 0` was
    False and the run reported success despite ingesting nothing.
    """
    from httpx import ASGITransport, AsyncClient

    async def _lookup(youtube_video_id: str):
        return EXISTING if youtube_video_id == "existing1" else None

    p1, p2, p3, p4, p5 = _channel_patches(["existing1", "brandnew1"])
    with (
        p1,
        p2,
        p3,
        p4,
        p5,
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            new_callable=AsyncMock,
            side_effect=_lookup,
        ),
        patch("backend.routes.channels.repo.update_sync_run", new_callable=AsyncMock) as mock_upd,
        patch(
            "backend.routes.channels.fetch_video_for_ingest",
            new_callable=AsyncMock,
            side_effect=RuntimeError("supadata is down"),
        ),
        patch("backend.services.supadata._get_client") as mock_get_client,
    ):
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = lambda *a, **k: _MockChannelVideosResult(
            ["existing1", "brandnew1"]
        )
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/channels/sync")

    assert response.status_code == 200
    body = response.json()

    assert body["videos_new"] == 0
    assert body["videos_skipped"] == 1
    assert body["videos_error"] == 1
    assert body["status"] == "failed", "a sync that ingested nothing and errored is not a success"
    assert mock_upd.await_args.kwargs["status"] == "failed"
