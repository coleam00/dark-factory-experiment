"""
Tests for POST /api/conversations/{conv_id}/messages/regenerate (issue #280).

Verifies the regenerate endpoint:
- streams a fresh assistant answer (tokens + sources event + [DONE]) after
  deleting the stale one,
- counts regeneration against the 25 msg/24h cap (MISSION §10 invariant #1),
- rejects over-cap requests with 429 BEFORE any mutation (no quota bypass),
- rejects conversations with nothing to regenerate (409) without burning a
  quota slot,
- returns 404 for cross-user access (no existence leak).

Follows the integration patterns in test_sources_event.py /
test_message_persist_shield.py: patch repository.* and the route's
stream_chat import, drive the app with httpx.AsyncClient.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app


def _user_msg(conv_id: str, content: str = "What is RAG?") -> dict:
    return {
        "id": str(uuid4()),
        "conversation_id": conv_id,
        "role": "user",
        "content": content,
        "sources": None,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _assistant_msg(conv_id: str, content: str = "Old answer.") -> dict:
    return {
        "id": str(uuid4()),
        "conversation_id": conv_id,
        "role": "assistant",
        "content": content,
        "sources": None,
        "created_at": "2026-01-01T00:00:01Z",
    }


def _make_mocks(test_user_id: str, test_conv_id: str, history: list[dict]):
    """Build the standard mock set used by every test in this module."""

    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "created_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_list_messages(conv_id, user_id):
        return history

    async def mock_list_videos():
        return [{"id": "v1", "title": "Test Video", "url": "u"}]

    return mock_get_user_by_id, mock_get_conversation, mock_list_messages, mock_list_videos


def _make_stream_chat(answer_text: str = "Fresh answer."):
    """A stream_chat fake that runs one tool call and yields tokens + [DONE]."""
    source_citations = [
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

    async def mock_execute_tool(
        name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
    ):
        return {"ok": True, "text": "context", "chunks": source_citations}

    return mock_stream_chat, mock_execute_tool


class TestRegenerateHappyPath:
    async def test_regenerate_streams_fresh_answer(self) -> None:
        """Deletes the stale answer, streams tokens + sources + [DONE], and
        persists the new assistant message."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        history = [_user_msg(test_conv_id), _assistant_msg(test_conv_id)]
        get_user, get_conv, list_msgs, list_videos = _make_mocks(
            test_user_id, test_conv_id, history
        )
        mock_stream_chat, mock_execute_tool = _make_stream_chat("Fresh answer.")

        mock_delete = AsyncMock(return_value=history[1]["id"])
        mock_create = AsyncMock(return_value={"id": str(uuid4())})

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
            patch("backend.db.repository.get_conversation", get_conv),
            patch("backend.db.repository.list_messages", list_msgs),
            patch("backend.db.repository.list_videos", list_videos),
            patch("backend.db.repository.delete_last_assistant_message", mock_delete),
            patch("backend.db.repository.create_message", mock_create),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 200
        output = response.text
        assert "Fresh answer." in output
        assert "event: sources" in output
        assert "data: [DONE]" in output

        # The stale assistant row was deleted (owner-scoped).
        mock_delete.assert_awaited_once_with(test_conv_id, test_user_id)

        # The fresh answer was persisted as an assistant message — and no
        # user message was inserted (regenerate re-runs the existing turn).
        assert mock_create.await_count == 1
        persisted = mock_create.call_args.kwargs
        assert persisted["role"] == "assistant"
        assert persisted["content"] == "Fresh answer."

    async def test_regenerate_counts_against_rate_limit(self) -> None:
        """check_and_record is awaited exactly once on the happy path."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        history = [_user_msg(test_conv_id), _assistant_msg(test_conv_id)]
        get_user, get_conv, list_msgs, list_videos = _make_mocks(
            test_user_id, test_conv_id, history
        )
        mock_stream_chat, mock_execute_tool = _make_stream_chat()

        mock_check = AsyncMock(return_value=None)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
            patch("backend.db.repository.get_conversation", get_conv),
            patch("backend.db.repository.list_messages", list_msgs),
            patch("backend.db.repository.list_videos", list_videos),
            patch("backend.db.repository.delete_last_assistant_message", AsyncMock()),
            patch("backend.db.repository.create_message", AsyncMock(return_value={"id": "m"})),
            patch("backend.rate_limit.check_and_record", mock_check),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 200
        assert mock_check.await_count == 1
        mock_check.assert_awaited_once_with(test_user_id)


class TestRegenerateRateLimit:
    async def test_regenerate_over_cap_returns_429_and_does_not_delete(self) -> None:
        """Over-cap regenerate is rejected with the standard 429 body and the
        stale answer is NOT deleted (no mutation, no quota bypass)."""
        from backend import rate_limit

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        history = [_user_msg(test_conv_id), _assistant_msg(test_conv_id)]
        get_user, get_conv, list_msgs, list_videos = _make_mocks(
            test_user_id, test_conv_id, history
        )

        reset_at = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
        mock_check = AsyncMock(side_effect=rate_limit.RateLimitExceeded(reset_at=reset_at))
        mock_delete = AsyncMock()
        mock_create = AsyncMock()

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
            patch("backend.db.repository.get_conversation", get_conv),
            patch("backend.db.repository.list_messages", list_msgs),
            patch("backend.db.repository.list_videos", list_videos),
            patch("backend.db.repository.delete_last_assistant_message", mock_delete),
            patch("backend.db.repository.create_message", mock_create),
            patch("backend.rate_limit.check_and_record", mock_check),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["window_hours"] == rate_limit.WINDOW_HOURS
        assert body["reset_at"] == reset_at.isoformat()

        # No mutation happened — the old answer survives.
        mock_delete.assert_not_awaited()
        mock_create.assert_not_awaited()


class TestRegeneratePreconditions:
    async def test_regenerate_no_assistant_returns_409_and_does_not_record(self) -> None:
        """History ending with a user message → 409, and no quota slot burned."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        history = [_user_msg(test_conv_id)]
        get_user, get_conv, list_msgs, list_videos = _make_mocks(
            test_user_id, test_conv_id, history
        )

        mock_check = AsyncMock()
        mock_delete = AsyncMock()

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
            patch("backend.db.repository.get_conversation", get_conv),
            patch("backend.db.repository.list_messages", list_msgs),
            patch("backend.db.repository.list_videos", list_videos),
            patch("backend.db.repository.delete_last_assistant_message", mock_delete),
            patch("backend.rate_limit.check_and_record", mock_check),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 409
        mock_check.assert_not_awaited()
        mock_delete.assert_not_awaited()

    async def test_regenerate_empty_history_returns_409(self) -> None:
        """An empty conversation has nothing to regenerate."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        get_user, get_conv, list_msgs, list_videos = _make_mocks(test_user_id, test_conv_id, [])

        mock_check = AsyncMock()

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
            patch("backend.db.repository.get_conversation", get_conv),
            patch("backend.db.repository.list_messages", list_msgs),
            patch("backend.db.repository.list_videos", list_videos),
            patch("backend.rate_limit.check_and_record", mock_check),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 409
        mock_check.assert_not_awaited()


class TestRegenerateScoping:
    async def test_regenerate_cross_user_returns_404(self) -> None:
        """A conversation the user doesn't own → 404 (no existence leak), no mutation."""
        test_user_id = str(uuid4())
        other_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        async def mock_get_user_by_id(user_id):
            return {
                "id": test_user_id,
                "email": "test@example.com",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(conv_id, user_id):
            return None

        mock_check = AsyncMock()
        mock_delete = AsyncMock()

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.delete_last_assistant_message", mock_delete),
            patch("backend.rate_limit.check_and_record", mock_check),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{other_conv_id}/messages/regenerate",
                    headers={"Cookie": f"session={valid_token}"},
                )

        assert response.status_code == 404
        mock_check.assert_not_awaited()
        mock_delete.assert_not_awaited()
