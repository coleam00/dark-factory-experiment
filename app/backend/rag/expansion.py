"""
Chunk expansion — fetch neighboring chunks within the same video and merge
overlapping/adjacent spans into a single contextual unit.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from backend.db import repository

logger = logging.getLogger(__name__)


async def expand_and_merge(
    chunks: list[dict],
    window: int = 1,
    _fetch_neighbors: Callable[[str, int, int], Awaitable[list[dict]]] | None = None,
) -> list[dict]:
    """
    Expand each retrieved chunk by its neighbors and merge into contiguous spans.

    Args:
        chunks: List of citation-shaped chunk dicts from retrieval
                (keys: chunk_id, video_id, video_title, video_url,
                 content, start_seconds, end_seconds, snippet)
        window: Number of neighbors on each side to fetch (default 1).
                0 returns input chunks unchanged.
        _fetch_neighbors: Optional callable for testing.
                Signature: (video_id, chunk_index, window) -> list[dict].
                Defaults to repository.get_chunk_neighbors.

    Returns:
        List of span dicts (same shape as input chunks but with merged content):
          - video_id, video_title, video_url from the video of the first chunk in span
          - content: concatenated text of all chunks in the span
          - start_seconds: from the first chunk in the span
          - end_seconds: from the last chunk in the span
          - snippet: from the originally-retrieved chunk (preserved for citation)
          - chunk_id: from the originally-retrieved chunk (preserved for citation)
    """
    if window <= 0 or not chunks:
        return chunks

    if _fetch_neighbors is None:
        _fetch_neighbors = repository.get_chunk_neighbors

    # Build index: (video_id, chunk_index) -> original retrieved chunk (for
    # citation anchoring). Keying by tuple prevents chunks at the same
    # chunk_index across different videos from shadowing each other — a
    # chunk_index-only key would pick the wrong anchor when neighbors from
    # video A collide with originals from video B at the same index.
    retrieved_by_index: dict[tuple[str, int], dict] = {}
    for c in chunks:
        retrieved_by_index[(c["video_id"], c["chunk_index"])] = c

    # Fetch neighbors for all chunks concurrently
    video_groups: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        video_groups[chunk["video_id"]].append(chunk)

    all_chunks: list[dict] = list(chunks)

    for video_id, video_chunks in video_groups.items():
        logger.debug("Expanding %d chunks for video %s", len(video_chunks), video_id)
        neighbor_tasks = [
            _fetch_neighbors(video_id, c["chunk_index"], window) for c in video_chunks
        ]
        task_results = await asyncio.gather(*neighbor_tasks, return_exceptions=True)
        for task_result in task_results:
            if isinstance(task_result, BaseException):
                logger.warning("Neighbor fetch failed for video %s: %s", video_id, task_result)
                continue
            for n in task_result:
                n["video_id"] = video_id
                all_chunks.append(n)

    # Group by video_id for merging
    by_video: dict[str, list[dict]] = defaultdict(list)
    for c in all_chunks:
        by_video[c["video_id"]].append(c)

    merged: list[dict] = []
    for current_video_id, video_chunks in by_video.items():
        # Dedupe by chunk id
        seen: set[str] = set()
        unique_chunks: list[dict] = []
        for c in video_chunks:
            cid = c.get("chunk_id") or c.get("id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            unique_chunks.append(c)

        unique_chunks.sort(key=lambda x: x["chunk_index"])

        # Group consecutive chunks into "raw spans" (no gap between indices)
        raw_spans: list[list[dict]] = []
        for chunk in unique_chunks:
            if not raw_spans:
                raw_spans.append([chunk])
            else:
                last_span = raw_spans[-1]
                last_chunk = last_span[-1]
                if chunk["chunk_index"] == last_chunk["chunk_index"] + 1:
                    last_span.append(chunk)
                else:
                    raw_spans.append([chunk])

        # Convert raw spans to result spans, anchoring citation to the first
        # originally-retrieved chunk in each raw span.
        for raw in raw_spans:
            # Find the first originally-retrieved chunk in this raw span
            anchor = raw[0]
            for c in raw:
                if (current_video_id, c["chunk_index"]) in retrieved_by_index:
                    anchor = c
                    break

            content = "\n\n".join(c["content"] for c in raw)
            merged.append(
                {
                    "video_id": raw[0]["video_id"],
                    "video_title": raw[0].get("video_title", ""),
                    "video_url": raw[0].get("video_url", ""),
                    "content": content,
                    "start_seconds": raw[0]["start_seconds"],
                    "end_seconds": raw[-1]["end_seconds"],
                    "snippet": anchor["snippet"],
                    "chunk_id": anchor.get("chunk_id") or anchor.get("id", ""),
                }
            )

    return merged
