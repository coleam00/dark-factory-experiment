"""
Message routes — POST /api/conversations/{conv_id}/messages

Orchestrates the full RAG pipeline:
  1. Verify conversation ownership (404 cross-user, no leak)
  2. Save user message
  3. Embed the query
  4. Retrieve top-K relevant chunks
  5. Build prompt with context
  6. Stream LLM response as SSE
  7. Send sources event before [DONE]
  8. Persist assistant message after stream completes
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend import rate_limit
from backend.auth.dependencies import get_current_user
from backend.config import TRANSCRIPT_TOOL_ENABLED, TRANSCRIPT_TOOL_MAX_PER_TURN
from backend.db import repository
from backend.llm.openrouter import stream_chat
from backend.rag.embeddings import embed_text
from backend.rag.retriever_hybrid import retrieve_hybrid
from backend.rag.tools import TOOL_SCHEMAS, execute_tool, serialize_tool_result

logger = logging.getLogger(__name__)

router = APIRouter()


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, description="Message content (non-empty)")

    @field_validator("content", mode="before")
    @classmethod
    def content_not_whitespace_only(cls, v: str) -> str:
        if isinstance(v, str) and v.strip() == "":
            raise ValueError("content must not be empty or whitespace-only")
        return v


# ---------------------------------------------------------------------------
# POST /api/conversations/{conv_id}/messages
# ---------------------------------------------------------------------------


@router.post("/conversations/{conv_id}/messages")
async def create_message(
    conv_id: str,
    body: MessageCreate,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Send a user message and stream the RAG-grounded assistant response.

    Returns:
        StreamingResponse with Content-Type: text/event-stream
        Each SSE event: "data: <token>\n\n"
        Final event: "data: [DONE]\n\n"
    """
    user_id = str(current_user["id"])

    # 1. Verify conversation exists AND belongs to current user.
    # 404 (not 403) — don't leak existence of other users' conversations.
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 2. Enforce the 25 msg / user / 24h cap (MISSION §10 invariant #1).
    #    Must run BEFORE any LLM or DB write so a rate-limited user cannot
    #    consume OpenRouter budget or leave an orphan user-message row. The
    #    audit row is inserted inside `check_and_record` on pass — partial
    #    streams still count, users can't game the counter by aborting.
    try:
        await rate_limit.check_and_record(user_id)
    except rate_limit.RateLimitExceeded as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "limit": rate_limit.DAILY_MESSAGE_CAP,
                "window_hours": rate_limit.WINDOW_HOURS,
                "reset_at": exc.reset_at.isoformat(),
            },
        )

    # Content is already validated non-empty by Pydantic; strip for storage
    user_content = body.content.strip()

    # 3. Persist the user message. create_message re-checks ownership atomically
    # so a race between the check above and insert can't leak cross-user.
    inserted = await repository.create_message(
        conversation_id=conv_id,
        user_id=user_id,
        role="user",
        content=user_content,
    )
    if inserted is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 4. Retrieve conversation history for LLM context
    all_messages = await repository.list_messages(conv_id, user_id=user_id)
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in all_messages]

    # 5. Embed the user query and retrieve relevant chunks
    context = ""
    chunks: list[dict] = []
    retrieval_failed = False
    try:
        query_embedding = await asyncio.to_thread(embed_text, user_content)
        chunks = await retrieve_hybrid(user_content, query_embedding, top_k=5)
        if chunks:
            context = _format_context(chunks)
    except asyncio.CancelledError:
        raise  # Must propagate to FastAPI for proper cancellation handling
    except Exception as exc:
        logger.warning("RAG retrieval failed (continuing without context): %s", exc)
        retrieval_failed = True

    # Build citation objects for the SSE sources event
    source_citations: list[dict] = [
        {
            "chunk_id": c.get("chunk_id", ""),
            "video_id": c.get("video_id", ""),
            "video_title": c.get("video_title", ""),
            "video_url": c.get("video_url", ""),
            "start_seconds": c.get("start_seconds", 0.0),
            "end_seconds": c.get("end_seconds", 0.0),
            "snippet": c.get("snippet", ""),
        }
        for c in chunks
        if c.get("chunk_id")
    ]

    # Attach retrieval status to citations so frontend can warn user
    if retrieval_failed:
        for citation in source_citations:
            citation["retrieval_failed"] = True

    # 6a. Prepare tool-use plumbing. The executor is a closure that mutates
    # tool_chunks_acc so the outer handler can merge tool-loaded chunks into
    # source_citations once streaming finishes. The whitelist prevents the
    # model from requesting ids that aren't actually in the library.
    tool_chunks_acc: list[dict] = []
    tools_param: list[dict] | None = None
    executor = None
    max_tool_calls = 0
    if TRANSCRIPT_TOOL_ENABLED:
        try:
            all_videos = await repository.list_videos()
            video_id_whitelist: set[str] = {v["id"] for v in all_videos if v.get("id")}
        except Exception as exc:
            logger.warning(
                "Failed to load video whitelist for transcript tool; disabling tool for this turn: %s",
                exc,
            )
            video_id_whitelist = set()
        if video_id_whitelist:

            async def _executor(name: str, raw_args: str) -> str:
                result = await execute_tool(name, raw_args, video_id_whitelist=video_id_whitelist)
                if result.get("ok") and result.get("chunks"):
                    tool_chunks_acc.extend(result["chunks"])
                return serialize_tool_result(result)

            tools_param = TOOL_SCHEMAS
            executor = _executor
            max_tool_calls = TRANSCRIPT_TOOL_MAX_PER_TURN

    # 6b. Stream the response
    async def event_generator() -> AsyncGenerator[str, None]:
        full_response = []
        try:
            async for sse_chunk in stream_chat(
                llm_messages,
                context=context,
                tools=tools_param,
                tool_executor=executor,
                max_tool_calls=max_tool_calls,
            ):
                # Intercept [DONE] to inject the sources event first. Merge
                # any chunks the model loaded via tool calls into
                # source_citations so citation chips include them.
                if sse_chunk == "data: [DONE]\n\n":
                    if tool_chunks_acc:
                        existing_ids = {
                            c.get("chunk_id") for c in source_citations if c.get("chunk_id")
                        }
                        for tc in tool_chunks_acc:
                            tc_id = tc.get("chunk_id")
                            if tc_id and tc_id not in existing_ids:
                                source_citations.append(tc)
                                existing_ids.add(tc_id)
                    if source_citations:
                        sources_json = json.dumps(source_citations)
                        yield f"event: sources\ndata: {sources_json}\n\n"
                full_response.append(sse_chunk)
                yield sse_chunk
        finally:
            # 7. Persist the complete assistant message
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
                try:
                    await repository.create_message(
                        conversation_id=conv_id,
                        user_id=user_id,
                        role="assistant",
                        content=assistant_text,
                        sources=source_citations if source_citations else None,
                    )
                    # Auto-generate title on first assistant reply
                    await _maybe_set_conversation_title(conv_id, user_id, user_content)
                except Exception as exc:
                    logger.error("Failed to persist assistant message: %s", exc)
                    raise  # Re-raise to surface the error to FastAPI

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a context block with video title and
    timestamp citations (mm:ss markers) to help the LLM ground its answer."""
    parts = []
    for chunk in chunks:
        video_title = chunk.get("video_title", "Unknown Video")
        content = chunk.get("content", "")
        start_s = chunk.get("start_seconds", 0.0)
        mins, secs = divmod(int(start_s), 60)
        timestamp = f"{mins:02d}:{secs:02d}"
        parts.append(f"[Source: {video_title} at {timestamp}]\n{content}")
    return "\n\n---\n\n".join(parts)


def _extract_text_from_sse(sse_chunks: list[str]) -> str:
    """
    Reconstruct the full assistant text from a list of SSE event strings.
    Each chunk looks like "data: <json-encoded-token>\n\n".
    Tokens are JSON-encoded strings to safely handle newlines and special characters.
    """
    tokens = []
    for chunk in sse_chunks:
        if not chunk.startswith("data: "):
            continue
        content = chunk[len("data: ") :].rstrip("\n")
        if not content or content == "[DONE]":
            continue
        # Skip JSON error payloads
        if content.startswith('{"error"'):
            continue
        # Try to decode JSON-encoded token (new format)
        try:
            decoded = json.loads(content)
            if isinstance(decoded, str):
                tokens.append(decoded)
            # If it's something else (shouldn't happen), skip it
        except ValueError:
            # Fallback: treat as raw text (backward compat with unencoded tokens)
            tokens.append(content)
    return "".join(tokens)


async def _maybe_set_conversation_title(
    conv_id: str, user_id: str, first_user_message: str
) -> None:
    """
    If the conversation title is still the default, auto-generate one from
    the first user message (simple truncation for Sprint 2; LLM-based in Sprint 6).
    """
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        return
    if conv.get("title") == "New Conversation":
        if len(first_user_message) > 50:
            title = first_user_message[:47].strip() + "…"
        else:
            title = first_user_message.strip()
        await repository.update_conversation_title(conv_id, user_id=user_id, title=title)
