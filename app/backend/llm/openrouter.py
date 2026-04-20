"""
OpenRouter streaming chat completions wrapper.

Uses the openai SDK pointed at OpenRouter's API to stream responses from
anthropic/claude-sonnet-4.6.

Exposes:
  stream_chat(messages, context) -> AsyncGenerator[str, None]
    Yields SSE-formatted strings: "data: <token>\n\n"

The FastAPI route wraps this generator in a StreamingResponse with
media_type="text/event-stream" and Cache-Control: no-cache.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import cast

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from backend.config import CHAT_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async client (module-level singleton)
# ---------------------------------------------------------------------------

_async_client: AsyncOpenAI | None = None


def _get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
        )
    return _async_client


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """\
You are a helpful assistant with access to transcripts from a YouTube creator's video library.
Answer the user's question based ONLY on the provided video context. If the answer isn't in the context, say so honestly.
When you reference a video, use its title only. Never write YouTube video IDs, chunk IDs, or other raw source identifiers in your response — the UI renders sources separately as clickable chips, so inline tokens like "(Source: Video HAkSUBdsd6M)" or "(Video 60G93MXT4DI)" are redundant clutter. Your prose should read naturally, as if the source list below were invisible.

Context:
{context}"""


def build_system_prompt(context: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(context=context)


# ---------------------------------------------------------------------------
# Streaming generator
# ---------------------------------------------------------------------------


async def stream_chat(
    messages: list[dict],
    context: str = "",
) -> AsyncGenerator[str, None]:
    """
    Stream a chat completion from anthropic/claude-sonnet-4.6 via OpenRouter.

    Args:
        messages: List of {"role": ..., "content": ...} dicts (conversation history).
        context:  RAG context string to inject into the system prompt.

    Yields:
        SSE-formatted strings: "data: <token>\n\n"
        On error, yields a final error event: 'data: {"error": "<message>"}\n\n'

    Raises:
        The generator propagates API exceptions so FastAPI can return HTTP 502/500.
        If the exception is raised *before* any token is yielded, callers receive
        a proper error response. After yielding has started, an error SSE event
        is yielded before the generator closes.
    """
    client = _get_async_client()
    system_prompt = build_system_prompt(context)

    # Prepend system message. The openai SDK wants a union of TypedDicts; we
    # only pass role+content dicts so the shape is fine but mypy can't narrow
    # a plain `list[dict]` into the TypedDict union — cast explicitly.
    full_messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        *cast(list[ChatCompletionMessageParam], messages),
    ]

    tokens_yielded = 0
    try:
        stream = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=full_messages,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                token = delta.content
                tokens_yielded += 1
                # JSON-encode the token so newlines/special chars don't break SSE format
                yield f"data: {json.dumps(token)}\n\n"

        # Signal end of stream
        yield "data: [DONE]\n\n"

    except (APIError, APIConnectionError, APIStatusError) as exc:
        logger.error("OpenRouter streaming API error: %s", exc)
        if tokens_yielded == 0:
            # No content streamed yet — raise so FastAPI returns a proper HTTP error
            raise RuntimeError(f"OpenRouter streaming failed: {exc}") from exc
        else:
            # Already streaming — send an error SSE event
            error_payload = json.dumps({"error": str(exc)})
            yield f"data: {error_payload}\n\n"

    except Exception as exc:
        logger.error("Unexpected error during streaming: %s", exc)
        if tokens_yielded == 0:
            raise RuntimeError(f"Streaming failed unexpectedly: {exc}") from exc
        else:
            error_payload = json.dumps({"error": str(exc)})
            yield f"data: {error_payload}\n\n"
