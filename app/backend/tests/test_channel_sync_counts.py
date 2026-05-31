"""
Regression tests for issue #295 — channel sync miscounts skipped videos as new
and hides failed runs.

Two bugs, one root cause: the skip branch in `sync_channel` used to increment
`videos_new` for videos that already existed and were *not* re-ingested. That
(1) inflated the "new videos" figure on routine no-op syncs and (2) masked real
ingestion outages, because the end-of-run status check
(`"failed" if videos_error > 0 and videos_new == 0`) never saw `videos_new == 0`
when some videos had been skipped.

These tests mock the `repo` layer (and Supadata / the ingest helper) so they run
without Postgres and without the skipped `temp_db_schema` SQLite fixture in
`test_channel_sync.py`. The persisted `videos_new` / `videos_error` / `status`
are read off the final `repo.update_sync_run` call as well as the JSON response.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

# Set env BEFORE any backend imports so config.py picks them up.
os.environ["JWT_SECRET"] = "test-secret-please-do-not-use-in-prod"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["SUPADATA_API_KEY"] = "test-supadata-key"
os.environ["YOUTUBE_CHANNEL_ID"] = "UC_testchannel"
os.environ["CHANNEL_SYNC_TYPE"] = "video"

import pytest

from backend.auth.dependencies import get_current_admin, get_current_user
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Channel sync is admin-gated; override both user and admin dependencies."""
    stub_user = {"id": "test-user", "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub_user
    app.dependency_overrides[get_current_admin] = lambda: stub_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_admin, None)


def _make_channel_video_ids(video_ids):
    """Build the dict shape returned by supadata.get_channel_video_ids."""
    return {"video_ids": list(video_ids), "short_ids": [], "live_ids": []}


def _sync_video_factory():
    """Return an AsyncMock that hands back a unique sync_video record per call."""
    counter = {"n": 0}

    async def create_sync_video(**kwargs):
        counter["n"] += 1
        return {"id": f"sv-{counter['n']}"}

    return AsyncMock(side_effect=create_sync_video)


async def test_sync_skipped_videos_not_counted_as_new():
    """All videos already in the DB → skipped → videos_new must be 0, status completed.

    Pre-fix this reported videos_new == 2 (one increment per skipped video).
    """
    existing_ids = {"vid-aaa": {"id": "row-aaa"}, "vid-bbb": {"id": "row-bbb"}}

    update_sync_run = AsyncMock()

    async def get_video_by_youtube_id(youtube_video_id):
        return existing_ids.get(youtube_video_id)

    with (
        patch(
            "backend.routes.channels.supadata.get_channel_video_ids",
            new=AsyncMock(return_value=_make_channel_video_ids(["vid-aaa", "vid-bbb"])),
        ),
        patch("backend.routes.channels.repo.create_sync_run", new=AsyncMock()),
        patch("backend.routes.channels.repo.create_sync_video", new=_sync_video_factory()),
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            new=AsyncMock(side_effect=get_video_by_youtube_id),
        ),
        patch("backend.routes.channels.repo.update_sync_video_status", new=AsyncMock()),
        patch("backend.routes.channels.repo.update_sync_run", new=update_sync_run),
        patch("backend.routes.channels.fetch_video_for_ingest", new=AsyncMock()) as mock_fetch,
        patch("backend.routes.channels.retriever_hybrid.invalidate_cache"),
        patch("backend.routes.channels.catalog.invalidate_catalog"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 2
    assert data["videos_new"] == 0  # both skipped — not new
    assert data["videos_error"] == 0
    assert data["status"] == "completed"

    # Skipped videos must never reach ingestion.
    mock_fetch.assert_not_called()

    # The persisted run reflects the same accurate counts.
    persisted = update_sync_run.call_args.kwargs
    assert persisted["videos_new"] == 0
    assert persisted["videos_error"] == 0
    assert persisted["status"] == "completed"


async def test_sync_failed_new_videos_surface_as_failed_despite_skips():
    """One skipped + one genuinely-new video that fails to ingest → run is failed.

    This is the core regression: pre-fix the skipped video bumped videos_new to 1,
    so the status check saw videos_new > 0 and reported "completed", hiding the
    fact that every genuinely-new video failed.
    """
    existing_ids = {"vid-old": {"id": "row-old"}}

    update_sync_run = AsyncMock()

    async def get_video_by_youtube_id(youtube_video_id):
        return existing_ids.get(youtube_video_id)

    async def failing_fetch(*args, **kwargs):
        raise RuntimeError("Transcript provider outage")

    with (
        patch(
            "backend.routes.channels.supadata.get_channel_video_ids",
            new=AsyncMock(return_value=_make_channel_video_ids(["vid-old", "vid-new"])),
        ),
        patch("backend.routes.channels.repo.create_sync_run", new=AsyncMock()),
        patch("backend.routes.channels.repo.create_sync_video", new=_sync_video_factory()),
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            new=AsyncMock(side_effect=get_video_by_youtube_id),
        ),
        patch("backend.routes.channels.repo.update_sync_video_status", new=AsyncMock()),
        patch("backend.routes.channels.repo.update_sync_run", new=update_sync_run),
        patch(
            "backend.routes.channels.fetch_video_for_ingest",
            new=AsyncMock(side_effect=failing_fetch),
        ),
        patch("backend.routes.channels.retriever_hybrid.invalidate_cache"),
        patch("backend.routes.channels.catalog.invalidate_catalog"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 2
    assert data["videos_new"] == 0  # the skip no longer counts as new
    assert data["videos_error"] == 1  # the genuinely-new video failed
    assert data["status"] == "failed"  # outage is surfaced, not hidden

    persisted = update_sync_run.call_args.kwargs
    assert persisted["videos_new"] == 0
    assert persisted["videos_error"] == 1
    assert persisted["status"] == "failed"
