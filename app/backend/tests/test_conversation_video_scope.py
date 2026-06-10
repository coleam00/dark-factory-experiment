"""Tests for per-conversation video scope (issue #279).

Covers:
  - PATCH /conversations/{id}/scope happy path, [] normalization, unknown-id
    422, and non-owner/missing 404.
  - Message flow: a scoped conversation threads video_ids into execute_tool and
    intersects the transcript whitelist with the scope.
  - Whitelist-load-failure + scoped conversation still guards the transcript
    tool with the scope set (never None).

Mock-based per existing conventions (see test_sources_event.py for the
route-level mocking pattern).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app


def _mock_user(user_id: str):
    async def mock_get_user_by_id(_uid):
        return {
            "id": user_id,
            "email": "test@example.com",
            "password_hash": "hashed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    return mock_get_user_by_id


# ---------------------------------------------------------------------------
# PATCH /conversations/{id}/scope
# ---------------------------------------------------------------------------


class TestUpdateScopeEndpoint:
    async def test_happy_path_sets_scope_and_returns_row(self) -> None:
        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)

        async def mock_list_videos():
            return [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]

        update_mock = AsyncMock(return_value=True)

        async def mock_get_conversation(cid, user_id):
            return {
                "id": conv_id,
                "user_id": user_id,
                "title": "Test",
                "scoped_video_ids": ["v1", "v2"],
                "created_at": "2026-01-01T00:00:00Z",
            }

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.update_conversation_scope", update_mock),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(
                    f"/api/conversations/{conv_id}/scope",
                    json={"video_ids": ["v1", "v2"]},
                    headers={"Cookie": f"session={token}"},
                )

        assert r.status_code == 200, r.text
        assert r.json()["scoped_video_ids"] == ["v1", "v2"]
        # The repository writer received exactly the requested scope.
        update_mock.assert_awaited_once()
        assert update_mock.call_args.args[2] == ["v1", "v2"]

    async def test_empty_list_normalizes_to_none(self) -> None:
        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)

        update_mock = AsyncMock(return_value=True)

        async def mock_get_conversation(cid, user_id):
            return {
                "id": conv_id,
                "user_id": user_id,
                "title": "Test",
                "scoped_video_ids": None,
                "created_at": "2026-01-01T00:00:00Z",
            }

        # list_videos should NOT be needed when clearing — but patch it anyway
        # so the test doesn't hit a real pool if behavior changes.
        async def mock_list_videos():
            return []

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.update_conversation_scope", update_mock),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(
                    f"/api/conversations/{conv_id}/scope",
                    json={"video_ids": []},
                    headers={"Cookie": f"session={token}"},
                )

        assert r.status_code == 200, r.text
        assert r.json()["scoped_video_ids"] is None
        # [] must reach the writer as None (unscoped).
        assert update_mock.call_args.args[2] is None

    async def test_unknown_video_id_returns_422(self) -> None:
        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)

        async def mock_list_videos():
            return [{"id": "v1"}]

        update_mock = AsyncMock(return_value=True)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.update_conversation_scope", update_mock),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(
                    f"/api/conversations/{conv_id}/scope",
                    json={"video_ids": ["v1", "ghost"]},
                    headers={"Cookie": f"session={token}"},
                )

        assert r.status_code == 422, r.text
        assert "ghost" in r.json()["detail"]
        # Must not have written anything when validation fails.
        update_mock.assert_not_awaited()

    async def test_non_owner_or_missing_returns_404(self) -> None:
        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)

        async def mock_list_videos():
            return [{"id": "v1"}]

        # update returns False → not found / not owner.
        update_mock = AsyncMock(return_value=False)

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.db.repository.update_conversation_scope", update_mock),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.patch(
                    f"/api/conversations/{conv_id}/scope",
                    json={"video_ids": ["v1"]},
                    headers={"Cookie": f"session={token}"},
                )

        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Message flow — scope threaded into execute_tool + transcript whitelist
# ---------------------------------------------------------------------------


class TestScopedMessageFlow:
    async def _run_message(self, conv_row: dict, library: list[dict]):
        """Drive POST /messages once, returning the captured execute_tool
        kwargs and the video_id_whitelist passed to it."""
        user_id = conv_row["user_id"]
        conv_id = conv_row["id"]
        token = encode_token(user_id)

        captured: dict = {}

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
            yield 'data: "ok"\n\n'
            if final_text_out is not None:
                final_text_out.append("ok")
            yield "data: [DONE]\n\n"

        async def mock_execute_tool(
            name,
            raw_args,
            video_id_whitelist=None,
            embedding_cache=None,
            is_member=False,
            video_ids=None,
        ):
            captured["video_ids"] = video_ids
            captured["video_id_whitelist"] = video_id_whitelist
            return {"ok": True, "text": "context", "chunks": []}

        async def mock_get_conversation(cid, user_id):
            return conv_row

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_list_messages(cid, user_id):
            return []

        async def mock_list_videos():
            return library

        async def mock_check_and_record(uid):
            return None

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.rate_limit.check_and_record", mock_check_and_record),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/api/conversations/{conv_id}/messages",
                    json={"content": "a question"},
                    headers={"Cookie": f"session={token}"},
                )
        return captured

    async def test_scope_threaded_and_whitelist_intersected(self) -> None:
        user_id = str(uuid4())
        conv = {
            "id": str(uuid4()),
            "user_id": user_id,
            "title": "Scoped",
            "scoped_video_ids": ["v1", "v2"],
            "created_at": "2026-01-01T00:00:00Z",
        }
        library = [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]

        captured = await self._run_message(conv, library)

        assert captured["video_ids"] == ["v1", "v2"]
        # Whitelist is the intersection of the library and the scope.
        assert captured["video_id_whitelist"] == {"v1", "v2"}

    async def test_unscoped_conversation_passes_none(self) -> None:
        user_id = str(uuid4())
        conv = {
            "id": str(uuid4()),
            "user_id": user_id,
            "title": "Unscoped",
            "scoped_video_ids": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
        library = [{"id": "v1"}, {"id": "v2"}]

        captured = await self._run_message(conv, library)

        assert captured["video_ids"] is None
        # Full library whitelist, no scope intersection.
        assert captured["video_id_whitelist"] == {"v1", "v2"}

    async def test_whitelist_load_failure_falls_back_to_scope_set(self) -> None:
        """If the library load fails but a scope is set, the transcript tool
        must still be guarded by the scope set — never left unguarded (None)."""
        user_id = str(uuid4())
        conv_id = str(uuid4())
        token = encode_token(user_id)
        conv = {
            "id": conv_id,
            "user_id": user_id,
            "title": "Scoped",
            "scoped_video_ids": ["v1", "v2"],
            "created_at": "2026-01-01T00:00:00Z",
        }

        captured: dict = {}

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
            yield 'data: "ok"\n\n'
            if final_text_out is not None:
                final_text_out.append("ok")
            yield "data: [DONE]\n\n"

        async def mock_execute_tool(
            name,
            raw_args,
            video_id_whitelist=None,
            embedding_cache=None,
            is_member=False,
            video_ids=None,
        ):
            captured["video_ids"] = video_ids
            captured["video_id_whitelist"] = video_id_whitelist
            return {"ok": True, "text": "context", "chunks": []}

        async def mock_get_conversation(cid, user_id):
            return conv

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_list_messages(cid, user_id):
            return []

        async def failing_list_videos():
            raise RuntimeError("pool down")

        async def mock_check_and_record(uid):
            return None

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", _mock_user(user_id)),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", failing_list_videos),
            patch("backend.rate_limit.check_and_record", mock_check_and_record),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post(
                    f"/api/conversations/{conv_id}/messages",
                    json={"content": "a question"},
                    headers={"Cookie": f"session={token}"},
                )

        assert captured["video_ids"] == ["v1", "v2"]
        # Even though the library load failed, the scope set guards the tool.
        assert captured["video_id_whitelist"] == {"v1", "v2"}
