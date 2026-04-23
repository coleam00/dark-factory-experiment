"""Video catalog cache — provides a formatted catalog block for the system prompt.

Maintains an in-process cache of video metadata from the DB.  The cache is
invalidated whenever new videos are ingested or a channel sync completes,
keeping the catalog block fresh without hitting the DB on every chat request.
"""

from __future__ import annotations

import logging

from backend.config import CATALOG_CACHE_TTL_SECONDS
from backend.db import repository

logger = logging.getLogger(__name__)

_catalog_cache: list[dict] | None = None


async def get_catalog() -> list[dict]:
    """Return the cached video list, fetching from the DB on first call.

    Returns an empty list on DB error so callers degrade gracefully.
    """
    global _catalog_cache
    if _catalog_cache is None:
        try:
            _catalog_cache = await repository.list_videos()
            logger.info("Video catalog cache populated with %d videos.", len(_catalog_cache))
        except Exception:
            logger.warning(
                "Failed to populate video catalog cache; skipping catalog block.",
                exc_info=True,
            )
            _catalog_cache = []  # prevent retry storm; cleared by next invalidate_catalog()
    return _catalog_cache


def invalidate_catalog() -> None:
    """Clear the in-process catalog cache so the next call re-fetches."""
    global _catalog_cache
    _catalog_cache = None
    logger.info("Video catalog cache invalidated.")


def build_catalog_block(videos: list[dict], tier: str) -> dict:
    """Format the video list as a content block with cache_control.

    Args:
        videos: List of video dicts (must have ``title`` and ``url`` keys).
        tier: ``"standard"`` (5-min ephemeral) or ``"extended"`` (1-hour TTL).

    Returns:
        A content block dict suitable for inclusion in the system message's
        content array.
    """
    video_lines = [
        f"{i}. {v.get('title') or 'Untitled'} — {v.get('url', '')}"
        for i, v in enumerate(videos, 1)
    ]
    lines = ["Available videos in the library:", "", *video_lines]

    cache_control: dict = {"type": "ephemeral"}
    if tier == "extended":
        cache_control["ttl"] = CATALOG_CACHE_TTL_SECONDS  # integer seconds per Anthropic API

    return {
        "type": "text",
        "text": "\n".join(lines),
        "cache_control": cache_control,
    }
