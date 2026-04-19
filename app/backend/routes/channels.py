"""
Channel sync routes — POST /api/channels/sync and GET /api/channels/sync-runs.

Enumerates all videos from a configured YouTube channel via Supadata,
ingests new ones through the existing chunk → embed → store pipeline,
and records sync history in channel_sync_runs / channel_sync_videos tables.

All DB access goes through repository.py — no raw SQL here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import CHANNEL_SYNC_TYPE, SUPADATA_API_KEY, YOUTUBE_CHANNEL_ID
from backend.db import repository as repo
from backend.db.repository import _new_id, _now
from backend.rag import retriever
from backend.rag.chunker import chunk_video_fallback, chunk_video_timestamped
from backend.rag.embeddings import embed_batch
from backend.services import supadata
from backend.services.video_ingest import fetch_video_for_ingest

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SyncResponse(BaseModel):
    sync_run_id: str
    status: str
    videos_total: int
    videos_new: int
    videos_error: int


class SyncRun(BaseModel):
    id: str
    status: str
    videos_total: int
    videos_new: int
    videos_error: int
    started_at: datetime
    finished_at: datetime | None


class SyncRunsResponse(BaseModel):
    sync_runs: list[SyncRun]


@router.post("/channels/sync", response_model=SyncResponse)
async def sync_channel(limit: int | None = None) -> SyncResponse:
    """
    Enumerate videos from the configured YouTube channel via Supadata,
    ingest any new videos (idempotent by youtube_video_id), and record a
    channel_sync_runs row with per-video status.

    Query params:
        limit: Max number of videos to process. Omit for full channel.
               Supadata returns videos newest-first, so limit=20 processes
               the 20 most recent videos.

    This is a synchronous, sequential operation — all videos are processed
    before the HTTP response is returned. The caller (e.g. systemd timer)
    should set an appropriate request timeout.
    """
    if not YOUTUBE_CHANNEL_ID:
        raise HTTPException(
            status_code=400,
            detail="YOUTUBE_CHANNEL_ID is not configured.",
        )
    if not SUPADATA_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="SUPADATA_API_KEY is not configured.",
        )

    sync_run_id = _new_id()
    started_at = _now()

    logger.info("Starting channel sync run %s for channel %s", sync_run_id, YOUTUBE_CHANNEL_ID)

    # Create sync run record
    await repo.create_sync_run(sync_run_id=sync_run_id, started_at=started_at)

    # Enumerate channel videos from Supadata. Pass limit through so we don't
    # pull 5000 IDs when the caller only wants 20.
    supadata_limit = limit if limit and limit > 0 else 5000
    try:
        channel_videos = await supadata.get_channel_video_ids(
            channel_id=YOUTUBE_CHANNEL_ID,
            type=CHANNEL_SYNC_TYPE,
            limit=supadata_limit,
        )
    except Exception as exc:
        logger.error("Failed to enumerate channel videos: %s", exc)
        await repo.update_sync_run(
            sync_run_id=sync_run_id,
            status="failed",
            finished_at=_now(),
            videos_total=0,
            videos_new=0,
            videos_error=0,
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to enumerate channel videos. Check server logs for details.",
        ) from exc

    all_video_ids = (
        channel_videos["video_ids"] + channel_videos["short_ids"] + channel_videos["live_ids"]
    )
    # Extra safety clamp in case Supadata returned more than requested across
    # the three buckets (e.g. type='all').
    if limit and limit > 0:
        all_video_ids = all_video_ids[:limit]
    videos_total = len(all_video_ids)
    videos_new = 0
    videos_error = 0

    logger.info(
        "Channel %s has %d videos (type=%s)",
        YOUTUBE_CHANNEL_ID,
        videos_total,
        CHANNEL_SYNC_TYPE,
    )

    # Process each video
    for youtube_video_id in all_video_ids:
        # Create pending sync video record — we need its ID to update on error
        sync_video_record = await repo.create_sync_video(
            sync_run_id=sync_run_id,
            youtube_video_id=youtube_video_id,
            status="pending",
        )

        existing = await repo.get_video_by_youtube_id(youtube_video_id)
        if existing is not None:
            logger.info("Video %s already ingested, skipping", youtube_video_id)
            videos_new += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="ingested",
            )
            continue

        # Fetch transcript + segments + title via the unified helper.
        youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
        try:
            supadata_data = await fetch_video_for_ingest(youtube_url, lang="en")
        except Exception as exc:
            logger.warning(
                "Transcript fetch failed for video %s: %s",
                youtube_video_id,
                exc,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message=f"Transcript fetch failed: {exc}",
            )
            continue

        transcript = supadata_data["transcript"]
        video_segments = supadata_data.get("segments", [])

        if not transcript:
            logger.warning(
                "No transcript available for video %s",
                youtube_video_id,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message="No transcript available",
            )
            continue

        title = supadata_data["title"]
        description = supadata_data.get("description") or f"Synced from channel {YOUTUBE_CHANNEL_ID}"

        # Ingest through chunk → embed → store pipeline
        try:
            video_record = await repo.create_video(
                title=title,
                description=description,
                url=youtube_url,
                transcript=transcript,
            )
        except Exception as exc:
            logger.error(
                "Failed to create video record for %s: %s",
                youtube_video_id,
                exc,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message=f"Video creation failed: {exc}",
            )
            continue

        video_id = video_record["id"]

        # Chunk the transcript
        if video_segments:
            chunk_dicts: list[dict]
            chunk_dicts, had_errors = chunk_video_timestamped(video_segments)
            if had_errors:
                logger.warning(
                    "Chunker fell back to raw text for some segments for video %s", youtube_video_id
                )
        else:
            chunk_dicts, had_errors = chunk_video_fallback(
                {"title": title, "transcript": transcript}
            )
            if had_errors:
                logger.warning("Chunker returned 0 chunks for video %s", youtube_video_id)

        if not chunk_dicts:
            logger.warning(
                "Chunker returned 0 chunks for video %s",
                youtube_video_id,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message="Chunker returned 0 chunks",
            )
            continue

        # Embed all chunks
        chunk_texts = [c["content"] for c in chunk_dicts]
        try:
            embeddings = embed_batch(chunk_texts)
        except Exception as exc:
            logger.error(
                "Embedding batch failed for video %s: %s",
                youtube_video_id,
                exc,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message=f"Embedding failed: {exc}",
            )
            continue

        # Store chunks with timestamp data
        try:
            for idx, (chunk, embedding) in enumerate(zip(chunk_dicts, embeddings, strict=False)):
                await repo.create_chunk(
                    video_id=video_id,
                    content=chunk["content"],
                    embedding=embedding,
                    chunk_index=idx,
                    start_seconds=chunk["start_seconds"],
                    end_seconds=chunk["end_seconds"],
                    snippet=chunk["snippet"],
                )
        except Exception as exc:
            logger.error(
                "Failed to store chunks for video %s: %s",
                youtube_video_id,
                exc,
            )
            videos_error += 1
            await repo.update_sync_video_status(
                video_id=sync_video_record["id"],
                status="error",
                error_message=f"Chunk storage failed: {exc}",
            )
            continue

        videos_new += 1
        await repo.update_sync_video_status(
            video_id=sync_video_record["id"],
            status="ingested",
        )
        logger.info(
            "Ingested video %s (%s): %d chunks",
            youtube_video_id,
            title,
            len(chunk_dicts),
        )

    # Invalidate retriever cache once at the end
    retriever.invalidate_cache()

    # Determine overall status
    status = "completed" if videos_error == 0 or videos_new > 0 else "failed"
    await repo.update_sync_run(
        sync_run_id=sync_run_id,
        status=status,
        finished_at=_now(),
        videos_total=videos_total,
        videos_new=videos_new,
        videos_error=videos_error,
    )

    logger.info(
        "Channel sync run %s complete: total=%d new=%d error=%d",
        sync_run_id,
        videos_total,
        videos_new,
        videos_error,
    )

    return SyncResponse(
        sync_run_id=sync_run_id,
        status=status,
        videos_total=videos_total,
        videos_new=videos_new,
        videos_error=videos_error,
    )


@router.get("/channels/sync-runs", response_model=SyncRunsResponse)
async def list_sync_runs() -> SyncRunsResponse:
    """
    List the 10 most recent channel sync runs, ordered newest first.

    Returns a SyncRunsResponse with per-run aggregates (videos_total,
    videos_new, videos_error). Individual video records can be retrieved
    via list_sync_videos_for_run(sync_run_id) if needed.
    """
    rows = await repo.list_sync_runs(limit=10)
    sync_runs = [SyncRun(**row) for row in rows]
    return SyncRunsResponse(sync_runs=sync_runs)
