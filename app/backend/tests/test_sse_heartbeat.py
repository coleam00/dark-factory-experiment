"""
Tests for the SSE keepalive heartbeat emitted by `stream_chat` during
silent periods (tool-call argument streaming and tool execution).

Without keepalives, Kimi K2.6 reliably goes 60-140 seconds of zero SSE
bytes on tool-heavy queries while the model streams tool_call args and
we await the executor. Browsers and reverse proxies idle-timeout those
sockets after ~60s, killing the connection before the first user-visible
token arrives. These tests lock in the fix from issue #158 follow-up:
comment-line heartbeats (`: keepalive\n\n`) emitted every few seconds
during silent phases.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch


class _FakeDeltaChunk:
    """Mimics a single chunk in OpenRouter's streaming response."""

    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        self.choices = [choice]


class _FakeToolCallDelta:
    """A tool_call fragment as OpenRouter streams it."""

    def __init__(
        self,
        index: int,
        call_id: str | None = None,
        name: str | None = None,
        arguments: str | None = None,
    ) -> None:
        self.index = index
        self.id = call_id
        self.type = "function" if call_id else None
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _FakeStream:
    """Async iterator over pre-scripted chunks with optional delays between
    emits. Delays exercise the real-world timing where tool_call args
    stream slowly from Kimi."""

    def __init__(self, chunks: list[_FakeDeltaChunk], delay_seconds: float = 0.0):
        self._chunks = chunks
        self._delay = delay_seconds

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield chunk


