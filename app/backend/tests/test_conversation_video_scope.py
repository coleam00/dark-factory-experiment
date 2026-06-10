"""
Route-level tests for per-conversation video scoping (issue #279).

Covers:
- POST /api/conversations with `video_ids` (persisted scope, [] → unscoped,
  unknown ids → 422)
- POST /api/conversations/{id}/scope (write-once: 200 → 409; 404 on
  missing/foreign; 422 on empty list)
- GET /api/conversations/{id} returns `scoped_video_ids`
- POST /api/conversations/{id}/messages threads the scope into execute_tool
  and narrows the transcript whitelist to scope ∩ library

Auth/users are faked in-memory (mirrors test_rate_limit.py); repository
functions are monkeypatched so no Postgres is needed.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from httpx import ASGITransport, AsyncClient

from backend.db import repository

# ---------------------------------------------------------------------------
# Integration fixtures (mirror test_rate_limit.py — users in memory, pg no-op)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str, **kwargs: Any) -> dict[str, Any]:
        import asyncpg

        for u in store.values():
            if str(u["email"]).lower() == email.lower():
                raise asyncpg.UniqueViolationError("duplicate email")
        uid = str(uuid4())
        row = {
            "id": uid,
            "email": email,
            "password_hash": password_hash,
            "created_at": None,
            "last_login_at": None,
        }
        store[uid] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def get_user_by_email(email: str) -> dict[str, Any] | None:
        for u in store.values():
            if str(u["email"]).lower() == email.lower():
                return dict(u)
        return None

    async def get_user_by_id(user_id: Any) -> dict[str, Any] | None:
        u = store.get(str(user_id))
        if not u:
            return None
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def update_last_login(user_id: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["last_login_at"] = "now"

    from backend.auth import dependencies as auth_deps
    from backend.db import users_repo
    from backend.routes import auth as auth_route

    monkeypatch.setattr(users_repo, "create_user", create_user)
    monkeypatch.setattr(users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(users_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(users_repo, "update_last_login", update_last_login)
    monkeypatch.setattr(auth_deps.users_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(auth_route.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_route.users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", update_last_login)
    return store


@pytest.fixture(autouse=True)
def stub_pg_lifecycle(monkeypatch):
    from backend.db import postgres as pg

    async def noop():
        return None

    monkeypatch.setattr(pg, "close_pg_pool", noop)


@pytest.fixture(autouse=True)
def fake_video_library(monkeypatch):
    """The id-existence validation calls repository.list_videos."""

    async def fake_list_videos() -> list[dict]:
        return [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]

    monkeypatch.setattr(repository, "list_videos", fake_list_videos)


async def _client() -> AsyncClient:
    from backend.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def _signup(email: str = "alice@example.com", password: str = "password123") -> AsyncClient:
    c = await _client()
    r = await c.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return c


# ---------------------------------------------------------------------------
# POST /api/conversations — scope at creation
# ---------------------------------------------------------------------------


async def test_create_conversation_with_video_ids_persists_scope():
    client = await _signup()
    try:
        r = await client.post("/api/conversations", json={"video_ids": ["v1", "v2"]})
        assert r.status_code == 201, r.text
        assert r.json()["scoped_video_ids"] == ["v1", "v2"]
    finally:
        await client.aclose()


async def test_create_conversation_empty_video_ids_is_unscoped():
    client = await _signup()
    try:
        r = await client.post("/api/conversations", json={"video_ids": []})
        assert r.status_code == 201, r.text
        assert r.json()["scoped_video_ids"] is None
    finally:
        await client.aclose()


async def test_create_conversation_without_video_ids_is_unscoped():
    client = await _signup()
    try:
        r = await client.post("/api/conversations", json={})
        assert r.status_code == 201, r.text
        assert r.json()["scoped_video_ids"] is None
    finally:
        await client.aclose()


async def test_create_conversation_unknown_video_id_returns_422():
    client = await _signup()
    try:
        r = await client.post("/api/conversations", json={"video_ids": ["v1", "nope"]})
        assert r.status_code == 422, r.text
        assert "nope" in r.json()["detail"]
    finally:
        await client.aclose()


async def test_create_conversation_dedupes_and_strips_falsy_ids():
    client = await _signup()
    try:
        r = await client.post(
            "/api/conversations", json={"video_ids": ["v1", "v1", "", "  ", "v2"]}
        )
        assert r.status_code == 201, r.text
        assert r.json()["scoped_video_ids"] == ["v1", "v2"]
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# POST /api/conversations/{id}/scope — write-once scope endpoint
# ---------------------------------------------------------------------------


async def test_set_scope_on_unscoped_conversation_returns_scope(monkeypatch):
    client = await _signup()
    try:
        captured: dict = {}

        async def fake_set_scope(conv_id, user_id, video_ids):
            captured["conv_id"] = conv_id
            captured["video_ids"] = video_ids
            return True

        async def fake_get_conversation(conv_id, user_id):
            return {"id": conv_id, "title": "t", "scoped_video_ids": captured["video_ids"]}

        monkeypatch.setattr(repository, "set_conversation_scope", fake_set_scope)
        monkeypatch.setattr(repository, "get_conversation", fake_get_conversation)

        r = await client.post("/api/conversations/c-1/scope", json={"video_ids": ["v1"]})
        assert r.status_code == 200, r.text
        assert r.json()["scoped_video_ids"] == ["v1"]
        assert captured["conv_id"] == "c-1"
        assert captured["video_ids"] == ["v1"]
    finally:
        await client.aclose()


async def test_set_scope_twice_returns_409(monkeypatch):
    client = await _signup()
    try:

        async def fake_set_scope(conv_id, user_id, video_ids):
            return False  # IS NULL guard rejected the write

        async def fake_get_conversation(conv_id, user_id):
            return {"id": conv_id, "title": "t", "scoped_video_ids": ["v9"]}

        monkeypatch.setattr(repository, "set_conversation_scope", fake_set_scope)
        monkeypatch.setattr(repository, "get_conversation", fake_get_conversation)

        r = await client.post("/api/conversations/c-1/scope", json={"video_ids": ["v1"]})
        assert r.status_code == 409, r.text
    finally:
        await client.aclose()


async def test_set_scope_on_missing_conversation_returns_404(monkeypatch):
    client = await _signup()
    try:

        async def fake_set_scope(conv_id, user_id, video_ids):
            return False

        async def fake_get_conversation(conv_id, user_id):
            return None  # not found or foreign — same 404, no existence leak

        monkeypatch.setattr(repository, "set_conversation_scope", fake_set_scope)
        monkeypatch.setattr(repository, "get_conversation", fake_get_conversation)

        r = await client.post("/api/conversations/c-x/scope", json={"video_ids": ["v1"]})
        assert r.status_code == 404, r.text
    finally:
        await client.aclose()


async def test_set_scope_with_empty_list_returns_422():
    client = await _signup()
    try:
        r = await client.post("/api/conversations/c-1/scope", json={"video_ids": []})
        assert r.status_code == 422, r.text
    finally:
        await client.aclose()


async def test_set_scope_with_unknown_video_id_returns_422(monkeypatch):
    client = await _signup()
    try:
        called = {"set": False}

        async def fake_set_scope(conv_id, user_id, video_ids):
            called["set"] = True
            return True

        monkeypatch.setattr(repository, "set_conversation_scope", fake_set_scope)

        r = await client.post("/api/conversations/c-1/scope", json={"video_ids": ["nope"]})
        assert r.status_code == 422, r.text
        assert called["set"] is False  # validation rejects before any write
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# GET /api/conversations/{id} — scope round-trip
# ---------------------------------------------------------------------------


async def test_get_conversation_returns_scoped_video_ids(monkeypatch):
    client = await _signup()
    try:

        async def fake_get_conversation(conv_id, user_id):
            return {"id": conv_id, "title": "t", "scoped_video_ids": ["v1", "v2"]}

        async def fake_list_messages(conv_id, user_id):
            return []

        monkeypatch.setattr(repository, "get_conversation", fake_get_conversation)
        monkeypatch.setattr(repository, "list_messages", fake_list_messages)

        r = await client.get("/api/conversations/c-1")
        assert r.status_code == 200, r.text
        assert r.json()["scoped_video_ids"] == ["v1", "v2"]
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# POST /api/conversations/{id}/messages — scope threading into tools
# ---------------------------------------------------------------------------


def _patch_message_route(monkeypatch, scoped_video_ids, captured: dict):
    """Wire fakes so a message send exercises the tool plumbing only."""
    from backend.routes import messages as messages_route

    async def fake_get_conversation(conv_id, user_id):
        return {
            "id": conv_id,
            "user_id": user_id,
            "title": "t",
            "scoped_video_ids": scoped_video_ids,
        }

    async def fake_create_message(**kwargs):
        return {"id": "m1", **kwargs}

    async def fake_list_messages(conv_id, user_id):
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(repository, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(repository, "create_message", fake_create_message)
    monkeypatch.setattr(repository, "list_messages", fake_list_messages)
    monkeypatch.setattr(messages_route, "LLM_TOOLS_ENABLED", True)

    async def fake_execute_tool(
        name,
        raw_arguments,
        video_id_whitelist=None,
        embedding_cache=None,
        is_member=False,
        video_ids=None,
    ):
        captured["video_ids"] = video_ids
        captured["whitelist"] = video_id_whitelist
        return {"ok": True, "text": "no results", "chunks": []}

    monkeypatch.setattr(messages_route, "execute_tool", fake_execute_tool)

    async def fake_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        is_member=False,
    ):
        # Simulate the model issuing one search tool call, then finishing.
        if tool_executor is not None:
            await tool_executor("search_videos", '{"query": "x"}')
        yield 'data: "answer"\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(messages_route, "stream_chat", fake_stream_chat)


async def test_message_route_threads_scope_into_execute_tool(monkeypatch):
    client = await _signup()
    try:
        captured: dict = {}
        _patch_message_route(monkeypatch, scoped_video_ids=["v1", "v2"], captured=captured)

        r = await client.post("/api/conversations/c-1/messages", json={"content": "hi"})
        assert r.status_code == 200, r.text

        assert captured["video_ids"] == ["v1", "v2"]
        # Whitelist = scope ∩ library; library is {v1, v2, v3} (fake_video_library)
        assert captured["whitelist"] == {"v1", "v2"}
    finally:
        await client.aclose()


async def test_message_route_unscoped_passes_none_and_full_whitelist(monkeypatch):
    client = await _signup()
    try:
        captured: dict = {}
        _patch_message_route(monkeypatch, scoped_video_ids=None, captured=captured)

        r = await client.post("/api/conversations/c-1/messages", json={"content": "hi"})
        assert r.status_code == 200, r.text

        assert captured["video_ids"] is None
        assert captured["whitelist"] == {"v1", "v2", "v3"}
    finally:
        await client.aclose()


async def test_message_route_whitelist_intersects_with_library(monkeypatch):
    """A scoped id that's no longer in the library is dropped from the whitelist."""
    client = await _signup()
    try:
        captured: dict = {}
        _patch_message_route(monkeypatch, scoped_video_ids=["v1", "deleted"], captured=captured)

        r = await client.post("/api/conversations/c-1/messages", json={"content": "hi"})
        assert r.status_code == 200, r.text

        assert captured["video_ids"] == ["v1", "deleted"]
        assert captured["whitelist"] == {"v1"}
    finally:
        await client.aclose()


async def test_message_route_empty_scope_array_treated_as_unscoped(monkeypatch):
    """asyncpg may hand back [] — `or None` must collapse it to unscoped."""
    client = await _signup()
    try:
        captured: dict = {}
        _patch_message_route(monkeypatch, scoped_video_ids=[], captured=captured)

        r = await client.post("/api/conversations/c-1/messages", json={"content": "hi"})
        assert r.status_code == 200, r.text

        assert captured["video_ids"] is None
        assert captured["whitelist"] == {"v1", "v2", "v3"}
    finally:
        await client.aclose()
