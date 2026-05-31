"""
Tests for the regenerate route — POST /api/conversations/{conv_id}/regenerate
(issue #280).

Mirrors the ASGI/httpx + patch integration style of test_sources_event.py.
Patch targets for the LLM/tool boundary live in `backend.routes.regenerate`
(stream_chat / execute_tool are imported into that module's namespace);
repository functions are patched on `backend.db.repository`.

The autouse `patch_rate_limit` fixture (see conftest.py) replaces
`rate_limit.check_and_record` with an in-memory fake backed by `message_store`,
so a consumed cap shows up as an appended timestamp there.
"""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend import rate_limit
from backend.auth.tokens import encode_token


def _answer_stream(answer_text: str, *, with_tool: bool):
    """Build a mock stream_chat that optionally drives a single tool call then
    streams `answer_text` and [DONE]."""

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if with_tool and tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(answer_text)}\n\n"
        if final_text_out is not None:
            final_text_out.append(answer_text)
        yield "data: [DONE]\n\n"

    return mock_stream_chat


def _user_and_conv():
    user_id = str(uuid4())
    conv_id = str(uuid4())
    return user_id, conv_id, encode_token(user_id)


def _user_getter(user_id: str):
    async def mock_get_user_by_id(uid):
        return {
            "id": user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    return mock_get_user_by_id


def _conv_getter(user_id: str, conv_id: str):
    async def mock_get_conversation(conv_id_arg, user_id):
        if conv_id_arg == conv_id:
            return {
                "id": conv_id,
                "user_id": user_id,
                "title": "Test",
                "created_at": "2026-01-01T00:00:00Z",
            }
        return None

    return mock_get_conversation


SOURCE_CHUNKS = [
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


class TestRegenerateRoute:
    async def test_regenerate_replaces_and_counts(self, message_store) -> None:
        """Happy path: new assistant message persisted, old one deleted, cap
        consumed exactly once."""
        user_id, conv_id, token = _user_and_conv()

        async def mock_list_messages(conversation_id, user_id):
            return [
                {"id": "u1", "role": "user", "content": "the question"},
                {"id": "a-old", "role": "assistant", "content": "stale answer"},
            ]

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        mock_create = AsyncMock(return_value={"id": str(uuid4())})
        mock_delete = AsyncMock(return_value=True)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _user_getter(user_id)),
            patch("backend.db.repository.get_conversation", _conv_getter(user_id, conv_id)),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.create_message", mock_create),
            patch("backend.db.repository.delete_message", mock_delete),
            patch(
                "backend.routes.regenerate.stream_chat",
                _answer_stream("A fresh answer.", with_tool=False),
            ),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        output = response.text
        assert response.status_code == 200
        assert "A fresh answer." in output
        assert "data: [DONE]" in output

        # New assistant message persisted.
        assert mock_create.called
        assert mock_create.call_args.kwargs["role"] == "assistant"
        # Old assistant message deleted by id.
        mock_delete.assert_awaited_once()
        assert mock_delete.await_args.args[0] == "a-old"
        # Cap consumed exactly once (autouse fake records to message_store).
        assert len(message_store[user_id]) == 1

    async def test_regenerate_emits_own_sources(self) -> None:
        """The regenerated answer ships its own citations via event: sources."""
        user_id, conv_id, token = _user_and_conv()

        async def mock_list_messages(conversation_id, user_id):
            return [
                {"id": "u1", "role": "user", "content": "the question"},
                {"id": "a-old", "role": "assistant", "content": "stale answer"},
            ]

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        async def mock_execute_tool(
            name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
        ):
            return {"ok": True, "text": "context", "chunks": SOURCE_CHUNKS}

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_delete_message(message_id, user_id_arg):
            return True

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _user_getter(user_id)),
            patch("backend.db.repository.get_conversation", _conv_getter(user_id, conv_id)),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.delete_message", mock_delete_message),
            patch(
                "backend.routes.regenerate.stream_chat",
                _answer_stream("Grounded answer.", with_tool=True),
            ),
            patch("backend.routes.regenerate.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        output = response.text
        assert "event: sources" in output
        assert "c1" in output
        assert "data: [DONE]" in output

    async def test_regenerate_rate_limited_preserves_old(self, message_store) -> None:
        """A 429 leaves the old answer intact — neither create nor delete runs."""
        user_id, conv_id, token = _user_and_conv()

        async def mock_list_messages(conversation_id, user_id):
            return [
                {"id": "u1", "role": "user", "content": "the question"},
                {"id": "a-old", "role": "assistant", "content": "stale answer"},
            ]

        mock_create = AsyncMock(return_value={"id": "new"})
        mock_delete = AsyncMock(return_value=True)

        from datetime import UTC, datetime, timedelta

        reset_at = datetime.now(UTC) + timedelta(hours=24)

        async def raising_check(uid):
            raise rate_limit.RateLimitExceeded(reset_at=reset_at)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _user_getter(user_id)),
            patch("backend.db.repository.get_conversation", _conv_getter(user_id, conv_id)),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.create_message", mock_create),
            patch("backend.db.repository.delete_message", mock_delete),
            patch.object(rate_limit, "check_and_record", raising_check),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["window_hours"] == rate_limit.WINDOW_HOURS
        assert "reset_at" in body
        # Old answer preserved — no persistence, no delete.
        assert not mock_create.called
        assert not mock_delete.called

    async def test_regenerate_409_when_last_is_user(self, message_store) -> None:
        """No trailing assistant message → 409 and the cap is NOT consumed."""
        user_id, conv_id, token = _user_and_conv()

        async def mock_list_messages(conversation_id, user_id):
            return [{"id": "u1", "role": "user", "content": "the question"}]

        mock_create = AsyncMock(return_value={"id": "new"})
        mock_delete = AsyncMock(return_value=True)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _user_getter(user_id)),
            patch("backend.db.repository.get_conversation", _conv_getter(user_id, conv_id)),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.create_message", mock_create),
            patch("backend.db.repository.delete_message", mock_delete),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 409
        # Guard runs before rate-limit, so no cap was consumed.
        assert len(message_store[user_id]) == 0
        assert not mock_create.called
        assert not mock_delete.called

    async def test_regenerate_404_foreign_conversation(self) -> None:
        """A conversation the user doesn't own (get_conversation → None) → 404."""
        user_id, conv_id, token = _user_and_conv()

        async def mock_get_conversation(conv_id, user_id):
            return None

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _user_getter(user_id)),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 404


class TestDeleteMessageRepository:
    """Unit tests for repository.delete_message (owner-scoped DELETE)."""

    @pytest.mark.parametrize(
        ("execute_result", "expected"),
        [("DELETE 1", True), ("DELETE 0", False)],
    )
    async def test_delete_message_returns_row_deleted(self, execute_result, expected) -> None:
        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=execute_result)

        class _FakeAcquire:
            def __await__(self):
                async def _do():
                    return mock_conn

                return _do().__await__()

            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *exc):
                return False

        with patch.object(repository, "_acquire", lambda: _FakeAcquire()):
            result = await repository.delete_message("msg-1", "user-1")

        assert result is expected
        # Owner-scoped: parameters are (message_id, user_id) in that order.
        assert mock_conn.execute.await_args.args[1] == "msg-1"
        assert mock_conn.execute.await_args.args[2] == "user-1"


def _app():
    # Imported lazily so conftest env/monkeypatches are applied first.
    from backend.main import app

    return app
