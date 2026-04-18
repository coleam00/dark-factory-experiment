"""
Admin routes — /api/admin/videos/* and /api/admin/videos/sync-channel.

Every endpoint in this module is gated by `get_current_admin` (registered at
router include time in main.py), so handlers do not need to re-verify the
session. A non-admin caller gets 403; an unauthenticated caller gets 401 from
the upstream `get_current_user`.

MISSION §Administrative surface: "A logged-in admin view ... identified by a
hardcoded user identifier, not by a role system." The identifier comes from
`ADMIN_USER_EMAIL` in the environment.

This file is auth-adjacent per FACTORY_RULES §5 (human-authored only). It does
not define auth primitives, but it is the sole consumer of `get_current_admin`
outside tests, so the factory is not permitted to edit it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import AnyUrl, BaseModel, Field

from backend.config import SUPADATA_API_KEY, YOUTUBE_CHANNEL_ID
from backend.db import repository as repo
from backend.ingest.supadata_client import SupadataClient, SupadataError
from backend.ingest.youtube_url import parse_youtube_url
from backend.rag import retriever
from backend.rag.chunker import chunk_video
from backend.rag.embeddings import embed_batch
from backend.routes.channels import sync_channel as _sync_channel_impl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class AddVideoRequest(BaseModel):
    url: AnyUrl = Field(..., description="YouTube video URL")


class AddVideoResponse(BaseModel):
    video_id: str
    chunks_created: int
    status: str


class ResyncResponse(BaseModel):
    video_id: str
    chunks_created: int
    status: str


class AdminVideo(BaseModel):
    id: str
    title: str
    description: str
    url: str
    created_at: str
    chunk_count: int


class AdminVideosResponse(BaseModel):
    videos: list[AdminVideo]


# ---------------------------------------------------------------------------
# Helpers — fetch + chunk + embed a URL. Returns (metadata, chunks_with_embeds).
# Does NOT write to the DB; the caller decides whether to create or replace.
# ---------------------------------------------------------------------------


async def _fetch_chunks_and_embeddings(url_str: str) -> tuple[dict, list[dict]]:
    """Fetch transcript via Supadata, chunk, embed. Raises HTTPException on failure."""
    try:
        parsed = parse_youtube_url(url_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = SupadataClient()
    try:
        supadata_data = await client.fetch_transcript(url_str, lang="en")
    except SupadataError as exc:
        logger.error("Supadata fetch failed for '%s': %s", url_str, exc)
        raise HTTPException(status_code=503, detail=f"Transcript fetch failed: {exc}") from exc
    finally:
        await client.close()

    title = supadata_data["title"]
    description = supadata_data["description"]
    transcript = supadata_data["transcript"]

    chunk_texts: list[str] = chunk_video({"title": title, "transcript": transcript})
    if not chunk_texts:
        raise HTTPException(
            status_code=422,
            detail="Chunker returned 0 chunks — transcript may be empty or malformed.",
        )

    try:
        embeddings = embed_batch(chunk_texts)
    except Exception as exc:
        logger.error("Embedding batch failed for '%s': %s", url_str, exc)
        raise HTTPException(
            status_code=502, detail=f"Embeddings API request failed: {exc}"
        ) from exc

    if len(embeddings) != len(chunk_texts):
        raise HTTPException(
            status_code=500, detail="Mismatch between chunk count and embedding count."
        )

    chunks = [
        {"content": text, "embedding": embedding, "chunk_index": idx}
        for idx, (text, embedding) in enumerate(zip(chunk_texts, embeddings, strict=False))
    ]
    metadata = {
        "title": title,
        "description": description,
        "transcript": transcript,
        "youtube_video_id": parsed.video_id,
    }
    return metadata, chunks


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/videos", response_model=AdminVideosResponse)
async def list_videos_admin() -> AdminVideosResponse:
    """List all videos with chunk counts, newest first."""
    rows = await repo.list_videos_admin()
    videos = [
        AdminVideo(
            id=r["id"],
            title=r["title"],
            description=r["description"],
            url=r["url"],
            created_at=str(r["created_at"]),
            chunk_count=int(r["chunk_count"]),
        )
        for r in rows
    ]
    return AdminVideosResponse(videos=videos)


@router.post("/videos", response_model=AddVideoResponse, status_code=status.HTTP_201_CREATED)
async def add_video(body: AddVideoRequest) -> AddVideoResponse:
    """Add a video by URL. Fetches transcript, chunks, embeds, stores.

    Fails atomically — the video row is only created after chunking and
    embedding both succeed, so we never leave a chunkless video behind.
    """
    url_str = str(body.url)
    metadata, chunks = await _fetch_chunks_and_embeddings(url_str)

    existing = await repo.get_video_by_youtube_id(metadata["youtube_video_id"])
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Video {metadata['youtube_video_id']} is already in the library.",
        )

    video_record = await repo.create_video(
        title=metadata["title"],
        description=metadata["description"],
        url=url_str,
        transcript=metadata["transcript"],
    )
    video_id = video_record["id"]

    try:
        await repo.replace_chunks_for_video(video_id, chunks)
    finally:
        retriever.invalidate_cache()

    logger.info("Admin added video %s (%s): %d chunks", video_id, metadata["title"], len(chunks))
    return AddVideoResponse(video_id=video_id, chunks_created=len(chunks), status="ok")


@router.delete("/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(video_id: str) -> None:
    """Delete a video and cascade its chunks. 404 if not found."""
    deleted = await repo.delete_video_cascade(video_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Video not found")
    retriever.invalidate_cache()
    logger.info("Admin deleted video %s", video_id)


@router.post("/videos/{video_id}/re-sync", response_model=ResyncResponse)
async def resync_video(video_id: str) -> ResyncResponse:
    """Re-fetch transcript for a video and replace its chunks atomically.

    If fetching, chunking, or embedding fails, the existing chunks are kept —
    only a successful re-fetch proceeds to the delete-old + insert-new
    transaction in `replace_chunks_for_video`.
    """
    existing = await repo.get_video(video_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Video not found")

    _, chunks = await _fetch_chunks_and_embeddings(existing["url"])

    try:
        await repo.replace_chunks_for_video(video_id, chunks)
    finally:
        retriever.invalidate_cache()

    logger.info("Admin re-synced video %s: %d chunks", video_id, len(chunks))
    return ResyncResponse(video_id=video_id, chunks_created=len(chunks), status="ok")


@router.post("/videos/sync-channel")
async def sync_channel_admin():
    """Trigger a full channel sync (delegates to /api/channels/sync logic).

    Returns the same payload as the underlying worker. Prefers this admin
    endpoint over hitting /api/channels/sync directly so the UI has a single
    admin-gated surface.
    """
    if not YOUTUBE_CHANNEL_ID or not SUPADATA_API_KEY:
        # Mirror the worker's 400 behaviour so the admin UI sees a clean error.
        missing = []
        if not YOUTUBE_CHANNEL_ID:
            missing.append("YOUTUBE_CHANNEL_ID")
        if not SUPADATA_API_KEY:
            missing.append("SUPADATA_API_KEY")
        raise HTTPException(
            status_code=400,
            detail=f"Channel sync not configured: missing {', '.join(missing)}.",
        )
    logger.info("Admin triggered channel sync at %s", datetime.now(UTC).isoformat())
    return await _sync_channel_impl()
