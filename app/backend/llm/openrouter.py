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


_TOOL_GUIDANCE = """\

You have access to a tool `get_video_transcript(video_id)` that returns the full timestamped transcript of one video from the library.

Call the tool when:
- The context above does not contain enough information to answer the user's question
- The user is asking about a specific video that the retrieved chunks do not cover in full
- A complete answer requires the arc of a whole video rather than isolated chunks

Do not call the tool when:
- The retrieved context already covers the question
- The question is a simple factual lookup

You may call this tool at most {max_per_turn} times per user turn. Valid video_ids are only those shown in the source citations of the retrieved context above (or in the catalog if one is present)."""


SYSTEM_PROMPT_TEMPLATE = """\
You are a helpful assistant with access to transcripts from a YouTube creator's video library.
Answer the user's question based ONLY on the provided video context. If the answer isn't in the context, say so honestly.
When you reference a video, use its title only. Never write YouTube video IDs, chunk IDs, or other raw source identifiers in your response — the UI renders sources separately as clickable chips, so inline tokens like "(Source: Video HAkSUBdsd6M)" or "(Video 60G93MXT4DI)" are redundant clutter. Your prose should read naturally, as if the source list below were invisible.

Context:
{context}"""


def build_system_prompt(context: str, tool_guidance_max_per_turn: int | None = None) -> str:
    """Build the system prompt. Appends tool-use guidance when a positive cap
    is supplied; omits it otherwise."""
    prompt = SYSTEM_PROMPT_TEMPLATE.format(context=context)
    if tool_guidance_max_per_turn is not None and tool_guidance_max_per_turn > 0:
        prompt += _TOOL_GUIDANCE.format(max_per_turn=tool_guidance_max_per_turn)
    return prompt


ToolExecutor = Callable[[str, str], Awaitable[str]]
"""(tool_name, raw_arguments_json) -> tool result string (role: tool content)."""


async def stream_chat(
    messages: list[dict],
    context: str = "",
    tools: list[dict] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_tool_calls: int = 0,
) -> AsyncGenerator[str, None]:
    """Stream a chat completion via OpenRouter. When tools + executor are
    supplied, execute tool calls in a loop until finish_reason=stop."""
    client = _get_async_client()
    tools_active = bool(tools) and tool_executor is not None and max_tool_calls > 0
    system_prompt = build_system_prompt(
        context, tool_guidance_max_per_turn=max_tool_calls if tools_active else None
    )

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
            stream = await client.chat.completions.create(messages=full_messages, **base_kwargs)
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
