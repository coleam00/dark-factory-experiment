"""
Regression tests for the silent-empty-response bug traced in prod logs.

Symptom: broad queries ("How does Cole recommend building AI agents?") ran
multiple tool-call rounds, emitted a `sources` SSE event with many citations,
and then terminated the stream without a single content token. The frontend
showed a Sources chip but zero response text. The backend skipped persistence
(routes/messages.py `if assistant_text`), leaving ~10% of 24h traffic as
orphan user rows with no assistant row.

Root cause candidate: `stream_chat` called `chat.completions.create` without
`max_tokens`, so OpenRouter applied its default for Anthropic via the OpenAI
shim. Broad queries serialize many tool_call args across rounds and can
exhaust the default budget before the model gets to compose a final answer,
returning finish_reason=length (or stop) with an empty content stream.

These tests lock in:
  1. An explicit `max_tokens` is always passed to chat.completions.create.
  2. The warning log fires when a final round yields zero content tokens so
     the failure is visible in production logs instead of being silent.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch


class _FakeDeltaChunk:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
        self.choices = [choice]


class _FakeStream:
    def __init__(self, chunks: list[_FakeDeltaChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for chunk in self._chunks:
            yield chunk


async def _run_stream_chat(
    mock_create: AsyncMock,
) -> list[str]:
    from backend.llm.openrouter import stream_chat

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=mock_create))
    )
    emitted: list[str] = []
    with (
        patch("backend.llm.openrouter._get_async_client", return_value=fake_client),
        patch(
            "backend.llm.openrouter.build_system_prompt",
            new=AsyncMock(return_value=[{"type": "text", "text": "sys"}]),
        ),
    ):
        async for chunk in stream_chat(
            messages=[{"role": "user", "content": "hi"}],
        ):
            emitted.append(chunk)
    return emitted


class TestMaxTokensPassedToOpenRouter:
    async def test_max_tokens_in_first_round_kwargs(self) -> None:
        """Every completion request must carry an explicit max_tokens so
        OpenRouter doesn't fall back to its per-provider default."""
        stream = _FakeStream([_FakeDeltaChunk(content="ok", finish_reason="stop")])
        create_mock = AsyncMock(return_value=stream)

        await _run_stream_chat(create_mock)

        assert create_mock.call_count == 1
        _, kwargs = create_mock.call_args_list[0]
        assert "max_tokens" in kwargs, (
            f"max_tokens missing from chat.completions.create kwargs: {kwargs!r}"
        )
        assert isinstance(kwargs["max_tokens"], int) and kwargs["max_tokens"] >= 4096, (
            f"max_tokens must be a generous int cap; got {kwargs['max_tokens']!r}"
        )


class _FakeToolCallDelta:
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


class TestToolChoiceNoneOnCapReached:
    """Regression for the prod bug where broad queries returned empty
    responses after hitting the tool-call cap.

    Root cause (verified via prod diagnostic logs): when tool_calls_made
    reaches max_tool_calls, the original code stripped `tools` from the
    next request. Anthropic via OpenRouter then saw conversation history
    full of tool_use/tool_result blocks but no declared tools, and
    returned finish_reason=stop with zero content tokens. Fix: keep
    tools declared but set tool_choice=\"none\" to forbid further tool
    calls while still giving the model a valid tool context — it then
    composes a final answer from the retrieved chunks it already has.
    """

    async def test_tools_still_declared_on_cap_reached_round(self) -> None:
        from backend.llm.openrouter import stream_chat

        # Round 1: model makes a tool call (hitting cap of 1).
        # Round 2 (final): model must compose answer using tool_choice=none.
        round1 = _FakeStream(
            [
                _FakeDeltaChunk(
                    tool_calls=[_FakeToolCallDelta(0, call_id="c1", name="search_videos")]
                ),
                _FakeDeltaChunk(tool_calls=[_FakeToolCallDelta(0, arguments='{"query":"x"}')]),
                _FakeDeltaChunk(finish_reason="tool_calls"),
            ]
        )
        round2 = _FakeStream(
            [
                _FakeDeltaChunk(content="Final answer"),
                _FakeDeltaChunk(finish_reason="stop"),
            ]
        )
        create_mock = AsyncMock(side_effect=[round1, round2])
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
        )

        async def exec_tool(name: str, raw_args: str) -> str:
            return "tool result payload"

        with (
            patch("backend.llm.openrouter._get_async_client", return_value=fake_client),
            patch(
                "backend.llm.openrouter.build_system_prompt",
                new=AsyncMock(return_value=[{"type": "text", "text": "sys"}]),
            ),
        ):
            async for _ in stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "search_videos"}}],
                tool_executor=exec_tool,
                max_tool_calls=1,
            ):
                pass

        assert create_mock.call_count == 2
        # Final-round kwargs must (a) still carry `tools`, (b) set
        # tool_choice="none" to forbid further calls. Stripping tools was
        # the original bug.
        _, final_kwargs = create_mock.call_args_list[1]
        assert "tools" in final_kwargs, (
            "`tools` must remain declared on the cap-reached final round; "
            "Anthropic returns empty content when tool history exists but "
            "no tools are declared"
        )
        assert final_kwargs.get("tool_choice") == "none", (
            "tool_choice must be 'none' on the cap-reached round so the "
            f"model composes an answer; got {final_kwargs.get('tool_choice')!r}"
        )

    async def test_no_tool_choice_on_pre_cap_rounds(self) -> None:
        """Rounds below the cap must not set tool_choice — default
        'auto' must be used so the model can decide whether to call tools
        or answer directly."""
        stream = _FakeStream([_FakeDeltaChunk(content="ok", finish_reason="stop")])
        create_mock = AsyncMock(return_value=stream)

        await _run_stream_chat(create_mock)

        _, kwargs = create_mock.call_args_list[0]
        assert "tool_choice" not in kwargs, (
            f"pre-cap round must not set tool_choice; got {kwargs.get('tool_choice')!r}"
        )


class TestEmptyFinalContentWarning:
    async def test_warning_logged_when_final_round_emits_zero_content(self, caplog) -> None:
        """The silent-empty-response prod bug must leave a fingerprint in
        application logs so future occurrences are debuggable without
        replaying the full SSE capture."""
        # Final round: only a finish_reason chunk, no content, no tool_calls.
        stream = _FakeStream([_FakeDeltaChunk(finish_reason="stop")])
        create_mock = AsyncMock(return_value=stream)

        with caplog.at_level(logging.WARNING, logger="backend.llm.openrouter"):
            await _run_stream_chat(create_mock)

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "zero content tokens" in r.getMessage()
        ]
        assert warnings, (
            "expected a WARNING about zero content tokens on the final round; "
            f"got {[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
        )

    async def test_no_warning_when_final_round_has_content(self, caplog) -> None:
        """Normal happy path — no warning noise."""
        stream = _FakeStream(
            [
                _FakeDeltaChunk(content="Hello "),
                _FakeDeltaChunk(content="world"),
                _FakeDeltaChunk(finish_reason="stop"),
            ]
        )
        create_mock = AsyncMock(return_value=stream)

        with caplog.at_level(logging.WARNING, logger="backend.llm.openrouter"):
            await _run_stream_chat(create_mock)

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "zero content tokens" in r.getMessage()
        ]
        assert not warnings, (
            f"unexpected zero-content warning on happy path: {[r.getMessage() for r in warnings]!r}"
        )
