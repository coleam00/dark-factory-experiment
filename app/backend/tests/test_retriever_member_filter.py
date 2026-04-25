"""Unit test: retrieve_hybrid threads is_member into the repository search calls.

Mocks the two repository entry points at the boundary so we don't need a live
Postgres pool. Asserts that:
- is_member=False sends `allowed_source_types=['youtube']` to both searches
- is_member=True sends `allowed_source_types=['youtube', 'dynamous']` to both
"""

from __future__ import annotations

import os
from typing import Any

# Standard test env (per conftest.py contract — see test_auth.py for the same pattern).
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

import pytest

from backend.rag.retriever_hybrid import retrieve_hybrid


class _Spy:
    """Captures calls to the patched async function."""

    def __init__(self, return_rows: list[dict[str, Any]] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.return_rows = return_rows or []

    async def __call__(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.return_rows


async def test_non_member_filters_to_youtube_only(monkeypatch: pytest.MonkeyPatch):
    keyword_spy = _Spy()
    vector_spy = _Spy()

    from backend.db import repository

    monkeypatch.setattr(repository, "keyword_search", keyword_spy)
    monkeypatch.setattr(repository, "vector_search_pg", vector_spy)

    # No need to mock get_video — both spies return empty lists, so the
    # post-merge hydration loop never runs.
    await retrieve_hybrid(
        query_text="how to set up agent",
        query_embedding=[0.1] * 1536,
        top_k=5,
        is_member=False,
    )

    assert keyword_spy.calls, "keyword_search not called"
    assert vector_spy.calls, "vector_search_pg not called"
    assert keyword_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube"]
    assert vector_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube"]


async def test_member_includes_dynamous(monkeypatch: pytest.MonkeyPatch):
    keyword_spy = _Spy()
    vector_spy = _Spy()

    from backend.db import repository

    monkeypatch.setattr(repository, "keyword_search", keyword_spy)
    monkeypatch.setattr(repository, "vector_search_pg", vector_spy)

    await retrieve_hybrid(
        query_text="advanced agent patterns",
        query_embedding=[0.2] * 1536,
        top_k=10,
        is_member=True,
    )

    assert keyword_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube", "dynamous"]
    assert vector_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube", "dynamous"]


async def test_default_is_non_member(monkeypatch: pytest.MonkeyPatch):
    """is_member parameter defaults to False — old callers stay safe."""
    keyword_spy = _Spy()
    vector_spy = _Spy()

    from backend.db import repository

    monkeypatch.setattr(repository, "keyword_search", keyword_spy)
    monkeypatch.setattr(repository, "vector_search_pg", vector_spy)

    await retrieve_hybrid(
        query_text="anything",
        query_embedding=[0.0] * 1536,
        top_k=5,
    )

    assert keyword_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube"]
    assert vector_spy.calls[0]["kwargs"]["allowed_source_types"] == ["youtube"]
