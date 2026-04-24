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
