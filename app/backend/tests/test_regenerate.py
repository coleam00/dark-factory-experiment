"""
Tests for regenerating the last assistant response (issue #280).

Covers the POST /api/conversations/{conv_id}/messages/regenerate route:
ownership (404), empty-conversation guard (409), rate-limit (429, before any
delete), the happy path (stale answer deleted, fresh answer streamed +
persisted), and the tolerant path (conversation already ends with a user
message). Also a repository unit test for delete_last_assistant_message.

Follows the mocking patterns in test_sources_event.py: monkeypatched
stream_chat / repository functions, httpx.AsyncClient against the ASGI app.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ANSWER_TEXT = "The video explains that this feature works well."
_ANSWER_CHUNK = f"data: {json.dumps(_ANSWER_TEXT)}\n\n"
_DONE_CHUNK = "data: [DONE]\n\n"

_SOURCE_CITATIONS = [
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


async def _mock_stream_chat(
    messages,
    tools=None,
    tool_executor=None,
    max_tool_calls=0,
    final_text_out=None,
    **_kwargs,
):
    if tool_executor is not None:
        await tool_executor("search_videos", json.dumps({"query": "test"}))
    yield _ANSWER_CHUNK
    if final_text_out is not None:
        final_text_out.append(_ANSWER_TEXT)
    yield _DONE_CHUNK


async def _mock_execute_tool(
    name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
):
    return {"ok": True, "text": "context", "chunks": _SOURCE_CITATIONS}


async def _mock_list_videos():
    return [{"id": "v1", "title": "Test Video", "url": "https://youtube.com/watch?v=abc"}]


def _user_factory(test_user_id: str):
    async def _get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    return _get_user_by_id


def _history(test_conv_id: str, last_role: str = "assistant"):
    """Conversation history ending in either an assistant or user message."""
    msgs = [
        {
            "id": "m1",
            "conversation_id": test_conv_id,
            "role": "user",
            "content": "What is hybrid RAG?",
            "created_at": "2026-01-01T00:00:00Z",
            "sources": None,
        },
        {
            "id": "m2",
            "conversation_id": test_conv_id,
            "role": "assistant",
            "content": "Old answer.",
            "created_at": "2026-01-01T00:00:01Z",
            "sources": None,
        },
    ]
    if last_role == "user":
        return msgs[:1]
    return msgs


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestRegenerateRoute:
    async def test_404_when_conversation_not_owned(self) -> None:
        """Unknown / other-user conversation → 404, no leak."""
        from backend.main import app

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return None

        with (
            patch(
                "backend.auth.dependencies.users_repo.get_user_by_id", _user_factory(test_user_id)
            ),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={token}"},
                )
        assert resp.status_code == 404

    async def test_409_when_no_user_message(self) -> None:
        """Empty conversation (no user message) → 409 Nothing to regenerate."""
        from backend.main import app

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return {"id": test_conv_id, "user_id": test_user_id, "title": "Test"}

        async def mock_list_messages(conv_id, user_id):
            return []

        delete_mock = AsyncMock(return_value=False)

        with (
            patch(
                "backend.auth.dependencies.users_repo.get_user_by_id", _user_factory(test_user_id)
            ),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.delete_last_assistant_message", delete_mock),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={token}"},
                )
        assert resp.status_code == 409
        delete_mock.assert_not_called()

    async def test_429_preserves_old_answer(self) -> None:
        """Rate-limited → 429 with the create_message body shape, and the old
        answer is NOT deleted (cap cannot be bypassed by regenerating)."""
        from datetime import UTC, datetime

        from backend import rate_limit
        from backend.main import app

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return {"id": test_conv_id, "user_id": test_user_id, "title": "Test"}

        async def mock_list_messages(conv_id, user_id):
            return _history(test_conv_id)

        reset_at = datetime(2026, 1, 2, tzinfo=UTC)

        async def mock_check_and_record(user_id):
            raise rate_limit.RateLimitExceeded(reset_at=reset_at)

        delete_mock = AsyncMock(return_value=True)

        with (
            patch(
                "backend.auth.dependencies.users_repo.get_user_by_id", _user_factory(test_user_id)
            ),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.rate_limit.check_and_record", mock_check_and_record),
            patch("backend.db.repository.delete_last_assistant_message", delete_mock),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={token}"},
                )
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["window_hours"] == rate_limit.WINDOW_HOURS
        assert "reset_at" in body
        # Old answer preserved — delete never ran.
        delete_mock.assert_not_called()

    async def test_happy_path_deletes_and_streams(self) -> None:
        """Last message is an assistant message → it is deleted, rate-limit is
        recorded once, a fresh answer streams with sources + [DONE], and the new
        assistant message is persisted."""
        from backend.main import app

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Existing title",
            }

        async def mock_list_messages(conv_id, user_id):
            return _history(test_conv_id)

        check_mock = AsyncMock(return_value=None)
        delete_mock = AsyncMock(return_value=True)
        create_mock = AsyncMock(side_effect=lambda **kw: {"id": str(uuid4()), **kw})

        with (
            patch(
                "backend.auth.dependencies.users_repo.get_user_by_id", _user_factory(test_user_id)
            ),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.rate_limit.check_and_record", check_mock),
            patch("backend.db.repository.delete_last_assistant_message", delete_mock),
            patch("backend.db.repository.create_message", create_mock),
            patch("backend.db.repository.list_videos", _mock_list_videos),
            patch("backend.routes.messages.stream_chat", _mock_stream_chat),
            patch("backend.routes.messages.execute_tool", _mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 200
        output = resp.text
        assert "event: sources" in output
        assert "data: [DONE]" in output

        # Rate-limit recorded exactly once (counts against the cap).
        assert check_mock.call_count == 1
        # Stale assistant message deleted with the right conv/user.
        delete_mock.assert_awaited_once_with(test_conv_id, test_user_id)
        # The fresh assistant message was persisted.
        assert create_mock.await_count == 1
        assert create_mock.await_args is not None
        assert create_mock.await_args.kwargs["role"] == "assistant"

    async def test_tolerant_when_delete_returns_false(self) -> None:
        """Conversation already ends with a user message (delete returns False)
        → still streams successfully."""
        from backend.main import app

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return {"id": test_conv_id, "user_id": test_user_id, "title": "Test"}

        async def mock_list_messages(conv_id, user_id):
            return _history(test_conv_id, last_role="user")

        check_mock = AsyncMock(return_value=None)
        delete_mock = AsyncMock(return_value=False)
        create_mock = AsyncMock(side_effect=lambda **kw: {"id": str(uuid4()), **kw})

        with (
            patch(
                "backend.auth.dependencies.users_repo.get_user_by_id", _user_factory(test_user_id)
            ),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.rate_limit.check_and_record", check_mock),
            patch("backend.db.repository.delete_last_assistant_message", delete_mock),
            patch("backend.db.repository.create_message", create_mock),
            patch("backend.db.repository.list_videos", _mock_list_videos),
            patch("backend.routes.messages.stream_chat", _mock_stream_chat),
            patch("backend.routes.messages.execute_tool", _mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 200
        assert "data: [DONE]" in resp.text
        delete_mock.assert_awaited_once()
        assert create_mock.await_count == 1


# ---------------------------------------------------------------------------
# Repository unit test
# ---------------------------------------------------------------------------


class _FakeAcquire:
    """Dual-purpose awaitable + async context manager wrapping a mock conn."""

    def __init__(self, conn):
        self._conn = conn

    def __await__(self):
        async def _do():
            return self._conn

        return _do().__await__()

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class TestDeleteLastAssistantMessageRepo:
    async def test_returns_true_and_touches_when_row_deleted(self) -> None:
        """When the outer DELETE removes a row (last message was an assistant
        message), returns True and touches the conversation."""
        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "m2"})

        with (
            patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)),
            patch.object(repository, "touch_conversation", AsyncMock()) as touch_mock,
        ):
            result = await repository.delete_last_assistant_message("conv-1", "user-1")

        assert result is True
        touch_mock.assert_awaited_once_with("conv-1", "user-1")

    async def test_returns_false_when_no_row_deleted(self) -> None:
        """When the DELETE removes nothing (trailing user message, empty
        conversation, or wrong user), returns False and does not touch."""
        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with (
            patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)),
            patch.object(repository, "touch_conversation", AsyncMock()) as touch_mock,
        ):
            result = await repository.delete_last_assistant_message("conv-1", "user-1")

        assert result is False
        touch_mock.assert_not_called()
