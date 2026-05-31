"""Tests for POST /api/messages/{message_id}/regenerate (issue #280).

Covers:
- Repository ownership invariant (structural, no DB hit)
- Happy path: streams a new response, calls create_message and delete_message,
  increments the rate-limit counter
- Error paths: 404 (not found), 403 (not an assistant), 400 (not last / no
  preceding user message), 429 (rate limit exceeded)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.auth.dependencies import get_current_user
from backend.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_user() -> dict[str, Any]:
    return {"id": str(uuid4()), "email": "test@example.com", "is_member": False}


@pytest.fixture
def bypass_auth(fake_user: dict[str, Any]):
    app.dependency_overrides[get_current_user] = lambda: fake_user
    yield fake_user
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(role: str = "assistant", conv_id: str | None = None) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "conversation_id": conv_id or str(uuid4()),
        "role": role,
        "content": "some content",
        "sources": None,
        "created_at": datetime.now(UTC).isoformat(),
    }


async def _drain(body_iter) -> list[str]:
    """Collect all chunks from a StreamingResponse body iterator."""
    chunks: list[str] = []
    async for chunk in body_iter:
        chunks.append(chunk)
    return chunks


async def _fake_stream(*args: Any, **kwargs: Any):
    final_text_out = kwargs.get("final_text_out")
    for token in ("Hello", " world"):
        yield f"data: {json.dumps(token)}\n\n"
    if final_text_out is not None:
        final_text_out.append("Hello world")
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Repository unit tests — ownership invariants
# ---------------------------------------------------------------------------


async def test_delete_message_requires_user_id():
    """delete_message must accept a user_id param — the ownership guard."""
    import inspect

    from backend.db import repository

    params = inspect.signature(repository.delete_message).parameters
    assert "user_id" in params


async def test_get_message_requires_user_id():
    """get_message must accept a user_id param — the ownership guard."""
    import inspect

    from backend.db import repository

    params = inspect.signature(repository.get_message).parameters
    assert "user_id" in params


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRegenerateHappyPath:
    async def test_streams_response_and_replaces_old_message(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        user_id = bypass_auth["id"]
        conv_id = str(uuid4())
        old_id = str(uuid4())

        user_msg = _msg(role="user", conv_id=conv_id)
        old_msg = {**_msg(role="assistant", conv_id=conv_id), "id": old_id}

        with (
            patch(
                "backend.routes.messages.repository.get_message",
                new_callable=AsyncMock,
                return_value=old_msg,
            ),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[user_msg, old_msg],
            ),
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                return_value={"id": str(uuid4())},
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.delete_message",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_delete,
            patch("backend.routes.messages.repository.list_videos", new_callable=AsyncMock, return_value=[]),
            patch("backend.routes.messages.stream_chat", side_effect=_fake_stream),
            patch("backend.routes.messages.LLM_TOOLS_ENABLED", False),
        ):
            from backend.routes.messages import regenerate_message

            resp = await regenerate_message(message_id=old_id, current_user=bypass_auth)
            chunks = await _drain(resp.body_iterator)

        # SSE tokens and terminator present
        assert any(json.dumps("Hello") in c for c in chunks)
        assert any("[DONE]" in c for c in chunks)

        # New message persisted with assistant content
        mock_create.assert_awaited_once()
        kw = mock_create.call_args.kwargs
        assert kw["role"] == "assistant"
        assert "Hello" in kw["content"]
        assert kw["conversation_id"] == conv_id

        # Old message deleted with the correct ownership args
        mock_delete.assert_awaited_once()
        del_args = mock_delete.call_args.args
        assert del_args[0] == old_id
        assert del_args[1] == user_id

    async def test_increments_rate_limit_counter(
        self, bypass_auth: dict[str, Any], message_store
    ) -> None:
        user_id = bypass_auth["id"]
        conv_id = str(uuid4())
        old_id = str(uuid4())
        user_msg = _msg(role="user", conv_id=conv_id)
        old_msg = {**_msg(role="assistant", conv_id=conv_id), "id": old_id}

        with (
            patch("backend.routes.messages.repository.get_message", new_callable=AsyncMock, return_value=old_msg),
            patch("backend.routes.messages.repository.list_messages", new_callable=AsyncMock, return_value=[user_msg, old_msg]),
            patch("backend.routes.messages.repository.create_message", new_callable=AsyncMock, return_value={"id": str(uuid4())}),
            patch("backend.routes.messages.repository.delete_message", new_callable=AsyncMock, return_value=True),
            patch("backend.routes.messages.repository.list_videos", new_callable=AsyncMock, return_value=[]),
            patch("backend.routes.messages.stream_chat", side_effect=_fake_stream),
            patch("backend.routes.messages.LLM_TOOLS_ENABLED", False),
        ):
            from backend.routes.messages import regenerate_message

            resp = await regenerate_message(message_id=old_id, current_user=bypass_auth)
            await _drain(resp.body_iterator)

        # Exactly one audit row recorded for this user
        assert len(message_store[user_id]) == 1


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestRegenerateErrors:
    async def test_404_when_message_not_found(self, bypass_auth: dict[str, Any]) -> None:
        from fastapi import HTTPException

        with patch(
            "backend.routes.messages.repository.get_message",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc_info:
                await regenerate_message(message_id=str(uuid4()), current_user=bypass_auth)

        assert exc_info.value.status_code == 404

    async def test_403_when_target_is_user_message(self, bypass_auth: dict[str, Any]) -> None:
        from fastapi import HTTPException

        target = _msg(role="user")

        with patch(
            "backend.routes.messages.repository.get_message",
            new_callable=AsyncMock,
            return_value=target,
        ):
            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc_info:
                await regenerate_message(message_id=target["id"], current_user=bypass_auth)

        assert exc_info.value.status_code == 403

    async def test_400_when_not_the_last_message(self, bypass_auth: dict[str, Any]) -> None:
        from fastapi import HTTPException

        conv_id = str(uuid4())
        target = _msg(role="assistant", conv_id=conv_id)
        later = _msg(role="user", conv_id=conv_id)

        with (
            patch("backend.routes.messages.repository.get_message", new_callable=AsyncMock, return_value=target),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[target, later],  # target is NOT the last element
            ),
        ):
            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc_info:
                await regenerate_message(message_id=target["id"], current_user=bypass_auth)

        assert exc_info.value.status_code == 400
        assert "last" in exc_info.value.detail.lower()

    async def test_400_when_no_preceding_user_message(self, bypass_auth: dict[str, Any]) -> None:
        from fastapi import HTTPException

        conv_id = str(uuid4())
        target = _msg(role="assistant", conv_id=conv_id)

        with (
            patch("backend.routes.messages.repository.get_message", new_callable=AsyncMock, return_value=target),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[target],  # no user message before it
            ),
        ):
            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc_info:
                await regenerate_message(message_id=target["id"], current_user=bypass_auth)

        assert exc_info.value.status_code == 400
        assert "preceding" in exc_info.value.detail.lower()

    async def test_429_when_rate_limit_exceeded(
        self, bypass_auth: dict[str, Any], message_store
    ) -> None:
        from fastapi.responses import JSONResponse

        from backend import rate_limit

        user_id = bypass_auth["id"]
        conv_id = str(uuid4())
        target = _msg(role="assistant", conv_id=conv_id)
        user_msg = _msg(role="user", conv_id=conv_id)

        # Seed the cap so the next call triggers 429
        now = datetime.now(UTC)
        message_store[user_id] = [now - timedelta(minutes=i) for i in range(rate_limit.DAILY_MESSAGE_CAP)]

        with (
            patch("backend.routes.messages.repository.get_message", new_callable=AsyncMock, return_value=target),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=[user_msg, target],
            ),
        ):
            from backend.routes.messages import regenerate_message

            resp = await regenerate_message(message_id=target["id"], current_user=bypass_auth)

        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 429

        body = json.loads(resp.body)
        assert body["error"] == "rate_limit_exceeded"
        assert body["limit"] == rate_limit.DAILY_MESSAGE_CAP
        assert "reset_at" in body

        # Counter must NOT have grown — 429 path does not record
        assert len(message_store[user_id]) == rate_limit.DAILY_MESSAGE_CAP
