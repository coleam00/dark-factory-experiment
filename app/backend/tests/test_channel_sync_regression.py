"""
Regression tests for channel sync counting + status (issue #295).

Two bugs are covered here:

1. Videos that already exist in the DB are *skipped* (idempotent behaviour)
   and must NOT be counted toward ``videos_new`` — only genuinely-ingested
   videos count.
2. Because the run-status check distinguishes a total failure from a partial
   success via ``videos_new == 0``, the first bug used to *mask* real
   ingestion outages: a run where every genuinely-new video failed still
   reported ``completed`` because the skipped videos inflated ``videos_new``.

This module is deliberately separate from ``test_channel_sync.py`` (which is
globally skipped pending an Alembic schema-fixture rewrite). Here we mock the
repository + Supadata boundaries directly, so no DB schema is required.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Set env BEFORE any backend imports so config.py picks them up.
os.environ["JWT_SECRET"] = "test-secret-please-do-not-use-in-prod"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["SUPADATA_API_KEY"] = "test-supadata-key"
os.environ["YOUTUBE_CHANNEL_ID"] = "UC_testchannel"
os.environ["CHANNEL_SYNC_TYPE"] = "video"

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


async def test_sync_channel_skipped_plus_failed_new_surfaces_failed():
    """
    A run mixing already-ingested (skipped) videos with genuinely-new videos
    that all fail to ingest must:
      - count ``videos_new == 0`` (skips don't inflate the count), and
      - surface ``status == "failed"`` (the failure isn't masked by skips).
    """

    # One video is already in the DB (skip), one is genuinely new (will fail).
    async def fake_get_video_by_youtube_id(youtube_video_id, *args, **kwargs):
        return {"id": "vid-existing"} if youtube_video_id == "existing_vid" else None

    # Each create_sync_video call returns a record carrying the id the route
    # needs for subsequent status updates.
    sync_video_counter = {"n": 0}

    async def fake_create_sync_video(*args, **kwargs):
        sync_video_counter["n"] += 1
        return {"id": f"sync-vid-{sync_video_counter['n']}"}

    # The genuinely-new video fails to fetch → ingestion error.
    async def failing_fetch(*args, **kwargs):
        raise RuntimeError("Transcript service unavailable")

    with (
        patch(
            "backend.routes.channels.supadata.get_channel_video_ids",
            new=AsyncMock(
                return_value={
                    "video_ids": ["existing_vid", "new_vid"],
                    "short_ids": [],
                    "live_ids": [],
                }
            ),
        ),
        patch("backend.routes.channels.repo.create_sync_run", new=AsyncMock()),
        patch("backend.routes.channels.repo.update_sync_run", new=AsyncMock()),
        patch(
            "backend.routes.channels.repo.create_sync_video",
            new=AsyncMock(side_effect=fake_create_sync_video),
        ),
        patch(
            "backend.routes.channels.repo.update_sync_video_status",
            new=AsyncMock(),
        ),
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            new=AsyncMock(side_effect=fake_get_video_by_youtube_id),
        ),
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
    # Skipped (already-ingested) video must NOT count as new.
    assert data["videos_new"] == 0
    # The genuinely-new video failed.
    assert data["videos_error"] == 1
    # The failure must be surfaced, not masked by the skipped video.
    assert data["status"] == "failed"
