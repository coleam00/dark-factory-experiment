"""
Integration tests for POST /api/conversations/{conv_id}/regenerate (issue #280).

The regenerate route re-runs the RAG/streaming flow against the conversation
history with the trailing assistant message excluded, then replaces that old
assistant message in place. It must:
  - stream the same SSE wire format as create_message,
  - call replace_last_assistant_message exactly once with the fresh content,
  - exclude the trailing assistant message from the LLM history,
  - reject when there is nothing to regenerate (409) without charging quota,
  - 404 on a non-owned conversation,
  - count as exactly one message against the daily cap (429 when capped).

Mirrors the httpx.AsyncClient + monkeypatch pattern in test_sources_event.py
and leans on conftest's patch_pg_pool / patch_rate_limit fixtures.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend import rate_limit
from backend.auth.tokens import encode_token


def _answer_stream(answer_text: str):
    """Build a mock stream_chat that records the history it was called with."""
    captured: dict = {}

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        captured["messages"] = messages
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "test"}))
        yield f"data: {json.dumps(answer_text)}\n\n"
        if final_text_out is not None:
            final_text_out.append(answer_text)
        yield "data: [DONE]\n\n"

    return mock_stream_chat, captured


def _user_then_assistant(test_conv_id: str):
    """History ending in an assistant message (the regenerate-able case)."""
    return [
        {
            "id": "m1",
            "conversation_id": test_conv_id,
            "role": "user",
            "content": "What is hybrid RAG?",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": "m2",
            "conversation_id": test_conv_id,
            "role": "assistant",
            "content": "Old answer.",
            "created_at": "2026-01-01T00:00:01Z",
        },
    ]


async def _mock_execute_tool(
    name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
):
    return {
        "ok": True,
        "text": "context",
        "chunks": [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "snippet": "Test snippet",
            }
        ],
    }


def _auth(test_user_id: str):
    async def mock_get_user_by_id(user_id):
        return {
            "id": test_user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    return mock_get_user_by_id


def _owned_conversation(test_conv_id: str, test_user_id: str):
    async def mock_get_conversation(conv_id, user_id):
        if conv_id == test_conv_id and user_id == test_user_id:
            return {
                "id": test_conv_id,
                "user_id": test_user_id,
                "title": "Test",
                "created_at": "2026-01-01T00:00:00Z",
            }
        return None

    return mock_get_conversation


class TestRegenerateHappyPath:
    async def test_regenerate_streams_and_replaces(self, message_store) -> None:
        """200 + text/event-stream; replace called once; history excludes the
        trailing assistant message; rate limit charged exactly once."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        mock_stream_chat, captured = _answer_stream("Fresh answer about hybrid RAG.")
        replace_spy = AsyncMock(return_value={"id": str(uuid4())})

        async def mock_list_messages(conv_id, user_id):
            return _user_then_assistant(test_conv_id)

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _auth(test_user_id)),
            patch(
                "backend.db.repository.get_conversation",
                _owned_conversation(test_conv_id, test_user_id),
            ),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.replace_last_assistant_message", replace_spy),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", _mock_execute_tool),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert "Fresh answer about hybrid RAG." in body
        assert "data: [DONE]" in body

        # replace_last_assistant_message called once with the streamed content.
        assert replace_spy.call_count == 1
        assert replace_spy.call_args.kwargs["content"] == "Fresh answer about hybrid RAG."

        # The history handed to the LLM excludes the trailing assistant message.
        history = captured["messages"]
        assert history[-1]["role"] == "user"
        assert all(m["role"] != "assistant" for m in history)

        # Counted exactly once against the cap.
        assert len(message_store[test_user_id]) == 1


class TestRegenerateNothingToRegenerate:
    async def test_409_when_last_message_is_user(self, message_store) -> None:
        """If the last message is a user turn, 409 — and no replace, no charge."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        replace_spy = AsyncMock()

        async def mock_list_messages(conv_id, user_id):
            return [
                {
                    "id": "m1",
                    "conversation_id": test_conv_id,
                    "role": "user",
                    "content": "Hi",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _auth(test_user_id)),
            patch(
                "backend.db.repository.get_conversation",
                _owned_conversation(test_conv_id, test_user_id),
            ),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.replace_last_assistant_message", replace_spy),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 409
        assert replace_spy.call_count == 0
        # Quota not charged for an invalid request.
        assert len(message_store[test_user_id]) == 0

    async def test_409_when_no_messages(self, message_store) -> None:
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        replace_spy = AsyncMock()

        async def mock_list_messages(conv_id, user_id):
            return []

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _auth(test_user_id)),
            patch(
                "backend.db.repository.get_conversation",
                _owned_conversation(test_conv_id, test_user_id),
            ),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.replace_last_assistant_message", replace_spy),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 409
        assert replace_spy.call_count == 0
        assert len(message_store[test_user_id]) == 0


class TestRegenerateNotFound:
    async def test_404_when_conversation_not_owned(self, message_store) -> None:
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        async def mock_get_conversation(conv_id, user_id):
            return None

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _auth(test_user_id)),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 404
        assert len(message_store[test_user_id]) == 0


class TestRegenerateRateLimited:
    async def test_429_when_capped(self, message_store) -> None:
        """At the cap, regenerate returns 429 with the standard body and does
        not replace the message."""
        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        token = encode_token(test_user_id)

        # Seed the in-memory store to the cap (all within the window).
        now = datetime.now(UTC)
        message_store[test_user_id] = [
            now - timedelta(minutes=i) for i in range(rate_limit.DAILY_MESSAGE_CAP)
        ]

        replace_spy = AsyncMock()

        async def mock_list_messages(conv_id, user_id):
            return _user_then_assistant(test_conv_id)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _auth(test_user_id)),
            patch(
                "backend.db.repository.get_conversation",
                _owned_conversation(test_conv_id, test_user_id),
            ),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.replace_last_assistant_message", replace_spy),
        ):
            transport = ASGITransport(app=_app())
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/regenerate",
                    headers={"Cookie": f"session={token}"},
                )

        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert body["window_hours"] == rate_limit.WINDOW_HOURS
        assert "reset_at" in body
        assert replace_spy.call_count == 0


def _app():
    # Import lazily so conftest env + fixtures are in place first.
    from backend.main import app

    return app
