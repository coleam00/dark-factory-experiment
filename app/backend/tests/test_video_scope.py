"""Repository-layer tests for per-conversation video scope (issue #279).

Verifies that:
  - keyword_search / vector_search_pg add a parameterized video_id filter only
    when a non-empty scope is supplied, and omit it otherwise.
  - create_conversation coerces an empty scope to NULL (unscoped) and persists
    a non-empty scope as JSON.
  - get_conversation_video_ids deserializes the stored JSON (and returns None
    when unscoped).

Uses a fake asyncpg connection so no real database is required.
"""

from __future__ import annotations

import json

import pytest

from backend.db import repository


class FakeConn:
    """Fake asyncpg connection recording execute/fetch/fetchval calls."""

    def __init__(self, *, fetchval_result=None, fetch_rows=None):
        self.execute_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []
        self._fetchval_result = fetchval_result
        self._fetch_rows = fetch_rows or []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self._fetch_rows

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self._fetchval_result


class FakeAcquire:
    """Async context manager that yields a FakeConn."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return False


# --- Search filter construction -------------------------------------------


@pytest.mark.asyncio
async def test_keyword_search_adds_video_scope_clause(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    await repository.keyword_search("q", top_k=5, video_ids=["v1", "v2"])
    query, args = conn.fetch_calls[0]
    assert "video_id = ANY($4::text[])" in query
    assert args[3] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_keyword_search_omits_clause_when_unscoped(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    await repository.keyword_search("q", top_k=5)
    query, args = conn.fetch_calls[0]
    assert "video_id = ANY" not in query
    assert len(args) == 3


@pytest.mark.asyncio
async def test_vector_search_adds_video_scope_clause(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    await repository.vector_search_pg([0.1] * 1536, top_k=5, video_ids=["v1"])
    query, args = conn.fetch_calls[0]
    assert "video_id = ANY($4::text[])" in query
    assert args[3] == ["v1"]


@pytest.mark.asyncio
async def test_vector_search_omits_clause_when_unscoped(monkeypatch):
    conn = FakeConn(fetch_rows=[])
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    await repository.vector_search_pg([0.1] * 1536, top_k=5)
    query, args = conn.fetch_calls[0]
    assert "video_id = ANY" not in query
    assert len(args) == 3


# --- create_conversation scope handling -----------------------------------


@pytest.mark.asyncio
async def test_create_conversation_coerces_empty_scope_to_none(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    result = await repository.create_conversation(user_id="u1", video_ids=[])
    assert result["video_ids"] is None
    # video_ids is bound as $4 (4th positional arg) — NULL when unscoped.
    _, args = conn.execute_calls[0]
    assert args[3] is None


@pytest.mark.asyncio
async def test_create_conversation_persists_non_empty_scope(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    result = await repository.create_conversation(user_id="u1", video_ids=["v1", "v2"])
    assert result["video_ids"] == ["v1", "v2"]
    _, args = conn.execute_calls[0]
    assert json.loads(args[3]) == ["v1", "v2"]


@pytest.mark.asyncio
async def test_create_conversation_default_is_unscoped(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    result = await repository.create_conversation(user_id="u1")
    assert result["video_ids"] is None
    _, args = conn.execute_calls[0]
    assert args[3] is None


# --- get_conversation_video_ids -------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation_video_ids_parses_json(monkeypatch):
    conn = FakeConn(fetchval_result=json.dumps(["v1", "v2"]))
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    result = await repository.get_conversation_video_ids("c1", user_id="u1")
    assert result == ["v1", "v2"]


@pytest.mark.asyncio
async def test_get_conversation_video_ids_none_when_unscoped(monkeypatch):
    conn = FakeConn(fetchval_result=None)
    monkeypatch.setattr(repository, "_acquire", lambda: FakeAcquire(conn))
    result = await repository.get_conversation_video_ids("c1", user_id="u1")
    assert result is None
