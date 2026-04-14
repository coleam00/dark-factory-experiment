"""
Message routes — POST /api/conversations/{conv_id}/messages

Orchestrates the full RAG pipeline:
  1. Save user message
  2. Embed the query
  3. Retrieve top-K relevant chunks
  4. Build prompt with context
  5. Stream LLM response as SSE
  6. Send sources event before [DONE]
  7. Persist assistant message after stream completes
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend.db import repository
from backend.llm.openrouter import stream_chat
from backend.rag.embeddings import embed_text
from backend.rag.retriever import retrieve

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
async def create_message(conv_id: str, body: MessageCreate):
    """
    Send a user message and stream the RAG-grounded assistant response.

    Returns:
        StreamingResponse with Content-Type: text/event-stream
        Each SSE event: "data: <token>\n\n"
        Final event: "data: [DONE]\n\n"
    """
    # 1. Verify conversation exists
    conv = await repository.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Content is already validated non-empty by Pydantic; strip for storage
    user_content = body.content.strip()

    # 2. Persist the user message
    await repository.create_message(
        conversation_id=conv_id,
        role="user",
        content=user_content,
    )

    # 3. Retrieve conversation history for LLM context
    all_messages = await repository.list_messages(conv_id)
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in all_messages]

    # 4. Embed the user query and retrieve relevant chunks
    context = ""
    chunks: list[dict] = []
    try:
        query_embedding = embed_text(user_content)
        chunks = await retrieve(query_embedding, k=5)
        if chunks:
            context = _format_context(chunks)
    except Exception as exc:
        logger.warning("RAG retrieval failed (continuing without context): %s", exc)

    # Extract unique source video titles for the SSE sources event
    source_titles: list[str] = list(
        dict.fromkeys(c.get("video_title", "") for c in chunks if c.get("video_title"))
    )

    # 5. Stream the response
    async def event_generator() -> AsyncGenerator[str, None]:
        full_response = []
        try:
            async for sse_chunk in stream_chat(llm_messages, context=context):
                # Intercept [DONE] to inject sources event first
                if sse_chunk == "data: [DONE]\n\n" and source_titles:
                    sources_json = json.dumps(source_titles)
                    yield f"event: sources\ndata: {sources_json}\n\n"
                full_response.append(sse_chunk)
                yield sse_chunk
        finally:
            # 6. Persist the complete assistant message
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
                try:
                    await repository.create_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=assistant_text,
                    )
                    # Auto-generate title on first assistant reply
                    await _maybe_set_conversation_title(conv_id, user_content)
                except Exception as exc:
                    logger.error("Failed to persist assistant message: %s", exc)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a context block with video title citations."""
    parts = []
    for chunk in chunks:
        video_title = chunk.get("video_title", "Unknown Video")
        content = chunk.get("content", "")
        parts.append(f"[Source: {video_title}]\n{content}")
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
        except (json.JSONDecodeError, ValueError):
            # Fallback: treat as raw text (backward compat with unencoded tokens)
            tokens.append(content)
    return "".join(tokens)


async def _maybe_set_conversation_title(conv_id: str, first_user_message: str) -> None:
    """
    If the conversation title is still the default, auto-generate one from
    the first user message (simple truncation for Sprint 2; LLM-based in Sprint 6).
    """
    conv = await repository.get_conversation(conv_id)
    if not conv:
        return
    if conv.get("title") == "New Conversation":
        if len(first_user_message) > 50:
            title = first_user_message[:47].strip() + "…"
        else:
            title = first_user_message.strip()
        await repository.update_conversation_title(conv_id, title)
