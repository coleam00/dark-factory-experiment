"""
Tests for conversation list filtering by text, date range, and video (issue #294).

Two tiers:
- SQL-construction tests: capture the query/args that `list_conversations_filtered`
  sends to the pool, without needing a real Postgres. These verify the WHERE
  clauses compose additively (AND), parameters are positional ($1, $2, ...),
  the ownership guard is always present, and ordering stays `updated_at DESC`.
- Route tests: `GET /api/conversations` passes optional query params through to
  the repository (auth bypassed via dependency_overrides, repo mocked).

Full behavioral tests against a real Postgres (ILIKE matching, date boundary
semantics, JSONB containment hits/misses) are gated behind the same skip used
by test_search_conversations.py, pending an asyncpg/Alembic test database.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.auth.dependencies import get_current_user
from backend.db import repository
from backend.db.repository import (
    create_conversation,
    create_message,
    list_conversations,
    list_conversations_filtered,
)
from backend.main import app

requires_postgres = pytest.mark.skip(
    reason="Requires a real test Postgres; pending asyncpg/Alembic test DB "
    "(same gap as test_search_conversations.py)."
)


# ---------------------------------------------------------------------------
# SQL construction (no DB needed — capture what gets sent to the pool)
# ---------------------------------------------------------------------------


class _CaptureConn:
    def __init__(self, captured: dict):
        self._captured = captured

    async def fetch(self, query: str, *args):
        self._captured["query"] = query
        self._captured["args"] = args
        return []


class _CaptureAcquire:
    def __init__(self, captured: dict):
        self._captured = captured

    async def __aenter__(self):
        return _CaptureConn(self._captured)

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def captured(monkeypatch) -> dict:
    """Route repository._acquire() to a connection that records fetch calls."""
    store: dict = {}
    monkeypatch.setattr(repository, "_acquire", lambda: _CaptureAcquire(store))
    return store


async def test_no_filters_only_user_id_guard(captured):
    await list_conversations_filtered("user-1")
    assert captured["args"] == ("user-1",)
    assert "c.user_id = $1" in captured["query"]
    assert "ILIKE" not in captured["query"]
    assert "interval" not in captured["query"]
    assert "EXISTS" not in captured["query"]
    assert "ORDER BY c.updated_at DESC" in captured["query"]


async def test_list_conversations_delegates_to_filtered(captured):
    await list_conversations("user-1")
    assert captured["args"] == ("user-1",)
    assert "c.user_id = $1" in captured["query"]


async def test_text_filter_uses_ilike_pattern(captured):
    await list_conversations_filtered("user-1", q="python")
    assert captured["args"] == ("user-1", "%python%")
    assert "c.title ILIKE $2" in captured["query"]


async def test_date_from_is_inclusive_lower_bound(captured):
    await list_conversations_filtered("user-1", date_from="2026-06-01")
    assert captured["args"] == ("user-1", "2026-06-01")
    assert "c.updated_at >= $2::timestamptz" in captured["query"]


async def test_date_to_is_inclusive_calendar_day(captured):
    await list_conversations_filtered("user-1", date_to="2026-06-07")
    assert captured["args"] == ("user-1", "2026-06-07")
    assert "c.updated_at < ($2::timestamptz + interval '1 day')" in captured["query"]


async def test_video_filter_uses_jsonb_containment_on_messages(captured):
    await list_conversations_filtered("user-1", video_id="vid-42")
    assert captured["args"] == ("user-1", json.dumps([{"video_id": "vid-42"}]))
    assert "m.sources @> $2::jsonb" in captured["query"]
    assert "m.conversation_id = c.id" in captured["query"]


async def test_all_filters_combine_with_and(captured):
    await list_conversations_filtered(
        "user-1", q="rag", date_from="2026-05-01", date_to="2026-05-31", video_id="vid-1"
    )
    assert captured["args"] == (
        "user-1",
        "%rag%",
        "2026-05-01",
        "2026-05-31",
        json.dumps([{"video_id": "vid-1"}]),
    )
    query = captured["query"]
    assert "c.user_id = $1" in query
    assert "c.title ILIKE $2" in query
    assert "c.updated_at >= $3::timestamptz" in query
    assert "c.updated_at < ($4::timestamptz + interval '1 day')" in query
    assert "m.sources @> $5::jsonb" in query
    # All clauses are AND-joined (no OR anywhere)
    assert " OR " not in query
    assert "ORDER BY c.updated_at DESC" in query


async def test_empty_string_filters_are_ignored(captured):
    await list_conversations_filtered("user-1", q="", date_from="", date_to="", video_id="")
    assert captured["args"] == ("user-1",)
    assert "ILIKE" not in captured["query"]


async def test_preview_subselect_is_preserved(captured):
    await list_conversations_filtered("user-1", video_id="vid-1")
    assert "AS preview" in captured["query"]


# ---------------------------------------------------------------------------
# Route param pass-through (auth bypassed, repository mocked)
# ---------------------------------------------------------------------------


@pytest.fixture
def bypass_auth():
    stub_user = {"id": "test-user", "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


async def _get(path: str) -> tuple[int, list]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(path)
    return res.status_code, res.json()


async def test_route_passes_filters_to_repository(bypass_auth):
    with patch(
        "backend.routes.conversations.repository.list_conversations_filtered",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        status, body = await _get(
            "/api/conversations?q=python&date_from=2026-06-01&date_to=2026-06-07&video_id=vid-1"
        )
    assert status == 200
    assert body == []
    mock_list.assert_awaited_once_with(
        user_id="test-user",
        q="python",
        date_from="2026-06-01",
        date_to="2026-06-07",
        video_id="vid-1",
    )


async def test_route_defaults_to_unfiltered(bypass_auth):
    with patch(
        "backend.routes.conversations.repository.list_conversations_filtered",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        status, _ = await _get("/api/conversations")
    assert status == 200
    mock_list.assert_awaited_once_with(
        user_id="test-user",
        q=None,
        date_from=None,
        date_to=None,
        video_id=None,
    )


# ---------------------------------------------------------------------------
# Behavioral tests — require a real test Postgres (skipped, see module docstring)
# ---------------------------------------------------------------------------


@requires_postgres
async def test_text_filter_is_case_insensitive():
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python Tutorial")
    await create_conversation(user_id=user_id, title="JavaScript Guide")
    results = await list_conversations_filtered(user_id, q="PYTHON")
    assert {r["title"] for r in results} == {"Python Tutorial"}


@requires_postgres
async def test_video_filter_matches_only_citing_conversations():
    user_id = str(uuid4())
    conv_a = await create_conversation(user_id=user_id, title="About video A")
    conv_b = await create_conversation(user_id=user_id, title="About video B")
    await create_message(
        conversation_id=conv_a["id"],
        user_id=user_id,
        role="assistant",
        content="answer",
        sources=[{"video_id": "vid-a", "chunk_id": "c1"}],
    )
    await create_message(
        conversation_id=conv_b["id"],
        user_id=user_id,
        role="assistant",
        content="answer",
        sources=[{"video_id": "vid-b", "chunk_id": "c2"}],
    )
    results = await list_conversations_filtered(user_id, video_id="vid-a")
    assert [r["id"] for r in results] == [conv_a["id"]]


@requires_postgres
async def test_combined_filters_and_ownership_scoping():
    alice = str(uuid4())
    bob = str(uuid4())
    await create_conversation(user_id=alice, title="RAG deep dive")
    await create_conversation(user_id=bob, title="RAG deep dive")
    results = await list_conversations_filtered(alice, q="rag")
    assert all(r["user_id"] == alice for r in results)
    # Ordering: newest updated_at first
    timestamps = [r["updated_at"] for r in results]
    assert timestamps == sorted(timestamps, reverse=True)
