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
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, cast

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from backend.config import CHAT_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

_async_client: AsyncOpenAI | None = None


def _get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    return _async_client


_BASE_SYSTEM_PROMPT = """\
You are a helpful assistant with access to transcripts from a YouTube creator's video library. You answer questions by retrieving grounded content from that library via the tools below.

When you reference a video, use its title only. Never write YouTube video IDs, chunk IDs, or raw source identifiers in your prose — the UI renders sources separately as clickable chips, so inline tokens like "(Source: Video HAkSUBdsd6M)" are redundant clutter.

Answer based ONLY on content you retrieved via your tools. If your searches return no relevant material, say so honestly — do not invent content."""


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


def build_system_prompt(max_tool_calls: int = 0) -> str:
    """Build the system prompt. Appends tool-use guidance when a positive cap
    is supplied; returns just the base prompt otherwise (diagnostic rollback)."""
    prompt = _BASE_SYSTEM_PROMPT
    if max_tool_calls > 0:
        prompt += _TOOL_GUIDANCE.format(max_per_turn=max_tool_calls)
    return prompt


ToolExecutor = Callable[[str, str], Awaitable[str]]
"""(tool_name, raw_arguments_json) -> tool result string (role: tool content)."""


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
    system_prompt = build_system_prompt(max_tool_calls=max_tool_calls if tools_active else 0)

    full_messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        *cast(list[ChatCompletionMessageParam], messages),
    ]
    base_kwargs: dict[str, Any] = {"model": CHAT_MODEL, "stream": True}
    if tools_active:
        base_kwargs["tools"] = tools

    tool_calls_made = 0
    tokens_yielded = 0
    try:
        while True:
            # Once the per-turn cap has been reached, stop declaring tools on
            # subsequent completions so the model can't keep burning round-trips
            # on tool calls that will all be capped. The final round answers
            # using whatever context the already-executed tools returned.
            kwargs = dict(base_kwargs)
            if tools_active and tool_calls_made >= max_tool_calls:
                kwargs.pop("tools", None)
            stream = await client.chat.completions.create(messages=full_messages, **kwargs)
            assistant_text_parts: list[str] = []
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
                    yield f"data: {json.dumps(delta.content)}\n\n"
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
                    if tool_calls_made >= max_tool_calls:
                        payload = (
                            f"Error: per-turn tool call cap ({max_tool_calls}) reached. "
                            "No more tool calls will be executed for this user turn."
                        )
                    else:
                        try:
                            payload = await tool_executor(
                                tc["function"]["name"], tc["function"]["arguments"]
                            )
                        except Exception as exc:
                            logger.warning("tool executor raised: %s", exc)
                            payload = f"Error: tool execution failed: {exc}"
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
