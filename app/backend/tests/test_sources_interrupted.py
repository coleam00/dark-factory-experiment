"""
Regression tests for issue #277 — a saved answer's sources must match what the
user saw streaming live, regardless of whether the stream completed cleanly or
was interrupted/errored mid-answer.

The invariant under test: the source list persisted with the assistant message
is *exactly* the list that was handed to the client via the `event: sources`
SSE event (after dedup → is_cited → collapse-by-video → cap → refusal gate), or
``None`` when no sources event ever reached the client. There is no second,
divergent finalization path in the persist branch.

These reuse the mock harness from test_sources_event.py: ASGITransport +
AsyncClient, patched stream_chat / execute_tool / repository functions, and an
AsyncMock on create_message to capture the persisted kwargs.
"""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app


def _two_chunks() -> list[dict]:
    return [
        {
            "chunk_id": "c1",
            "video_id": "v1",
            "video_title": "Test Video 1",
            "video_url": "https://youtube.com/watch?v=abc",
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "snippet": "Snippet 1",
        },
        {
            "chunk_id": "c2",
            "video_id": "v2",
            "video_title": "Test Video 2",
            "video_url": "https://youtube.com/watch?v=def",
            "start_seconds": 5.0,
            "end_seconds": 15.0,
            "snippet": "Snippet 2",
        },
    ]


def _patches(mock_stream_chat, mock_create, chunks):
    """Build the standard patch set for a messages-route integration test."""
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        return {
            "id": test_conv_id,
            "user_id": test_user_id,
            "title": "Test",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_execute_tool(
        name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
    ):
        return {"ok": True, "text": "context", "chunks": chunks}

    async def mock_list_messages(conv_id, user_id):
        return []

    async def mock_list_videos():
        return [
            {"id": "v1", "title": "Test Video 1", "url": "u1"},
            {"id": "v2", "title": "Test Video 2", "url": "u2"},
        ]

    ctx = (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.create_message", mock_create),
        patch("backend.db.repository.list_messages", mock_list_messages),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.routes.messages.stream_chat", mock_stream_chat),
        patch("backend.routes.messages.execute_tool", mock_execute_tool),
    )
    return ctx, test_conv_id, valid_token


async def test_interrupted_stream_persists_sources_none() -> None:
    """Upstream errors mid-answer (error payload, no [DONE]). The live view
    never saw an `event: sources`, so the persisted row must have
    sources=None. Direct regression test for issue #277."""
    partial_text = "Here is the start of the answer"

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        # Tool executor populates tool_chunks_acc with 2 chunks.
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(partial_text)}\n\n"
        # Upstream flakes: emit an error payload and return WITHOUT [DONE] and
        # without populating final_text_out (mirrors openrouter.py on error).
        yield 'data: {"error": "upstream flaked"}\n\n'

    mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])
    ctx, conv_id, token = _patches(mock_stream_chat, mock_create, _two_chunks())

    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{conv_id}/messages",
                json={"content": "a question"},
                headers={"Cookie": f"session={token}"},
            )

    assert "event: sources" not in response.text

    assert mock_create.call_count == 2
    assistant_kwargs = mock_create.call_args_list[1].kwargs
    assert assistant_kwargs["role"] == "assistant"
    assert assistant_kwargs["sources"] is None
    assert assistant_kwargs["content"] == partial_text


async def test_midstream_exception_persists_sources_none() -> None:
    """An exception raised mid-stream still triggers the shielded persist in
    the finally block; because no `event: sources` was emitted, sources=None."""
    partial_text = "Partial answer before the crash"

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(partial_text)}\n\n"
        raise RuntimeError("upstream connection dropped")

    mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])
    ctx, conv_id, token = _patches(mock_stream_chat, mock_create, _two_chunks())

    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(RuntimeError):
                async with client.stream(
                    "POST",
                    f"/api/conversations/{conv_id}/messages",
                    json={"content": "a question"},
                    headers={"Cookie": f"session={token}"},
                ) as response:
                    async for _ in response.aiter_bytes():
                        pass

    # The shielded persist in finally still ran (user msg + assistant msg).
    assert mock_create.call_count == 2
    assistant_kwargs = mock_create.call_args_list[1].kwargs
    assert assistant_kwargs["role"] == "assistant"
    assert assistant_kwargs["sources"] is None


async def test_emitted_sources_equal_persisted_exactly() -> None:
    """On a clean completion, the persisted sources kwarg is byte-for-byte the
    same list that was emitted via `event: sources` — deduped, collapsed, and
    capped identically. Pins the by-construction invariant."""
    # 3 chunks across 2 videos (c1 and c3 share v1). The answer cites c1.
    chunks = [
        {
            "chunk_id": "c1",
            "video_id": "v1",
            "video_title": "Video 1",
            "video_url": "https://youtube.com/watch?v=abc",
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "snippet": "Snippet 1",
        },
        {
            "chunk_id": "c2",
            "video_id": "v2",
            "video_title": "Video 2",
            "video_url": "https://youtube.com/watch?v=def",
            "start_seconds": 5.0,
            "end_seconds": 15.0,
            "snippet": "Snippet 2",
        },
        {
            "chunk_id": "c3",
            "video_id": "v1",
            "video_title": "Video 1",
            "video_url": "https://youtube.com/watch?v=abc",
            "start_seconds": 30.0,
            "end_seconds": 40.0,
            "snippet": "Snippet 3",
        },
    ]
    answer_text = "The first video explains it [c:c1]."

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(answer_text)}\n\n"
        if final_text_out is not None:
            final_text_out.append(answer_text)
        yield "data: [DONE]\n\n"

    mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])
    ctx, conv_id, token = _patches(mock_stream_chat, mock_create, chunks)

    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{conv_id}/messages",
                json={"content": "a question"},
                headers={"Cookie": f"session={token}"},
            )

    # Parse the emitted sources event out of the raw SSE body.
    emitted = None
    for line in response.text.split("\n"):
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            candidate = line[len("data: ") :]
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, list):
                emitted = parsed
                break
    assert emitted is not None, f"no sources event found in: {response.text!r}"

    assert mock_create.call_count == 2
    persisted = mock_create.call_args_list[1].kwargs["sources"]

    # Persisted == emitted, element for element.
    assert persisted == emitted

    # Collapsed to one entry per video (v1's two chunks merged).
    video_ids = [s["video_id"] for s in persisted]
    assert len(video_ids) == len(set(video_ids)), "duplicate video_id persisted"
    v1_entry = next(s for s in persisted if s["video_id"] == "v1")
    assert v1_entry["segment_count"] == 2
    assert v1_entry["is_cited"] is True


async def test_interrupted_refusal_persists_sources_none() -> None:
    """A refusal cut off mid-phrase (error payload, no [DONE]) must never
    attach source chips on reload — chips never decorate a declined answer."""
    # Refusal phrase cut mid-word; the stream errors before [DONE].
    truncated_refusal = "Sorry, the video library does not co"

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(truncated_refusal)}\n\n"
        yield 'data: {"error": "upstream flaked"}\n\n'

    mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])
    ctx, conv_id, token = _patches(mock_stream_chat, mock_create, _two_chunks())

    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5], ctx[6]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{conv_id}/messages",
                json={"content": "off-topic question"},
                headers={"Cookie": f"session={token}"},
            )

    assert "event: sources" not in response.text

    assert mock_create.call_count == 2
    assistant_kwargs = mock_create.call_args_list[1].kwargs
    assert assistant_kwargs["sources"] is None
