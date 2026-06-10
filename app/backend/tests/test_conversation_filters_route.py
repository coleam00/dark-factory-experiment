"""Route tests for GET /api/conversations/filter.

These run without a real database: `repository.filter_conversations` is
monkeypatched to capture the parsed arguments, and the auth dependency is
overridden. The point is to verify FastAPI query-param parsing (datetimes,
optional params), UTC coercion of naive datetimes, and — critically — that the
route is registered ahead of /conversations/{conv_id} so it isn't shadowed.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


@pytest.fixture
def captured(monkeypatch):
    """Capture the args the route hands to repository.filter_conversations."""
    calls: dict[str, Any] = {}

    async def fake_filter_conversations(
        user_id,
        *,
        query=None,
        date_from=None,
        date_to=None,
        video_id=None,
    ):
        calls["user_id"] = user_id
        calls["query"] = query
        calls["date_from"] = date_from
        calls["date_to"] = date_to
        calls["video_id"] = video_id
        return []

    from backend.db import repository

    monkeypatch.setattr(repository, "filter_conversations", fake_filter_conversations)
    return calls


def _make_client():
    from backend.auth.dependencies import get_current_user
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "user-1",
        "email": "u@example.com",
    }
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="https://testserver")
    return app, client


async def test_params_parse_and_user_from_auth(captured):
    app, client = _make_client()
    try:
        r = await client.get(
            "/api/conversations/filter",
            params={
                "q": "rag",
                "date_from": "2026-05-01T00:00:00Z",
                "date_to": "2026-05-08T00:00:00Z",
                "video_id": "vid-9",
            },
        )
        assert r.status_code == 200, r.text
        assert captured["user_id"] == "user-1"
        assert captured["query"] == "rag"
        assert captured["video_id"] == "vid-9"
        # Aware-datetime equality compares the UTC instant, not the tzinfo object.
        assert captured["date_from"] == datetime(2026, 5, 1, tzinfo=UTC)
        assert captured["date_to"] == datetime(2026, 5, 8, tzinfo=UTC)
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def test_missing_params_are_none(captured):
    app, client = _make_client()
    try:
        r = await client.get("/api/conversations/filter")
        assert r.status_code == 200, r.text
        assert captured["query"] is None
        assert captured["date_from"] is None
        assert captured["date_to"] is None
        assert captured["video_id"] is None
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def test_naive_datetime_coerced_to_utc(captured):
    app, client = _make_client()
    try:
        r = await client.get(
            "/api/conversations/filter",
            params={"date_from": "2026-05-01T12:00:00"},
        )
        assert r.status_code == 200, r.text
        assert captured["date_from"] == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        assert captured["date_from"].tzinfo is not None
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def test_malformed_date_returns_422(captured):
    app, client = _make_client()
    try:
        r = await client.get(
            "/api/conversations/filter",
            params={"date_from": "not-a-date"},
        )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def test_route_not_shadowed_by_conv_id(captured):
    """Regression for the include-order requirement: /conversations/filter must
    hit the filter handler, not the /conversations/{conv_id} path-param handler
    (which would 404 since "filter" is not a real conversation id)."""
    app, client = _make_client()
    try:
        r = await client.get("/api/conversations/filter")
        assert r.status_code == 200, r.text
        # The filter repository function was reached — the path-param handler
        # for /conversations/{conv_id} never calls it.
        assert "user_id" in captured
    finally:
        app.dependency_overrides.clear()
        await client.aclose()
