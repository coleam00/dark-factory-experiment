"""YouTube metadata via the public oEmbed endpoint.

Supadata's `youtube.video()` SDK method can't be parsed by the current
Pydantic model (YoutubeVideo rejects the `is_live` field the API returns),
so we pull the bits we actually need — title and author — from YouTube's
own oEmbed endpoint. No auth, no key, safe for 20-5000 calls per sync.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_OEMBED_URL = "https://www.youtube.com/oembed"
_YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"


async def _fetch_og_description(video_id: str) -> str | None:
    """Scrape og:description from the YouTube video page as a fallback.

    YouTube embeds the video description in:
        <meta property="og:description" content="DESCRIPTION TEXT">

    Returns None on any failure (network error, non-200, missing tag).
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
            },
        ) as client:
            resp = await client.get(watch_url)
            if resp.status_code != 200:
                return None
            html = resp.text
            match = re.search(
                r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
                html,
            )
            if not match:
                return None
            content = match.group(1)
            return content or None
    except Exception as exc:
        logger.warning("og:description scrape failed for %s: %s", video_id, exc)
        return None


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
            return title or None
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("oEmbed timeout for %s: %s", video_id, exc)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("oEmbed HTTP error %s for %s: %s", exc.response.status_code, video_id, exc)
        return None
    except httpx.NetworkError as exc:
        logger.warning("oEmbed network error for %s: %s", video_id, exc)
        return None
    except Exception as exc:
        logger.warning("oEmbed title fetch failed for %s: %s", video_id, exc)
        return None


async def get_video_description(video_id: str) -> str | None:
    """Return the YouTube video description, or None if the lookup fails.

    Calls YouTube Data API v3 videos.list?part=snippet when YOUTUBE_API_KEY
    is set. Returns None on any failure (no key, network error, quota exceeded)
    so callers can fall back to placeholder text.

    Never raises — a missing description falls back to the caller's placeholder.
    """
    from backend.config import YOUTUBE_API_KEY

    if not YOUTUBE_API_KEY:
        return await _fetch_og_description(video_id)

    params = {
        "part": "snippet",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
        "hl": "en",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_YOUTUBE_API_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "YouTube Data API %s for %s: %s",
                    resp.status_code,
                    video_id,
                    resp.text[:200],
                )
                return None
            items = resp.json().get("items", [])
            if not items:
                return None
            description = items[0].get("snippet", {}).get("description", "")
            return description or None
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("YouTube Data API timeout for %s: %s", video_id, exc)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "YouTube Data API HTTP error %s for %s: %s",
            exc.response.status_code,
            video_id,
            exc,
        )
        return None
    except httpx.NetworkError as exc:
        logger.warning("YouTube Data API network error for %s: %s", video_id, exc)
        return None
    except Exception as exc:
        logger.warning("YouTube Data API description fetch failed for %s: %s", video_id, exc)
        return None
