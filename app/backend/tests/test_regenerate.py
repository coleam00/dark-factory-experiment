"""
Tests for POST /api/conversations/{conv_id}/messages/regenerate
(routes/messages.py::regenerate_message).

The regenerate flow must:
  1. Delete the latest assistant message and stream a replacement that is
     persisted in its place (with its own sources).
  2. Count against the 25 msg/user/24h cap — `rate_limit.check_and_record`
     runs BEFORE the delete so a user cannot delete-spam past the cap.
  3. Refuse (400) when there is no assistant answer to regenerate, WITHOUT
     consuming a quota slot or deleting anything.
  4. Not re-run the conversation auto-title logic (no new user message).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.auth.dependencies import get_current_user
from backend.main import app


@pytest.fixture
def bypass_auth():
    stub = {"id": str(uuid4()), "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


async def _fake_stream(*args: Any, **kwargs: Any):
    final_text_out = kwargs.get("final_text_out")
    for token in ("Fresh", " answer", "."):
        yield f"data: {json.dumps(token)}\n\n"
    if final_text_out is not None:
        final_text_out.append("Fresh answer.")
    yield "data: [DONE]\n\n"


async def _drain(resp: Any) -> None:
    """Consume the StreamingResponse so the persist `finally` block runs."""
    async for _ in resp.body_iterator:
        pass
    # Let the shielded save settle.
    await asyncio.sleep(0.05)


class TestRegenerateHappyPath:
    async def test_deletes_old_answer_records_quota_and_persists_replacement(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        user = bypass_auth
        conv = {"id": str(uuid4()), "user_id": user["id"], "title": "Some title"}
        last_assistant_id = str(uuid4())
        history = [
            {"id": str(uuid4()), "role": "user", "content": "what is X?"},
            {"id": last_assistant_id, "role": "assistant", "content": "old answer"},
        ]

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=conv,
            ),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=history,
            ),
            patch(
                "backend.routes.messages.rate_limit.check_and_record",
                new_callable=AsyncMock,
            ) as mock_rate,
            patch(
                "backend.routes.messages.repository.delete_message",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_delete,
            patch(
                "backend.routes.messages.repository.create_message",
                new_callable=AsyncMock,
                return_value={"id": str(uuid4())},
            ) as mock_create,
            patch(
                "backend.routes.messages.repository.list_videos",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("backend.routes.messages.stream_chat", side_effect=_fake_stream),
            patch(
                "backend.routes.messages._maybe_set_conversation_title",
                new_callable=AsyncMock,
            ) as mock_title,
        ):
            from backend.routes.messages import regenerate_message

            resp = await regenerate_message(conv_id=conv["id"], current_user=user)
            await _drain(resp)

        # Quota consumed exactly once.
        mock_rate.assert_awaited_once()
        # Old assistant message deleted (scoped to conv + user).
        mock_delete.assert_awaited_once()
        del_args = mock_delete.await_args.args
        assert del_args[0] == last_assistant_id
        assert del_args[1] == conv["id"]
        assert del_args[2] == user["id"]
        # Replacement persisted as an assistant message.
        mock_create.assert_awaited_once()
        assert mock_create.await_args.kwargs["role"] == "assistant"
        assert "Fresh answer" in mock_create.await_args.kwargs["content"]
        # Title logic must NOT re-run (no new user message).
        mock_title.assert_not_called()


class TestRegenerateGuards:
    async def test_400_when_last_message_is_not_assistant(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """No assistant answer to regenerate → 400, no quota burned, no delete."""
        user = bypass_auth
        conv = {"id": str(uuid4()), "user_id": user["id"], "title": "t"}
        history = [{"id": str(uuid4()), "role": "user", "content": "hi"}]

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=conv,
            ),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=history,
            ),
            patch(
                "backend.routes.messages.rate_limit.check_and_record",
                new_callable=AsyncMock,
            ) as mock_rate,
            patch(
                "backend.routes.messages.repository.delete_message",
                new_callable=AsyncMock,
            ) as mock_delete,
        ):
            from fastapi import HTTPException

            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc:
                await regenerate_message(conv_id=conv["id"], current_user=user)
            assert exc.value.status_code == 400

        mock_rate.assert_not_called()
        mock_delete.assert_not_called()

    async def test_rate_limited_does_not_delete_old_answer(
        self, bypass_auth: dict[str, Any]
    ) -> None:
        """When the user is over the cap, the old answer must NOT be deleted."""
        from datetime import datetime, timezone

        from backend import rate_limit

        user = bypass_auth
        conv = {"id": str(uuid4()), "user_id": user["id"], "title": "t"}
        history = [
            {"id": str(uuid4()), "role": "user", "content": "q"},
            {"id": str(uuid4()), "role": "assistant", "content": "a"},
        ]

        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=conv,
            ),
            patch(
                "backend.routes.messages.repository.list_messages",
                new_callable=AsyncMock,
                return_value=history,
            ),
            patch(
                "backend.routes.messages.rate_limit.check_and_record",
                new_callable=AsyncMock,
                side_effect=rate_limit.RateLimitExceeded(
                    reset_at=datetime(2030, 1, 1, tzinfo=timezone.utc)
                ),
            ),
            patch(
                "backend.routes.messages.repository.delete_message",
                new_callable=AsyncMock,
            ) as mock_delete,
        ):
            from backend.routes.messages import regenerate_message

            resp = await regenerate_message(conv_id=conv["id"], current_user=user)

        assert resp.status_code == 429
        mock_delete.assert_not_called()

    async def test_404_when_conversation_not_owned(self, bypass_auth: dict[str, Any]) -> None:
        user = bypass_auth
        with (
            patch(
                "backend.routes.messages.repository.get_conversation",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.routes.messages.rate_limit.check_and_record",
                new_callable=AsyncMock,
            ) as mock_rate,
        ):
            from fastapi import HTTPException

            from backend.routes.messages import regenerate_message

            with pytest.raises(HTTPException) as exc:
                await regenerate_message(conv_id=str(uuid4()), current_user=user)
            assert exc.value.status_code == 404
        mock_rate.assert_not_called()
