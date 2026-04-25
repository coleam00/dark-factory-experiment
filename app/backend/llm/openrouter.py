"""OpenRouter streaming chat completions wrapper.

Streams tokens from anthropic/claude-sonnet-4.6 via OpenRouter, with optional
tool-use support (e.g. `get_video_transcript` in backend.rag.tools).

`stream_chat(messages, context, tools=None, tool_executor=None, max_tool_calls=0)`
yields SSE strings `data: <token>\\n\\n`. When tools are passed, runs a
multi-turn loop: stream tokens, execute tool_calls, feed results back,
continue until finish_reason=stop. Terminates with `data: [DONE]\\n\\n`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, cast

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from backend.config import (
    CATALOG_ENABLED,
    CATALOG_TIER,
    CHAT_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from backend.rag import catalog

logger = logging.getLogger(__name__)

_async_client: AsyncOpenAI | None = None


def _get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    return _async_client


_BASE_SYSTEM_PROMPT = """\
You are a helpful assistant with access to transcripts from a YouTube creator's video library. You answer questions by retrieving grounded content from that library via the tools below.

When you reference a video, use its title only. Never write YouTube video IDs or raw source identifiers in your prose — the UI renders sources separately as clickable chips, so inline tokens like "(Source: Video HAkSUBdsd6M)" are redundant clutter. The one structured exception is the citation marker described below.

CITATIONS — MANDATORY when grounding a claim in retrieved content:
After any sentence that draws on a retrieved chunk, append the marker `[c:<chunk_id>]` immediately at the end of that sentence, using the literal `chunk_id` value from the tool result. Cite only chunks you actually used to support that specific claim — chunks you retrieved but did not lean on are context, not citations, and must NOT receive a marker. Multiple chunks supporting the same sentence stack: `[c:abc][c:def]`. Do not cite training knowledge — markers are for retrieved chunks only. The UI strips these markers from the displayed text and uses them to render a focused "Sources cited" list, with the broader retrieval kept as an expandable transparency layer.

Answer based ONLY on content you retrieved via your tools. If your searches return no relevant material, clearly and briefly decline. When declining because the library does not cover the topic, include this exact phrase in your reply: "the video library does not cover that topic". The exact phrasing is important — the UI relies on it to suppress misleading source citations on off-topic questions. Keep the decline short (two to three sentences) and do not invent content."""


_TOOL_GUIDANCE = """\

You have four retrieval tools. You MUST call at least one before answering any question about the library content:

- `search_videos(query, top_k=10)` — hybrid search (keyword + semantic via RRF). Your default. Start here.
- `keyword_search_videos(query, top_k=10)` — exact-term matching (tsvector FTS). Best for proper nouns, acronyms, literal phrases.
- `semantic_search_videos(query, top_k=10)` — conceptual similarity (vector cosine). Best when the user's wording may not match transcripts literally.
- `get_video_transcript(video_id)` — full timestamped transcript of one video. Call only after a search surfaced a video and you need its full arc. Expensive — use sparingly.

