"""
Tests for conversation history filtering (issue #294).

Two layers:
1. Unit tests for the pure WHERE-clause builder
   `repository._build_conversation_filters` — no DB needed.
2. Route-level tests for GET /api/conversations with the new optional
   query params (q, video_id, date_from, date_to), monkeypatching the
   repository so no Postgres is required.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.dependencies import get_current_user
from backend.db.repository import _build_conversation_filters
from backend.main import app
from backend.routes import conversations as conversations_route

# ---------------------------------------------------------------------------
# Unit tests: _build_conversation_filters
# ---------------------------------------------------------------------------


def test_no_filters_returns_empty():
    conditions, params = _build_conversation_filters(
        q=None, video_id=None, date_from=None, date_to=None, start_index=2
    )
    assert conditions == []
    assert params == []


def test_q_filter_alone():
    conditions, params = _build_conversation_filters(
        q="docker", video_id=None, date_from=None, date_to=None, start_index=2
    )
    assert conditions == ["c.title ILIKE $2"]
    assert params == ["%docker%"]


def test_video_id_filter_alone():
    conditions, params = _build_conversation_filters(
        q=None, video_id="vid-1", date_from=None, date_to=None, start_index=2
    )
    assert len(conditions) == 1
    assert conditions[0].startswith("EXISTS (SELECT 1 FROM messages m")
    assert "$2::text" in conditions[0]
    assert params == ["vid-1"]


def test_date_from_filter_alone():
    d = date(2026, 6, 1)
    conditions, params = _build_conversation_filters(
        q=None, video_id=None, date_from=d, date_to=None, start_index=2
    )
    assert conditions == ["c.created_at >= $2::date"]
    assert params == [d]


def test_date_to_filter_alone_is_inclusive_end_of_day():
    d = date(2026, 6, 7)
    conditions, params = _build_conversation_filters(
        q=None, video_id=None, date_from=None, date_to=d, start_index=2
    )
    assert conditions == ["c.created_at < ($2::date + interval '1 day')"]
    assert params == [d]


def test_all_filters_number_placeholders_sequentially():
    d_from = date(2026, 6, 1)
    d_to = date(2026, 6, 7)
    conditions, params = _build_conversation_filters(
        q="foo", video_id="vid-1", date_from=d_from, date_to=d_to, start_index=2
    )
    assert len(conditions) == 4
    assert "$2" in conditions[0]
    assert "$3" in conditions[1]
    assert "$4" in conditions[2]
    assert "$5" in conditions[3]
    assert params == ["%foo%", "vid-1", d_from, d_to]


def test_start_index_is_respected():
    conditions, params = _build_conversation_filters(
        q="foo", video_id=None, date_from=None, date_to=None, start_index=7
    )
    assert conditions == ["c.title ILIKE $7"]
    assert params == ["%foo%"]


def test_user_input_never_appears_in_sql_fragments():
    """User-supplied values must travel only through params — never the SQL."""
    hostile = "'; DROP TABLE conversations; --"
    conditions, params = _build_conversation_filters(
        q=hostile,
        video_id=hostile,
        date_from=None,
        date_to=None,
        start_index=2,
    )
    for fragment in conditions:
        assert hostile not in fragment
    assert f"%{hostile}%" in params
    assert hostile in params


# ---------------------------------------------------------------------------
# Route-level tests: GET /api/conversations with filter params
# ---------------------------------------------------------------------------


@pytest.fixture
def bypass_auth():
    """Satisfy the auth dependency; ownership scoping itself is unchanged."""
    stub = {"id": str(uuid4()), "email": "t@t"}
    app.dependency_overrides[get_current_user] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def capture_repo_calls(monkeypatch):
    """Replace repository.list_conversations and record its kwargs."""
    calls: list[dict[str, Any]] = []

    async def fake_list_conversations(
        user_id: str,
        *,
        q: str | None = None,
        video_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        calls.append(
            {
                "user_id": user_id,
                "q": q,
                "video_id": video_id,
                "date_from": date_from,
                "date_to": date_to,
            }
        )
        return []

    monkeypatch.setattr(
        conversations_route.repository, "list_conversations", fake_list_conversations
    )
    return calls


def _make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="https://testserver")


async def test_list_conversations_passes_filters_to_repository(bypass_auth, capture_repo_calls):
    async with _make_client() as client:
        r = await client.get(
            "/api/conversations",
            params={
                "q": "foo",
                "video_id": "v1",
                "date_from": "2026-06-01",
                "date_to": "2026-06-07",
            },
        )
    assert r.status_code == 200
    assert r.json() == []
    assert capture_repo_calls == [
        {
            "user_id": bypass_auth["id"],
            "q": "foo",
            "video_id": "v1",
            "date_from": date(2026, 6, 1),
            "date_to": date(2026, 6, 7),
        }
    ]


async def test_list_conversations_no_params_back_compat(bypass_auth, capture_repo_calls):
    async with _make_client() as client:
        r = await client.get("/api/conversations")
    assert r.status_code == 200
    assert capture_repo_calls == [
        {
            "user_id": bypass_auth["id"],
            "q": None,
            "video_id": None,
            "date_from": None,
            "date_to": None,
        }
    ]


async def test_date_from_after_date_to_returns_422(bypass_auth, capture_repo_calls):
    async with _make_client() as client:
        r = await client.get(
            "/api/conversations",
            params={"date_from": "2026-06-07", "date_to": "2026-06-01"},
        )
    assert r.status_code == 422
    assert capture_repo_calls == []


async def test_malformed_date_returns_422(bypass_auth, capture_repo_calls):
    async with _make_client() as client:
        r = await client.get("/api/conversations", params={"date_from": "notadate"})
    assert r.status_code == 422
    assert capture_repo_calls == []
