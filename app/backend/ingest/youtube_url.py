"""
YouTube URL parsing and validation.

Provides parse_youtube_url() that extracts a video ID from various YouTube URL
formats and validates it. Raises ValueError for invalid/malformed URLs.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ParsedYouTubeUrl(NamedTuple):
    """Result of parse_youtube_url()."""

    video_id: str
    url: str


# Supported URL patterns:
#   https://www.youtube.com/watch?v=<video_id>
#   https://youtu.be/<video_id>
#   https://www.youtube.com/shorts/<video_id>
_RE_WATCH = re.compile(r"youtube\.com/watch\?.*v=([^&\s]+)", re.IGNORECASE)
_RE_SHORT = re.compile(r"youtube\.com/shorts/([^&\s?]+)", re.IGNORECASE)
_RE_YOUTU_BE = re.compile(r"youtu\.be/([^&\s?]+)", re.IGNORECASE)


def parse_youtube_url(url: str) -> ParsedYouTubeUrl:
    """
    Extract and validate a YouTube video ID from a URL string.

    Args:
        url: A string that may be a YouTube URL.

    Returns:
        A ParsedYouTubeUrl named tuple with the extracted video_id and the
        original URL string.

    Raises:
        ValueError: If the URL is not a recognized YouTube format or the
            extracted video ID is empty.
    """
    video_id: str | None = None

    # Try youtube.com/watch?v=...
    match = _RE_WATCH.search(url)
    if match:
        video_id = match.group(1)

    # Try youtube.com/shorts/...
    if video_id is None:
        match = _RE_SHORT.search(url)
        if match:
            video_id = match.group(1)

    # Try youtu.be/...
    if video_id is None:
        match = _RE_YOUTU_BE.search(url)
        if match:
            video_id = match.group(1)

    if not video_id:
        raise ValueError(
            f"Invalid or unrecognized YouTube URL: '{url}'. "
            "Supported formats: youtube.com/watch?v=, youtu.be/, youtube.com/shorts/"
        )

    if not video_id.strip():
        raise ValueError(f"Could not extract a non-empty video ID from: '{url}'")

    return ParsedYouTubeUrl(video_id=video_id.strip(), url=url)
