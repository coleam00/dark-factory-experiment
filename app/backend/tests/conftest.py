"""Shared pytest fixtures for DynaChat backend tests.

Conventions (see CLAUDE.md §Testing):
- Use pytest-asyncio for async tests (`@pytest.mark.asyncio` or the
  `asyncio_mode = "auto"` mode configured in `pyproject.toml`).
- Use `httpx.AsyncClient` against a test FastAPI app for integration tests.
- Tests must NEVER touch `app/backend/data/chat.db`. Spin up a temp SQLite
  database per-test via the `tmp_path` fixture.
"""

import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

# Set auth-related env vars BEFORE any backend import so config.py picks them up.
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

import pytest

import backend.rag.retriever as retriever_module
from backend import rate_limit


@pytest.fixture(autouse=True)
def reset_retriever_cache():
    """Ensure the module-level retriever cache is clean before and after every test."""
    retriever_module._cache = None
    yield
    retriever_module._cache = None


@pytest.fixture
def message_store() -> dict[str, list[datetime]]:
    """Per-test in-memory `user_messages` store keyed by stringified UUID.

    Exposed so integration tests can seed rows directly (e.g. "user already
    has 25 messages; verify 429 on next send").
    """
    return defaultdict(list)


@pytest.fixture(autouse=True)
def patch_rate_limit(monkeypatch, message_store):
    """Replace `rate_limit.check_and_record` + `.get_status` with in-memory fakes.

    The real implementation opens a transaction against Postgres (which isn't
    available in the test environment). The fakes preserve the atomic contract
    by doing count + insert in a single sync block — sufficient for the
    single-threaded httpx.AsyncClient tests.
    """

    async def fake_check_and_record(user_id: UUID | str) -> None:
        uid = str(user_id)
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=rate_limit.WINDOW_HOURS)
        in_window = [t for t in message_store[uid] if t > cutoff]
        if len(in_window) >= rate_limit.DAILY_MESSAGE_CAP:
            oldest = min(in_window)
            raise rate_limit.RateLimitExceeded(
                reset_at=oldest + timedelta(hours=rate_limit.WINDOW_HOURS)
            )
        message_store[uid].append(now)

    async def fake_get_status(user_id: UUID | str) -> rate_limit.RateLimitStatus:
        uid = str(user_id)
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=rate_limit.WINDOW_HOURS)
        in_window = [t for t in message_store[uid] if t > cutoff]
        used = len(in_window)
        remaining = max(0, rate_limit.DAILY_MESSAGE_CAP - used)
        resets_at: datetime | None = None
        if in_window:
            resets_at = min(in_window) + timedelta(hours=rate_limit.WINDOW_HOURS)
        return rate_limit.RateLimitStatus(
            used=used, remaining=remaining, resets_at=resets_at
        )

    monkeypatch.setattr(rate_limit, "check_and_record", fake_check_and_record)
    monkeypatch.setattr(rate_limit, "get_status", fake_get_status)
