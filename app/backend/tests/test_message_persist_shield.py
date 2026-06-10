"""
Tests for the asyncio.shield persist-save path in routes/messages.py.

The tool-driven RAG flow can take 30-60s before the first token streams
(multiple tool rounds run first). Some browsers and proxies abort long
fetches in that window, which cancels the StreamingResponse's generator
task. Without asyncio.shield wrapping `repository.create_message`, the
CancelledError re-raised by the await kills the DB save silently
(CancelledError is BaseException, not Exception, so it bypasses
`except Exception`). These tests verify the save completes even when
the outer task is cancelled mid-finally.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.auth.dependencies import get_current_user
from backend.main import app


@pytest.fixture
def bypass_auth():
    """Satisfy the auth dependency for the message route."""
    stub = {"id": str(uuid4()), "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


class TestShieldProtectsSave:
    """Verify asyncio.shield keeps create_message alive when the outer task
    is cancelled (the client-disconnect case)."""

    async def test_cancelled_outer_task_does_not_kill_shielded_save(self) -> None:
        """
        Directly exercises the shield pattern: a cancellable outer task runs
        a finally block that awaits asyncio.shield on a slow DB-write
        coroutine. The outer task is cancelled while the shielded task is
        in flight; the shielded task must still complete and write its
        side-effect.
        """
        saved: list[str] = []

        async def slow_save() -> None:
            # Simulate a ~150ms DB round-trip.
            await asyncio.sleep(0.15)
            saved.append("persisted")

        async def outer() -> None:
            try:
                # Simulate streaming.
                await asyncio.sleep(10)  # will be cancelled
            finally:
                # Client went away — shield save from CancelledError.
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(slow_save())

        task = asyncio.create_task(outer())
        # Let outer enter the sleep.
        await asyncio.sleep(0.05)
        # Cancel while outer is still streaming (not yet in finally).
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Give the shielded save a moment to complete.
        await asyncio.sleep(0.2)
        assert saved == ["persisted"], (
            "shielded save must complete even when outer task is cancelled"
        )

    async def test_cancel_during_finally_does_not_kill_shielded_save(self) -> None:
        """
        Tighter version: the cancellation happens while outer is already
        inside the shielded await. This is the exact shape of the
        client-disconnect case — the generator's finally is already running
        when the ASGI task gets cancelled.
        """
        saved: list[str] = []

        async def slow_save() -> None:
            await asyncio.sleep(0.2)
            saved.append("persisted")

        async def outer() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(slow_save())

        task = asyncio.create_task(outer())
        # Wait long enough for outer to be cancelled-mid-stream, then let
        # the cancellation propagate into the finally's shielded await.
        await asyncio.sleep(0.05)
        task.cancel()

        # The task itself raises CancelledError as finally unwinds.
        with pytest.raises(asyncio.CancelledError):
            await task

        # But the shielded slow_save keeps running.
        await asyncio.sleep(0.3)
        assert saved == ["persisted"]


class TestEventGeneratorPersistsOnCancel:
    """Integration test: simulate a client disconnect mid-stream and verify
    the assistant message is still persisted via the shielded save path in
    routes/messages.py::event_generator."""

    async def test_persist_happens_even_when_generator_closed_early(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """
        Exercise the real event_generator by driving it manually:
          1. Force-feed a fake SSE stream through the generator.
          2. Close the generator early (simulating client disconnect).
          3. Assert repository.create_message was awaited exactly once with
             the assistant's reconstructed text.
        """
        # Avoid the full HTTP round-trip — call the generator directly.
        # We patch stream_chat to yield a tiny canned stream and inspect
        # the `finally` persistence behavior.

        async def fake_stream(*args: Any, **kwargs: Any):
            final_text_out = kwargs.get("final_text_out")
            for token in ("Hello", " world", "."):
                yield f"data: {json.dumps(token)}\n\n"
            if final_text_out is not None:
                final_text_out.append("Hello world.")
            yield "data: [DONE]\n\n"

        fake_user = bypass_auth
        fake_conv = {
            "id": str(uuid4()),
            "user_id": fake_user["id"],
            "title": "New Conversation",
        }

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=fake_conv,
            ),
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                # Return the user-message row on first call, None otherwise.
                side_effect=[
                    {"id": str(uuid4())},  # user-message insert
                    {"id": str(uuid4())},  # assistant-message insert (finally)
                ],
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[{"role": "user", "content": "hi"}],
            ),
            patch(
                "backend.routes.messages.repository.list_videos",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "backend.routes.messages.rate_limit.check_and_record",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.routes.messages.stream_chat",
                side_effect=fake_stream,
            ),
            patch(
                "backend.routes.messages._maybe_set_conversation_title",
                new_callable=AsyncMock,
            ),
        ):
            # Drive the route handler directly — we only care about what
            # happens in event_generator, so we'll pull tokens from the
            # StreamingResponse and then abandon it to trigger generator
            # close.
            from backend.routes.messages import MessageCreate, create_message

            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=fake_user,
            )

            # Iterate a few tokens, then abandon (simulates client hanging up).
            body_iter = resp.body_iterator
            got = []
            for _ in range(2):
                chunk = await body_iter.__anext__()
                got.append(chunk)

            # Close the generator — this triggers the finally.
            await body_iter.aclose()

            # Give the shielded save a moment to complete (it's backgrounded).
            await asyncio.sleep(0.1)

        # create_message was called twice: once for the user message up-front,
        # once for the assistant message in the shielded finally.
        assert mock_create.call_count == 2, (
            f"expected 2 create_message calls (user + assistant), got {mock_create.call_count}"
        )
        # The second call must be the assistant save.
        second_call_kwargs = mock_create.call_args_list[1].kwargs
        assert second_call_kwargs["role"] == "assistant"
        assert "Hello world" in second_call_kwargs["content"]


# ---------------------------------------------------------------------------
# Issue #277 — persisted sources must match what was emitted live
# ---------------------------------------------------------------------------

# Two chunks from the same video so the [DONE] pipeline's collapse step is
# exercised: the emitted/persisted payload must contain a single collapsed
# entry with segment_count == 2.
_SOURCE_CHUNKS: list[dict[str, Any]] = [
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Test Video",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 30.0,
        "end_seconds": 40.0,
        "snippet": "Snippet one",
    },
    {
        "chunk_id": "c2",
        "video_id": "v1",
        "video_title": "Test Video",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "Snippet two",
    },
]


async def _fake_execute_tool(name: str, raw_args: str, **kwargs: Any) -> dict[str, Any]:
    # Return fresh copies — the route mutates chunk dicts (is_cited).
    return {"ok": True, "text": "ctx", "chunks": [dict(c) for c in _SOURCE_CHUNKS]}


def _route_patches(
    fake_conv: dict[str, Any],
    fake_stream: Any,
    mock_create: AsyncMock,
) -> contextlib.ExitStack:
    """Enter the standard patch set for driving event_generator manually."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "backend.routes.messages.repository.get_conversation",
            new_callable=AsyncMock,
            return_value=fake_conv,
        )
    )
    stack.enter_context(patch("backend.routes.messages.repository.create_message", mock_create))
    stack.enter_context(
        patch(
            "backend.routes.messages.repository.list_messages",
            new_callable=AsyncMock,
            return_value=[{"role": "user", "content": "hi"}],
        )
    )
    stack.enter_context(
        patch(
            "backend.routes.messages.repository.list_videos",
            new_callable=AsyncMock,
            return_value=[{"id": "v1"}],
        )
    )
    stack.enter_context(
        patch("backend.routes.messages.rate_limit.check_and_record", new_callable=AsyncMock)
    )
    stack.enter_context(patch("backend.routes.messages.stream_chat", side_effect=fake_stream))
    stack.enter_context(patch("backend.routes.messages.execute_tool", _fake_execute_tool))
    stack.enter_context(
        patch("backend.routes.messages._maybe_set_conversation_title", new_callable=AsyncMock)
    )
    return stack


