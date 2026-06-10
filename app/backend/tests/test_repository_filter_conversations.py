"""Tests for repository.filter_conversations.

Live-Postgres repository tests are skipped repo-wide pending the asyncpg/Alembic
rewrite, so these assert on the SQL text and the bound parameters handed to
`conn.fetch` (mocked) rather than on query results. The point is to lock in the
clause assembly: user scoping is always $1, placeholders are numbered
sequentially, the date upper bound is exclusive, and user values are bound as
parameters (never interpolated).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from backend.db import repository


def _patch_acquire(mock_conn):
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    return patch.object(repository, "_acquire", return_value=mock_acquire)


def _make_conn():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    return mock_conn


def _fetch_call(mock_conn):
    args = mock_conn.fetch.call_args[0]
    sql = args[0]
    params = list(args[1:])
    return sql, params


async def test_no_filters_only_user_scope():
    conn = _make_conn()
    with _patch_acquire(conn):
        await repository.filter_conversations("user-1")
    sql, params = _fetch_call(conn)
    assert params == ["user-1"]
    assert "c.user_id = $1" in sql
    assert "ORDER BY c.updated_at DESC" in sql
    assert "AS preview" in sql
    # No optional clauses present.
    assert "ILIKE" not in sql
    assert "c.updated_at >=" not in sql
    assert "c.updated_at <" not in sql
    assert "@>" not in sql


async def test_query_filter():
    conn = _make_conn()
    with _patch_acquire(conn):
        await repository.filter_conversations("user-1", query="rust")
    sql, params = _fetch_call(conn)
    assert params == ["user-1", "%rust%"]
    assert "c.title ILIKE $2" in sql


async def test_date_from_filter_inclusive():
    conn = _make_conn()
    dt = datetime(2026, 5, 1, tzinfo=UTC)
    with _patch_acquire(conn):
        await repository.filter_conversations("user-1", date_from=dt)
    sql, params = _fetch_call(conn)
    assert params == ["user-1", dt]
    assert "c.updated_at >= $2" in sql


async def test_date_to_uses_exclusive_upper_bound():
    conn = _make_conn()
    dt = datetime(2026, 5, 8, tzinfo=UTC)
    with _patch_acquire(conn):
        await repository.filter_conversations("user-1", date_to=dt)
    sql, params = _fetch_call(conn)
    assert params == ["user-1", dt]
    assert "c.updated_at < $2" in sql
    assert "c.updated_at <= $2" not in sql


async def test_video_filter_uses_jsonb_containment():
    conn = _make_conn()
    with _patch_acquire(conn):
        await repository.filter_conversations("user-1", video_id="vid-9")
    sql, params = _fetch_call(conn)
    assert params == ["user-1", json.dumps([{"video_id": "vid-9"}])]
    assert "EXISTS" in sql
    assert "@>" in sql
    assert "$2::jsonb" in sql


async def test_all_filters_sequential_placeholders_and_and_joined():
    conn = _make_conn()
    df = datetime(2026, 5, 1, tzinfo=UTC)
    dt = datetime(2026, 5, 8, tzinfo=UTC)
    with _patch_acquire(conn):
        await repository.filter_conversations(
            "user-1", query="rag", date_from=df, date_to=dt, video_id="vid-9"
        )
    sql, params = _fetch_call(conn)
    assert params == [
        "user-1",
        "%rag%",
        df,
        dt,
        json.dumps([{"video_id": "vid-9"}]),
    ]
    assert "c.user_id = $1" in sql
    assert "c.title ILIKE $2" in sql
    assert "c.updated_at >= $3" in sql
    assert "c.updated_at < $4" in sql
    assert "$5::jsonb" in sql
    assert " AND " in sql


async def test_user_scope_always_first_param():
    """user_id is bound as $1 regardless of which other filters are supplied."""
    conn = _make_conn()
    with _patch_acquire(conn):
        await repository.filter_conversations("user-xyz", video_id="v", query="q")
    sql, params = _fetch_call(conn)
    assert params[0] == "user-xyz"
    assert "c.user_id = $1" in sql
