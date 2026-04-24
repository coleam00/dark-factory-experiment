"""
Tests for SSE status events emitted by `stream_chat` during tool-call rounds.

Issue: Users see a loading spinner with no progress signal during 60-120s
tool-call rounds, leading them to assume the app is broken and bounce.

Solution: Emit `event: status\ndata: {...}\n\n` before each tool_executor await
(tool_call_start) and after each completion (tool_call_done). Frontend shows
an ephemeral "🔍 Searching: query…" indicator while tool calls are in flight.

These tests verify:
1. tool_call_start event appears before tool execution result
2. tool_call_done event appears after tool execution
3. status events do NOT end up in `_extract_text_from_sse` output
4. payload shape: type, tool keys present; subject present for search tools
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
    """Async iterator over pre-scripted chunks."""

    def __init__(self, chunks: list[_FakeDeltaChunk]):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            yield chunk


class TestSseStatusEventsDuringToolCalls:
    async def _collect(self, tool_exec_slow: bool = False):
        """Drive `stream_chat` through one tool-call round and collect emitted SSE chunks."""
        from backend.llm.openrouter import stream_chat

        # Round 1: model streams a search_videos tool call.
        round1_chunks = [
            _FakeDeltaChunk(
                tool_calls=[_FakeToolCallDelta(0, call_id="call_1", name="search_videos")]
            ),
        ]
        for _i in range(6):
            round1_chunks.append(
                _FakeDeltaChunk(
                    tool_calls=[
                        _FakeToolCallDelta(
                            0, arguments='{"query": "Cole agent approach", "top_k": 5}'
                        )
                    ]
                )
            )
        round1_chunks.append(_FakeDeltaChunk(finish_reason="tool_calls"))

        # Round 2: final content + stop.
        round2_chunks = [
            _FakeDeltaChunk(content="Here's what I found: "),
            _FakeDeltaChunk(content="the approach works."),
            _FakeDeltaChunk(finish_reason="stop"),
        ]

        streams = [
            _FakeStream(round1_chunks),
            _FakeStream(round2_chunks),
        ]
        create_mock = AsyncMock(side_effect=streams)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        async def tool_executor(name: str, raw_args: str) -> str:
            if tool_exec_slow:
                await asyncio.sleep(0.05)
            # Return a realistic chunk list for a search result.
            return (
                '[{"chunk_id":"c1","video_id":"v1","video_title":"Cole on Agentic AI",'
                '"start_seconds":42,"snippet":"Cole builds agents that plan and delegate"}]'
            )

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
                tool_executor=tool_executor,
                max_tool_calls=3,
            ):
                emitted.append(chunk)
        return emitted

    async def test_tool_call_start_status_event_emitted_before_tool_execution(self) -> None:
        """The backend must emit `event: status` with `tool_call_start` BEFORE
        awaiting tool_executor — the frontend needs time to display the indicator
        before the 60-120s wait begins."""
        emitted = await self._collect(tool_exec_slow=False)

        # Find the tool_call_start status event.
        start_idx = next(
            (
                i
                for i, c in enumerate(emitted)
                if c.startswith("event: status\n") and "tool_call_start" in c
            ),
            -1,
        )
        assert start_idx >= 0, f"expected tool_call_start status event; got {emitted!r}"

        # The tool_call_done event (emitted after tool execution) must come AFTER
        # tool_call_start in the SSE stream. This proves tool_call_start was emitted
        # before tool_executor awaited.
        done_idx = next(
            (
                i
                for i, c in enumerate(emitted)
                if c.startswith("event: status\n") and "tool_call_done" in c
            ),
            -1,
        )
        assert done_idx >= 0, f"expected tool_call_done status event; got {emitted!r}"
        assert start_idx < done_idx, (
            f"tool_call_start (index {start_idx}) must appear BEFORE tool_call_done (index {done_idx})"
        )

    async def test_tool_call_done_status_event_emitted_after_tool_execution(self) -> None:
        """After tool_executor completes, the backend must emit `event: status`
        with `tool_call_done`. The presence of the event in the stream proves
        tool execution completed and the done signal was sent."""
        emitted = await self._collect(tool_exec_slow=False)

        # Find the tool_call_done status event.
        done_idx = next(
            (
                i
                for i, c in enumerate(emitted)
                if c.startswith("event: status\n") and "tool_call_done" in c
            ),
            -1,
        )
        assert done_idx >= 0, f"expected tool_call_done status event; got {emitted!r}"

        # The event must appear before any final round content tokens arrive.
        first_content_idx = next(
            (i for i, c in enumerate(emitted) if c.startswith('data: "')),
            -1,
        )
        assert first_content_idx >= 0, f"expected final content token; got {emitted!r}"
        assert done_idx < first_content_idx, (
            f"tool_call_done (index {done_idx}) must appear before content (index {first_content_idx})"
        )

    async def test_status_events_do_not_appear_in_extracted_text(self) -> None:
        """`_extract_text_from_sse` must skip status events since they use the
        `event:` prefix, not `data:`. This test ensures status events never
        leak into persisted assistant text."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = [
            ": keepalive\n\n",
            'event: status\ndata: {"type": "tool_call_start", "tool": "search_videos", "subject": "Cole agent approach"}\n\n',
            'data: "Hello"\n\n',
            'event: status\ndata: {"type": "tool_call_done", "tool": "search_videos"}\n\n',
            'data: " world"\n\n',
            "data: [DONE]\n\n",
        ]
        text = _extract_text_from_sse(chunks)
        # Status events are silently skipped (event: prefix != data: prefix).
        assert text == "Hello world", f"status events leaked into extracted text: {text!r}"
        # Confirm no 'tool_call_start/done' strings leak through.
        assert "tool_call" not in text

    async def test_status_event_payload_shape_for_search_tool(self) -> None:
        """For search_videos, the status payload must include:
        - type: 'tool_call_start' or 'tool_call_done'
        - tool: 'search_videos'
        - subject: the query string extracted from tool args"""
        import json

        emitted = await self._collect(tool_exec_slow=False)

        # Collect all status event payloads.
        status_payloads = []
        for chunk in emitted:
            if chunk.startswith("event: status\n"):
                # Extract the data line.
                for line in chunk.split("\n"):
                    if line.startswith("data: "):
                        payload = json.loads(line[6:])
                        status_payloads.append(payload)
                        break

        assert len(status_payloads) == 2, f"expected 2 status events; got {len(status_payloads)}"

        # First: tool_call_start with subject.
        start_payload = status_payloads[0]
        assert start_payload["type"] == "tool_call_start", (
            f"expected tool_call_start; got {start_payload}"
        )
        assert start_payload["tool"] == "search_videos"
        assert "subject" in start_payload, f"search tool must have subject; got {start_payload}"
        assert "Cole agent" in start_payload["subject"], (
            f"subject should contain query; got {start_payload}"
        )

        # Second: tool_call_done (no subject required).
        done_payload = status_payloads[1]
        assert done_payload["type"] == "tool_call_done"
        assert done_payload["tool"] == "search_videos"

    async def test_status_event_payload_shape_for_get_video_transcript(self) -> None:
        """For get_video_transcript, subject should be the video_id."""
        import json

        from backend.llm.openrouter import stream_chat

        # Single round: get_video_transcript tool call.
        round1_chunks = [
            _FakeDeltaChunk(
                tool_calls=[_FakeToolCallDelta(0, call_id="call_1", name="get_video_transcript")]
            ),
        ]
        for _i in range(4):
            round1_chunks.append(
                _FakeDeltaChunk(
                    tool_calls=[_FakeToolCallDelta(0, arguments='{"video_id": "dQw4w9WgXcQ"}')]
                )
            )
        round1_chunks.append(_FakeDeltaChunk(finish_reason="tool_calls"))
        round2_chunks = [
            _FakeDeltaChunk(content="The video covers "),
            _FakeDeltaChunk(content="important topics."),
            _FakeDeltaChunk(finish_reason="stop"),
        ]

        streams = [_FakeStream(round1_chunks), _FakeStream(round2_chunks)]
        create_mock = AsyncMock(side_effect=streams)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        async def tool_executor(name: str, raw_args: str) -> str:
            return '[{"chunk_id":"c1","text":"transcript content"}]'

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
                tools=[{"type": "function", "function": {"name": "get_video_transcript"}}],
                tool_executor=tool_executor,
                max_tool_calls=3,
            ):
                emitted.append(chunk)

        # Find the tool_call_start status event.
        start_chunk = next(
            (c for c in emitted if c.startswith("event: status\n") and "tool_call_start" in c),
            None,
        )
        assert start_chunk is not None, f"expected tool_call_start; got {emitted!r}"

        for line in start_chunk.split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert payload["type"] == "tool_call_start"
                assert payload["tool"] == "get_video_transcript"
                assert payload["subject"] == "dQw4w9WgXcQ", (
                    f"get_video_transcript subject should be video_id; got {payload}"
                )
                break


