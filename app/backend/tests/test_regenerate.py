"""
Tests for POST /api/conversations/{conv_id}/messages/regenerate (issue #280).

Verifies the ordered guard flow of the regenerate endpoint:
ownership 404 → regenerability 409 (no quota charge) → rate limit 429
(conversation untouched) → atomic delete (race-safe 409) → shared SSE stream.

Mocking follows the established patterns in test_sources_event.py /
test_rate_limit.py: monkeypatched repository functions, a fake stream_chat,
and httpx.AsyncClient against the real app. Postgres is never touched.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend import rate_limit
from backend.auth.tokens import encode_token
from backend.main import app

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SOURCE_CITATIONS = [
    {
        "chunk_id": "c1",
        "video_id": "v1",
        "video_title": "Test Video",
        "video_url": "https://youtube.com/watch?v=abc",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "snippet": "Test snippet",
    }
]


def _history(conv_id: str) -> list[dict]:
    """A conversation ending in an assistant answer — the regenerable shape."""
    return [
        {
            "id": "m1",
            "conversation_id": conv_id,
            "role": "user",
            "content": "What is RAG?",
            "sources": None,
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "m2",
            "conversation_id": conv_id,
            "role": "assistant",
            "content": "Old answer",
            "sources": None,
            "created_at": "2026-01-01T00:00:01Z",
        },
    ]


def _make_stream_chat(answer_text: str, captured_messages: list | None = None):
    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if captured_messages is not None:
            captured_messages.append(messages)
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(answer_text)}\n\n"
        if final_text_out is not None:
            final_text_out.append(answer_text)
        yield "data: [DONE]\n\n"

    return mock_stream_chat


async def _mock_execute_tool(
    name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
):
    return {"ok": True, "text": "context", "chunks": [dict(c) for c in SOURCE_CITATIONS]}


class _RegenHarness:
    """Bundles the common patch set for regenerate integration tests."""

    def __init__(self, *, history: list[dict] | None = None, answer_text: str = "Fresh answer."):
        self.user_id = str(uuid4())
        self.conv_id = str(uuid4())
        self.token = encode_token(self.user_id)
        self.history = history if history is not None else _history(self.conv_id)
        self.captured_llm_messages: list = []
        self.check_and_record = AsyncMock()
        self.delete_last = AsyncMock(return_value="m2")
        self.create_message = AsyncMock(return_value={"id": str(uuid4())})
        self.answer_text = answer_text

    def patches(self):
        user_id = self.user_id
        conv_id = self.conv_id
        history = self.history

        async def mock_get_user_by_id(uid):
            return {
                "id": user_id,
                "email": "test@example.com",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(cid, user_id=None, **_kw):
            if cid == conv_id:
                return {
                    "id": conv_id,
                    "user_id": user_id,
                    "title": "Test",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            return None

        async def mock_list_messages(cid, user_id=None, **_kw):
            return [dict(m) for m in history]

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        return (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.create_message", self.create_message),
            patch("backend.db.repository.delete_last_assistant_message", self.delete_last),
            patch("backend.rate_limit.check_and_record", self.check_and_record),
            patch(
                "backend.routes.messages.stream_chat",
                _make_stream_chat(self.answer_text, self.captured_llm_messages),
            ),
            patch("backend.routes.messages.execute_tool", _mock_execute_tool),
        )

    async def post(self, client: AsyncClient, conv_id: str | None = None):
        return await client.post(
            f"/api/conversations/{conv_id or self.conv_id}/messages/regenerate",
            headers={"Cookie": f"session={self.token}"},
        )


async def _run(harness: _RegenHarness, conv_id: str | None = None):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in harness.patches():
            stack.enter_context(p)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await harness.post(client, conv_id)


# ---------------------------------------------------------------------------
# Ownership — 404, no quota charge, no delete
# ---------------------------------------------------------------------------


async def test_regenerate_404_for_unknown_conversation():
    harness = _RegenHarness()
    response = await _run(harness, conv_id=str(uuid4()))

    assert response.status_code == 404
    assert not harness.check_and_record.called, "404 must not charge quota"
    assert not harness.delete_last.called, "404 must not delete anything"


# ---------------------------------------------------------------------------
# Regenerability validation — 409 before quota
# ---------------------------------------------------------------------------


async def test_regenerate_409_for_empty_conversation():
    harness = _RegenHarness(history=[])
    response = await _run(harness)

    assert response.status_code == 409
    assert not harness.check_and_record.called, "invalid regenerate must not charge quota"
    assert not harness.delete_last.called


async def test_regenerate_409_when_last_message_is_user():
    harness = _RegenHarness()
    harness.history = harness.history[:1]  # only the user message remains
    response = await _run(harness)

    assert response.status_code == 409
    assert not harness.check_and_record.called, "invalid regenerate must not charge quota"
    assert not harness.delete_last.called


async def test_regenerate_409_when_no_user_message_precedes_assistant():
    harness = _RegenHarness()
    harness.history = [harness.history[1]]  # lone assistant message
    response = await _run(harness)

    assert response.status_code == 409
    assert not harness.check_and_record.called
    assert not harness.delete_last.called


# ---------------------------------------------------------------------------
# Rate limit — 429 body matches send path, conversation untouched
# ---------------------------------------------------------------------------


async def test_regenerate_429_leaves_conversation_intact():
    from datetime import UTC, datetime

    harness = _RegenHarness()
    reset_at = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    harness.check_and_record.side_effect = rate_limit.RateLimitExceeded(reset_at=reset_at)

    response = await _run(harness)

    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
    assert body["window_hours"] == rate_limit.WINDOW_HOURS
    assert body["reset_at"] == reset_at.isoformat()
    assert not harness.delete_last.called, "429 must run before the delete"
    assert not harness.create_message.called


# ---------------------------------------------------------------------------
# Happy path — quota charged once, delete called, fresh SSE stream
# ---------------------------------------------------------------------------


async def test_regenerate_happy_path_streams_and_persists():
    harness = _RegenHarness()
    response = await _run(harness)

    assert response.status_code == 200
    output = response.text

    # Quota charged exactly once; the stale answer was deleted.
    assert harness.check_and_record.call_count == 1
    assert harness.check_and_record.call_args.args[0] == harness.user_id
    harness.delete_last.assert_called_once_with(harness.conv_id, harness.user_id)

    # SSE stream: tokens + sources event before [DONE].
    assert '"Fresh answer."' in output or "Fresh answer." in output
    assert "event: sources" in output
    assert output.index("event: sources") < output.index("data: [DONE]")

    # History sent to the LLM ends with the user's question and excludes the
    # deleted assistant answer.
    assert len(harness.captured_llm_messages) == 1
    llm_messages = harness.captured_llm_messages[0]
    assert llm_messages[-1] == {"role": "user", "content": "What is RAG?"}
    assert all(m["content"] != "Old answer" for m in llm_messages)

    # Exactly one persist (the fresh assistant message) — no user-message insert.
    assert harness.create_message.call_count == 1
    persist_kwargs = harness.create_message.call_args.kwargs
    assert persist_kwargs["role"] == "assistant"
    assert persist_kwargs["content"] == "Fresh answer."
    assert persist_kwargs["sources"], "non-refusal answer must persist its sources"


# ---------------------------------------------------------------------------
# Concurrency — lost race returns 409, no stream
# ---------------------------------------------------------------------------


async def test_regenerate_409_when_delete_loses_race():
    harness = _RegenHarness()
    harness.delete_last.return_value = None

    response = await _run(harness)

    assert response.status_code == 409
    assert harness.captured_llm_messages == [], "no LLM stream after a lost race"
    assert not harness.create_message.called


# ---------------------------------------------------------------------------
# Refusal — shared helper still suppresses sources on regenerate
# ---------------------------------------------------------------------------


async def test_regenerate_refusal_suppresses_sources():
    refusal = "Those topics are not covered in any of the videos."
    harness = _RegenHarness(answer_text=refusal)

    response = await _run(harness)

    assert response.status_code == 200
    output = response.text
    assert "event: sources" not in output, "refusal must suppress the sources event"
    assert "data: [DONE]" in output

    assert harness.create_message.call_count == 1
    persist_kwargs = harness.create_message.call_args.kwargs
    assert persist_kwargs["role"] == "assistant"
    assert persist_kwargs["sources"] is None, "refusal must persist sources=None"
