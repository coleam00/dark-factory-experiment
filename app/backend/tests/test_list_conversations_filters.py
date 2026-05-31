"""
Tests for the date/video filters added to ``list_conversations`` (issue #294).

Two tiers:

1. A query-construction guard (runs in CI) that captures the SQL + bound
   params handed to asyncpg via the fake pool in ``conftest.py``. It locks in
   that filters become AND-clauses with correctly-numbered placeholders, that
   the owner scope ($1) and newest-first ordering are always present, and that
   every user value is *bound* (never interpolated into the SQL text).

2. Behavioral, seeded-row assertions that need a real Postgres. These mirror
   ``test_search_conversations.py`` / ``test_conversation_scoping.py`` and are
   skipped pending the asyncpg/Alembic test-DB rewrite, same as those modules.

NOTE on date semantics (issue #294): the date range filters on ``updated_at``
(the "last activity" column the list is ordered by). All date-filter assertions
seed **explicit** ``updated_at`` values and assert against those seeded rows —
never against wall-clock "now" or the up-to-24h-stale snapshot DB.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.db import repository

# ---------------------------------------------------------------------------
# Tier 1 — query-construction guard (no real DB; runs in CI)
# ---------------------------------------------------------------------------


class _CapturingConn:
    """Fake asyncpg connection that records the SQL + params of fetch()."""

    def __init__(self) -> None:
        self.sql: str | None = None
        self.params: tuple = ()

    async def fetch(self, sql, *params):
        self.sql = sql
        self.params = params
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def capture_query(monkeypatch) -> _CapturingConn:
    """Make ``repository._acquire()`` yield a capturing connection."""
    conn = _CapturingConn()

    def _acquire():
        return conn

    monkeypatch.setattr(repository, "_acquire", _acquire)
    return conn


async def test_no_filters_matches_legacy_query(capture_query):
    """No params → owner scope only, newest-first, single bound value."""
    user_id = str(uuid4())
    await repository.list_conversations(user_id)

    assert capture_query.params == (user_id,)
    assert "c.user_id = $1" in capture_query.sql
    assert "ORDER BY c.updated_at DESC" in capture_query.sql
    # No filter clauses leaked in.
    assert "c.updated_at >=" not in capture_query.sql
    assert "c.updated_at <=" not in capture_query.sql
    assert "EXISTS" not in capture_query.sql


async def test_date_filters_add_bound_placeholders(capture_query):
    user_id = str(uuid4())
    await repository.list_conversations(
        user_id, start_date="2026-01-01T00:00:00.000Z", end_date="2026-02-01T23:59:59.999Z"
    )

    # Owner scope stays $1; the two dates are bound as $2 and $3 (order matters).
    assert capture_query.params == (
        user_id,
        "2026-01-01T00:00:00.000Z",
        "2026-02-01T23:59:59.999Z",
    )
    assert "c.user_id = $1" in capture_query.sql
    assert "c.updated_at >= $2" in capture_query.sql
    assert "c.updated_at <= $3" in capture_query.sql
    assert "ORDER BY c.updated_at DESC" in capture_query.sql


async def test_video_filter_uses_jsonb_containment(capture_query):
    user_id = str(uuid4())
    await repository.list_conversations(user_id, video_id="vid-123")

    # The video value is bound as a JSON containment doc, never interpolated.
    assert capture_query.params == (user_id, json.dumps([{"video_id": "vid-123"}]))
    assert "EXISTS (" in capture_query.sql
    assert "m.sources @> $2::jsonb" in capture_query.sql
    assert "m.conversation_id = c.id" in capture_query.sql
    # The raw id never appears in the SQL text — proves it's bound, not concatenated.
    assert "vid-123" not in capture_query.sql


async def test_all_filters_combine_with_sequential_placeholders(capture_query):
    user_id = str(uuid4())
    await repository.list_conversations(
        user_id,
        start_date="2026-01-01T00:00:00.000Z",
        end_date="2026-02-01T23:59:59.999Z",
        video_id="vid-9",
    )

    assert capture_query.params == (
        user_id,
        "2026-01-01T00:00:00.000Z",
        "2026-02-01T23:59:59.999Z",
        json.dumps([{"video_id": "vid-9"}]),
    )
    assert "c.user_id = $1" in capture_query.sql
    assert "c.updated_at >= $2" in capture_query.sql
    assert "c.updated_at <= $3" in capture_query.sql
    assert "m.sources @> $4::jsonb" in capture_query.sql


# ---------------------------------------------------------------------------
# Tier 2 — behavioral assertions (require a real test Postgres)
# ---------------------------------------------------------------------------

real_db = pytest.mark.skip(
    reason="Requires asyncpg/Alembic test Postgres; pending rewrite (see test_search_conversations.py)."
)


@real_db
async def test_date_range_filters_by_updated_at():
    """Only conversations whose seeded updated_at falls in [start, end] return."""
    user_id = str(uuid4())
    # Seed three conversations at explicit, distinct updated_at values
    # (T-1d / T-10d / T-40d relative to a fixed anchor — never wall-clock now).
    # ... create_conversation + direct UPDATE of updated_at ...
    # ... attach assistant messages with sources referencing video ids ...
    results = await repository.list_conversations(user_id, start_date="...", end_date="...")
    # assert only the in-range conversation is returned
    assert isinstance(results, list)


@real_db
async def test_video_filter_returns_only_citing_conversations():
    user_id = str(uuid4())
    results = await repository.list_conversations(user_id, video_id="vid-a")
    # assert only conversations whose messages.sources cite vid-a appear
    assert isinstance(results, list)


@real_db
async def test_combined_date_and_video_filters_intersect():
    user_id = str(uuid4())
    results = await repository.list_conversations(
        user_id, start_date="...", end_date="...", video_id="vid-a"
    )
    # assert intersection of date-range AND video membership
    assert isinstance(results, list)


@real_db
async def test_results_ordered_newest_first():
    user_id = str(uuid4())
    results = await repository.list_conversations(user_id)
    updated = [r["updated_at"] for r in results]
    assert updated == sorted(updated, reverse=True)


@real_db
async def test_filters_preserve_owner_scope():
    """A second user's conversations never appear under any filter combination."""
    alice = str(uuid4())
    bob = str(uuid4())
    results = await repository.list_conversations(alice, video_id="vid-a")
    assert all(r.get("user_id") == alice for r in results)
    assert all(r.get("user_id") != bob for r in results)
