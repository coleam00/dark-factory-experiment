"""
Regenerate route — POST /api/conversations/{conv_id}/regenerate

Re-streams the last assistant response after deleting the old one.
Mirrors the streaming, tool, citation, and persistence logic from
messages.py without modifying the protected path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from backend import rate_limit
from backend.auth.dependencies import get_current_user
from backend.config import CITATIONS_MAX_COUNT, LLM_TOOLS_ENABLED, LLM_TOOLS_MAX_PER_TURN
from backend.db import regenerate_repo, repository
from backend.llm.openrouter import stream_chat
from backend.rag.citations import CitationMarkerStripper, extract_cited_chunk_ids
from backend.rag.tools import TOOL_SCHEMAS, execute_tool, serialize_tool_result
from backend.routes.messages import (
    router,
    _strip_markers_from_sse_chunk,
    _extract_text_from_sse,
    _is_refusal,
    _collapse_by_video,
)

logger = logging.getLogger(__name__)


@router.post("/conversations/{conv_id}/regenerate")
async def regenerate_message(
    conv_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Regenerate the most recent assistant message.

    Deletes the last assistant response, then re-runs the LLM stream
    using the conversation history up to the last user turn. Counts
    against the daily message cap.
    """
    user_id = str(current_user["id"])

    # 1. Ownership check
    conv = await repository.get_conversation(conv_id, user_id=user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 2. Verify there is an assistant message to regenerate BEFORE consuming a rate-limit slot.
    #    check_and_record commits the audit row atomically; a 409 afterwards would waste a slot.
    if not await regenerate_repo.has_last_assistant_message(conv_id, user_id):
        raise HTTPException(status_code=409, detail="No assistant message to regenerate")

    # 3. Rate limit — same cap as normal sends
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

    # 4. Delete the last assistant message (re-check handles rare concurrent-request races)
    deleted = await regenerate_repo.delete_last_assistant_message(conv_id, user_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="No assistant message to regenerate")

    # 5. Load history (now sans the deleted assistant turn)
    all_messages = await repository.list_messages(conv_id, user_id=user_id)
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in all_messages]

    # 6. Tool plumbing (same as messages.py)
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

        is_member_for_turn = bool(current_user.get("is_member", False))

        async def _executor(name: str, raw_args: str) -> str:
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

    # 7. Stream
    async def event_generator() -> AsyncGenerator[str, None]:
        full_response: list[str] = []
        final_text_buf: list[str] = []
        marker_stripper = CitationMarkerStripper()
        try:
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
                    tail = marker_stripper.flush()
                    if tail:
                        tail_chunk = f"data: {json.dumps(tail)}\n\n"
                        full_response.append(tail_chunk)
                        yield tail_chunk
                    if tool_chunks_acc:
                        seen: set[str] = set()
                        for tc in tool_chunks_acc:
                            tc_id = tc.get("chunk_id")
                            if tc_id and tc_id not in seen:
                                source_citations.append(tc)
                                seen.add(tc_id)
                    if source_citations:
                        final_text_raw = final_text_buf[0] if final_text_buf else ""
                        cited_ids = extract_cited_chunk_ids(final_text_raw)
                        for chunk in source_citations:
                            chunk["is_cited"] = chunk.get("chunk_id") in cited_ids
                        source_citations[:] = _collapse_by_video(source_citations)
                        cited = [c for c in source_citations if c.get("is_cited")]
                        uncited = [c for c in source_citations if not c.get("is_cited")]
                        source_citations[:] = cited + uncited[:CITATIONS_MAX_COUNT]
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
                    continue
                full_response.append(stripped)
                yield stripped
        finally:
            assistant_text = _extract_text_from_sse(full_response)
            if assistant_text:
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
