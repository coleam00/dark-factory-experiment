"""
Regression tests for channel sync bookkeeping (issue #295).

These tests mock all repository and service dependencies so they can run
without a real database or external services.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _existing_video():
    return {"id": "vid-existing", "youtube_video_id": "existing1"}


async def test_skipped_videos_do_not_count_as_new():
    """A video that already exists and is skipped must not increment videos_new."""
    existing = _existing_video()

    def fake_get_video_by_youtube_id(youtube_id):
        return existing if youtube_id == "existing1" else None

    captured = {}

    async def fake_update_sync_run(
        *, sync_run_id, status, finished_at, videos_total, videos_new, videos_error
    ):
        captured.update(
            {
                "status": status,
                "videos_total": videos_total,
                "videos_new": videos_new,
                "videos_error": videos_error,
            }
        )
        return True

    with (
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            side_effect=fake_get_video_by_youtube_id,
        ),
        patch(
            "backend.routes.channels.repo.create_sync_run",
            new_callable=AsyncMock,
            return_value={"id": "run-1"},
        ),
        patch(
            "backend.routes.channels.repo.update_sync_run",
            side_effect=fake_update_sync_run,
        ),
        patch(
            "backend.routes.channels.repo.create_sync_video",
            new_callable=AsyncMock,
            return_value={"id": "sv-1"},
        ),
        patch(
            "backend.routes.channels.repo.update_sync_video_status",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.routes.channels.supadata.get_channel_video_ids",
            new_callable=AsyncMock,
            return_value={
                "video_ids": ["existing1", "new1"],
                "short_ids": [],
                "live_ids": [],
            },
        ),
        patch(
            "backend.routes.channels.fetch_video_for_ingest",
            new_callable=AsyncMock,
            return_value={
                "title": "New Video",
                "description": "desc",
                "transcript": "Some transcript.",
                "segments": [],
            },
        ),
        patch(
            "backend.routes.channels.chunk_video_timestamped",
            return_value=([], False),
        ),
        patch(
            "backend.routes.channels.chunk_video_fallback",
            return_value=(
                [
                    {
                        "content": "chunk1",
                        "start_seconds": 0,
                        "end_seconds": 1,
                        "snippet": "chunk1",
                    }
                ],
                False,
            ),
        ),
        patch(
            "backend.routes.channels.embed_batch",
            return_value=[[0.1] * 512],
        ),
        patch(
            "backend.routes.channels.repo.create_video",
            new_callable=AsyncMock,
            return_value={"id": "vid-new"},
        ),
        patch(
            "backend.routes.channels.repo.create_chunk",
            new_callable=AsyncMock,
        ),
        patch("backend.routes.channels.retriever_hybrid.invalidate_cache"),
        patch("backend.routes.channels.catalog.invalidate_catalog"),
        patch(
            "backend.routes.channels.get_video_title",
            new_callable=AsyncMock,
            return_value=(None, "Test Channel"),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 2
    assert data["videos_new"] == 1
    assert data["videos_error"] == 0
    assert data["status"] == "completed"
    assert captured["videos_new"] == 1


async def test_all_new_videos_fail_status_is_failed():
    """When all genuinely-new videos fail to ingest, the run status must be 'failed'."""

    def fake_get_video_by_youtube_id(_youtube_id):
        return None  # no existing videos

    captured = {}

    async def fake_update_sync_run(
        *, sync_run_id, status, finished_at, videos_total, videos_new, videos_error
    ):
        captured.update(
            {
                "status": status,
                "videos_total": videos_total,
                "videos_new": videos_new,
                "videos_error": videos_error,
            }
        )
        return True

    with (
        patch(
            "backend.routes.channels.repo.get_video_by_youtube_id",
            side_effect=fake_get_video_by_youtube_id,
        ),
        patch(
            "backend.routes.channels.repo.create_sync_run",
            new_callable=AsyncMock,
            return_value={"id": "run-1"},
        ),
        patch(
            "backend.routes.channels.repo.update_sync_run",
            side_effect=fake_update_sync_run,
        ),
        patch(
            "backend.routes.channels.repo.create_sync_video",
            new_callable=AsyncMock,
            return_value={"id": "sv-1"},
        ),
        patch(
            "backend.routes.channels.repo.update_sync_video_status",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.routes.channels.supadata.get_channel_video_ids",
            new_callable=AsyncMock,
            return_value={
                "video_ids": ["new1"],
                "short_ids": [],
                "live_ids": [],
            },
        ),
        patch(
            "backend.routes.channels.fetch_video_for_ingest",
            new_callable=AsyncMock,
            side_effect=Exception("transcript unavailable"),
        ),
        patch("backend.routes.channels.retriever_hybrid.invalidate_cache"),
        patch("backend.routes.channels.catalog.invalidate_catalog"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 1
    assert data["videos_new"] == 0
    assert data["videos_error"] == 1
    assert data["status"] == "failed"
    assert captured["status"] == "failed"
