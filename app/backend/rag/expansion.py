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
        List of citation dicts (same shape as input chunks but with merged
        content). A contiguous span emits ONE entry per originally-retrieved
        chunk it contains (issue #276), each:
          - video_id, video_title, video_url from the span's first chunk
          - content: concatenated text of all chunks in the span (shared context)
          - start_seconds / end_seconds: from this entry's own retrieved chunk
          - snippet / chunk_id: from this entry's own retrieved chunk
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
                n = dict(n)
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

        # Convert raw spans to result entries. A span can contain more than one
        # originally-retrieved chunk when two retrieved moments are close enough
        # that their neighbor windows overlap into one contiguous run. Emit one
        # citation entry PER originally-retrieved chunk so two nearby moments
        # from the same video don't collapse into a single chip (issue #276).
        # Each entry shares the merged span content (small-to-big context) but
        # anchors its timestamp/snippet/chunk_id to its OWN chunk, so the
        # deep-link opens at the moment the model actually cited rather than at
        # the (earlier) start of the expanded span.
        for raw in raw_spans:
            content = "\n\n".join(c["content"] for c in raw)
            span_video_id = raw[0]["video_id"]
            span_title = raw[0].get("video_title", "")
            span_url = raw[0].get("video_url", "")

            anchors = [
                c for c in raw if (current_video_id, c["chunk_index"]) in retrieved_by_index
            ]
            if not anchors:
                # Defensive: every span is built around at least one retrieved
                # chunk, but fall back to the first chunk if that ever changes.
                anchors = [raw[0]]

            for anchor in anchors:
                merged.append(
                    {
                        "video_id": span_video_id,
                        "video_title": span_title,
                        "video_url": span_url,
                        # Issue #147: source_type/lesson_url come from the anchor
                        # (an originally-retrieved chunk that went through
                        # _hydrate_chunks); neighbors fetched via
                        # get_chunk_neighbors don't carry these columns.
                        "source_type": anchor.get("source_type", "youtube"),
                        "lesson_url": anchor.get("lesson_url", ""),
                        "content": content,
                        "start_seconds": anchor.get("start_seconds", 0.0),
                        "end_seconds": anchor.get("end_seconds", 0.0),
                        "snippet": anchor.get("snippet", ""),
                        "chunk_id": anchor.get("chunk_id") or anchor.get("id", ""),
                    }
                )

    return merged
