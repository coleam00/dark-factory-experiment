"""Video catalog cache — injects a cached library-awareness block into the LLM system prompt.

The catalog lists every video in the library, enabling the model to answer
library-existence questions ("Which videos cover X?", "Has Cole talked about Y?")
without relying solely on chunk-level retrieval. The catalog is cached in-memory
and invalidated on every ingest or sync operation.
"""

from __future__ import annotations

import logging

from backend.config import CATALOG_ENABLED, CATALOG_TIER
from backend.db import repository

logger = logging.getLogger(__name__)

# Module-level catalog cache (stores the rendered string)
_catalog_cache: str | None = None


def invalidate_catalog_cache() -> None:
    """Clear the catalog cache."""
    global _catalog_cache
    _catalog_cache = None
    logger.info("Video catalog cache invalidated.")


async def get_catalog_block() -> str | None:
    """
    Return the cached catalog block, or fetch and render a new one if cold.

    Returns None when CATALOG_ENABLED is False (safe rollback path).
    Returns "" when the library is empty (no catalog block injected).
    """
    if not CATALOG_ENABLED:
        return None

    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    videos = await repository.list_videos()
    _catalog_cache = _render_catalog(videos, CATALOG_TIER)
    return _catalog_cache


def _render_catalog(videos: list[dict], tier: str) -> str:
    """
    Render the video catalog as a formatted string block.

    Args:
        videos: List of video dicts from repository.list_videos()
        tier: Either "minimal" (titles only) or "standard" (titles + descriptions)

    Returns:
        A formatted catalog string, or "" if no videos exist.
    """
    if not videos:
        return ""

    lines = ["<video_library>"]
    for video in videos:
        title = video.get("title", "Untitled")
        video_id = video.get("id", "")
        lines.append(f"  - [{video_id}] {title}")
        if tier == "standard":
            description = video.get("description", "")
            if description:
                lines.append(f"    {description}")

    lines.append("</video_library>")
    return "\n".join(lines)
