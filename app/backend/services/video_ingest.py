"""
Unified "fetch everything we need to ingest a YouTube video" helper.

Wraps:
  - Supadata SDK for transcript + timestamped segments
  - YouTube oEmbed for the real title (the transcript endpoint does not
    return a title)

Callers get back a single dict with {title, description, transcript,
segments, youtube_video_id} so both /api/ingest/from-url (routes/ingest.py)
and the admin /api/admin/videos endpoints (routes/admin.py) share one
orchestration path instead of maintaining parallel clients.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from supadata import Supadata, SupadataError

from backend.config import SUPADATA_API_KEY, YOUTUBE_API_KEY
from backend.ingest.youtube_url import parse_youtube_url
from backend.services.youtube_meta import get_video_description, get_video_title

logger = logging.getLogger(__name__)


_client: Supadata | None = None


def _get_client() -> Supadata:
    """Return the module-level Supadata singleton."""
    global _client
    if _client is None:
        _client = Supadata(api_key=SUPADATA_API_KEY)
    return _client


async def fetch_video_for_ingest(url: str, lang: str = "en") -> dict[str, Any]:
    """
    Fetch transcript + segments + title for a YouTube URL.

    Args:
        url: Full YouTube video URL (watch, shorts, or youtu.be short form).
        lang: Transcript language code (default 'en').

    Returns:
        {
            "youtube_video_id": str,
            "title": str,
            "description": str,
            "transcript": str,
            "segments": list[{"start": float, "end": float, "text": str}],
        }

    Raises:
        ValueError: If url is not a recognised YouTube URL.
        SupadataError: On Supadata API errors.
    """
    parsed = parse_youtube_url(url)
    client = _get_client()

    # SDK is synchronous; offload to a thread so we don't block the event loop.
    result = await asyncio.to_thread(client.transcript, url=url, lang=lang)

    segments: list[dict[str, Any]] = []
    content = getattr(result, "content", None)
    if isinstance(content, str):
        transcript = content
    elif isinstance(content, list):
        parts = [getattr(chunk, "text", "") or "" for chunk in content]
        for chunk in content:
            offset_ms = getattr(chunk, "offset", 0) or 0
            duration_ms = getattr(chunk, "duration", 0) or 0
            start_s = float(offset_ms) / 1000.0
            end_s = start_s + float(duration_ms) / 1000.0
            segments.append({"start": start_s, "end": end_s, "text": getattr(chunk, "text", "") or ""})
        transcript = " ".join(parts)
    else:
        transcript = ""

    fetched_title = await get_video_title(parsed.video_id)
    title = fetched_title if fetched_title else f"Video {parsed.video_id}"
    real_description = await get_video_description(parsed.video_id, YOUTUBE_API_KEY)
    description = real_description or f"Ingested from {url}"

    return {
        "youtube_video_id": parsed.video_id,
        "title": title,
        "description": description,
        "transcript": transcript,
        "segments": segments,
    }


__all__ = ["SupadataError", "fetch_video_for_ingest"]
