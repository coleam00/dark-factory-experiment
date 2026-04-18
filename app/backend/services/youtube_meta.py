"""YouTube metadata via the public oEmbed endpoint.

Supadata's `youtube.video()` SDK method can't be parsed by the current
Pydantic model (YoutubeVideo rejects the `is_live` field the API returns),
so we pull the bits we actually need — title and author — from YouTube's
own oEmbed endpoint. No auth, no key, safe for 20-5000 calls per sync.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_OEMBED_URL = "https://www.youtube.com/oembed"


async def get_video_title(video_id: str) -> str | None:
    """Return the YouTube video title, or None if the lookup fails.

    Never raises — a missing title falls back to the caller's placeholder.
    """
    params = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_OEMBED_URL, params=params)
            if resp.status_code != 200:
                logger.warning("oEmbed %s for %s: %s", resp.status_code, video_id, resp.text[:200])
                return None
            title = resp.json().get("title")
            return str(title) if title else None
    except Exception as exc:
        logger.warning("oEmbed title fetch failed for %s: %s", video_id, exc)
        return None
