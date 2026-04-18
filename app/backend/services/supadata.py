"""
Supadata client — wraps the Supadata Python SDK for channel video enumeration
and transcript fetching. Uses a module-level singleton client pattern (mirrors
backend.rag.embeddings).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypedDict

from supadata import Supadata, SupadataError

from backend.config import SUPADATA_API_KEY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client (module-level singleton — re-used across calls)
# ---------------------------------------------------------------------------

_client: Supadata | None = None


def _get_client() -> Supadata:
    """
    Return the module-level Supadata singleton.

    The client is created once on first call and reused for all subsequent
    calls. This mirrors the pattern in backend.rag.embeddings. Caller does not
    need to and should not call close() on the returned client.
    """
    global _client
    if _client is None:
        _client = Supadata(api_key=SUPADATA_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ChannelVideos(TypedDict):
    video_ids: list[str]
    short_ids: list[str]
    live_ids: list[str]


async def get_channel_video_ids(
    channel_id: str, type: str = "video", limit: int = 5000
) -> ChannelVideos:
    """
    Fetch all video IDs for a YouTube channel via Supadata.

    Args:
        channel_id: YouTube channel URL, ID, or handle
        type: Content type filter — 'all', 'video', 'short', 'live'
        limit: Max results to return (up to 5000)

    Returns:
        ChannelVideos dict with video_ids, short_ids, live_ids

    Raises:
        SupadataError: On API errors after retries
    """
    client = _get_client()
    max_retries = 3
    base_delay = 2.0

    for attempt in range(max_retries):
        try:
            result = client.youtube.channel.videos(
                id=channel_id,
                type=type,
                limit=limit,
            )
            return ChannelVideos(
                video_ids=list(result.video_ids or []),
                short_ids=list(result.short_ids or []),
                live_ids=list(result.live_ids or []),
            )
        except SupadataError as exc:
            if exc.status == 429 and attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                logger.warning(
                    "Supadata rate limit (429), retrying in %ds (attempt %d)",
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)
                continue
            logger.error(
                "Supadata channel.videos failed after %d attempts: %s",
                max_retries,
                exc,
            )
            raise
        except (TimeoutError, OSError) as exc:
            logger.error("Network error in get_channel_video_ids: %s", exc)
            raise SupadataError(error="network_error", message=str(exc), details="") from exc
        # Let other exceptions (TypeError, AttributeError, CancelledError) propagate

    # All attempts returned or raised; keep mypy happy with an explicit fallback.
    raise RuntimeError("get_channel_video_ids: retries exhausted without return")


async def get_transcript(video_id: str, lang: str = "en") -> str | None:
    """
    Fetch a YouTube video transcript via Supadata.

    Args:
        video_id: YouTube video ID (11 characters)
        lang: Language code (default 'en' — required per Supadata bug note)

    Returns:
        Transcript string or None if unavailable

    Raises:
        SupadataError: On API errors after retries
    """
    client = _get_client()
    max_retries = 3
    base_delay = 2.0

    for attempt in range(max_retries):
        try:
            result = client.youtube.transcript(video_id=video_id, lang=lang)
            if result and result.text:
                return str(result.text)
            return None
        except SupadataError as exc:
            if exc.status == 429 and attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                logger.warning("Supadata transcript rate limit (429), retrying in %ds", delay)
                await asyncio.sleep(delay)
                continue
            if exc.status in (404, 400):
                logger.warning("Transcript unavailable for video %s: %s", video_id, exc)
                return None
            logger.error("Supadata transcript failed for video %s: %s", video_id, exc)
            raise
        except (TimeoutError, OSError) as exc:
            logger.error("Network error in get_transcript for %s: %s", video_id, exc)
            raise SupadataError(error="network_error", message=str(exc), details="") from exc
        # Let other exceptions (TypeError, AttributeError, CancelledError) propagate

    return None  # Satisfy mypy — unreachable after loop exhausts
