"""
Ingest route — POST /api/ingest

Accepts a video (title, description, url, transcript), runs the full
chunking → embedding → storage pipeline, and returns a summary.

All DB access goes through repository.py — no raw SQL here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import AnyUrl, BaseModel, Field, field_validator

from backend.db import repository
from backend.ingest.supadata_client import SupadataClient, SupadataError
from backend.ingest.youtube_url import parse_youtube_url
from backend.rag import retriever
from backend.rag.chunker import chunk_video, chunk_video_fallback, chunk_video_timestamped
from backend.rag.embeddings import embed_batch

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    title: str = Field(..., min_length=1, description="Video title (non-empty)")
    description: str = Field(..., min_length=1, description="Short description (non-empty)")
    url: AnyUrl = Field(..., description="Valid URL to the YouTube video")
    transcript: str = Field(..., min_length=1, description="Full transcript text (non-empty)")
    segments: list[dict] | None = Field(
        default=None,
        description=(
            "Optional timestamped segments from Supadata. Each dict must have "
            "'start' (float seconds), 'end' (float seconds), 'text' (str). "
            "If provided, real timestamps are stored. If absent, timestamps "
            "are estimated evenly across the transcript."
        ),
    )

    @field_validator("segments", mode="before")
    @classmethod
    def validate_segments(cls, v: list[dict] | None) -> list[dict] | None:
        if v is None:
            return None
        for seg in v:
            if not isinstance(seg, dict):
                raise ValueError("Each segment must be a dict")
            for key in ("start", "end", "text"):
                if key not in seg:
                    raise ValueError(f"Segment missing required key: '{key}'")
            # Validate types: start and end must be numeric, text must be string
            start = seg.get("start")
            end = seg.get("end")
            text = seg.get("text")
            if not isinstance(start, int | float):
                raise ValueError(f"Segment 'start' must be a number, got {type(start).__name__}")
            if not isinstance(end, int | float):
                raise ValueError(f"Segment 'end' must be a number, got {type(end).__name__}")
            if not isinstance(text, str):
                raise ValueError(f"Segment 'text' must be a string, got {type(text).__name__}")
        return v

    @field_validator("title", "description", "transcript", mode="before")
    @classmethod
    def no_empty_strings(cls, v: str) -> str:
        if isinstance(v, str) and v.strip() == "":
            raise ValueError("Field must not be an empty string")
        return v


class IngestResponse(BaseModel):
    video_id: str
    chunks_created: int
    status: str


class IngestFromUrlRequest(BaseModel):
    url: AnyUrl = Field(..., description="YouTube video URL")


class IngestFromUrlResponse(BaseModel):
    video_id: str
    chunks_created: int
    status: str


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.post("/ingest", response_model=IngestResponse)
async def ingest_video(body: IngestRequest) -> IngestResponse:
    """
    Ingest a new video: chunk transcript → embed chunks → store in DB.

    Returns:
        { video_id, chunks_created, status }

    Raises:
        HTTP 422 for validation errors (handled automatically by FastAPI/Pydantic).
        HTTP 502 if the embeddings API is unavailable.
        HTTP 500 for unexpected errors.
    """
    url_str = str(body.url)
    logger.info("Ingesting video: '%s'", body.title)

    # 1. Create the video record in the DB
    video_record = await repository.create_video(
        title=body.title,
        description=body.description,
        url=url_str,
        transcript=body.transcript,
    )
    video_id = video_record["id"]

    # 2. Chunk the transcript using Docling HybridChunker
    #    Use timestamped path if segments are provided; otherwise fall back to
    #    estimated timestamps derived from plain transcript.
    video_dict = {
        "title": body.title,
        "transcript": body.transcript,
    }

    if body.segments:
        # Precise timestamps from Supadata (#57)
        chunk_dicts: list[dict] = chunk_video_timestamped(body.segments)
    else:
        # Legacy plain-text ingest: estimated timestamps
        chunk_dicts = chunk_video_fallback(video_dict)

    if not chunk_dicts:
        logger.warning("Chunker returned 0 chunks for video '%s'", body.title)
        return IngestResponse(video_id=video_id, chunks_created=0, status="stored_no_chunks")

    logger.info("Generated %d chunks for '%s'", len(chunk_dicts), body.title)

    # 3. Embed all chunks in a single batched API call
    chunk_texts = [c["content"] for c in chunk_dicts]
    try:
        embeddings = embed_batch(chunk_texts)
    except Exception as exc:
        logger.error("Embedding batch failed for video '%s': %s", body.title, exc)
        # Clean up the orphan video record to avoid leaving cruft
        await repository.delete_video(video_id)
        raise HTTPException(
            status_code=502,
            detail=f"Embeddings API request failed: {exc}",
        ) from exc

    if len(embeddings) != len(chunk_texts):
        raise HTTPException(
            status_code=500,
            detail="Mismatch between chunk count and embedding count.",
        )

    # 4. Store each chunk with its embedding and timestamp data
    try:
        for idx, (chunk, embedding) in enumerate(zip(chunk_dicts, embeddings, strict=False)):
            await repository.create_chunk(
                video_id=video_id,
                content=chunk["content"],
                embedding=embedding,
                chunk_index=idx,
                start_seconds=chunk["start_seconds"],
                end_seconds=chunk["end_seconds"],
                snippet=chunk["snippet"],
            )
    finally:
        retriever.invalidate_cache()

    logger.info("Ingestion complete for '%s': %d chunks stored", body.title, len(chunk_dicts))

    return IngestResponse(
        video_id=video_id,
        chunks_created=len(chunk_dicts),
        status="ok",
    )


@router.post("/ingest/from-url", response_model=IngestFromUrlResponse)
async def ingest_from_url(body: IngestFromUrlRequest) -> IngestFromUrlResponse:
    """
    Ingest a YouTube video by URL alone — fetches transcript via Supadata.

    Returns:
        { video_id, chunks_created, status }

    Raises:
        HTTP 400 if the URL is not a valid YouTube URL.
        HTTP 503 if Supadata is rate-limited.
        HTTP 502 if Supadata or the embeddings API is unavailable.
        HTTP 422 for validation errors.
        HTTP 401 for unauthenticated callers.
    """
    url_str = str(body.url)

    # 1. Parse and validate the YouTube URL
    try:
        parsed = parse_youtube_url(url_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    video_id_from_url = parsed.video_id
    logger.info("Ingesting from URL: '%s' (video_id=%s)", url_str, video_id_from_url)

    # 2. Fetch transcript + metadata from Supadata
    client = SupadataClient()
    try:
        supadata_data = await client.fetch_transcript(url_str, lang="en")
    except SupadataError as exc:
        logger.error("Supadata fetch failed for '%s': %s", url_str, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Transcript fetch failed: {exc}",
        ) from exc
    finally:
        await client.close()

    title = supadata_data["title"]
    description = supadata_data["description"]
    transcript = supadata_data["transcript"]

    # 3. Create the video record in the DB
    video_record = await repository.create_video(
        title=title,
        description=description,
        url=url_str,
        transcript=transcript,
    )
    video_id = video_record["id"]

    # 4. Chunk the transcript using Docling HybridChunker
    video_dict = {
        "title": title,
        "transcript": transcript,
    }
    chunk_texts: list[str] = chunk_video(video_dict)

    if not chunk_texts:
        logger.warning("Chunker returned 0 chunks for video '%s'", title)
        return IngestFromUrlResponse(video_id=video_id, chunks_created=0, status="stored_no_chunks")

    logger.info("Generated %d chunks for '%s'", len(chunk_texts), title)

    # 5. Embed all chunks in a single batched API call
    try:
        embeddings = embed_batch(chunk_texts)
    except Exception as exc:
        logger.error("Embedding batch failed for video '%s': %s", title, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Embeddings API request failed: {exc}",
        ) from exc

    if len(embeddings) != len(chunk_texts):
        raise HTTPException(
            status_code=500,
            detail="Mismatch between chunk count and embedding count.",
        )

    # 6. Store each chunk with its embedding
    try:
        for idx, (text, embedding) in enumerate(zip(chunk_texts, embeddings, strict=False)):
            await repository.create_chunk(
                video_id=video_id,
                content=text,
                embedding=embedding,
                chunk_index=idx,
            )
    finally:
        retriever.invalidate_cache()

    logger.info("Ingestion complete for '%s': %d chunks stored", title, len(chunk_texts))

    return IngestFromUrlResponse(
        video_id=video_id,
        chunks_created=len(chunk_texts),
        status="ok",
    )