class TestExtractToolSubject:
    """Edge-case tests for _extract_tool_subject helper."""

    async def test_unknown_tool_returns_empty_string(self) -> None:
        """Unknown tools should return empty string subject."""
        from backend.llm.openrouter import _extract_tool_subject

        subject = _extract_tool_subject("future_tool", '{"query": "something"}')
        assert subject == "", f"unknown tool should return empty string; got {subject!r}"

    async def test_with_duplicated_args_from_streaming(self) -> None:
        """Streaming can duplicate/accumulate argument fragments. Regex returns first match."""
        from backend.llm.openrouter import _extract_tool_subject

        # Simulates streaming artifact: arguments written twice
        duplicated = '{"query": "Cole agent approach"}{"query": "Cole agent approach"}'
        subject = _extract_tool_subject("search_videos", duplicated)
        assert subject == "Cole agent approach", f"regex should return first match; got {subject!r}"

    async def test_keyword_search_videos(self) -> None:
        """keyword_search_videos should also extract subject (same regex as search_videos)."""
        from backend.llm.openrouter import _extract_tool_subject

        subject = _extract_tool_subject("keyword_search_videos", '{"query": "test"}')
        assert subject == "test", f"keyword_search_videos should extract query; got {subject!r}"

    async def test_semantic_search_videos(self) -> None:
        """semantic_search_videos should also extract subject."""
        from backend.llm.openrouter import _extract_tool_subject

        subject = _extract_tool_subject("semantic_search_videos", '{"query": "test"}')
        assert subject == "test", f"semantic_search_videos should extract query; got {subject!r}"

    async def test_malformed_json_still_matches_via_regex(self) -> None:
        """Malformed JSON that would fail json.loads should still yield subject via regex."""
        from backend.llm.openrouter import _extract_tool_subject

        # truncated JSON that would fail json.loads()
        malformed = '{"query": "incomplete", '
        subject = _extract_tool_subject("search_videos", malformed)
        # Regex should still find the partial match
        assert subject == "incomplete", f"malformed JSON should still match; got {subject!r}"

    async def test_get_video_transcript_unknown_tool(self) -> None:
        """get_video_transcript with unknown args still extracts video_id correctly."""
        from backend.llm.openrouter import _extract_tool_subject

        subject = _extract_tool_subject("get_video_transcript", '{"video_id": "abc123"}')
        assert subject == "abc123", f"get_video_transcript should extract video_id; got {subject!r}"

    async def test_get_video_transcript_malformed(self) -> None:
        """Malformed JSON for transcript tool should return empty string."""
        from backend.llm.openrouter import _extract_tool_subject

        malformed = '{"video_id": "'
        subject = _extract_tool_subject("get_video_transcript", malformed)
        assert subject == "", f"malformed JSON should return empty; got {subject!r}"
