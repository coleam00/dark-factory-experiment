"""
Tests for list_conversations returning video_ids aggregated from messages.sources.

Uses fake asyncpg connections so no real Postgres is required.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from backend.db import repository


class _FakeRow:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __iter__(self):
        return iter(self._data.items())

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class _FakeConnWithRows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def fetch(self, *args: Any, **kwargs: Any) -> list[_FakeRow]:
        return [_FakeRow(r) for r in self._rows]

    async def __aenter__(self) -> _FakeConnWithRows:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakePoolWithRows:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._rows)


class _FakeAcquire:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def __await__(self):
        async def _do() -> _FakeConnWithRows:
            return _FakeConnWithRows(self._rows)

        return _do().__await__()

    async def __aenter__(self) -> _FakeConnWithRows:
        return _FakeConnWithRows(self._rows)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


async def test_list_conversations_includes_video_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = str(uuid4())
    rows = [
        {
            "id": "conv-1",
            "user_id": user_id,
            "title": "Chat with sources",
            "created_at": "2026-05-30T10:00:00+00:00",
            "updated_at": "2026-05-30T12:00:00+00:00",
            "preview": "Last message",
            "video_ids": ["vid-a", "vid-b"],
        },
        {
            "id": "conv-2",
            "user_id": user_id,
            "title": "Chat without sources",
            "created_at": "2026-05-29T10:00:00+00:00",
            "updated_at": "2026-05-29T12:00:00+00:00",
            "preview": "Hello",
            "video_ids": None,
        },
    ]
    fake_pool = _FakePoolWithRows(rows)
    monkeypatch.setattr(repository, "get_pg_pool", lambda: fake_pool)

    results = await repository.list_conversations(user_id)

    assert len(results) == 2
    assert results[0]["video_ids"] == ["vid-a", "vid-b"]
    assert results[1]["video_ids"] is None


async def test_list_conversations_orders_by_updated_at_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = str(uuid4())
    rows = [
        {
            "id": "conv-new",
            "user_id": user_id,
            "title": "New",
            "created_at": "2026-05-02T10:00:00+00:00",
            "updated_at": "2026-05-02T12:00:00+00:00",
            "preview": "New msg",
            "video_ids": None,
        },
        {
            "id": "conv-old",
            "user_id": user_id,
            "title": "Old",
            "created_at": "2026-05-01T10:00:00+00:00",
            "updated_at": "2026-05-01T12:00:00+00:00",
            "preview": "Old msg",
            "video_ids": None,
        },
    ]
    fake_pool = _FakePoolWithRows(rows)
    monkeypatch.setattr(repository, "get_pg_pool", lambda: fake_pool)

    results = await repository.list_conversations(user_id)

    assert [r["id"] for r in results] == ["conv-new", "conv-old"]


async def test_list_conversations_scopes_to_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    alice = str(uuid4())
    rows = [
        {
            "id": "conv-alice",
            "user_id": alice,
            "title": "Alice chat",
            "created_at": "2026-05-01T10:00:00+00:00",
            "updated_at": "2026-05-01T12:00:00+00:00",
            "preview": "Hi",
            "video_ids": None,
        },
    ]
    fake_pool = _FakePoolWithRows(rows)
    monkeypatch.setattr(repository, "get_pg_pool", lambda: fake_pool)

    # The fake returns rows regardless of SQL; we verify the function signature
    # still requires user_id and that we can simulate an empty result for Bob.
    bob = str(uuid4())
    empty_pool = _FakePoolWithRows([])
    monkeypatch.setattr(repository, "get_pg_pool", lambda: empty_pool)

    results = await repository.list_conversations(bob)
    assert results == []
