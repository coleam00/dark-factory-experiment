"""Regression tests for issue #277 — persisted sources must match the live view.

When an answer is interrupted or errors mid-stream, the assistant message that
gets saved must carry exactly the source citations the user saw streaming:
deduplicated, collapsed per video, capped, and refusal-suppressed the same way.
Before the fix, source finalization lived inline in the ``[DONE]`` SSE branch and
the persist path re-derived its own decision, so the two could diverge — the
reload view could show a longer, duplicated, or refusal-attached source set.

These tests pin the invariant: whatever is emitted as ``event: sources`` is what
gets persisted, and nothing is persisted when no sources event was streamed.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.auth.dependencies import get_current_user
from backend.main import app
from backend.routes.messages import MessageCreate, _finalize_source_citations, create_message


@pytest.fixture
def bypass_auth():
    stub = {"id": str(uuid4()), "email": "t@t", "is_member": True}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


# Raw chunks a single turn's tool calls returned: two chunks from the same
# video (v1) plus one from v2, AND a duplicate chunk_id. Finalization must
# collapse this to one citation per video → exactly two entries.
_RAW_CHUNKS = [
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Video One",
        "video_url": "https://youtube.com/watch?v=one",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "first snippet",
    },
    {
        "chunk_id": "c2",
        "video_id": "v1",
        "video_title": "Video One",
        "video_url": "https://youtube.com/watch?v=one",
        "start_seconds": 30.0,
        "end_seconds": 40.0,
        "snippet": "second snippet (same video)",
    },
    {
        "chunk_id": "c3",
        "video_id": "v2",
        "video_title": "Video Two",
        "video_url": "https://youtube.com/watch?v=two",
        "start_seconds": 5.0,
        "end_seconds": 15.0,
        "snippet": "third snippet",
    },
    # Duplicate chunk_id — must be dropped by dedup.
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Video One",
        "video_url": "https://youtube.com/watch?v=one",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "first snippet",
    },
]


def _parse_sources_event(sse_output: str) -> list[dict] | None:
    """Return the parsed payload of the (single) ``event: sources`` block, or
    None if no such event was streamed."""
    for raw_event in sse_output.split("\n\n"):
        lines = raw_event.split("\n")
        if lines and lines[0].strip() == "event: sources":
            data_line = next((ln for ln in lines if ln.startswith("data: ")), None)
            if data_line is not None:
                return json.loads(data_line[len("data: ") :])
    return None


def _patches(mock_stream, mock_create):
    """Common patch set for driving the message route with fakes."""

    async def mock_execute_tool(
        name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
    ):
        return {"ok": True, "text": "context", "chunks": [dict(c) for c in _RAW_CHUNKS]}

    async def mock_list_messages(conv_id, user_id):
        return [{"role": "user", "content": "hi"}]

    async def mock_list_videos():
        return [{"id": "v1", "title": "Video One"}, {"id": "v2", "title": "Video Two"}]

    return (
        patch("backend.routes.messages.repository.create_message", mock_create),
        patch(
            "backend.routes.messages.repository.list_messages",
            new=AsyncMock(side_effect=mock_list_messages),
        ),
        patch(
            "backend.routes.messages.repository.list_videos",
            new=AsyncMock(side_effect=mock_list_videos),
        ),
        patch("backend.routes.messages.rate_limit.check_and_record", new=AsyncMock()),
        patch("backend.routes.messages.stream_chat", new=mock_stream),
        patch("backend.routes.messages.execute_tool", new=mock_execute_tool),
        patch("backend.routes.messages._maybe_set_conversation_title", new=AsyncMock()),
    )


class TestFinalizeHelper:
    """The pure finalizer is the single source of truth for both paths."""

    def test_dedups_and_collapses_per_video(self) -> None:
        finalized = _finalize_source_citations([dict(c) for c in _RAW_CHUNKS], "")
        assert len(finalized) == 2
        assert {c["video_id"] for c in finalized} == {"v1", "v2"}
        # Same-video chunks collapse — the v1 entry records both segments.
        v1 = next(c for c in finalized if c["video_id"] == "v1")
        assert v1["segment_count"] == 2

    def test_empty_when_no_chunk_ids(self) -> None:
        assert _finalize_source_citations([{"video_id": "v1"}], "") == []


class TestPersistedMatchesStreamed:
    """The persisted sources must deep-equal the streamed ``event: sources``."""

    async def test_normal_answer_persists_exactly_what_streamed(self, bypass_auth: dict) -> None:
        from httpx import ASGITransport, AsyncClient

        conv_id = str(uuid4())
        answer = "Video One explains the setup."

        async def mock_stream(
            messages, tools=None, tool_executor=None, max_tool_calls=0, final_text_out=None, **_k
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield f"data: {json.dumps(answer)}\n\n"
            if final_text_out is not None:
                final_text_out.append(answer)
            yield "data: [DONE]\n\n"

        mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])

        async def mock_get_conversation(conv_id_, user_id):
            return {"id": conv_id, "user_id": bypass_auth["id"], "title": "New Conversation"}

        p = _patches(mock_stream, mock_create)
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            p[6],
            patch("backend.routes.messages.repository.get_conversation", new=mock_get_conversation),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/messages",
                    json={"content": "tell me about video one"},
                )
            await asyncio.sleep(0.05)  # let the shielded save settle

        streamed = _parse_sources_event(resp.text)
        assert streamed is not None and len(streamed) == 2

        assert mock_create.call_count == 2
        persisted = mock_create.call_args_list[1].kwargs["sources"]
        # Saved sources are EXACTLY what streamed — deduped + collapsed, never
        # the raw 4-chunk list.
        assert persisted == streamed
        assert {c["video_id"] for c in persisted} == {"v1", "v2"}

    async def test_interrupted_before_done_persists_no_sources(self, bypass_auth: dict) -> None:
        """Model errors mid-stream (no [DONE]). The live view never received a
        sources event, so the saved message must carry sources=None — not the
        raw chunks the tool happened to retrieve."""
        from httpx import ASGITransport, AsyncClient

        conv_id = str(uuid4())

        async def mock_stream(
            messages, tools=None, tool_executor=None, max_tool_calls=0, final_text_out=None, **_k
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield f"data: {json.dumps('Partial ans')}\n\n"
            # Upstream blew up: error payload, NO [DONE] sentinel.
            yield f"data: {json.dumps({'error': 'upstream timeout'})}\n\n"

        mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])

        async def mock_get_conversation(conv_id_, user_id):
            return {"id": conv_id, "user_id": bypass_auth["id"], "title": "New Conversation"}

        p = _patches(mock_stream, mock_create)
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            p[6],
            patch("backend.routes.messages.repository.get_conversation", new=mock_get_conversation),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/messages",
                    json={"content": "tell me about video one"},
                )
            await asyncio.sleep(0.05)

        assert _parse_sources_event(resp.text) is None
        assert "event: sources" not in resp.text
        assert mock_create.call_count == 2
        assert mock_create.call_args_list[1].kwargs["sources"] is None


class TestCancelAtSourcesYield:
    """The persist decision is captured BEFORE the yield that can throw, so a
    disconnect right at the sources event still saves the finalized snapshot —
    never a raw or partially-finalized one."""

    async def test_close_after_sources_event_persists_finalized_snapshot(
        self, bypass_auth: dict
    ) -> None:
        conv_id = str(uuid4())
        answer = "Video One explains the setup."

        async def mock_stream(
            messages, tools=None, tool_executor=None, max_tool_calls=0, final_text_out=None, **_k
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield f"data: {json.dumps(answer)}\n\n"
            if final_text_out is not None:
                final_text_out.append(answer)
            yield "data: [DONE]\n\n"

        mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])

        async def mock_get_conversation(conv_id_, user_id):
            return {"id": conv_id, "user_id": bypass_auth["id"], "title": "New Conversation"}

        p = _patches(mock_stream, mock_create)
        with (
            p[0],
            p[1],
            p[2],
            p[3],
            p[4],
            p[5],
            p[6],
            patch("backend.routes.messages.repository.get_conversation", new=mock_get_conversation),
        ):
            resp = await create_message(
                conv_id=conv_id,
                body=MessageCreate(content="tell me about video one"),
                current_user=bypass_auth,
            )
            body_iter = resp.body_iterator

            # Pull chunks until we have seen the sources event, then abandon
            # the stream (simulate the client hanging up at that instant).
            saw_sources = False
            async for chunk in body_iter:
                if chunk.startswith("event: sources"):
                    saw_sources = True
                    break
            assert saw_sources, "expected a sources event before [DONE]"

            await body_iter.aclose()
            await asyncio.sleep(0.05)  # let the shielded save settle

        assert mock_create.call_count == 2
        persisted = mock_create.call_args_list[1].kwargs["sources"]
        assert persisted is not None
        # The captured snapshot is the finalized (collapsed) set, not the raw
        # 4-chunk list with duplicates.
        assert len(persisted) == 2
        assert {c["video_id"] for c in persisted} == {"v1", "v2"}