Strategy:
- Default to `search_videos` unless the question clearly calls for keyword or semantic specifically.
- If the first call returns insufficient or irrelevant context, issue another with a better query or a different strategy.
- Reach for `get_video_transcript` only when chunk-level results are clearly not enough.
- You have {max_per_turn} tool calls total per user turn. Spend them deliberately."""


SYSTEM_PROMPT_TEMPLATE = _BASE_SYSTEM_PROMPT


async def build_system_prompt(max_tool_calls: int = 0) -> list[dict]:
    """Build the system prompt as a list of content blocks.

    Returns a multi-block array suitable for ``{"role": "system", "content": [...]}``.
    When ``CATALOG_ENABLED`` is true and videos exist, a catalog block with
    ``cache_control`` is appended so Anthropic can cache the static content.
    """
    text = _BASE_SYSTEM_PROMPT
    if max_tool_calls > 0:
        text += _TOOL_GUIDANCE.format(max_per_turn=max_tool_calls)

    blocks: list[dict] = [{"type": "text", "text": text}]

    if CATALOG_ENABLED:
        videos = await catalog.get_catalog()
        if videos:
            blocks.append(catalog.build_catalog_block(videos, CATALOG_TIER))
            return blocks

    # No catalog block — anchor cache on the base block instead
    blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


ToolExecutor = Callable[[str, str], Awaitable[str]]
"""(tool_name, raw_arguments_json) -> tool result string (role: tool content)."""


def _extract_tool_subject(tool_name: str, tool_args_raw: str) -> str:
    """Extract a human-readable subject from tool arguments for status events.

    Returns a short string suitable for display in a "Searching: ..." indicator.
    Returns "" on parse failure or unknown tool — callers always get a safe value.
    """
    try:
        args = json.loads(tool_args_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("_extract_tool_subject: parse error for %r: %s", tool_args_raw, exc)
        return ""
    if not isinstance(args, dict):
        return ""
    if tool_name in ("search_videos", "keyword_search_videos", "semantic_search_videos"):
        return str(args.get("query", ""))
    if tool_name == "get_video_transcript":
        return str(args.get("video_id", ""))
    return ""


async def stream_chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_calls: int = 0,
    final_text_out: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream a chat completion via OpenRouter. When tools + executor are
    supplied, execute tool calls in a loop until finish_reason=stop.

    All RAG retrieval is expected to happen via tools; no pre-retrieved
    context is injected into the system prompt.

    If ``final_text_out`` is supplied, the function appends exactly one
    string to it: the assistant's text from the *final* round (i.e. the
    round whose finish_reason was ``stop``, not an intermediate
    tool_calls round). Callers use this for refusal detection so
    inter-round commentary like "let me try a different search" does
    not trigger false positive refusals.
    """
    client = _get_async_client()
    tools_active = bool(tools) and tool_executor is not None and max_tool_calls > 0
    system_blocks = await build_system_prompt(max_tool_calls=max_tool_calls if tools_active else 0)

    full_messages: list[ChatCompletionMessageParam] = [
        # openai stubs don't model list-content system messages; runtime accepts it.
        {"role": "system", "content": system_blocks},  # type: ignore[misc,list-item]
        *cast(list[ChatCompletionMessageParam], messages),
    ]
    base_kwargs: dict[str, Any] = {
        "model": CHAT_MODEL,
        "stream": True,
        # Explicit output cap. Without this, OpenRouter applies its own default
        # (historically 4096 for Anthropic via the OpenAI-compat shim), which
        # broad queries can exhaust: many tool_call rounds each serialize JSON
        # args into the output budget, and on the final round the model has
        # nothing left, returning finish_reason=length with zero visible
        # content. Silent ~10%/24h failures in prod traced back to this.
        "max_tokens": 8192,
    }
    if tools_active:
        base_kwargs["tools"] = tools

    tool_calls_made = 0
    tokens_yielded = 0
    round_num = 0
    # Heartbeat cadence: Kimi K2.6 regularly goes 60-140s of silent tool-call
    # streaming + tool execution before emitting the first user-visible text
    # token. Browsers and reverse proxies idle-timeout SSE connections after
    # ~60s of no bytes. Emitting SSE comment lines (`: <text>\n\n`) every few
    # seconds keeps the socket warm. Comments are spec-valid SSE that clients
    # ignore, so the frontend is unaffected.
    HEARTBEAT_INTERVAL_SECONDS = 5.0
    last_heartbeat_at = time.monotonic()

    def _heartbeat_due() -> bool:
        return (time.monotonic() - last_heartbeat_at) >= HEARTBEAT_INTERVAL_SECONDS

    cap_reached_continuation_appended = False
    try:
        while True:
            round_num += 1
            # Once the per-turn cap has been reached, force the model to
            # compose a final answer instead of calling more tools. We must
            # NOT strip `tools` from the request: the conversation history
            # already contains tool_use/tool_result blocks, and Anthropic's
            # API (via OpenRouter) returns finish_reason=stop with zero
            # content tokens when it sees tool-use context but no declared
            # tools. So we keep tools declared and set tool_choice="none".
            #
            # Empirically, tool_choice="none" alone is *necessary but not
            # sufficient* — Sonnet 4.6 still sometimes returns
            # finish_reason=stop with zero content on long tool histories.
            # The bulletproof fix is to also append a synthetic user message
            # explicitly asking for the final answer once the cap is hit.
            # This nudges the model out of "I'll keep gathering" mode into
            # "compose now" mode. The synthetic message is only appended
            # once per turn so we don't loop adding more.
            kwargs = dict(base_kwargs)
            if tools_active and tool_calls_made >= max_tool_calls:
                kwargs["tool_choice"] = "none"
                if not cap_reached_continuation_appended:
                    full_messages.append(
                        cast(
                            ChatCompletionMessageParam,
                            {
                                "role": "user",
                                "content": (
                                    "You have reached your tool-call budget for this turn "
                                    "and may not call any more tools. Please answer my original "
                                    "question now using only the search results above. Do not "
                                    "ask me follow-up questions and do not announce that you are "
                                    "answering — just produce the final answer."
                                ),
                            },
                        )
                    )
                    cap_reached_continuation_appended = True
            stream = await client.chat.completions.create(messages=full_messages, **kwargs)
            assistant_text_parts: list[str] = []
            round_content_deltas = 0
            # Tool call deltas arrive as fragments keyed by index; accumulate.
            pending: dict[int, dict[str, Any]] = {}
            finish_reason: str | None = None

            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                if delta and delta.content:
                    assistant_text_parts.append(delta.content)
                    tokens_yielded += 1
                    round_content_deltas += 1
                    yield f"data: {json.dumps(delta.content)}\n\n"
                    last_heartbeat_at = time.monotonic()
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        slot = pending.setdefault(
                            tc.index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.type:
                            slot["type"] = tc.type
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
                    # Emit a keepalive while the model streams tool_call args.
                    # No content token is arriving during this phase, so
                    # without this the socket can go silent for 30+ seconds
                    # on long tool-call sequences.
                    if _heartbeat_due():
                        yield ": keepalive\n\n"
                        last_heartbeat_at = time.monotonic()

            logger.info(
                "stream_chat round=%d finish_reason=%s content_deltas=%d tool_calls_pending=%d tool_calls_made=%d",
                round_num,
                finish_reason,
                round_content_deltas,
                len(pending),
                tool_calls_made,
            )

            if finish_reason == "tool_calls" and pending and tool_executor:
                assistant_text = "".join(assistant_text_parts)
                ordered = [pending[i] for i in sorted(pending.keys())]
                full_messages.append(
                    cast(
                        ChatCompletionMessageParam,
                        {
                            "role": "assistant",
                            "content": assistant_text or None,
                            "tool_calls": ordered,
                        },
                    )
                )
                for tc in ordered:
                    # Tool execution (embedding + DB queries) can take a few
                    # seconds per call. Emit a keepalive right before we
                    # await it so browsers/proxies don't idle-timeout the
                    # socket while the coroutine is suspended.
                    yield ": keepalive\n\n"
                    last_heartbeat_at = time.monotonic()
                    tool_name = tc["function"]["name"]
                    tool_args_raw = tc["function"]["arguments"]
                    if tool_calls_made < max_tool_calls:
                        subject = _extract_tool_subject(tool_name, tool_args_raw)
                        yield (
                            "event: status\n"
                            f"data: {json.dumps({'type': 'tool_call_start', 'tool': tool_name, 'subject': subject})}\n\n"
                        )
                        try:
                            payload = await tool_executor(tool_name, tool_args_raw)
                        except Exception as exc:
                            logger.warning("tool executor raised: %s", exc, exc_info=True)
                            payload = f"Error: tool execution failed: {exc}"
                        yield (
                            "event: status\n"
                            f"data: {json.dumps({'type': 'tool_call_done', 'tool': tool_name})}\n\n"
                        )
                    else:
                        payload = (
                            f"Error: per-turn tool call cap ({max_tool_calls}) reached. "
                            "No more tool calls will be executed for this user turn."
                        )
                    tool_calls_made += 1
                    full_messages.append(
                        cast(
                            ChatCompletionMessageParam,
                            {"role": "tool", "tool_call_id": tc["id"], "content": payload},
                        )
                    )
                continue

            # Final round reached (finish_reason is not tool_calls, or pending/
            # executor missing). Stash the final-round text for the caller's
            # refusal check.
            if final_text_out is not None:
                final_text_out.append("".join(assistant_text_parts))
            if round_content_deltas == 0:
                logger.warning(
                    "stream_chat final round emitted zero content tokens "
                    "(round=%d finish_reason=%s tool_calls_made=%d). "
                    "Caller will persist nothing; user will see empty answer.",
                    round_num,
                    finish_reason,
                    tool_calls_made,
                )
            break

        yield "data: [DONE]\n\n"

    except (APIError, APIConnectionError, APIStatusError) as exc:
        logger.error("OpenRouter streaming API error: %s", exc)
        if tokens_yielded == 0:
            raise RuntimeError(f"OpenRouter streaming failed: {exc}") from exc
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    except Exception as exc:
        logger.error("Unexpected error during streaming: %s", exc)
        if tokens_yielded == 0:
            raise RuntimeError(f"Streaming failed unexpectedly: {exc}") from exc
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
