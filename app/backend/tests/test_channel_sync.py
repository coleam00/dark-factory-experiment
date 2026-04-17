"""
Tests for channel sync functionality.

Mirrors test patterns from test_ingest_cache_invalidation.py — uses
httpx.AsyncClient via ASGITransport against the real FastAPI app,
temp SQLite DB per test, auth bypassed via dependency_overrides.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from supadata import SupadataError

# Set env BEFORE any backend imports so config.py picks them up
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SUPADATA_API_KEY", "test-supadata-key")
os.environ.setdefault("YOUTUBE_CHANNEL_ID", "UC_testchannel")
os.environ.setdefault("CHANNEL_SYNC_TYPE", "video")

from backend.auth.dependencies import get_current_user
from backend.db import repository
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Channel sync requires auth; satisfy the gate with a stub user."""
    app.dependency_overrides[get_current_user] = lambda: {"id": "test-user", "email": "t@t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def temp_db_schema(tmp_path, monkeypatch):
    """Point DB_PATH at a temp file AND initialise the schema before each test."""
    db_path = tmp_path / "test_chat.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    import asyncio

    async def _init():
        from backend.db.schema import init_db

        await init_db()

    asyncio.run(_init())
    return db_path


@pytest.fixture(autouse=True)
def mock_get_video_by_youtube_id():
    """Stub get_video_by_youtube_id so pre-existing videos aren't required."""
    from backend.db import repository

    async def stub(*args, **kwargs):
        return None

    original = repository.get_video_by_youtube_id
    repository.get_video_by_youtube_id = stub
    yield
    repository.get_video_by_youtube_id = original


# ---------------------------------------------------------------------------
# Mock Supadata responses
# ---------------------------------------------------------------------------


class MockChannelVideosResult:
    """Plain sync object returned by client.youtube.channel.videos()."""

    def __init__(self, video_ids=None, short_ids=None, live_ids=None):
        self.video_ids = video_ids or []
        self.short_ids = short_ids or []
        self.live_ids = live_ids or []


class MockTranscriptResult:
    """Plain sync object returned by client.youtube.transcript()."""

    def __init__(self, text="This is a sample transcript for the video."):
        self.text = text


class NoTranscriptResult:
    """Plain sync object returned when no transcript is available."""

    def __init__(self):
        self.text = None


async def make_mock_channel_videos(video_ids, short_ids=None, live_ids=None):
    """Return an async function that resolves to MockChannelVideosResult."""

    async def fn(*args, **kwargs):
        return MockChannelVideosResult(
            video_ids=video_ids,
            short_ids=short_ids or [],
            live_ids=live_ids or [],
        )

    return fn


async def make_mock_transcript(text):
    """Return an async function that resolves to MockTranscriptResult."""

    async def fn(*args, **kwargs):
        return MockTranscriptResult(text=text)

    return fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_sync_channel_idempotent_skips_existing_videos():
    """
    If a video is already in the DB (matched by youtube_video_id in URL),
    it is skipped and counted as 'new' (already ingested = already counted).
    """
    # Pre-ingest a video so get_video_by_youtube_id returns it
    await repository.create_video(
        title="Already ingested",
        description="Already in DB",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        transcript="Already ingested transcript.",
    )

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = await make_mock_channel_videos(
            ["dQw4w9WgXcQ", "abc123def456"]
        )
        mock_client.youtube.transcript = await make_mock_transcript(
            "This is a sample transcript for the video."
        )
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["sync_run_id"]
    assert data["videos_total"] == 2
    assert data["videos_new"] == 2  # one skipped (already in DB), one "new" (abc...)
    assert data["videos_error"] == 0


async def test_sync_channel_returns_sync_run_id():
    """POST /api/channels/sync returns a sync_run_id immediately."""
    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = await make_mock_channel_videos(["dQw4w9WgXcQ"])
        mock_client.youtube.transcript = await make_mock_transcript(
            "This is a sample transcript for the video."
        )
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert "sync_run_id" in data
    assert data["status"] in ("running", "completed")


async def test_sync_channel_empty_channel():
    """Sync with 0 videos from channel results in status=completed."""
    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = await make_mock_channel_videos([])
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 0
    assert data["status"] == "completed"


async def test_sync_channel_no_transcript_creates_error_row():
    """Video with unavailable transcript increments videos_error count."""

    async def no_transcript_fn(*args, **kwargs):
        return NoTranscriptResult()

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = await make_mock_channel_videos(["noTranscriptVideo"])
        mock_client.youtube.transcript = no_transcript_fn
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_error"] == 1
    assert data["videos_new"] == 0


async def test_sync_channel_429_triggers_backoff():
    """Supadata 429 causes exponential backoff and retry."""
    call_count = 0

    async def mock_channel_videos_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SupadataError(
                error="rate limited",
                message="Rate limit exceeded",
                details="Try again later",
                status=429,
            )
        return MockChannelVideosResult(video_ids=["dQw4w9WgXcQ"])

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = mock_channel_videos_with_retry
        mock_client.youtube.transcript = await make_mock_transcript(
            "This is a sample transcript for the video."
        )
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    # Retry should have happened (call_count = 2)
    assert call_count == 2
    assert response.status_code == 200


async def test_list_sync_runs_empty():
    """GET /api/channels/sync-runs on empty table returns [{}]."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/channels/sync-runs")

    assert response.status_code == 200
    data = response.json()
    assert data["sync_runs"] == []


async def test_list_sync_runs_returns_recent_runs():
    """GET /api/channels/sync-runs returns recent sync run history."""
    now_str = datetime.now(UTC).isoformat()
    await repository.create_sync_run(sync_run_id="run-1", started_at=now_str)
    await repository.update_sync_run(
        sync_run_id="run-1",
        status="completed",
        finished_at=now_str,
        videos_total=5,
        videos_new=3,
        videos_error=2,
    )
    await repository.create_sync_run(sync_run_id="run-2", started_at=now_str)
    await repository.update_sync_run(
        sync_run_id="run-2",
        status="failed",
        finished_at=now_str,
        videos_total=10,
        videos_new=0,
        videos_error=10,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/channels/sync-runs")

    assert response.status_code == 200
    data = response.json()
    assert len(data["sync_runs"]) == 2
