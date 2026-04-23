"""LLM tool definitions and executor for `get_video_transcript`.

The tool lets the model read a single video's full timestamped transcript
when retrieved chunks are insufficient. Schema is OpenAI-compatible;
OpenRouter translates it to Anthropic tool-use format for Claude. Per-turn
call caps are enforced by the caller — this module is stateless.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.db import repository

logger = logging.getLogger(__name__)


GET_VIDEO_TRANSCRIPT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_video_transcript",
        "description": (
            "Read the full transcript of a single video from the library. "
            "Call when retrieved context is insufficient, when the user asks "
            "about a specific video, or when a comprehensive answer requires "
            "the full arc of a video rather than isolated chunks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {
                    "type": "string",
                    "description": "Internal video_id. Must be a valid id from the library.",
                }
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [GET_VIDEO_TRANSCRIPT_TOOL]


def _format_timestamped_transcript(video: dict, chunks: list[dict]) -> str:
    """Render chunks as a transcript with [mm:ss] markers — same syntax as
    the retrieved-context block so the model sees consistent formatting."""
    title = video.get("title", "Unknown Video")
    parts = [f"# {title}\n"]
    for chunk in chunks:
        start_s = chunk.get("start_seconds") or 0.0
        mins, secs = divmod(int(start_s), 60)
        content = chunk.get("content", "").strip()
        if content:
            parts.append(f"[{mins:02d}:{secs:02d}] {content}")
    return "\n\n".join(parts)


async def execute_get_video_transcript(
    raw_arguments: str | dict,
    video_id_whitelist: set[str] | None = None,
) -> dict[str, Any]:
    """Validate args, look up a video, and return its timestamped transcript.

    Returns ``{"ok": True, "text": str, "chunks": list[dict], ...}`` on success
    (chunks mirror retrieved-chunk shape so caller can merge into source_citations)
    or ``{"ok": False, "error": str}`` on any failure. ``video_id_whitelist=None``
    disables the membership check (useful for tests)."""
    if isinstance(raw_arguments, str):
        try:
            args = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"invalid JSON arguments: {exc}"}
    else:
        args = raw_arguments

    video_id = args.get("video_id") if isinstance(args, dict) else None
    if not isinstance(video_id, str) or not video_id.strip():
        return {"ok": False, "error": "missing required parameter: video_id"}
    video_id = video_id.strip()

    if video_id_whitelist is not None and video_id not in video_id_whitelist:
        return {
            "ok": False,
            "error": (
                f"video_id {video_id!r} is not in the current library. "
                "Only ids from the catalog or from retrieved citations are valid."
            ),
        }

    try:
        video = await repository.get_video(video_id)
    except Exception as exc:
        logger.warning("tool: get_video failed for %s: %s", video_id, exc)
        return {"ok": False, "error": f"failed to look up video: {exc}"}
    if not video:
        return {"ok": False, "error": f"video not found: {video_id}"}

    try:
        chunks = await repository.list_chunks_for_video(video_id)
    except Exception as exc:
        logger.warning("tool: list_chunks_for_video failed for %s: %s", video_id, exc)
        return {"ok": False, "error": f"failed to load chunks: {exc}"}
    if not chunks:
        return {"ok": False, "error": f"no chunks available for video: {video_id}"}

    citation_chunks = [
        {
            "chunk_id": c.get("id", ""),
            "video_id": video_id,
            "video_title": video.get("title", ""),
            "video_url": video.get("url", ""),
            "content": c.get("content", ""),
            "chunk_index": c.get("chunk_index", 0),
            "start_seconds": c.get("start_seconds", 0.0),
            "end_seconds": c.get("end_seconds", 0.0),
            "snippet": c.get("snippet", ""),
        }
        for c in chunks
    ]

    return {
        "ok": True,
        "text": _format_timestamped_transcript(video, chunks),
        "chunks": citation_chunks,
        "video": {"id": video_id, "title": video.get("title", ""), "url": video.get("url", "")},
    }


async def execute_tool(
    name: str,
    raw_arguments: str | dict,
    video_id_whitelist: set[str] | None = None,
) -> dict[str, Any]:
    """Dispatch by tool name. Unknown names return an error dict so the
    model sees the refusal and stops calling."""
    if name == "get_video_transcript":
        return await execute_get_video_transcript(
            raw_arguments, video_id_whitelist=video_id_whitelist
        )
    return {"ok": False, "error": f"unknown tool: {name}"}


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Convert an executor result into the `role: tool` message content."""
    if result.get("ok"):
        return str(result.get("text", ""))
    return f"Error: {result.get('error') or 'tool execution failed'}"
