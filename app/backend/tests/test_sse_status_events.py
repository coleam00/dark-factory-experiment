"""
Tests for the SSE status events emitted by `stream_chat` around each
tool-executor call.

Status events give the frontend a real-time "Searching: ..." indicator
during the 60-120s silent tool-call phase (issue #168). They use the
named SSE event `event: status` so existing clients that don't recognise
them silently skip (per the SSE spec), and `_extract_text_from_sse`
already ignores non-`data:` lines.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


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


class TestSseStatusEvents:
    """Verify that `stream_chat` emits `event: status` SSE events around
    each tool-executor call."""

    async def _collect(self, tool_args: str = '{"query": "building agents"}') -> list[str]:
        """Drive `stream_chat` with a canned two-round flow and return emitted chunks.

        Round 1: model streams a single tool_call.
        Tool executor: instant (returns immediately).
        Round 2: model emits final content tokens + finish_reason=stop.
        """
        from backend.llm.openrouter import stream_chat

        round1_chunks = [
            _FakeDeltaChunk(
                tool_calls=[_FakeToolCallDelta(0, call_id="call_1", name="search_videos")]
            ),
            _FakeDeltaChunk(tool_calls=[_FakeToolCallDelta(0, arguments=tool_args)]),
            _FakeDeltaChunk(finish_reason="tool_calls"),
        ]

        round2_chunks = [
            _FakeDeltaChunk(content="Answer "),
            _FakeDeltaChunk(content="here."),
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

        async def instant_executor(name: str, raw_args: str) -> str:
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
                tool_executor=instant_executor,
                max_tool_calls=3,
            ):
                emitted.append(chunk)
        return emitted

    async def test_status_event_emitted_before_tool_executor_await(self) -> None:
        """A `tool_call_start` status event must appear before the first content token."""
        emitted = await self._collect()

        first_status_idx = next((i for i, c in enumerate(emitted) if "tool_call_start" in c), -1)
        first_content_idx = next(
            (i for i, c in enumerate(emitted) if c.startswith('data: "Answer')), -1
        )
        assert first_status_idx >= 0, f"expected a tool_call_start status event; got {emitted!r}"
        assert first_content_idx >= 0, f"expected a content token; got {emitted!r}"
        assert first_status_idx < first_content_idx, (
            "tool_call_start status event must arrive before the first content token"
        )

    async def test_status_event_emitted_on_tool_completion(self) -> None:
        """A `tool_call_done` event must appear after a `tool_call_start` event."""
        emitted = await self._collect()

        start_idx = next((i for i, c in enumerate(emitted) if "tool_call_start" in c), -1)
        done_idx = next((i for i, c in enumerate(emitted) if "tool_call_done" in c), -1)
        assert start_idx >= 0, "expected a tool_call_start status event"
        assert done_idx >= 0, "expected a tool_call_done status event"
        assert done_idx > start_idx, "tool_call_done must appear after tool_call_start"

    async def test_status_events_do_not_appear_in_final_text(self) -> None:
        """Status events must not leak into the reconstructed assistant text."""
        from backend.routes.messages import _extract_text_from_sse

        emitted = await self._collect()
        reconstructed = _extract_text_from_sse(emitted)
        assert "tool_call_start" not in reconstructed
        assert "tool_call_done" not in reconstructed
        assert reconstructed == "Answer here."

    async def test_status_event_payload_shape(self) -> None:
        """Each status event must have the correct JSON structure."""
        emitted = await self._collect()

        status_events = [c for c in emitted if c.startswith("event: status\n")]
        assert len(status_events) >= 2, (
            f"expected at least 2 status events (start + done); got {len(status_events)}"
        )

        for event_str in status_events:
            # Extract the data line from the event
            lines = event_str.strip().split("\n")
            data_line = next(line for line in lines if line.startswith("data: "))
            payload = json.loads(data_line[len("data: ") :])

            assert "type" in payload, f"status event missing 'type': {payload}"
            assert "tool" in payload, f"status event missing 'tool': {payload}"
            assert isinstance(payload["tool"], str)

            if payload["type"] == "tool_call_start":
                assert "subject" in payload, f"tool_call_start missing 'subject': {payload}"
                assert isinstance(payload["subject"], str)
                assert payload["tool"] == "search_videos"
                assert payload["subject"] == "building agents"
            elif payload["type"] == "tool_call_done":
                assert payload["tool"] == "search_videos"
            else:
                raise AssertionError(f"unexpected status event type: {payload['type']}")


@pytest.mark.parametrize(
    "tool_name,args_raw,expected",
    [
        ("search_videos", json.dumps({"query": "agents"}), "agents"),
        ("keyword_search_videos", json.dumps({"query": "RAG"}), "RAG"),
        ("semantic_search_videos", json.dumps({"query": "LLM"}), "LLM"),
        ("get_video_transcript", json.dumps({"video_id": "abc123"}), "abc123"),
        ("unknown_tool", json.dumps({"query": "x"}), ""),
        ("search_videos", "not-valid-json {", ""),  # JSONDecodeError
        ("search_videos", json.dumps([1, 2, 3]), ""),  # non-dict guard
        ("search_videos", json.dumps({}), ""),  # missing key → empty string
    ],
)
def test_extract_tool_subject(tool_name: str, args_raw: str, expected: str) -> None:
    from backend.llm.openrouter import _extract_tool_subject

    assert _extract_tool_subject(tool_name, args_raw) == expected


def test_extract_tool_subject_type_error() -> None:
    """TypeError branch: json.loads raises when passed None."""
    from backend.llm.openrouter import _extract_tool_subject

    result = _extract_tool_subject("search_videos", None)  # type: ignore[arg-type]
    assert result == ""


class TestCapReachedNoStatusEvents:
    """Verify that status events are NOT emitted when the per-turn cap is already reached."""

    async def test_no_status_events_when_cap_reached(self) -> None:
        from backend.llm.openrouter import stream_chat

        tool_args = '{"query": "test"}'

        round1_chunks = [
            _FakeDeltaChunk(
                tool_calls=[_FakeToolCallDelta(0, call_id="call_1", name="search_videos")]
            ),
            _FakeDeltaChunk(tool_calls=[_FakeToolCallDelta(0, arguments=tool_args)]),
            _FakeDeltaChunk(finish_reason="tool_calls"),
        ]

        round2_chunks = [
            _FakeDeltaChunk(content="Sorry, cap reached."),
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

        async def instant_executor(name: str, raw_args: str) -> str:
            return "result"

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
                tool_executor=instant_executor,
                max_tool_calls=0,  # cap already reached from the start
            ):
                emitted.append(chunk)

        # No status events should have been emitted
        status_events = [c for c in emitted if c.startswith("event: status\n")]
        assert len(status_events) == 0, (
            f"expected no status events when cap=0, got: {status_events}"
        )
