"""
Regression tests for issue #277 — saved sources must match the live view even
when the stream is interrupted or errors mid-stream (no ``[DONE]``).

Before the fix, the dedup / citation-marking / same-video-collapse / cap /
refusal-suppress pipeline ran **only** inside the ``if sse_chunk ==
"data: [DONE]\\n\\n":`` branch of ``event_generator()``. When the upstream
model flaked, ``stream_chat()`` yielded a ``data: {"error": ...}`` chunk and
returned without ``[DONE]``; the ``finally`` block then persisted a list that
had never been finalized — duplicate chunk_ids, multiple chips per video, and
chips on answers that actually refused. The live and reloaded views disagreed.

The fix routes BOTH the live ``event: sources`` path and the persist path
through the shared pure helper ``_finalize_source_citations``, so persistence
is identical to the finalized live view regardless of how the stream ended.
"""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app
from backend.routes.messages import _finalize_source_citations


# ---------------------------------------------------------------------------
# Shared fixtures — raw chunks the executor returns across two overlapping
# tool rounds: a duplicate chunk_id ("c1" twice) and two chunks from the same
# video ("v1": c1/c2). The finalized list must dedup c1 and collapse v1 to one.
# ---------------------------------------------------------------------------

_ROUND_ONE_CHUNKS = [
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Video 1",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "snippet one",
    },
    {
        "chunk_id": "c2",
        "video_id": "v1",
        "video_title": "Video 1",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 30.0,
        "end_seconds": 40.0,
        "snippet": "snippet two",
    },
]

_ROUND_TWO_CHUNKS = [
    # Duplicate of c1 (same chunk re-fetched in a later round).
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Video 1",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "snippet one",
    },
    {
        "chunk_id": "c3",
        "video_id": "v2",
        "video_title": "Video 2",
        "video_url": "https://youtube.com/watch?v=def",
        "start_seconds": 5.0,
        "end_seconds": 15.0,
        "snippet": "snippet three",
    },
]

# Raw accumulator after both rounds: 4 entries (c1 dup, c2, c3), 3 unique
# chunk_ids across 2 videos -> finalized to 2 collapsed entries.
_RAW_ACCUMULATED = _ROUND_ONE_CHUNKS + _ROUND_TWO_CHUNKS


def _make_overlapping_stream(answer_text: str, *, refusal: bool, append_final: bool):
    """Build a ``mock_stream_chat`` that runs two overlapping tool rounds, yields
    one content token, then errors WITHOUT ``[DONE]`` (interrupted stream)."""

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "round one"}))
            await tool_executor("search_videos", json.dumps({"query": "round two"}))
        yield f"data: {json.dumps(answer_text)}\n\n"
        if append_final and final_text_out is not None:
            final_text_out.append(answer_text)
        # Upstream flakes: error payload, then return WITHOUT [DONE].
        yield 'data: {"error": "upstream flaked"}\n\n'

    return mock_stream_chat


def _round_executor():
    """An execute_tool mock that returns round-one chunks on the first call and
    round-two chunks on the second, simulating overlapping retrieval."""
    calls = {"n": 0}

    async def mock_execute_tool(
        name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
    ):
        calls["n"] += 1
        chunks = _ROUND_ONE_CHUNKS if calls["n"] == 1 else _ROUND_TWO_CHUNKS
        return {"ok": True, "text": "context", "chunks": chunks}

    return mock_execute_tool


async def _drive_request(mock_stream_chat, mock_execute_tool):
    """Drive a single message POST through the ASGI app with all boundaries
    mocked. Returns (response, mock_create) so callers can inspect persistence."""
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

    # First call: user message. Second call: assistant message in finally.
    mock_create = AsyncMock(side_effect=[{"id": str(uuid4())}, {"id": str(uuid4())}])

    async def mock_list_messages(conv_id, user_id):
        return []

    async def mock_list_videos():
        return [
            {"id": "v1", "title": "Video 1", "url": "https://youtube.com/watch?v=abc"},
            {"id": "v2", "title": "Video 2", "url": "https://youtube.com/watch?v=def"},
        ]

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch("backend.db.repository.create_message", mock_create),
        patch("backend.db.repository.list_messages", mock_list_messages),
        patch("backend.db.repository.list_videos", mock_list_videos),
        patch("backend.routes.messages.stream_chat", mock_stream_chat),
        patch("backend.routes.messages.execute_tool", mock_execute_tool),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/conversations/{test_conv_id}/messages",
                json={"content": "question about video"},
                headers={"Cookie": f"session={valid_token}"},
            )

    return response, mock_create


