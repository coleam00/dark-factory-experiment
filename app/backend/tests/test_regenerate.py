"""
Tests for the regenerate endpoint (issue #280).
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

import pytest


# ---------------------------------------------------------------------------
# Repository unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_last_assistant_message_returns_true_when_deleted(monkeypatch):
    from backend.db import regenerate_repo

    calls = []

    class FakeConn:
        async def execute(self, *args):
            calls.append(args)
            return "DELETE 1"

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *exc):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(regenerate_repo, "get_pg_pool", lambda: FakePool())

    result = await regenerate_repo.delete_last_assistant_message("conv-123", "user-456")
    assert result is True
    assert len(calls) == 1
    assert calls[0][1] == "conv-123"
    assert calls[0][2] == "user-456"


@pytest.mark.asyncio
async def test_delete_last_assistant_message_returns_false_when_none(monkeypatch):
    from backend.db import regenerate_repo

    class FakeConn:
        async def execute(self, *args):
            return "DELETE 0"

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *exc):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(regenerate_repo, "get_pg_pool", lambda: FakePool())

    result = await regenerate_repo.delete_last_assistant_message("conv-123", "user-456")
    assert result is False


# ---------------------------------------------------------------------------
# has_last_assistant_message unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_last_assistant_message_returns_true_when_row_exists(monkeypatch):
    from backend.db import regenerate_repo

    class FakeConn:
        async def fetchrow(self, *args):
            return {"?column?": 1}

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *exc):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(regenerate_repo, "get_pg_pool", lambda: FakePool())

    result = await regenerate_repo.has_last_assistant_message("conv-123", "user-456")
    assert result is True


@pytest.mark.asyncio
async def test_has_last_assistant_message_returns_false_when_no_row(monkeypatch):
    from backend.db import regenerate_repo

    class FakeConn:
        async def fetchrow(self, *args):
            return None

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *exc):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    monkeypatch.setattr(regenerate_repo, "get_pg_pool", lambda: FakePool())

    result = await regenerate_repo.has_last_assistant_message("conv-123", "user-456")
    assert result is False


# ---------------------------------------------------------------------------
# Router wiring test
# ---------------------------------------------------------------------------


def test_regenerate_route_is_registered():
    """The regenerate endpoint must be mounted on the messages router."""
    from backend.routes.messages import router

    paths = [str(r.path) for r in router.routes]
    assert "/conversations/{conv_id}/regenerate" in paths
