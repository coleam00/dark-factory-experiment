"""
Regression tests for channel-sync counting + run status (issue #295).

Two bugs are covered here:

1. Videos skipped because they already exist in the DB used to be counted
   in `videos_new`, so a routine idempotent sync reported the whole channel
   as freshly ingested.
2. Because skips inflated `videos_new`, the run-status check
   (`videos_new == 0`) never fired, so a run in which every genuinely-new
   video failed to ingest was still reported as "completed" instead of
   "failed" — hiding real ingestion outages.

These call the route handler `sync_channel` directly with the repo and
service boundaries patched, so no Postgres / Supadata / OpenRouter access
is needed. They live in a separate module from `test_channel_sync.py`
(which is entirely skipped pending an Alembic fixture rewrite).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

# Set env BEFORE any backend imports so config.py picks them up.
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SUPADATA_API_KEY", "test-supadata-key")
os.environ.setdefault("YOUTUBE_CHANNEL_ID", "UC_testchannel")
os.environ.setdefault("CHANNEL_SYNC_TYPE", "video")

from backend.routes import channels


def _channel_videos(video_ids):
    return {"video_ids": list(video_ids), "short_ids": [], "live_ids": []}


def _ingest_payload(title="A video"):
    return {
        "title": title,
        "description": "desc",
        "transcript": "Some transcript text.",
        "segments": [],
    }


def _patch_common(stack, *, get_video_side_effect, fetch_side_effect):
    """Patch every boundary the sync loop touches and return key mocks.

    `get_video_side_effect` controls which youtube IDs look already-ingested;
    `fetch_side_effect` controls transcript-fetch success/failure per call.
    """
    p = stack.enter_context

    update_sync_run = p(patch.object(channels.repo, "update_sync_run", new=AsyncMock()))
    p(patch.object(channels.repo, "create_sync_run", new=AsyncMock()))
    p(
        patch.object(
            channels.repo,
            "create_sync_video",
            new=AsyncMock(return_value={"id": "sv-id"}),
        )
    )
    p(patch.object(channels.repo, "update_sync_video_status", new=AsyncMock()))
    p(
        patch.object(
            channels.repo,
            "get_video_by_youtube_id",
            new=AsyncMock(side_effect=get_video_side_effect),
        )
    )
    p(
        patch.object(
            channels.repo,
            "create_video",
            new=AsyncMock(return_value={"id": "video-id"}),
        )
    )
    p(patch.object(channels.repo, "create_chunk", new=AsyncMock()))

    p(
        patch.object(
            channels.supadata,
            "get_channel_video_ids",
            new=AsyncMock(),
        )
    )
    p(patch.object(channels, "fetch_video_for_ingest", new=AsyncMock(side_effect=fetch_side_effect)))
    p(patch.object(channels, "get_video_title", new=AsyncMock(return_value=(None, "Chan"))))
    p(
        patch.object(
            channels,
            "chunk_video_fallback",
            new=lambda *a, **k: (
                [{"content": "c", "start_seconds": 0.0, "end_seconds": 1.0, "snippet": "c"}],
                False,
            ),
        )
    )
    p(
        patch.object(
            channels,
            "chunk_video_timestamped",
            new=lambda *a, **k: ([], False),
        )
    )
    p(patch.object(channels, "embed_batch", new=lambda texts: [[0.1] * 512 for _ in texts]))
    p(patch.object(channels.retriever_hybrid, "invalidate_cache", new=lambda: None))
    p(patch.object(channels.catalog, "invalidate_catalog", new=lambda: None))

    return update_sync_run


async def test_skipped_videos_not_counted_as_new():
    """A run that skips an existing video and ingests one new video reports
    videos_new == 1 (only the genuinely-new one), not 2."""
    import contextlib

    existing = {"id": "existing-id"}

    async def get_video(youtube_video_id):
        # First ID already in DB → skipped; second is brand new.
        return existing if youtube_video_id == "already-here" else None

    async def fetch_ok(url, lang="en"):
        return _ingest_payload()

    with contextlib.ExitStack() as stack:
        update_sync_run = _patch_common(
            stack, get_video_side_effect=get_video, fetch_side_effect=fetch_ok
        )
        channels.supadata.get_channel_video_ids.return_value = _channel_videos(
            ["already-here", "brand-new"]
        )

        resp = await channels.sync_channel()

    assert resp.videos_total == 2
    assert resp.videos_new == 1  # only "brand-new", NOT the skipped "already-here"
    assert resp.videos_error == 0
    assert resp.status == "completed"
    # The persisted run aggregate must agree with the response.
    assert update_sync_run.await_args.kwargs["videos_new"] == 1
    assert update_sync_run.await_args.kwargs["status"] == "completed"


async def test_run_failed_when_all_new_videos_fail_despite_a_skip():
    """If every genuinely-new video fails to ingest, the run is reported as
    'failed' even when other videos were skipped as already-present.

    Before the fix the skipped video inflated videos_new to 1, so the
    `videos_error > 0 and videos_new == 0` check never fired and the run
    was misreported as 'completed' — masking the ingestion outage.
    """
    import contextlib

    existing = {"id": "existing-id"}

    async def get_video(youtube_video_id):
        return existing if youtube_video_id == "already-here" else None

    async def fetch_fail(url, lang="en"):
        raise RuntimeError("Transcript service down")

    with contextlib.ExitStack() as stack:
        update_sync_run = _patch_common(
            stack, get_video_side_effect=get_video, fetch_side_effect=fetch_fail
        )
        channels.supadata.get_channel_video_ids.return_value = _channel_videos(
            ["already-here", "brand-new"]
        )

        resp = await channels.sync_channel()

    assert resp.videos_total == 2
    assert resp.videos_new == 0  # the only new video failed; the skip doesn't count
    assert resp.videos_error == 1
    assert resp.status == "failed"
    assert update_sync_run.await_args.kwargs["status"] == "failed"