class TestInterruptedStreamPersistence:
    """The persist path must finalize the raw accumulator identically to the
    live path even when the stream never reaches ``[DONE]``."""

    async def test_interrupted_stream_persists_deduped_collapsed_sources(self) -> None:
        """An errored stream (no [DONE]) persists deduplicated, same-video
        collapsed sources — not the raw/duplicate accumulator."""
        answer = "The video explains it works."
        mock_stream_chat = _make_overlapping_stream(answer, refusal=False, append_final=True)
        response, mock_create = await _drive_request(mock_stream_chat, _round_executor())

        assert response.status_code == 200
        # user msg + assistant msg
        assert mock_create.call_count == 2, f"expected 2 calls, got {mock_create.call_count}"
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"

        sources = assistant_kwargs["sources"]
        assert sources is not None
        # No duplicate chunk_ids survived.
        chunk_ids = [s.get("chunk_id") for s in sources]
        assert len(chunk_ids) == len(set(chunk_ids)), f"duplicate chunk_ids persisted: {chunk_ids}"
        # One entry per video_id (collapsed).
        video_ids = [s.get("video_id") for s in sources]
        assert len(video_ids) == len(set(video_ids)), f"same-video not collapsed: {video_ids}"
        assert set(video_ids) == {"v1", "v2"}
        # Each collapsed entry carries segment_count.
        assert all("segment_count" in s for s in sources)
        # Strictly fewer than the raw accumulated count (4 raw -> 2 finalized).
        assert len(sources) < len(_RAW_ACCUMULATED)
        assert len(sources) == 2

    async def test_interrupted_stream_matches_finalized_helper(self) -> None:
        """Persisted sources equal what the shared finalizer produces from the
        raw accumulated chunks — locking persist to the same pipeline as live."""
        answer = "The video explains it works."
        mock_stream_chat = _make_overlapping_stream(answer, refusal=False, append_final=True)
        response, mock_create = await _drive_request(mock_stream_chat, _round_executor())

        assert response.status_code == 200
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        expected = _finalize_source_citations(list(_RAW_ACCUMULATED), answer)
        assert assistant_kwargs["sources"] == expected

    async def test_interrupted_refusal_persists_sources_none(self) -> None:
        """When the interrupted answer is a refusal, persisted sources is None —
        no misleading chip on reload, matching the live suppression."""
        refusal = "Those topics are not covered in any of the videos."
        mock_stream_chat = _make_overlapping_stream(refusal, refusal=True, append_final=True)
        response, mock_create = await _drive_request(mock_stream_chat, _round_executor())

        assert response.status_code == 200
        assistant_kwargs = mock_create.call_args_list[1].kwargs
        assert assistant_kwargs["role"] == "assistant"
        assert assistant_kwargs["sources"] is None, (
            f"interrupted refusal should persist sources=None; "
            f"got {assistant_kwargs['sources']!r}"
        )


class TestFinalizeSourceCitationsUnit:
    """Direct unit tests of the shared finalizer."""

    def test_empty_input_returns_none(self) -> None:
        assert _finalize_source_citations([], "any text") is None

    def test_dedups_duplicate_chunk_ids(self) -> None:
        chunks = [
            {"chunk_id": "c1", "video_id": "v1", "start_seconds": 1.0},
            {"chunk_id": "c1", "video_id": "v1", "start_seconds": 1.0},
        ]
        result = _finalize_source_citations(chunks, "")
        assert result is not None
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c1"

    def test_collapses_same_video(self) -> None:
        chunks = [
            {"chunk_id": "c1", "video_id": "v1", "start_seconds": 10.0},
            {"chunk_id": "c2", "video_id": "v1", "start_seconds": 30.0},
            {"chunk_id": "c3", "video_id": "v2", "start_seconds": 5.0},
        ]
        result = _finalize_source_citations(chunks, "")
        assert result is not None
        video_ids = sorted(s["video_id"] for s in result)
        assert video_ids == ["v1", "v2"]
        # v1 collapsed two segments.
        v1 = next(s for s in result if s["video_id"] == "v1")
        assert v1["segment_count"] == 2

    def test_refusal_text_returns_none(self) -> None:
        chunks = [{"chunk_id": "c1", "video_id": "v1", "start_seconds": 1.0}]
        refusal = "Those topics are not covered in any of the videos."
        assert _finalize_source_citations(chunks, refusal) is None

    def test_chunks_without_chunk_id_return_none(self) -> None:
        chunks = [{"video_id": "v1", "start_seconds": 1.0}]
        assert _finalize_source_citations(chunks, "") is None
