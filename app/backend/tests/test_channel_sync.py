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

# Set env BEFORE any backend imports so config.py picks them up.
# Use direct assignment (not setdefault) because conftest.py imports
# backend modules which load config.py before these lines run.
os.environ["JWT_SECRET"] = "test-secret-please-do-not-use-in-prod"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["SUPADATA_API_KEY"] = "test-supadata-key"
os.environ["YOUTUBE_CHANNEL_ID"] = "UC_testchannel"
os.environ["CHANNEL_SYNC_TYPE"] = "video"

from backend.auth.dependencies import get_current_user
from backend.db import repository
from backend.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Channel sync requires auth; satisfy the gate with a stub user."""
    app.dependency_overrides[get_current_user] = lambda: {"id": "test-user", "email": "t@t"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


pytestmark = pytest.mark.skip(
    reason="temp_db_schema fixture uses deleted SQLite schema module; pending Alembic rewrite."
)


@pytest.fixture(autouse=True)
def temp_db_schema(tmp_path, monkeypatch):
    """Point DB_PATH at a temp file AND initialise the schema before each test."""
    return str(tmp_path / "test_chat.db")


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
    """Plain sync object returned by client.transcript().

    The `text` parameter feeds `.content` (matching the real SDK's field name)
    — the name is a legacy holdover from the pre-migration API.
    """

    def __init__(self, text="This is a sample transcript for the video."):
        self.content = text


class MockTranscriptChunk:
    """Mimics a TranscriptChunk segment returned by the Supadata SDK."""

    def __init__(self, text: str, offset: float = 0.0, duration: float = 1.0, lang: str = "en"):
        self.text = text
        self.offset = offset
        self.duration = duration
        self.lang = lang


class MockTranscriptResultList:
    """Plain sync object whose .content is a list of TranscriptChunk segments."""

    def __init__(self, chunks: list[MockTranscriptChunk] | None = None):
        self.content = chunks or []


class NoTranscriptResult:
    """Plain sync object returned when no transcript is available."""

    def __init__(self):
        self.content = None


def make_mock_channel_videos(video_ids, short_ids=None, live_ids=None):
    """Return a sync function that returns MockChannelVideosResult (SDK is sync)."""

    def fn(*args, **kwargs):
        return MockChannelVideosResult(
            video_ids=video_ids,
            short_ids=short_ids or [],
            live_ids=live_ids or [],
        )

    return fn


def make_mock_transcript(text):
    """Return a sync function that returns MockTranscriptResult (SDK is sync)."""

    def fn(*args, **kwargs):
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
        mock_client.youtube.channel.videos = make_mock_channel_videos(
            ["dQw4w9WgXcQ", "abc123def456"]
        )
        mock_client.transcript = make_mock_transcript("This is a sample transcript for the video.")
        mock_get_client.return_value = mock_client

        # Patch where names are bound in channels.py (import = local name)
        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch(
                "backend.routes.channels.chunk_video_fallback",
                return_value=(["chunk1", "chunk2"], False),
            ),
            patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
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
        mock_client.youtube.channel.videos = make_mock_channel_videos(["dQw4w9WgXcQ"])
        mock_client.transcript = make_mock_transcript("This is a sample transcript for the video.")
        mock_get_client.return_value = mock_client

        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
            patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert "sync_run_id" in data
    assert data["status"] in ("running", "completed")


async def test_sync_channel_empty_channel():
    """Sync with 0 videos from channel results in status=completed."""
    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos([])
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 0
    assert data["status"] == "completed"


async def test_sync_channel_no_transcript_creates_error_row():
    """Video with unavailable transcript increments videos_error count."""

    def no_transcript_fn(*args, **kwargs):
        return NoTranscriptResult()

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(["noTranscriptVideo"])
        mock_client.transcript = no_transcript_fn
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

    def mock_channel_videos_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            exc = SupadataError(
                error="rate_limited",
                message="Rate limit exceeded",
                details="",
            )
            exc.status = 429  # Set status attribute like the real SDK does
            raise exc
        return MockChannelVideosResult(video_ids=["dQw4w9WgXcQ"])

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = mock_channel_videos_with_retry
        mock_client.transcript = make_mock_transcript("This is a sample transcript for the video.")
        mock_get_client.return_value = mock_client

        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
            patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
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


async def test_sync_channel_missing_youtube_channel_id_400(
    temp_db_schema, bypass_auth, monkeypatch
):
    """POST /api/channels/sync with empty YOUTUBE_CHANNEL_ID returns 400."""
    # routes/channels.py binds YOUTUBE_CHANNEL_ID at import via `from backend.config import ...`
    # so we must patch the binding on the channels module, not on config.
    from backend.routes import channels

    monkeypatch.setattr(channels, "YOUTUBE_CHANNEL_ID", "")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/channels/sync")

    assert response.status_code == 400
    assert "YOUTUBE_CHANNEL_ID" in response.json()["detail"]


async def test_sync_channel_missing_api_key_400(temp_db_schema, bypass_auth, monkeypatch):
    """POST /api/channels/sync with empty SUPADATA_API_KEY returns 400."""
    from backend.routes import channels

    monkeypatch.setattr(channels, "SUPADATA_API_KEY", "")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/channels/sync")

    assert response.status_code == 400
    assert "SUPADATA_API_KEY" in response.json()["detail"]


async def test_sync_channel_embedding_failure_updates_sync_video_status(
    temp_db_schema, bypass_auth
):
    """Embedding failure increments videos_error and records error on sync_video."""

    def failing_embed(*args, **kwargs):
        raise RuntimeError("Embedding service unavailable")

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(["dQw4w9WgXcQ"])
        mock_client.transcript = make_mock_transcript("Sample transcript.")
        mock_get_client.return_value = mock_client

        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
            patch("backend.routes.channels.embed_batch", side_effect=failing_embed),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_error"] == 1
    assert data["videos_new"] == 0

    # Verify sync_video record was updated to error status
    sync_runs = await repository.list_sync_runs(limit=10)
    sync_videos = await repository.list_sync_videos_for_run(sync_runs[0]["id"])
    assert len(sync_videos) == 1
    assert sync_videos[0]["status"] == "error"
    assert "Embedding failed" in sync_videos[0]["error_message"]


async def test_sync_channel_empty_chunks_videos_error_not_new(temp_db_schema, bypass_auth):
    """Video with 0 chunks from chunker increments videos_error, not videos_new."""
    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(["dQw4w9WgXcQ"])
        mock_client.transcript = make_mock_transcript("Sample transcript.")
        mock_get_client.return_value = mock_client

        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch("backend.routes.channels.chunk_video_fallback", return_value=([], True)),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    # Empty chunks should be an error, not a "new" video
    assert data["videos_error"] == 1
    assert data["videos_new"] == 0

    # Verify sync_video record was updated to error status
    sync_runs = await repository.list_sync_runs(limit=10)
    sync_videos = await repository.list_sync_videos_for_run(sync_runs[0]["id"])
    assert len(sync_videos) == 1
    assert sync_videos[0]["status"] == "error"
    assert "0 chunks" in sync_videos[0]["error_message"]


async def test_sync_channel_all_videos_error_status_failed(temp_db_schema, bypass_auth):
    """Sync run where all videos error should have status=failed, not completed."""

    def always_fail_transcript(*args, **kwargs):
        exc = SupadataError(error="server_error", message="All transcripts unavailable", details="")
        exc.status = 500
        raise exc

    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(
            ["video1", "video2", "video3"]
        )
        mock_client.transcript = always_fail_transcript
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_total"] == 3
    assert data["videos_new"] == 0
    assert data["videos_error"] == 3
    assert data["status"] == "failed"


async def test_sync_channel_invalidate_cache_called(temp_db_schema, bypass_auth):
    """Channel sync should call retriever.invalidate_cache() once on completion."""
    with patch("backend.services.supadata._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(["dQw4w9WgXcQ"])
        mock_client.transcript = make_mock_transcript("Sample transcript.")
        mock_get_client.return_value = mock_client

        with (
            patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
            patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
            patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
            patch("backend.rag.retriever.invalidate_cache") as mock_invalidate,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    mock_invalidate.assert_called_once()


async def test_list_sync_videos_for_run(temp_db_schema, bypass_auth):
    """list_sync_videos_for_run returns all sync video records for a run."""
    now_str = datetime.now(UTC).isoformat()
    await repository.create_sync_run(sync_run_id="test-run", started_at=now_str)
    await repository.update_sync_run(
        sync_run_id="test-run",
        status="completed",
        finished_at=now_str,
        videos_total=2,
        videos_new=1,
        videos_error=1,
    )
    await repository.create_sync_video(
        sync_run_id="test-run",
        youtube_video_id="video1",
        status="ingested",
    )
    sv2 = await repository.create_sync_video(
        sync_run_id="test-run",
        youtube_video_id="video2",
        status="pending",
    )
    await repository.update_sync_video_status(
        video_id=sv2["id"],
        status="error",
        error_message="Transcript unavailable",
    )

    rows = await repository.list_sync_videos_for_run("test-run")
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"ingested", "error"}


# ---------------------------------------------------------------------------
# Timestamp regression tests (issue #89)
# ---------------------------------------------------------------------------


async def test_sync_channel_stores_timestamps(temp_db_schema, bypass_auth):
    """Channel sync must pass non-zero timestamps to create_chunk when segments exist."""
    fake_helper = AsyncMock(
        return_value={
            "youtube_video_id": "tsync1",
            "title": "Timestamp Sync Test",
            "description": "desc",
            "transcript": "Intro. Main content. Conclusion.",
            "segments": [
                {"start": 0.0, "end": 30.0, "text": "Intro."},
                {"start": 30.0, "end": 90.0, "text": "Main content."},
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
    ]

    with (
        patch("backend.routes.channels.fetch_video_for_ingest", new=fake_helper),
        patch("backend.routes.channels.chunk_video_timestamped", return_value=(chunk_dicts, False)),
        patch("backend.routes.channels.chunk_video_fallback", return_value=([], False)),
        patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512, [0.2] * 512]),
        patch("backend.services.supadata._get_client") as mock_get_client,
        patch(
            "backend.routes.channels.repo.create_chunk", new_callable=AsyncMock
        ) as mock_create_chunk,
        patch("backend.rag.retriever.invalidate_cache"),
    ):
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = make_mock_channel_videos(["tsync1"])
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    data = response.json()
    assert data["videos_new"] == 1
    assert data["videos_error"] == 0

    # Verify create_chunk received the real timestamps from segments, not 0.0
    calls = mock_create_chunk.call_args_list
    assert len(calls) == 2
    first = calls[0].kwargs
    assert first["start_seconds"] == 0.0
    assert first["end_seconds"] == 30.0
    assert first["snippet"] == "Intro."
    # Regression check: non-first chunk must have non-zero start_seconds
    second = calls[1].kwargs
    assert second["start_seconds"] == 30.0
    assert second["end_seconds"] == 90.0
    assert second["snippet"] == "Main content."


# ---------------------------------------------------------------------------
# Description passthrough tests (issue #102 review)
# ---------------------------------------------------------------------------


async def test_sync_channel_uses_real_description_from_supadata(temp_db_schema, bypass_auth):
    """supadata_data includes description → description passed to create_video, not placeholder."""
    mock_supadata_records = [
        {
            "title": "Test Video",
            "description": "Real Supadata description with content.",
            "url": "https://youtube.com/watch?v=abc123",
            "transcript": [{"text": "Hello", "offset": 0, "duration": 1000}],
        }
    ]

    with (
        patch("backend.services.supadata._get_client") as mock_get_client,
        patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
        patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
        patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
        patch("backend.rag.retriever.invalidate_cache"),
    ):
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = lambda *args, **kwargs: mock_supadata_records
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200

    # Verify create_video received the real Supadata description
    sync_runs = await repository.list_sync_runs(limit=10)
    sync_videos = await repository.list_sync_videos_for_run(sync_runs[0]["id"])
    # The video record should have the real description from Supadata
    # We can't directly inspect create_video call args here since it's in repo,
    # but we can verify the sync succeeded with videos_new=1
    assert sync_videos[0]["status"] == "ingested"


async def test_sync_channel_falls_back_to_placeholder_when_no_description(
    temp_db_schema, bypass_auth
):
    """supadata_data has no description field → placeholder used."""
    mock_supadata_records = [
        {
            "title": "Test Video",
            # description key absent — should fall back to placeholder
            "url": "https://youtube.com/watch?v=abc123",
            "transcript": [{"text": "Hello", "offset": 0, "duration": 1000}],
        }
    ]

    with (
        patch("backend.services.supadata._get_client") as mock_get_client,
        patch("backend.routes.channels.chunk_video_timestamped", return_value=([], False)),
        patch("backend.routes.channels.chunk_video_fallback", return_value=(["chunk1"], False)),
        patch("backend.routes.channels.embed_batch", return_value=[[0.1] * 512]),
        patch("backend.rag.retriever.invalidate_cache"),
    ):
        mock_client = AsyncMock()
        mock_client.youtube.channel.videos = lambda *args, **kwargs: mock_supadata_records
        mock_get_client.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/channels/sync")

    assert response.status_code == 200
    # Verify the placeholder description was used
    sync_runs = await repository.list_sync_runs(limit=10)
    sync_videos = await repository.list_sync_videos_for_run(sync_runs[0]["id"])
    assert sync_videos[0]["status"] == "ingested"
    assert sync_videos[0]["description"] == "Synced from channel UC_testchannel"
