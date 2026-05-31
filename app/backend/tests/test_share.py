"""Tests for share-link endpoints (issue #278).

Covers:
- Owner can create a share link; response carries token and /share/ path.
- GET /api/share/{token} with no auth cookie returns title + messages + sources.
- Revoke makes a previously-working token return 404.
- Unknown/garbage token -> 404.
- Non-owner cannot create or revoke another user's link -> 404.
- Re-minting rotates the token and the previous token stops resolving.
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

    monkeypatch.setattr(pg, "init_pg_pool", noop)
    monkeypatch.setattr(pg, "close_pg_pool", noop)


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


async def _signup(client: AsyncClient, email: str, password: str = "password123") -> str:
    r = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Repository fakes for share token logic
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_share_repo(monkeypatch):
    """In-memory fake for share-token repository functions."""
    conversations: dict[str, dict[str, Any]] = {}
    messages: dict[str, list[dict[str, Any]]] = {}
    tokens: dict[str, str] = {}  # token -> conversation_id

    async def set_conversation_share_token(conv_id: str, user_id: str, token: str) -> bool:
        conv = conversations.get(conv_id)
        if not conv or conv["user_id"] != user_id:
            return False
        # rotate: clear old token
        old_token = conv.get("share_token")
        if old_token and old_token in tokens:
            del tokens[old_token]
        conv["share_token"] = token
        conv["share_created_at"] = "2026-05-31T12:00:00+00:00"
        tokens[token] = conv_id
        return True

    async def clear_conversation_share_token(conv_id: str, user_id: str) -> bool:
        conv = conversations.get(conv_id)
        if not conv or conv["user_id"] != user_id:
            return False
        old_token = conv.get("share_token")
        if old_token and old_token in tokens:
            del tokens[old_token]
        conv["share_token"] = None
        conv["share_created_at"] = None
        return True

    async def get_conversation_by_share_token(token: str) -> dict[str, Any] | None:
        conv_id = tokens.get(token)
        if not conv_id:
            return None
        conv = conversations.get(conv_id)
        if not conv:
            return None
        return {
            "id": conv["id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
        }

    async def list_messages_for_share_token(token: str) -> list[dict[str, Any]]:
        conv_id = tokens.get(token)
        if not conv_id:
            return []
        return messages.get(conv_id, [])

    async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict[str, Any]:
        conv_id = str(uuid4())
        conv = {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "created_at": "2026-05-31T10:00:00+00:00",
            "updated_at": "2026-05-31T10:00:00+00:00",
            "share_token": None,
            "share_created_at": None,
        }
        conversations[conv_id] = conv
        messages[conv_id] = []
        return conv

    async def get_conversation(conv_id: str, user_id: str) -> dict[str, Any] | None:
        conv = conversations.get(conv_id)
        if not conv or conv["user_id"] != user_id:
            return None
        return conv

    async def list_messages(conv_id: str, user_id: str) -> list[dict[str, Any]]:
        conv = conversations.get(conv_id)
        if not conv or conv["user_id"] != user_id:
            return []
        return messages.get(conv_id, [])

    async def create_message(
        *, conversation_id: str, user_id: str, role: str, content: str, sources: list[dict] | None = None
    ) -> dict[str, Any] | None:
        conv = conversations.get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return None
        msg = {
            "id": str(uuid4()),
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "sources": sources,
            "created_at": "2026-05-31T10:05:00+00:00",
        }
        messages[conversation_id].append(msg)
        return msg

    from backend.db import repository as repo

    monkeypatch.setattr(repo, "set_conversation_share_token", set_conversation_share_token)
    monkeypatch.setattr(repo, "clear_conversation_share_token", clear_conversation_share_token)
    monkeypatch.setattr(repo, "get_conversation_by_share_token", get_conversation_by_share_token)
    monkeypatch.setattr(repo, "list_messages_for_share_token", list_messages_for_share_token)
    monkeypatch.setattr(repo, "create_conversation", create_conversation)
    monkeypatch.setattr(repo, "get_conversation", get_conversation)
    monkeypatch.setattr(repo, "list_messages", list_messages)
    monkeypatch.setattr(repo, "create_message", create_message)

    return {"conversations": conversations, "messages": messages, "tokens": tokens}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_owner_can_create_share_link(client):
    await _signup(client, "owner@example.com")
    r = await client.post("/api/conversations", json={"title": "Secret chat"})
    assert r.status_code == 201
    conv_id = r.json()["id"]

    r_share = await client.post(f"/api/conversations/{conv_id}/share")
    assert r_share.status_code == 200
    body = r_share.json()
    assert "token" in body
    assert body["url_path"] == f"/share/{body['token']}"


async def test_get_shared_conversation_no_auth_returns_data(client):
    await _signup(client, "owner2@example.com")
    r = await client.post("/api/conversations", json={"title": "Shared chat"})
    conv_id = r.json()["id"]

    # Seed a message with sources
    r_msg = await client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "hello"},
    )
    assert r_msg.status_code == 200

    r_share = await client.post(f"/api/conversations/{conv_id}/share")
    token = r_share.json()["token"]

    # Clear cookies to simulate logged-out reader
    client.cookies.clear()
    r_public = await client.get(f"/api/share/{token}")
    assert r_public.status_code == 200
    body = r_public.json()
    assert body["title"] == "Shared chat"
    assert len(body["messages"]) > 0
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "hello"
    # Must not include user_id or any owner-identifying field
    assert "user_id" not in body
    assert "user_id" not in body["messages"][0]


async def test_revoke_makes_token_404(client):
    await _signup(client, "owner3@example.com")
    r = await client.post("/api/conversations", json={"title": "Revoke me"})
    conv_id = r.json()["id"]

    r_share = await client.post(f"/api/conversations/{conv_id}/share")
    token = r_share.json()["token"]

    # Verify it works before revocation
    client.cookies.clear()
    r_before = await client.get(f"/api/share/{token}")
    assert r_before.status_code == 200

    # Re-auth as owner and revoke
    await client.post("/api/auth/login", json={"email": "owner3@example.com", "password": "password123"})
    r_revoke = await client.delete(f"/api/conversations/{conv_id}/share")
    assert r_revoke.status_code == 204

    # Now the token should 404
    client.cookies.clear()
    r_after = await client.get(f"/api/share/{token}")
    assert r_after.status_code == 404


async def test_unknown_token_returns_404(client):
    r = await client.get("/api/share/totally-fake-token-12345")
    assert r.status_code == 404


async def test_non_owner_cannot_create_or_revoke(client):
    await _signup(client, "alice@example.com")
    r = await client.post("/api/conversations", json={"title": "Alice chat"})
    alice_conv = r.json()["id"]

    # Bob signs up and tries to share Alice's conversation
    await _signup(client, "bob@example.com")
    r_create = await client.post(f"/api/conversations/{alice_conv}/share")
    assert r_create.status_code == 404

    # Alice creates a link first
    await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "password123"})
    r_share = await client.post(f"/api/conversations/{alice_conv}/share")
    assert r_share.status_code == 200

    # Bob tries to revoke it
    await client.post("/api/auth/login", json={"email": "bob@example.com", "password": "password123"})
    r_revoke = await client.delete(f"/api/conversations/{alice_conv}/share")
    assert r_revoke.status_code == 404


async def test_re_mint_rotates_token(client):
    await _signup(client, "rotater@example.com")
    r = await client.post("/api/conversations", json={"title": "Rotate me"})
    conv_id = r.json()["id"]

    r1 = await client.post(f"/api/conversations/{conv_id}/share")
    token1 = r1.json()["token"]

    # Re-mint
    r2 = await client.post(f"/api/conversations/{conv_id}/share")
    token2 = r2.json()["token"]

    assert token1 != token2

    # Old token stops resolving
    client.cookies.clear()
    r_old = await client.get(f"/api/share/{token1}")
    assert r_old.status_code == 404

    # New token works
    r_new = await client.get(f"/api/share/{token2}")
    assert r_new.status_code == 200


async def test_public_share_is_read_only_no_mutations(client):
    """There is no POST/PUT/PATCH/DELETE under /api/share/{token}."""
    # Attempting to POST to the share endpoint should 405 (method not allowed)
    # because only GET is registered.
    r_post = await client.post("/api/share/some-token", json={"content": "bad"})
    assert r_post.status_code == 405

    r_delete = await client.delete("/api/share/some-token")
    assert r_delete.status_code == 405

    r_patch = await client.patch("/api/share/some-token", json={})
    assert r_patch.status_code == 405