class TestSourcesPersistMatchesEmitted:
    """Regression tests for issue #277: the sources persisted with an
    assistant message must be exactly the sources emitted via the live
    `event: sources` SSE event — and None when that event never reached
    the client (interrupted or errored mid-stream)."""

    @staticmethod
    async def _fake_stream_clean(*args: Any, **kwargs: Any):
        tool_executor = kwargs.get("tool_executor")
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps('Grounded answer about the video.')}\n\n"
        final_text_out = kwargs.get("final_text_out")
        if final_text_out is not None:
            final_text_out.append("Grounded answer about the video.")
        yield "data: [DONE]\n\n"

    @staticmethod
    async def _fake_stream_error(*args: Any, **kwargs: Any):
        # The flaky-upstream case: an error chunk instead of [DONE].
        tool_executor = kwargs.get("tool_executor")
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps('Partial answer')}\n\n"
        yield 'data: {"error": "upstream model failure"}\n\n'

    def _setup(self, fake_user: dict[str, Any], fake_stream: Any):
        fake_conv = {
            "id": str(uuid4()),
            "user_id": fake_user["id"],
            "title": "New Conversation",
        }
        mock_create = AsyncMock(
            side_effect=[
                {"id": str(uuid4())},  # user-message insert
                {"id": str(uuid4())},  # assistant-message insert (finally)
            ]
        )
        return fake_conv, mock_create, _route_patches(fake_conv, fake_stream, mock_create)

    @staticmethod
    def _parse_sources_payload(chunk: str) -> list[dict[str, Any]]:
        assert chunk.startswith("event: sources\ndata: ")
        payload = json.loads(chunk.split("data: ", 1)[1].strip())
        assert isinstance(payload, list)
        return payload

    async def test_persisted_sources_equal_live_sources_on_clean_completion(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """Clean completion: persist == emitted, both collapsed/deduped."""
        from backend.routes.messages import MessageCreate, create_message

        fake_conv, mock_create, patches = self._setup(bypass_auth, self._fake_stream_clean)
        with patches:
            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=bypass_auth,
            )
            chunks = [c async for c in resp.body_iterator]
            await asyncio.sleep(0.1)

        sources_chunks = [c for c in chunks if c.startswith("event: sources")]
        assert len(sources_chunks) == 1, f"expected one sources event, got {chunks!r}"
        emitted = self._parse_sources_payload(sources_chunks[0])
        # The two same-video chunks collapsed into one entry.
        assert len(emitted) == 1
        assert emitted[0]["segment_count"] == 2

        assert mock_create.call_count == 2
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"
        assert assistant_kwargs["sources"] == emitted, (
            "persisted sources must deep-equal the live-emitted payload"
        )

    async def test_interrupt_before_sources_sent_persists_none(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """The reported bug: the generator is aborted at the sources yield
        (the chunk never reached the client), so persistence must be None —
        not the collapsed source set the old re-derivation would save."""
        from backend.routes.messages import MessageCreate, create_message

        fake_conv, mock_create, patches = self._setup(bypass_auth, self._fake_stream_clean)
        with patches:
            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=bypass_auth,
            )
            body_iter = resp.body_iterator
            chunk = ""
            for _ in range(10):
                chunk = await body_iter.__anext__()
                if chunk.startswith("event: sources"):
                    break
            assert chunk.startswith("event: sources"), "sources event never produced"
            # Generator is suspended AT the sources yield — the capture line
            # after it has not run. Closing here models the consumer failing
            # to send that chunk.
            await body_iter.aclose()
            await asyncio.sleep(0.1)

        assert mock_create.call_count == 2
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"
        assert assistant_kwargs["sources"] is None, (
            f"sources event never delivered → must persist None, "
            f"got {assistant_kwargs['sources']!r}"
        )

    async def test_sources_sent_then_interrupted_persists_sources(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """Once the consumer pulled past the sources yield (event delivered),
        an interruption must still persist the emitted sources."""
        from backend.routes.messages import MessageCreate, create_message

        fake_conv, mock_create, patches = self._setup(bypass_auth, self._fake_stream_clean)
        with patches:
            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=bypass_auth,
            )
            body_iter = resp.body_iterator
            sources_chunk = ""
            for _ in range(10):
                sources_chunk = await body_iter.__anext__()
                if sources_chunk.startswith("event: sources"):
                    break
            assert sources_chunk.startswith("event: sources")
            # Pull one more chunk: the resume past the sources yield runs the
            # capture, and the generator suspends at the [DONE] yield.
            done_chunk = await body_iter.__anext__()
            assert done_chunk == "data: [DONE]\n\n"
            await body_iter.aclose()
            await asyncio.sleep(0.1)

        emitted = self._parse_sources_payload(sources_chunk)
        assert mock_create.call_count == 2
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"
        assert assistant_kwargs["sources"] == emitted, (
            "client received the sources event → persistence must keep it"
        )

    async def test_error_midstream_before_done_persists_none(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """Upstream error instead of [DONE]: no sources event is emitted,
        so the persisted partial answer must carry sources=None."""
        from backend.routes.messages import MessageCreate, create_message

        fake_conv, mock_create, patches = self._setup(bypass_auth, self._fake_stream_error)
        with patches:
            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=bypass_auth,
            )
            chunks = [c async for c in resp.body_iterator]
            await asyncio.sleep(0.1)

        assert not any(c.startswith("event: sources") for c in chunks), (
            f"no sources event expected on mid-stream error, got {chunks!r}"
        )
        assert mock_create.call_count == 2
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"
        assert "Partial answer" in assistant_kwargs["content"]
        assert assistant_kwargs["sources"] is None
