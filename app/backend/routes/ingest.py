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
from backend.rag.chunker import chunk_video
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
    video_dict = {
        "title": body.title,
        "transcript": body.transcript,
    }
    chunk_texts: list[str] = chunk_video(video_dict)

    if not chunk_texts:
        logger.warning("Chunker returned 0 chunks for video '%s'", body.title)
        return IngestResponse(video_id=video_id, chunks_created=0, status="stored_no_chunks")

    logger.info("Generated %d chunks for '%s'", len(chunk_texts), body.title)

    # 3. Embed all chunks in a single batched API call
    try:
        embeddings = embed_batch(chunk_texts)
    except Exception as exc:
        logger.error("Embedding batch failed for video '%s': %s", body.title, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Embeddings API request failed: {exc}",
        ) from exc

    if len(embeddings) != len(chunk_texts):
        raise HTTPException(
            status_code=500,
            detail="Mismatch between chunk count and embedding count.",
        )

    # 4. Store each chunk with its embedding
    for idx, (text, embedding) in enumerate(zip(chunk_texts, embeddings, strict=False)):
        await repository.create_chunk(
            video_id=video_id,
            content=text,
            embedding=embedding,
            chunk_index=idx,
        )

    logger.info("Ingestion complete for '%s': %d chunks stored", body.title, len(chunk_texts))

    return IngestResponse(
        video_id=video_id,
        chunks_created=len(chunk_texts),
        status="ok",
    )
