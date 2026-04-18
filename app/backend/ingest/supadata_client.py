"""
Supadata HTTP client for fetching YouTube video transcripts.

Provides SupadataClient.fetch_transcript() which:
  - Calls GET /v1/youtube/transcript with url + lang parameters
  - Retries 429 with Retry-After backoff (max 2 retries)
  - Retries 5xx with jittered exponential backoff (max 2 retries)
  - Handles the Supadata 500-without-lang bug by retrying with lang="en"
  - Returns a normalized dict: {title, description, transcript, segments}
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from backend.config import SUPADATA_API_KEY

logger = logging.getLogger(__name__)

SUPADATA_BASE_URL = "https://api.supadata.io"
MAX_RETRIES = 2


class SupadataError(Exception):
    """Raised when the Supadata API returns an unrecoverable error."""


class SupadataClient:
    """
    Async HTTP client for the Supadata transcript API.

    Args:
        base_url: Optional base URL override (useful for testing).
    """

    def __init__(self, base_url: str = SUPADATA_BASE_URL) -> None:
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {SUPADATA_API_KEY}",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_transcript(
        self,
        url: str,
        lang: str = "en",
    ) -> dict[str, Any]:
        """
        Fetch transcript metadata + segments for a YouTube video via Supadata.

        Args:
            url: The full YouTube video URL.
            lang: Language code for transcript (default "en").

        Returns:
            A dict with keys:
              - title (str): Video title
              - description (str): Video description
              - transcript (str): Full concatenated transcript text
              - segments (list[dict]): List of {text, start_seconds} dicts

        Raises:
            SupadataError: On unrecoverable HTTP errors (4xx except 429,
                5xx after retries exhausted).
            httpx.HTTPError: On network-level failures.
        """
        client = await self._get_client()

        async def _do_request(lang_param: str) -> httpx.Response:
            return await client.get(
                "/v1/youtube/transcript",
                params={"url": url, "lang": lang_param},
            )

        # Try once; handle retries below
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await _do_request(lang)

                if response.status_code == 200:
                    data = response.json()
                    return self._normalize(data)

                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    wait_seconds = float(retry_after) if retry_after else 2**attempt
                    logger.warning(
                        "Supadata 429 for '%s', attempt %d/%d. Retrying in %.1fs.",
                        url,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        wait_seconds,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(wait_seconds)
                        continue
                    else:
                        raise SupadataError(
                            f"Supadata rate-limited (429) after {MAX_RETRIES + 1} attempts. "
                            "Try again later."
                        )

                if response.status_code == 500:
                    # Known Supadata bug: 500 when lang is missing or wrong.
                    # If this is the first attempt without explicit lang, retry with lang="en".
                    if lang != "en":
                        logger.warning(
                            "Supadata 500 for '%s' with lang='%s'. Retrying with lang='en'.",
                            url,
                            lang,
                        )
                        response = await _do_request("en")
                        if response.status_code == 200:
                            return self._normalize(response.json())
                    raise SupadataError(
                        f"Supadata returned 500 Internal Server Error: {response.text}"
                    )

                if 500 <= response.status_code < 600:
                    wait_seconds = (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Supadata %d for '%s', attempt %d/%d. Retrying in %.1fs.",
                        response.status_code,
                        url,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        wait_seconds,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(wait_seconds)
                        continue
                    else:
                        raise SupadataError(
                            f"Supadata returned {response.status_code} after "
                            f"{MAX_RETRIES + 1} attempts."
                        )

                # 4xx non-429 — not retryable
                raise SupadataError(f"Supadata returned {response.status_code}: {response.text}")

            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait_seconds = (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Supadata HTTP error '%s' for '%s', attempt %d/%d. Retrying in %.1fs.",
                        exc,
                        url,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        wait_seconds,
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    raise SupadataError(
                        f"Supadata request failed after {MAX_RETRIES + 1} attempts: {exc}"
                    ) from exc

        # Should not reach here, but satisfy mypy/pytest
        raise SupadataError(f"Supadata request exhausted all retries for '{url}'") from last_exc

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize the Supadata API response into the canonical dict shape.

        Supadata returns different field names depending on the endpoint.
        This method abstracts that so callers get a consistent output.
        """
        # Most Supadata responses contain a nested "video" or "data" object.
        # Adapt as needed based on actual API shape; these fields are typical.
        video = data.get("video", data.get("data", data))

        title = video.get("title", "")
        description = video.get("description", "")

        # Transcript segments: may be under "transcript", "segments", or "caption"
        raw_segments: list[dict[str, Any]] = (
            video.get("transcript") or video.get("segments") or video.get("caption") or []
        )

        # Build concatenated full-text transcript
        transcript_parts: list[str] = []
        segments: list[dict[str, Any]] = []
        for seg in raw_segments:
            text = seg.get("text", seg.get("content", ""))
            start = seg.get("start_seconds", seg.get("start", 0.0))
            transcript_parts.append(text)
            segments.append({"text": text, "start_seconds": start})

        transcript = " ".join(transcript_parts)

        return {
            "title": title,
            "description": description,
            "transcript": transcript,
            "segments": segments,
        }
