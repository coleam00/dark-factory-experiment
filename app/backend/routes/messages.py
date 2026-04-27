"""
Message routes — POST /api/conversations/{conv_id}/messages

Orchestrates the tool-driven RAG flow:
  1. Verify conversation ownership (404 cross-user, no leak)
  2. Enforce 25 msg/user/24h cap
  3. Save user message
  4. Load conversation history
  5. Invoke the LLM with retrieval tools declared; the model drives
     retrieval via tool calls (search_videos, keyword_search_videos,
     semantic_search_videos, get_video_transcript). No pre-retrieval.
  6. Stream the response as SSE
  7. Send the sources event (populated from the model's tool calls) before [DONE]
  8. Persist the assistant message after the stream completes
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
from backend.config import CITATIONS_MAX_COUNT, LLM_TOOLS_ENABLED, LLM_TOOLS_MAX_PER_TURN
from backend.db import repository
from backend.llm.openrouter import stream_chat
from backend.rag.citations import (
    CitationMarkerStripper,
    extract_cited_chunk_ids,
)
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

    # 5. Set up tool plumbing. All retrieval happens inside the LLM loop via
    # tool calls — no pre-retrieval runs here. The executor closure collects
    # every chunk returned by any tool call so the final SSE `sources` event
    # lists exactly what the model actually read. The video_id whitelist is
    # only consulted by the transcript tool (it guards against hallucinated
    # ids); the search tools ignore it.
    source_citations: list[dict] = []
    tool_chunks_acc: list[dict] = []
    embedding_cache: dict[str, list[float]] = {}
    tools_param: list[dict] | None = None
    executor = None
    max_tool_calls = 0
    if LLM_TOOLS_ENABLED:
        try:
            all_videos = await repository.list_videos()
            video_id_whitelist: set[str] = {v["id"] for v in all_videos if v.get("id")}
        except Exception as exc:
            logger.warning(
                "Failed to load video whitelist for tool use; transcript tool calls will be unguarded: %s",
                exc,
            )
            video_id_whitelist = set()

        # Captured at the start of the turn so the entire tool sequence sees
        # consistent ACL — even if /me later flips is_member, the in-flight
        # turn won't change behavior mid-flight.
        is_member_for_turn = bool(current_user.get("is_member") or False)

        async def _executor(name: str, raw_args: str) -> str:
            # Pass `None` (not empty set) when the whitelist failed to load so
            # the transcript tool falls back to open lookups instead of rejecting
            # every id.
            whitelist = video_id_whitelist if video_id_whitelist else None
            result = await execute_tool(
                name,
                raw_args,
                video_id_whitelist=whitelist,
                embedding_cache=embedding_cache,
                is_member=is_member_for_turn,
            )
            if result.get("ok") and result.get("chunks"):
                tool_chunks_acc.extend(result["chunks"])
            return serialize_tool_result(result)

        tools_param = TOOL_SCHEMAS
        executor = _executor
        max_tool_calls = LLM_TOOLS_MAX_PER_TURN

    # 6. Stream the response. The model drives retrieval via tool calls;
    # chunks it pulls flow into source_citations via tool_chunks_acc.
    # ``final_text_buf`` receives the assistant's final-round text so the
    # refusal check ignores inter-round commentary ("let me try semantic").
    async def event_generator() -> AsyncGenerator[str, None]:
        full_response: list[str] = []
        final_text_buf: list[str] = []
        # Two-tier citations (issue #176): strip `[c:<id>]` markers from the
        # stream; use them at [DONE] to flag is_cited on retrieved chunks.
        marker_stripper = CitationMarkerStripper()
        try:
            # is_member_for_turn is captured above when tools are wired.
            # Re-bind to a local that's always defined so we can pass it
            # to stream_chat regardless of whether tools are enabled
            # (catalog filtering belongs to the prompt, not the tools).
            turn_is_member = bool(current_user.get("is_member") or False)
            async for sse_chunk in stream_chat(
                llm_messages,
                tools=tools_param,
                tool_executor=executor,
                max_tool_calls=max_tool_calls,
                final_text_out=final_text_buf,
                is_member=turn_is_member,
            ):
                if sse_chunk == "data: [DONE]\n\n":
                    # Flush any text held back as a partial marker.
                    tail = marker_stripper.flush()
                    if tail:
                        tail_chunk = f"data: {json.dumps(tail)}\n\n"
                        full_response.append(tail_chunk)
                        yield tail_chunk
                    # Dedup tool-loaded chunks into source_citations (existing).
                    if tool_chunks_acc:
                        seen: set[str] = set()
                        for tc in tool_chunks_acc:
                            tc_id = tc.get("chunk_id")
                            if tc_id and tc_id not in seen:
                                source_citations.append(tc)
                                seen.add(tc_id)
                    # Mark is_cited from markers in the raw final-round text.
                    # Marker IDs that don't match any retrieved chunk are
                    # silently dropped (hallucinations).
                    if source_citations:
                        final_text_raw = final_text_buf[0] if final_text_buf else ""
                        cited_ids = extract_cited_chunk_ids(final_text_raw)
                        for chunk in source_citations:
                            chunk["is_cited"] = chunk.get("chunk_id") in cited_ids
                        # Collapse same-video chunks (issue #208): keep one
                        # entry per video_id, choosing the earliest-cited
                        # timestamp as the representative.
                        source_citations[:] = _collapse_by_video(source_citations)
                        # Cap fallback (issue #176): cited pass through, non-cited sliced.
                        cited = [c for c in source_citations if c.get("is_cited")]
                        uncited = [c for c in source_citations if not c.get("is_cited")]
                        source_citations[:] = cited + uncited[:CITATIONS_MAX_COUNT]
                    # Suppress sources on refusal (existing behaviour).
                    if source_citations:
                        final_text = (
                            final_text_buf[0]
                            if final_text_buf
                            else _extract_text_from_sse(full_response)
                        )
                        if not _is_refusal(final_text):
                            sources_json = json.dumps(source_citations)
                            yield f"event: sources\ndata: {sources_json}\n\n"
                    full_response.append(sse_chunk)
                    yield sse_chunk
                    continue

                stripped = _strip_markers_from_sse_chunk(sse_chunk, marker_stripper)
                if stripped is None:
                    # Whole token held back as a partial marker.
                    continue
                full_response.append(stripped)
                yield stripped
        finally:
            # 7. Persist the complete assistant message.
            #
            # Shield the DB writes from client-disconnect CancelledError.
            # The tool-driven flow can take 30-60s before any token streams
            # (the model runs several tool rounds before composing the final
            # answer). Some clients/proxies abort long fetches in that window,
            # which cancels this generator's task. Without asyncio.shield, the
            # `await repository.create_message(...)` re-raises CancelledError
            # immediately — and because CancelledError is a BaseException (not
            # Exception) in Python 3.11, it bypasses the try/except below and
            # silently drops the save. Shielding runs the save as a detached
            # task that completes independently of the client's connection
            # state; we catch the re-raised CancelledError so the generator
            # can exit cleanly.
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
                # Apply the same refusal detection used for the live SSE
                # `event: sources` suppression so reloading the conversation
                # later doesn't bring the misleading chip back. Prefer the
                # final-round text when available — it's what the SSE path
                # checks — and fall back to the full reconstructed text if
                # the final_text buffer wasn't populated (e.g. pre-tool
                # flow). If the message is a refusal we persist sources=None
                # regardless of what the tool calls retrieved; the frontend
                # renders the chip based on the stored field, so dropping
                # it here keeps the reload UX consistent with the stream.
                refusal_check_text = final_text_buf[0] if final_text_buf else assistant_text
                sources_to_persist: list[dict] | None = (
                    None
                    if not source_citations or _is_refusal(refusal_check_text)
                    else source_citations
                )
                try:
                    await asyncio.shield(
                        repository.create_message(
                            conversation_id=conv_id,
                            user_id=user_id,
                            role="assistant",
                            content=assistant_text,
                            sources=sources_to_persist,
                        )
                    )
                except asyncio.CancelledError:
                    logger.info(
                        "Client disconnected mid-persist; shielded create_message continues in background"
                    )
                except Exception as exc:
                    logger.error("Failed to persist assistant message: %s", exc)
                    raise
                # Title auto-generation is best-effort: client-disconnect swallowed,
                # unexpected errors logged but not re-raised (message was saved above).
                try:
                    await asyncio.shield(
                        _maybe_set_conversation_title(conv_id, user_id, user_content)
                    )
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Failed to update conversation title: %s", exc)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markers_from_sse_chunk(sse_chunk: str, stripper: CitationMarkerStripper) -> str | None:
    """Run an SSE token chunk through ``stripper``; return a rewritten chunk
    or ``None`` if the entire token content was held back. Non-token chunks
    (events, heartbeats, error payloads) pass through unchanged.
    """
    if not sse_chunk.startswith("data: "):
        return sse_chunk
    payload = sse_chunk[len("data: ") :].rstrip("\n")
    if not payload or payload == "[DONE]" or payload.startswith('{"error"'):
        return sse_chunk
    try:
        decoded = json.loads(payload)
    except ValueError:
        decoded = payload
    if not isinstance(decoded, str):
        return sse_chunk
    safe = stripper.feed(decoded)
    if not safe:
        return None
    return f"data: {json.dumps(safe)}\n\n"


def _extract_text_from_sse(sse_chunks: list[str]) -> str:
    """Reconstruct assistant text from a list of SSE event strings."""
    tokens = []
    for chunk in sse_chunks:
        if not chunk.startswith("data: "):
            logger.debug("Skipping non-data SSE chunk: %r", chunk[:100])
            continue
        content = chunk[len("data: ") :].rstrip("\n")
        if not content or content == "[DONE]" or content.startswith('{"error"'):
            continue
        try:
            decoded = json.loads(content)
            if isinstance(decoded, str):
                tokens.append(decoded)
        except ValueError:
            tokens.append(content)
    return "".join(tokens)


def _is_refusal(text: str) -> bool:
    """
    Returns True if the assistant text explicitly declines to answer because
    the query falls outside the video context.

    This check prevents misleading "Sources (N)" renders when the LLM
    correctly refused to use any retrieved chunks.

    The pattern list is a belt-and-suspenders fallback. The primary
    mechanism is the system prompt instruction in `llm/openrouter.py`
    telling the model to use the exact phrase "the video library does not
    cover that topic" when declining. We include that phrase below plus
    a curated set of natural refusal phrasings observed across Sonnet 4.6
    and Kimi K2.6 responses during E2E validation (issue #158).

    Pattern curation rules — every entry should be specific enough that
    it cannot plausibly appear in a substantive, grounded answer. We
    prefer multi-word phrases anchored to refusal intent over single
    tokens ("couldn't find" alone is too loose; "I couldn't find" scoped
    to first person is safer).
    """
    refusal_patterns = (
        # -- Enforced phrase (system prompt instructs the model to emit this)
        "the video library does not cover that topic",
        # -- Sonnet-era phrasings (existing)
        "not covered in any of the videos",
        "aren't covered in any of the videos",
        "n't covered",
        "n't in the context",
        "n't part of",
        "n't available",
        "n't discussed",
        "not in the context",
        "don't have information about",
        "can't help with that",
        "I can only answer questions about",
        "outside the scope of",
        "don't have access to",
        "not part of my knowledge",
        "haven't been provided",
        "not available in",
        "cannot answer questions about",
        "not able to help",
        # -- Kimi-era phrasings (observed in E2E for issue #158)
        # Categorical denials. We deliberately DO NOT add the shorter phrase
        # "none of the videos" because it plausibly appears in partial answers
        # like "The library covers X, but none of the videos go into Y". The
        # longer phrase "none of the videos in this library" is anchored to a
        # wholesale denial and is what Kimi actually uses on refusals.
        "none of the videos in this library",
        "video library doesn't contain",
        "video library does not contain",
        "the library doesn't contain",
        "the library does not contain",
        # Search-result-specific refusals. We avoid the bare "didn't return
        # any relevant" pattern because it plausibly appears in technical
        # discussion of retrieval benchmarks ("Chroma didn't return any
        # relevant results in the benchmark Cole ran"). Instead we anchor to
        # the natural forms Kimi actually emits on refusals: first-person
        # or determiner-prefixed search framings. See E2E samples in #158.
        "the search didn't return any relevant",
        "the search did not return any relevant",
        "my search didn't return any relevant",
        "my search did not return any relevant",
        "search of the video library didn't return",
        "search of the video library did not return",
        "search of the library didn't return",
        "search of the library did not return",
        "my search returned nothing",
        "the search returned nothing",
        "no relevant material here",
        # "Grounded" framing Kimi uses
        "don't have any grounded",
        "do not have any grounded",
        "no grounded information",
        "no grounded material",
        # First-person search phrasings (scoped to first person to avoid
        # false positives on partial answers that happen to use "couldn't
        # find"). We deliberately do NOT include bare "i didn't find" /
        # "i did not find" — those appear in natural partial-answer nuance
        # clauses like "within those videos I didn't find a specific
        # comparison to X". The "couldn't" forms are rarer outside of
        # outright refusals.
        "i couldn't find",
        "i could not find",
        # Variant of "I can only answer questions about" used by Kimi
        "i can only answer based on",
        "i can only answer using content",
    )
    is_refusal = any(pattern.lower() in text.lower() for pattern in refusal_patterns)
    if is_refusal:
        logger.debug("Refusal detected in assistant response")
    return is_refusal


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


def _collapse_by_video(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse multiple chunks from the same video into a single citation entry.

    After the ``is_cited`` pass, chunks from the same video are redundant in
    the UI — the user needs one clickable chip per video, not one per 5-second
    transcript segment (issue #208).

    For each ``video_id`` group:
    - ``is_cited`` is True if ANY chunk in the group was cited by the LLM.
    - ``start_seconds`` is the earliest timestamp among cited chunks (or among
      all chunks if none were cited), so the deep-link opens near the most
      relevant moment.
    - ``segment_count`` records how many chunks were collapsed so the frontend
      can optionally display "(N segments)".
    - All other fields are taken from the representative chunk.

    Insertion order of videos is preserved (first-seen wins).
    """
    seen: dict[str, list[dict]] = {}
    for c in chunks:
        vid = c.get("video_id") or ""
        if vid not in seen:
            seen[vid] = []
        seen[vid].append(c)

    collapsed: list[dict] = []
    for group in seen.values():
        cited_in_group = [c for c in group if c.get("is_cited")]
        if cited_in_group:
            representative = min(cited_in_group, key=lambda c: c.get("start_seconds") or 0.0)
            is_cited = True
        else:
            representative = min(group, key=lambda c: c.get("start_seconds") or 0.0)
            is_cited = False
        entry = dict(representative)
        entry["is_cited"] = is_cited
        entry["segment_count"] = len(group)
        collapsed.append(entry)

    return collapsed
