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

from backend.config import SUPADATA_API_KEY
from backend.ingest.youtube_url import parse_youtube_url
from backend.services.youtube_meta import get_video_description, get_video_title

logger = logging.getLogger(__name__)


class VideoIngestError(Exception):
    """Raised when video ingest input is invalid (e.g., bad URL)."""


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
    try:
        parsed = parse_youtube_url(url)
    except ValueError as exc:
        raise VideoIngestError(f"Invalid YouTube URL: {exc}") from exc

    client = _get_client()

    # SDK is synchronous; offload to a thread so we don't block the event loop.
    result = await asyncio.to_thread(client.transcript, url=url, lang=lang)

    content = getattr(result, "content", None)
    transcript = ""
    segments: list[dict[str, Any]] = []
    if isinstance(content, str):
        transcript = content
    elif isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            text = getattr(chunk, "text", "") or ""
            offset_ms = getattr(chunk, "offset", 0) or 0
            duration_ms = getattr(chunk, "duration", 0) or 0
            start_s = float(offset_ms) / 1000.0
            end_s = start_s + float(duration_ms) / 1000.0
            parts.append(text)
            segments.append({"start": start_s, "end": end_s, "text": text})
        transcript = " ".join(parts)

    fetched_title, _ = await get_video_title(parsed.video_id)
    title = fetched_title or f"Video {parsed.video_id}"

    # Fetch real description from YouTube Data API; fall back to placeholder if unavailable
    fetched_description = await get_video_description(parsed.video_id)
    description = fetched_description or f"Ingested from {url}"

    return {
        "youtube_video_id": parsed.video_id,
        "title": title,
        "description": description,
        "transcript": transcript,
        "segments": segments,
    }


__all__ = ["SupadataError", "VideoIngestError", "fetch_video_for_ingest"]
