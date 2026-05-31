"""
Regenerate route — POST /api/conversations/{conv_id}/regenerate

Re-runs the tool-driven RAG flow for the trailing user question and replaces
the most recent assistant answer with a fresh one (its own citations). Mirrors
the streaming/persistence idiom of ``routes/messages.py`` exactly:

  1. Verify conversation ownership (404 cross-user, no leak)
  2. Guard: there must be a trailing assistant message to regenerate (409)
  3. Enforce the 25 msg/user/24h cap (same call as the send path — regenerate
     cannot bypass the limit)
  4. Re-stream the response against the existing history (minus the stale
     answer)
  5. Send the sources event (the model's tool calls) before [DONE]
  6. Persist the new assistant message, THEN delete the old one — so a
     failed/aborted regenerate leaves the prior answer intact (no orphaned
     user-message-with-no-reply).

This lives in a separate (non-protected) module so it can be added without
touching ``routes/messages.py``. It reuses that module's pure helpers
(``_collapse_by_video``, ``_extract_text_from_sse``, ``_is_refusal``,
``_strip_markers_from_sse_chunk``) read-only — importing does not modify them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

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
from backend.routes.messages import (
    _collapse_by_video,
    _extract_text_from_sse,
    _is_refusal,
    _strip_markers_from_sse_chunk,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/conversations/{conv_id}/regenerate
# ---------------------------------------------------------------------------


@router.post("/conversations/{conv_id}/regenerate")
async def regenerate_message(
    conv_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Regenerate the most recent assistant message and stream the replacement.

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

    # 2. There must be a trailing assistant message to regenerate. This runs
    #    BEFORE the rate-limit check so a no-op never consumes the cap.
    all_messages = await repository.list_messages(conv_id, user_id=user_id)
    if not all_messages or all_messages[-1]["role"] != "assistant":
        raise HTTPException(status_code=409, detail="No assistant message to regenerate")
    old_assistant_id = all_messages[-1]["id"]

    # 3. Enforce the 25 msg / user / 24h cap (MISSION §10 invariant #1).
    #    Identical to the send path so regenerate cannot bypass the cap. Runs
    #    before streaming/delete so the old answer is preserved on 429.
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

    # 4. Build LLM history that ENDS at the user's question — drop the stale
    #    assistant answer so the model recomposes a fresh reply.
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in all_messages[:-1]]

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
        is_member_for_turn = bool(current_user.get("is_member", False))

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
            # 7. Persist the NEW assistant message, then delete the old one.
            #
            # Shield the DB writes from client-disconnect CancelledError (see
            # the long note in messages.py:create_message). We persist the
            # replacement first and only delete the stale answer once that
            # succeeds — so an aborted/failed regenerate never leaves a user
            # message without a reply.
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
                # Apply the same refusal detection used for the live SSE
                # `event: sources` suppression so reloading the conversation
                # later doesn't bring the misleading chip back.
                refusal_check_text = final_text_buf[0] if final_text_buf else assistant_text
                sources_to_persist: list[dict] | None = (
                    None
                    if not source_citations or _is_refusal(refusal_check_text)
                    else source_citations
                )
                persisted = None
                try:
                    persisted = await asyncio.shield(
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
                    logger.error("Failed to persist regenerated assistant message: %s", exc)
                    raise
                # Only delete the stale answer once the replacement is saved.
                # If persistence was cancelled/failed we keep the old message
                # so the conversation always has a reply.
                if persisted is not None:
                    try:
                        await asyncio.shield(
                            repository.delete_message(old_assistant_id, user_id)
                        )
                    except asyncio.CancelledError:
                        logger.info(
                            "Client disconnected before stale-answer delete; shielded delete continues in background"
                        )
                    except Exception as exc:
                        logger.warning("Failed to delete stale assistant message: %s", exc)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
