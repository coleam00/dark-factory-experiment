"""Tests for GET /api/conversation-videos."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_user
from backend.db import repository
from backend.main import app


@pytest.fixture
def override_auth():
    """Override get_current_user with a fixed test user."""
    original = app.dependency_overrides.get(get_current_user)

    async def fake_user():
        return {"id": "user-123", "email": "test@example.com"}

    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is not None:
        app.dependency_overrides[get_current_user] = original
    else:
        app.dependency_overrides.pop(get_current_user, None)


async def test_list_conversation_videos_authenticated(override_auth, monkeypatch):
    """Authenticated request returns 200 with the expected shape."""
    expected: list[dict[str, Any]] = [
        {"conversation_id": "conv-1", "video_id": "vid-1"},
        {"conversation_id": "conv-2", "video_id": "vid-2"},
    ]

    async def fake_list(user_id: str) -> list[dict[str, Any]]:
        assert user_id == "user-123"
        return expected

    monkeypatch.setattr(repository, "list_conversation_video_refs", fake_list)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/conversation-videos")

    assert response.status_code == 200
    assert response.json() == expected


async def test_list_conversation_videos_unauthenticated():
    """Unauthenticated request returns 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/conversation-videos")

    assert response.status_code == 401
