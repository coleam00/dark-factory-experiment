"""Route-level tests for conversation-history filters (issue #294).

These exercise GET /api/conversations and assert the optional date / video /
text query params are parsed and forwarded to the repository. The repository
call is monkeypatched and the auth dependency is overridden, so no database is
touched (httpx ASGITransport does not run the lifespan that would connect PG).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.auth.dependencies import get_current_user  # noqa: E402
from backend.db import repository  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture
def filter_client(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_list_conversations(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(repository, "list_conversations", fake_list_conversations)
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1"}
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield captured, client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_list_conversations_without_filters(filter_client):
    captured, client = filter_client
    async with client:
        resp = await client.get("/api/conversations")
    assert resp.status_code == 200
    assert captured == {
        "user_id": "user-1",
        "query": None,
        "start_date": None,
        "end_date": None,
        "video_id": None,
    }


async def test_list_conversations_forwards_all_filters(filter_client):
    captured, client = filter_client
    async with client:
        resp = await client.get(
            "/api/conversations",
            params={
                "q": "rag",
                "start_date": "2026-01-01T00:00:00.000Z",
                "end_date": "2026-02-01T23:59:59.999Z",
                "video_id": "vid-1",
            },
        )
    assert resp.status_code == 200
    assert captured["user_id"] == "user-1"
    assert captured["query"] == "rag"
    assert captured["start_date"] == "2026-01-01T00:00:00.000Z"
    assert captured["end_date"] == "2026-02-01T23:59:59.999Z"
    assert captured["video_id"] == "vid-1"
