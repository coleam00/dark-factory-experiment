"""
Lightweight tests for conversation list filtering (issue #294).

The repository is mocked so these tests stay hermetic and fast.
They verify that the /api/conversations endpoint forwards query params
correctly and branches between list_conversations and search_conversations.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    """Replace Postgres-backed users_repo with an in-memory dict."""
    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str, **kwargs: Any) -> dict[str, Any]:
        import asyncpg

        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
                raise asyncpg.UniqueViolationError("duplicate email")
        uid = str(uuid4())
        row = {
            "id": uid,
            "email": email,
            "password_hash": password_hash,
            "created_at": None,
            "last_login_at": None,
            "is_member": False,
            "member_verified_at": None,
        }
        store[uid] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def get_user_by_email(email: str) -> dict[str, Any] | None:
        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
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

    from backend.db import users_repo
    from backend.auth import dependencies as auth_deps
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


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


@pytest.fixture(autouse=True)
def fake_conversation_repo(monkeypatch):
    """Mock repository.list_conversations and search_conversations to capture calls."""
    from backend.db import repository

    calls: list[dict[str, Any]] = []

    async def fake_list(user_id: str) -> list[dict]:
        calls.append({"fn": "list_conversations", "user_id": user_id})
        return [{"id": "c1", "title": "Test", "user_id": user_id, "created_at": "now", "updated_at": "now", "preview": "hi"}]

    async def fake_search(
        user_id: str,
        *,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        video_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        calls.append({
            "fn": "search_conversations",
            "user_id": user_id,
            "query": query,
            "date_from": date_from,
            "date_to": date_to,
            "video_id": video_id,
            "limit": limit,
        })
        return [{"id": "c2", "title": "Search", "user_id": user_id, "created_at": "now", "updated_at": "now", "preview": "hi"}]

    monkeypatch.setattr(repository, "list_conversations", fake_list)
    monkeypatch.setattr(repository, "search_conversations", fake_search)

    return calls


async def test_list_conversations_without_params_calls_list(fake_conversation_repo, client):
    await client.post("/api/auth/signup", json={"email": "u1@example.com", "password": "password123"})
    r = await client.get("/api/conversations")
    assert r.status_code == 200
    assert len(fake_conversation_repo) == 1
    assert fake_conversation_repo[0]["fn"] == "list_conversations"


async def test_list_conversations_with_q_calls_search(fake_conversation_repo, client):
    await client.post("/api/auth/signup", json={"email": "u2@example.com", "password": "password123"})
    r = await client.get("/api/conversations?q=hello")
    assert r.status_code == 200
    assert len(fake_conversation_repo) == 1
    call = fake_conversation_repo[0]
    assert call["fn"] == "search_conversations"
    assert call["query"] == "hello"


async def test_list_conversations_with_date_params_calls_search(fake_conversation_repo, client):
    await client.post("/api/auth/signup", json={"email": "u3@example.com", "password": "password123"})
    r = await client.get("/api/conversations?date_from=2026-01-01T00:00:00&date_to=2026-01-31T23:59:59")
    assert r.status_code == 200
    assert len(fake_conversation_repo) == 1
    call = fake_conversation_repo[0]
    assert call["fn"] == "search_conversations"
    assert call["date_from"] == "2026-01-01T00:00:00"
    assert call["date_to"] == "2026-01-31T23:59:59"


async def test_list_conversations_with_video_id_calls_search(fake_conversation_repo, client):
    await client.post("/api/auth/signup", json={"email": "u4@example.com", "password": "password123"})
    r = await client.get("/api/conversations?video_id=vid-123")
    assert r.status_code == 200
    assert len(fake_conversation_repo) == 1
    call = fake_conversation_repo[0]
    assert call["fn"] == "search_conversations"
    assert call["video_id"] == "vid-123"


async def test_list_conversations_combined_filters_calls_search(fake_conversation_repo, client):
    await client.post("/api/auth/signup", json={"email": "u5@example.com", "password": "password123"})
    r = await client.get("/api/conversations?q=hello&date_from=2026-01-01T00:00:00&video_id=vid-123")
    assert r.status_code == 200
    assert len(fake_conversation_repo) == 1
    call = fake_conversation_repo[0]
    assert call["fn"] == "search_conversations"
    assert call["query"] == "hello"
    assert call["date_from"] == "2026-01-01T00:00:00"
    assert call["video_id"] == "vid-123"