class TestSseKeepaliveDuringToolCalls:
    async def _collect(self, delay_per_chunk: float, tool_exec_delay: float):
        """Drive `stream_chat` end-to-end with a canned two-round flow.

        Round 1: model streams tool_call args (many chunks, no content).
        Tool executor: slow (simulates embedding + DB query).
        Round 2: model emits final content tokens + finish_reason=stop.

        Returns the list of SSE chunks yielded by stream_chat.
        """
        from backend.llm.openrouter import stream_chat

        # Round 1: many tool_call arg fragments. Each fragment carries no
        # content, mimicking Kimi streaming `{"query": "..."}` one token
        # at a time. Total 12 fragments * delay_per_chunk adds up to the
        # silent window a browser would otherwise idle-timeout on.
        round1_chunks = [
            _FakeDeltaChunk(
                tool_calls=[_FakeToolCallDelta(0, call_id="call_1", name="search_videos")]
            ),
        ]
        for i in range(12):
            round1_chunks.append(
                _FakeDeltaChunk(tool_calls=[_FakeToolCallDelta(0, arguments=f"chunk{i}")])
            )
        round1_chunks.append(_FakeDeltaChunk(finish_reason="tool_calls"))

        # Round 2: final content + stop.
        round2_chunks = [
            _FakeDeltaChunk(content="Answer "),
            _FakeDeltaChunk(content="here."),
            _FakeDeltaChunk(finish_reason="stop"),
        ]

        streams = [
            _FakeStream(round1_chunks, delay_seconds=delay_per_chunk),
            _FakeStream(round2_chunks, delay_seconds=0.0),
        ]
        create_mock = AsyncMock(side_effect=streams)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        async def slow_executor(name: str, raw_args: str) -> str:
            await asyncio.sleep(tool_exec_delay)
            return "tool result payload"

        emitted: list[str] = []
        with (
            patch("backend.llm.openrouter._get_async_client", return_value=fake_client),
            patch(
                "backend.llm.openrouter.build_system_prompt",
                new=AsyncMock(return_value=[{"type": "text", "text": "system"}]),
            ),
        ):
            async for chunk in stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "search_videos"}}],
                tool_executor=slow_executor,
                max_tool_calls=3,
            ):
                emitted.append(chunk)
        return emitted

    async def test_keepalive_emitted_while_model_streams_tool_call_args(self) -> None:
        """With 12 tool_call fragments spaced 1s apart (12s total silent
        window), at least one `: keepalive` comment must appear before the
        content tokens. Without the heartbeat, this window would be zero
        bytes and browsers would idle-timeout around 60s in prod."""
        emitted = await self._collect(delay_per_chunk=1.0, tool_exec_delay=0.0)

        # Must contain at least one keepalive, emitted before the first
        # content token.
        first_keepalive_idx = next(
            (i for i, c in enumerate(emitted) if c.startswith(": keepalive")), -1
        )
        first_content_idx = next(
            (i for i, c in enumerate(emitted) if c.startswith('data: "Answer')), -1
        )
        assert first_keepalive_idx >= 0, f"expected a keepalive comment; got {emitted!r}"
        assert first_content_idx >= 0, f"expected a content token; got {emitted!r}"
        assert first_keepalive_idx < first_content_idx, (
            "keepalive must arrive before the first content token"
        )

    async def test_keepalive_emitted_before_tool_execution(self) -> None:
        """The executor awaits suspend the coroutine; we emit a keepalive
        just before the `await tool_executor(...)` so the socket stays
        warm while the DB query runs. At least one keepalive must be
        adjacent to the tool execution phase (i.e. after the tool_call
        round completes but before the next completion starts)."""
        # Short streaming delay but longer tool exec — isolates the
        # pre-exec keepalive.
        emitted = await self._collect(delay_per_chunk=0.0, tool_exec_delay=0.1)

        keepalive_count = sum(1 for c in emitted if c.startswith(": keepalive"))
        assert keepalive_count >= 1, (
            f"expected at least 1 keepalive (pre-exec); got 0. emitted={emitted!r}"
        )

    async def test_keepalive_does_not_replace_content_tokens(self) -> None:
        """Sanity: when content streams normally, we still emit data chunks
        and the final response reconstructs correctly. Keepalives are
        comments and must not be counted as content."""
        emitted = await self._collect(delay_per_chunk=0.0, tool_exec_delay=0.0)

        data_chunks = [c for c in emitted if c.startswith("data: ")]
        # The final-round content "Answer " + "here." reconstructs correctly.
        joined = "".join(c[len("data: ") : -2] for c in data_chunks if c != "data: [DONE]\n\n")
        # Each data payload is JSON-encoded; parse them.
        import json

        content_parts = []
        for c in data_chunks:
            if c == "data: [DONE]\n\n":
                continue
            payload = c[len("data: ") : -2]
            try:
                decoded = json.loads(payload)
                if isinstance(decoded, str):
                    content_parts.append(decoded)
            except json.JSONDecodeError:
                pass
        assert "".join(content_parts) == "Answer here.", (
            f"reconstructed content mismatch; joined={joined!r}, parts={content_parts!r}"
        )

        # Terminates with [DONE].
        assert emitted[-1] == "data: [DONE]\n\n"

    async def test_keepalive_format_is_spec_valid_sse_comment(self) -> None:
        """Each keepalive must be exactly `: keepalive\\n\\n` — standard SSE
        comment syntax. The frontend parser in useStreamingResponse.ts
        only matches lines that startsWith('event:') or 'data:'; any other
        line (including ':' comments) is silently ignored. If the format
        changes, frontend rendering is unaffected because the comment is
        not a data line."""
        emitted = await self._collect(delay_per_chunk=1.0, tool_exec_delay=0.0)

        for chunk in emitted:
            if chunk.startswith(":"):
                assert chunk == ": keepalive\n\n", (
                    f"malformed keepalive: {chunk!r} — must be exactly ': keepalive\\n\\n'"
                )


class TestBackendSseChunkHandling:
    """The route wrapper in `routes/messages.py` stores every yielded chunk
    in `full_response` for refusal detection and SSE replay. Verify that
    `_extract_text_from_sse` skips keepalive comments so they never leak
    into the persisted assistant text."""

    def test_extract_text_skips_keepalive_comments(self) -> None:
        from backend.routes.messages import _extract_text_from_sse

        chunks = [
            ": keepalive\n\n",
            'data: "Hello"\n\n',
            ": keepalive\n\n",
            'data: " world"\n\n',
            ": keepalive\n\n",
            "data: [DONE]\n\n",
        ]
        assert _extract_text_from_sse(chunks) == "Hello world"
