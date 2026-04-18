"""
Hybrid retriever — Reciprocal Rank Fusion (RRF) over keyword and vector search.

Combines Postgres tsvector full-text search with pgvector cosine similarity
using RRF to produce a unified ranking. Each method over-fetches 2*top_k
candidates before merging (standard RRF practice).

Requires Postgres with:
  - search_vector tsvector column on chunks (GENERATED ALWAYS AS)
  - GIN index on search_vector
  - pgvector extension loaded

Falls back to raising a clear error if DATABASE_URL is not set (no silent
cosine fallback).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from backend.config import HYBRID_K_CONSTANT, HYBRID_OVERFETCH_FACTOR, KEYWORD_LANGUAGE
from backend.db import repository

logger = logging.getLogger(__name__)

# Module-level video metadata cache (populated on demand per chunk result)
_video_cache: dict[str, dict[str, str]] = {}


def invalidate_cache() -> None:
    """Clear the video metadata cache."""
    global _video_cache
    _video_cache.clear()
    logger.info("Hybrid retriever video cache invalidated.")


async def retrieve_hybrid(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """
    Hybrid retrieval via Reciprocal Rank Fusion (RRF).

    Args:
        query_text: Original user query string (used for keyword search)
        query_embedding: Query embedding from embed_text() (used for vector search)
        top_k: Maximum number of results to return (default 5)

    Returns:
        A list of dicts (length <= top_k), each containing:
          - chunk_id: str
          - content: str
          - video_id: str
          - video_title: str
          - video_url: str
          - start_seconds: float
          - end_seconds: float
          - snippet: str
          - score: float (RRF score, higher is better)
        Sorted by score descending. Returns [] if the DB has no matching chunks.

    Raises:
        RuntimeError: If DATABASE_URL is not set (hybrid requires Postgres).
    """
    from backend.config import DATABASE_URL

    if not DATABASE_URL:
        raise RuntimeError(
            "Hybrid retrieval requires Postgres (DATABASE_URL is not set). "
            "Use the VPS snapshot database via SSH tunnel for local dev."
        )

    # Over-fetch factor — each method returns 2*top_k before merging
    fetch_k = top_k * HYBRID_OVERFETCH_FACTOR

    # Run keyword and vector searches concurrently
    keyword_task = repository.keyword_search(query_text, top_k=fetch_k, language=KEYWORD_LANGUAGE)
    vector_task = repository.vector_search_pg(query_embedding, top_k=fetch_k)

    keyword_hits, vector_hits = await keyword_task, await vector_task

    logger.debug(
        "Hybrid retrieval: %d keyword hits, %d vector hits (fetch_k=%d)",
        len(keyword_hits),
        len(vector_hits),
        fetch_k,
    )

    if not keyword_hits and not vector_hits:
        return []

    # RRF merge
    merged = _rrf_merge(keyword_hits, vector_hits, k=HYBRID_K_CONSTANT, top_k=top_k)

    # Hydrate with video metadata (title, url) via cached lookups
    results: list[dict] = []
    for chunk in merged:
        video_id = chunk["video_id"]
        if video_id not in _video_cache:
            video = await repository.get_video(video_id)
            if video:
                _video_cache[video_id] = {
                    "title": video["title"],
                    "url": video["url"],
                }
            else:
                logger.warning(
                    "Video not found for video_id=%s, chunk_id=%s",
                    video_id,
                    chunk.get("id", "?"),
                )
                _video_cache[video_id] = {"title": "Unknown Video", "url": ""}

        video_meta = _video_cache[video_id]
        results.append(
            {
                "chunk_id": chunk["id"],
                "content": chunk["content"],
                "video_id": video_id,
                "video_title": video_meta["title"],
                "video_url": video_meta["url"],
                "start_seconds": chunk.get("start_seconds", 0.0),
                "end_seconds": chunk.get("end_seconds", 0.0),
                "snippet": chunk.get("snippet", ""),
                "score": chunk.get("rrf_score", 0.0),
            }
        )

    return results


def _rrf_merge(
    keyword_hits: list[dict],
    vector_hits: list[dict],
    k: int = 60,
    top_k: int = 5,
) -> list[dict]:
    """
    Reciprocal Rank Fusion (RRF) to merge ranked results from two retrieval methods.

    RRF score = Σ 1 / (k + rank_i) across all methods
    where rank_i is the 0-based position of the item in method i's result list.

    Args:
        keyword_hits: List of chunk dicts from keyword search (must have "id" key)
        vector_hits: List of chunk dicts from vector search (must have "id" key)
        k: RRF k constant (default 60 — controls how much rank matters vs. presence)
        top_k: Maximum number of merged results to return

    Returns:
        List of chunk dicts (with "rrf_score" added) sorted by RRF score descending
    """
    scores: dict[str, float] = defaultdict(float)
    rows: dict[str, dict] = {}

    for rank, row in enumerate(keyword_hits):
        chunk_id = row["id"]
        scores[chunk_id] += 1.0 / (k + rank)
        rows[chunk_id] = row

    for rank, row in enumerate(vector_hits):
        chunk_id = row["id"]
        scores[chunk_id] += 1.0 / (k + rank)
        if chunk_id not in rows:
            rows[chunk_id] = row

    ranked = sorted(scores, key=scores.__getitem__, reverse=True)[:top_k]
    return [{**rows[cid], "rrf_score": scores[cid]} for cid in ranked]
