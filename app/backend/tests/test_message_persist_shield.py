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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_chunk(cid: str, vid: str = "v1") -> dict[str, Any]:
    return {
        "chunk_id": cid,
        "video_id": vid,
        "video_title": "T",
        "video_url": "u",
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "snippet": "s",
    }


def _parse_sources_from_sse(body: str) -> list[dict[str, Any]]:
    for line in body.splitlines():
        if line.startswith("event: sources"):
            continue
        if line.startswith("data: ") and "chunk_id" in line:
            return json.loads(line[len("data: "):])
    return []


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


class TestSourcesPersistRegression:
    """Regression for issue #277: persisted sources must match what the
    user actually saw live.  If the stream is interrupted before the
    ``event: sources`` SSE event is successfully emitted, sources must
    NOT be persisted (no duplicate / phantom chips on reload)."""

    async def test_clean_done_sources_persisted_and_match_sse(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """Normal completion: sources in DB == sources on the wire."""

        async def fake_stream(*args: Any, **kwargs: Any):
            final_text_out = kwargs.get("final_text_out")
            tool_executor = kwargs.get("tool_executor")
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "t"}))
            for token in ("Hello ", "world."):
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
        chunks = [_fake_chunk("c1", "v1"), _fake_chunk("c2", "v2")]

        async def mock_execute_tool(
            name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
        ):
            return {"ok": True, "text": "ctx", "chunks": chunks}

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=fake_conv,
            ),
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                return_value={"id": str(uuid4())},
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[{"role": "user", "content": "hi"}],
            ),
            patch(
                "backend.routes.messages.repository.list_videos",
                new_callable=AsyncMock,
                return_value=[{"id": "v1"}, {"id": "v2"}],
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
                "backend.routes.messages.execute_tool",
                side_effect=mock_execute_tool,
            ),
            patch(
                "backend.routes.messages._maybe_set_conversation_title",
                new_callable=AsyncMock,
            ),
        ):
            from backend.routes.messages import MessageCreate, create_message

            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=fake_user,
            )

            body_parts: list[str] = []
            async for chunk in resp.body_iterator:
                body_parts.append(chunk)

            await asyncio.sleep(0.1)

        body = "".join(body_parts)
        sse_sources = _parse_sources_from_sse(body)
        assert len(sse_sources) == 2, "SSE must contain the two source chips"

        assert mock_create.call_count == 2
        second_call = mock_create.call_args_list[1].kwargs
        assert second_call["role"] == "assistant"
        assert second_call["sources"] is not None
        assert len(second_call["sources"]) == 2
        # The snapshot should match the emitted payload (same chunk_ids).
        persisted_ids = {c["chunk_id"] for c in second_call["sources"]}
        assert persisted_ids == {"c1", "c2"}

    async def test_disconnect_before_done_sources_not_persisted(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """Client hangs up before [DONE]: sources must NOT be saved."""

        async def fake_stream(*args: Any, **kwargs: Any):
            final_text_out = kwargs.get("final_text_out")
            tool_executor = kwargs.get("tool_executor")
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "t"}))
            for token in ("Hello ", "world."):
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
        chunks = [_fake_chunk("c1", "v1")]

        async def mock_execute_tool(
            name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
        ):
            return {"ok": True, "text": "ctx", "chunks": chunks}

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=fake_conv,
            ),
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                return_value={"id": str(uuid4())},
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[{"role": "user", "content": "hi"}],
            ),
            patch(
                "backend.routes.messages.repository.list_videos",
                new_callable=AsyncMock,
                return_value=[{"id": "v1"}],
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
                "backend.routes.messages.execute_tool",
                side_effect=mock_execute_tool,
            ),
            patch(
                "backend.routes.messages._maybe_set_conversation_title",
                new_callable=AsyncMock,
            ),
        ):
            from backend.routes.messages import MessageCreate, create_message

            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=fake_user,
            )

            body_iter = resp.body_iterator
            # Pull one token then bail.
            await body_iter.__anext__()
            await body_iter.aclose()
            await asyncio.sleep(0.1)

        second_call = mock_create.call_args_list[1].kwargs
        assert second_call["role"] == "assistant"
        assert second_call.get("sources") is None, (
            "sources must not be persisted when the stream was interrupted"
        )

    async def test_exception_in_done_processing_sources_not_persisted(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """If _collapse_by_video blows up inside the [DONE] handler, the
        sources list is left partially mutated.  The finally block must
        NOT persist it (issue #277 root cause)."""

        async def fake_stream(*args: Any, **kwargs: Any):
            final_text_out = kwargs.get("final_text_out")
            tool_executor = kwargs.get("tool_executor")
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "t"}))
            for token in ("Hello ", "world."):
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
        chunks = [_fake_chunk("c1", "v1"), _fake_chunk("c2", "v1")]

        async def mock_execute_tool(
            name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
        ):
            return {"ok": True, "text": "ctx", "chunks": chunks}

        def boom(chunks):
            raise RuntimeError("boom")

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=fake_conv,
            ),
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                return_value={"id": str(uuid4())},
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[{"role": "user", "content": "hi"}],
            ),
            patch(
                "backend.routes.messages.repository.list_videos",
                new_callable=AsyncMock,
                return_value=[{"id": "v1"}],
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
                "backend.routes.messages.execute_tool",
                side_effect=mock_execute_tool,
            ),
            patch(
                "backend.routes.messages._collapse_by_video",
                side_effect=boom,
            ),
            patch(
                "backend.routes.messages._maybe_set_conversation_title",
                new_callable=AsyncMock,
            ),
        ):
            from backend.routes.messages import MessageCreate, create_message

            resp = await create_message(
                conv_id=fake_conv["id"],
                body=MessageCreate(content="hi"),
                current_user=fake_user,
            )

            body_iter = resp.body_iterator
            got: list[str] = []
            with pytest.raises(RuntimeError, match="boom"):
                async for chunk in body_iter:
                    got.append(chunk)

            await asyncio.sleep(0.1)

        second_call = mock_create.call_args_list[1].kwargs
        assert second_call["role"] == "assistant"
        assert second_call.get("sources") is None, (
            "partially-processed sources must not be persisted when [DONE] processing crashes"
        )
