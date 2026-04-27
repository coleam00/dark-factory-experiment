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
    LLM_REASONING_EFFORT,
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

When you reference a video, use its title only. Never write YouTube video IDs or raw source identifiers as commentary in your prose — phrasings like "(Source: Video HAkSUBdsd6M)" or "[Building AI Agents]" inline are redundant clutter; the UI renders sources separately as clickable chips. The structured citation marker described in CITATIONS below is the one exception: it is a UI protocol token, not exposition, and is stripped before the user sees the text.

Answer based ONLY on content you retrieved via your tools. If your searches return no relevant material, clearly and briefly decline. When declining because the library does not cover the topic, include this exact phrase in your reply: "the video library does not cover that topic". The exact phrasing is important — the UI relies on it to suppress misleading source citations on off-topic questions. Keep the decline short (two to three sentences) and do not invent content.

PARTIAL COVERAGE — when retrieved chunks confirm the topic IS in the library but cover it only briefly (you found the right video, but the chunks alone don't expand every fine-grained detail), give the high-level answer the chunks support and end with a pointer to the cited video for the deeper breakdown. Do NOT apologize and do NOT hedge. Forbidden openers and qualifiers in this case: "Unfortunately, the search results don't contain…", "I don't have enough information about…", "The search results are limited…", "Without more detail I can't…". Those phrasings are misleading when a real citation chip is rendering — the user can click it to watch the full segment. Trust the chip. Replace those hedges with confident pointers, for example: "For the full walkthrough of each step, see the cited video." or "Watch the cited segment for the deeper breakdown." This partial-coverage rule does NOT override the refusal rule above: if retrieval returns nothing genuinely relevant, still use the refusal phrase. Use this rule only when at least one retrieved chunk substantively addresses the asked topic."""


_TOOL_GUIDANCE = """\

You have four retrieval tools. You MUST call at least one before answering any question about the library content:

- `search_videos(query, top_k=10)` — hybrid search (keyword + semantic via RRF). Your default. Start here.
- `keyword_search_videos(query, top_k=10)` — exact-term matching (tsvector FTS). Best for proper nouns, acronyms, literal phrases.
- `semantic_search_videos(query, top_k=10)` — conceptual similarity (vector cosine). Best when the user's wording may not match transcripts literally.
- `get_video_transcript(video_id)` — full timestamped transcript of one video. Use this whenever search confidently identifies a single video that addresses the asked topic. Chunk-level search results often skip the elaboration the full transcript captures, so the transcript call is what lets you give precise, accurate detail rather than a partial guess.

Strategy:
- Default to `search_videos` unless the question clearly calls for keyword or semantic specifically.
- If the first call returns insufficient or irrelevant context, issue another with a better query or a different strategy.
- When two or more search chunks come from the same video and that video clearly addresses the asked topic, your next step is `get_video_transcript(video_id)` on that video. This is the expected flow, not an escape hatch — chunks alone routinely lack fine-grained detail (acronym expansions, step-by-step breakdowns, exact terminology), and the full transcript fixes that. Skipping this step on a single-winner question leads to confidently wrong answers from partial chunks.
- **Catalog identifiers — match against the catalog, do not search for them.** When the user names a video by a curriculum identifier ("lesson 1.6", "module 7", "AI Agent Mastery course lesson 3", "workshop #4", "the Knowledge Graphs workshop"), the chunk search index will NOT match — the index is built from spoken transcript content only, with no titles, lesson numbers, module numbers, or workshop labels in it. The right move is: scan the catalog block above for the matching video title, lift its `id=...` value, and call `get_video_transcript(video_id)` directly. Treat catalog identifiers as a routing hint into the catalog, not as a search query. Issuing `search_videos("lesson 1.6")` is a wasted call because no chunk content contains the words "lesson 1.6".
- For broad questions that genuinely span many videos (no clear single winner), more `search_videos` calls with refined queries beat one transcript dump — transcript is for going deep on a confidently-identified video.
- You have {max_per_turn} tool calls total per user turn. Spend them deliberately.

CITATIONS — REQUIRED, NOT OPTIONAL.

Every chunk in a tool result begins with a marker like `[c:abc123]`. After
any sentence in your answer that draws from a retrieved chunk, append the
SAME marker that appeared at the start of that chunk. Markers are stripped
from the displayed text by the UI and used to render the prominent
"Sources cited" list. Without the marker the user sees no citation chip
for that chunk.

Worked example.

  Tool result you receive:

      [c:abc123] Pydantic AI Tutorial at 03:15
      The key is to define your data models first using Pydantic.

      ---

      [c:def456] Pydantic AI Tutorial at 05:42
      Once you have your models, you can pass them to the agent as the
      result_type parameter.

  Your answer (markers shown — they will be stripped from what the user sees):

      Cole's approach starts with defining Pydantic data models[c:abc123].
      He then passes those models to the agent as the result_type
      parameter[c:def456].

Rules.
- The marker is required for every sentence grounded in a retrieved chunk.
  No marker = no citation chip renders.
- Cite only chunks you actually drew from. Retrieved chunks you did not lean
  on are context, not citations — do not mark them.
- Multiple chunks supporting the same sentence stack: `[c:abc123][c:def456]`.
- Copy the marker verbatim from the tool result. Do not invent ids.
- Markers are protocol tokens, not exposition: never explain them, never
  describe them, just emit them at sentence end.
- When multiple chunks come from the same video, cite only one marker per video per sentence. The UI automatically collapses same-video citations into a single chip — emitting every chunk marker from a transcript call clutters the raw text without adding information for the user."""


SYSTEM_PROMPT_TEMPLATE = _BASE_SYSTEM_PROMPT


async def build_system_prompt(max_tool_calls: int = 0, is_member: bool = False) -> list[dict]:
    """Build the system prompt as a list of content blocks.

    Returns a multi-block array suitable for ``{"role": "system", "content": [...]}``.
    When ``CATALOG_ENABLED`` is true and videos exist, a catalog block with
    ``cache_control`` is appended so Anthropic can cache the static content.

    The catalog block is filtered by ``is_member``: non-members see only
    ``source_type='youtube'`` videos, mirroring the retrieval-layer ACL
    (issue #147). Without this filter, non-members would see every paid
    Dynamous lesson title and id in the cached prompt — defense-in-depth
    blocks transcript retrieval, but the model can still leak titles and
    ids in its prose by referring to "the catalog".
    """
    text = _BASE_SYSTEM_PROMPT
    if max_tool_calls > 0:
        text += _TOOL_GUIDANCE.format(max_per_turn=max_tool_calls)

    blocks: list[dict] = [{"type": "text", "text": text}]

    if CATALOG_ENABLED:
        videos = await catalog.get_catalog()
        if not is_member:
            videos = [v for v in videos if v.get("source_type", "youtube") == "youtube"]
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
    is_member: bool = False,
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

    ``is_member`` flows through to ``build_system_prompt`` so the catalog
    block (issue #147) only lists YouTube videos for non-members.
    """
    client = _get_async_client()
    tools_active = bool(tools) and tool_executor is not None and max_tool_calls > 0
    system_blocks = await build_system_prompt(
        max_tool_calls=max_tool_calls if tools_active else 0,
        is_member=is_member,
    )

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
    if LLM_REASONING_EFFORT:
        # OpenRouter normalises this across providers — for Gemini 3 Flash,
        # "minimal" maps to thinking disabled (~190+ tok/s); for OpenAI o-series
        # it maps to lowest reasoning budget. Anthropic ignores it.
        base_kwargs["extra_body"] = {"reasoning": {"effort": LLM_REASONING_EFFORT}}
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
