"""
Tests for the regenerate-last-answer endpoint (issue #280).

POST /api/conversations/{conv_id}/regenerate must:
  - count against the daily cap exactly like a normal send (MISSION §10 #1),
  - 404 when the conversation has no assistant turn to replace,
  - 404 on a cross-user attempt (no leak of existence),
  - stream the fresh answer in the identical SSE shape as a normal send
    (token chunks → sources event → [DONE]),
  - delete only the most recent assistant message (repository unit test).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token

# ---------------------------------------------------------------------------
# Shared mock builders
# ---------------------------------------------------------------------------

_ANSWER_TEXT = "Here is a fresh take on your question."
_SOURCES = [
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
    yield f"data: {json.dumps(_ANSWER_TEXT)}\n\n"
    if final_text_out is not None:
        final_text_out.append(_ANSWER_TEXT)
    yield "data: [DONE]\n\n"


async def _mock_execute_tool(
    name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
):
    return {"ok": True, "text": "context", "chunks": _SOURCES}


async def _mock_list_videos():
    return [{"id": "v1", "title": "Test Video", "url": "https://youtube.com/watch?v=abc"}]


def _patches(*, conv_owner_id: str, conv_id: str, deleted_id: str | None, create_mock):
    """Build the standard patch set for an authenticated regenerate request."""

    async def mock_get_user_by_id(user_id):
        return {
            "id": conv_owner_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    async def mock_get_conversation(c_id, user_id):
        # Owner-scoped: only return the conversation for its real owner.
        if c_id == conv_id and str(user_id) == conv_owner_id:
            return {
                "id": conv_id,
                "user_id": conv_owner_id,
                "title": "Existing Title",
                "created_at": "2026-01-01T00:00:00Z",
            }
        return None

    async def mock_delete_last_assistant_message(conversation_id, user_id):
        return deleted_id

    async def mock_list_messages(c_id, user_id):
        # History ends on the user's question (assistant already deleted).
        return [{"role": "user", "content": "original question"}]

    return [
        patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
        patch("backend.db.repository.get_conversation", mock_get_conversation),
        patch(
            "backend.db.repository.delete_last_assistant_message",
            mock_delete_last_assistant_message,
        ),
        patch("backend.db.repository.create_message", create_mock),
        patch("backend.db.repository.list_messages", mock_list_messages),
        patch("backend.db.repository.list_videos", _mock_list_videos),
        patch("backend.routes.messages.stream_chat", _mock_stream_chat),
        patch("backend.routes.messages.execute_tool", _mock_execute_tool),
    ]


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------


class TestRegenerateEndpoint:
    async def test_regenerate_streams_sse_shape_matching_send(self) -> None:
        """Happy path: regenerate emits token chunks, a sources event, and [DONE]."""
        from backend.main import app

        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)
        create_mock = AsyncMock(return_value={"id": str(uuid4())})

        with _block(
            _patches(
                conv_owner_id=user_id,
                conv_id=conv_id,
                deleted_id=str(uuid4()),
                create_mock=create_mock,
            )
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 200
        out = resp.text
        assert json.dumps(_ANSWER_TEXT) in out  # token chunk
        assert "event: sources" in out  # its own citations
        assert "data: [DONE]" in out
        # The fresh assistant message was persisted.
        assert create_mock.call_count == 1
        assert create_mock.call_args.kwargs["role"] == "assistant"

    async def test_regenerate_consumes_rate_limit_quota(self, message_store) -> None:
        """A successful regenerate records one message against the daily cap."""
        from backend.main import app

        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)
        create_mock = AsyncMock(return_value={"id": str(uuid4())})

        assert len(message_store[user_id]) == 0
        with _block(
            _patches(
                conv_owner_id=user_id,
                conv_id=conv_id,
                deleted_id=str(uuid4()),
                create_mock=create_mock,
            )
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 200
        # check_and_record (conftest fake) appended exactly one timestamp.
        assert len(message_store[user_id]) == 1

    async def test_regenerate_rejected_when_cap_reached(self, message_store) -> None:
        """At the cap, regenerate returns 429 and never streams."""
        from datetime import UTC, datetime

        from backend import rate_limit
        from backend.main import app

        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)
        # Pre-fill the window to the cap so check_and_record raises.
        message_store[user_id] = [datetime.now(UTC)] * rate_limit.DAILY_MESSAGE_CAP
        create_mock = AsyncMock(return_value={"id": str(uuid4())})

        with _block(
            _patches(
                conv_owner_id=user_id,
                conv_id=conv_id,
                deleted_id=str(uuid4()),
                create_mock=create_mock,
            )
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 429
        body = resp.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        # No assistant message was persisted on a rate-limited regenerate.
        assert create_mock.call_count == 0

    async def test_regenerate_404_when_no_assistant_message(self) -> None:
        """When the conversation has no assistant turn, regenerate 404s and
        does not stream."""
        from backend.main import app

        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)
        create_mock = AsyncMock(return_value={"id": str(uuid4())})

        with _block(
            _patches(
                conv_owner_id=user_id,
                conv_id=conv_id,
                deleted_id=None,  # nothing to delete
                create_mock=create_mock,
            )
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 404
        assert create_mock.call_count == 0

    async def test_regenerate_cross_user_returns_404(self) -> None:
        """A user regenerating someone else's conversation gets 404 (no leak),
        and the assistant message is never deleted."""
        from backend.main import app

        owner_id = str(uuid4())
        attacker_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(attacker_id)
        create_mock = AsyncMock(return_value={"id": str(uuid4())})

        delete_mock = AsyncMock(return_value=str(uuid4()))

        async def mock_get_user_by_id(user_id):
            return {
                "id": attacker_id,
                "email": "attacker@example.com",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(c_id, user_id):
            # Conversation belongs to owner_id; attacker query returns None.
            if c_id == conv_id and str(user_id) == owner_id:
                return {"id": conv_id, "user_id": owner_id, "title": "t"}
            return None

        with _block(
            [
                patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
                patch("backend.db.repository.get_conversation", mock_get_conversation),
                patch("backend.db.repository.delete_last_assistant_message", delete_mock),
                patch("backend.db.repository.create_message", create_mock),
            ]
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/conversations/{conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert resp.status_code == 404
        delete_mock.assert_not_called()
        assert create_mock.call_count == 0


# ---------------------------------------------------------------------------
# Repository unit test: delete_last_assistant_message
# ---------------------------------------------------------------------------


class TestDeleteLastAssistantMessage:
    async def test_returns_deleted_id_and_targets_newest_assistant(self) -> None:
        """The DELETE selects the newest assistant message (ORDER BY created_at
        DESC LIMIT 1) scoped to the owner, and returns its id."""
        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "msg-newest"})
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")  # touch_conversation

        with patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)):
            deleted = await repository.delete_last_assistant_message(
                conversation_id="conv-1", user_id="user-1"
            )

        assert deleted == "msg-newest"
        sql = mock_conn.fetchrow.call_args.args[0]
        assert "DELETE FROM messages" in sql
        assert "role = 'assistant'" in sql
        assert "ORDER BY m.created_at DESC" in sql
        assert "LIMIT 1" in sql
        # Ownership join present so a cross-user call can't delete.
        assert "c.user_id = $2" in sql

    async def test_returns_none_when_no_assistant_message(self) -> None:
        """No assistant row → returns None and does not touch the conversation."""
        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock()

        with patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)):
            deleted = await repository.delete_last_assistant_message(
                conversation_id="conv-1", user_id="user-1"
            )

        assert deleted is None
        # touch_conversation must not run when nothing was deleted.
        mock_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test helpers
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


class _block:
    """Enter a list of context managers together (like contextlib.ExitStack)."""

    def __init__(self, managers):
        self._managers = managers

    def __enter__(self):
        for m in self._managers:
            m.__enter__()
        return self

    def __exit__(self, *exc):
        for m in reversed(self._managers):
            m.__exit__(*exc)
        return False
