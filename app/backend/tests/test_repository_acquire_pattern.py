"""
Regression tests for _acquire() async context manager pattern.

The _acquire() function is synchronous and returns an asyncpg.pool.PoolAcquireContext,
which IS itself an async context manager. The correct usage is:
    async with _acquire() as conn:

NOT:
    async with await _acquire() as conn:   # WRONG — _acquire() is not awaitable

These tests verify that keyword_search and vector_search_pg use the correct pattern.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from backend.db import repository


class _SyncAcquireNotAwaitable:
    """A PoolAcquireContext mock that explicitly CANNOT be awaited.

    This mimics the real asyncpg.pool.PoolAcquireContext behavior:
    - It has __aenter__ and __aexit__ (async context manager)
    - It does NOT have __await__ (not a coroutine)

    If any code tries `await _acquire()`, this will raise TypeError.
    """

    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """Minimal connection mock that returns empty results."""

    async def execute(self, *args, **kwargs):
        return None

    async def fetch(self, *args, **kwargs):
        return []

    async def fetchrow(self, *args, **kwargs):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_sync_acquire():
    """Return a _SyncAcquireNotAwaitable instance."""
    return _SyncAcquireNotAwaitable()


class TestKeywordSearchAcquirePattern:
    """Verify keyword_search uses `async with _acquire()` not `await _acquire()`."""

    async def test_keyword_search_works_with_sync_acquire_context_manager(self):
        """keyword_search must use `_acquire()` as an async context manager directly.

        This test uses a mock that cannot be awaited (only used as a context manager).
        If the code uses `await _acquire()`, pytest will fail with TypeError.
        """
        with patch.object(repository, "_acquire", _make_sync_acquire):
            # If the code uses `await _acquire()`, this will raise:
            # TypeError: object '_SyncAcquireNotAwaitable' can't be used in 'await' expression
            result = await repository.keyword_search("test query", top_k=5)
            assert isinstance(result, list)

    async def test_keyword_search_does_not_await_acquire(self):
        """Double-check: verify _acquire is called but not awaited by checking mock interaction."""
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=_FakeConn())
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        # Do NOT set __await__ — this makes it non-awaitable

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.keyword_search("test", top_k=5)

            # Verify _acquire was called once (not awaited — that's the point)
            repository._acquire.assert_called_once()

            # Verify __aenter__ was called (context manager protocol, not await)
            mock_acquire.__aenter__.assert_called_once()

            # Verify __await__ was NOT called (ensuring no `await _acquire()`)
            # MagicMock auto-creates attributes, so __await__ exists but is a new MagicMock
            # If await was called, it would have been invoked (called). Since we never await,
            # the mock's __await__ was never invoked.
            await_mock = getattr(mock_acquire, "__await__", None)
            if await_mock is not None:
                assert not await_mock.called, (
                    "await _acquire() was called — this is the bug this test prevents"
                )

            assert isinstance(result, list)


class TestVectorSearchPgAcquirePattern:
    """Verify vector_search_pg uses `async with _acquire()` not `await _acquire()`."""

    async def test_vector_search_pg_works_with_sync_acquire_context_manager(self):
        """vector_search_pg must use `_acquire()` as an async context manager directly.

        This test uses a mock that cannot be awaited (only used as a context manager).
        If the code uses `await _acquire()`, pytest will fail with TypeError.
        """
        with patch.object(repository, "_acquire", _make_sync_acquire):
            result = await repository.vector_search_pg([0.1] * 1536, top_k=5)
            assert isinstance(result, list)

    async def test_vector_search_pg_does_not_await_acquire(self):
        """Double-check: verify _acquire is called but not awaited."""
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=_FakeConn())
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        # Do NOT set __await__ — this makes it non-awaitable

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.vector_search_pg([0.1] * 1536, top_k=5)

            repository._acquire.assert_called_once()
            mock_acquire.__aenter__.assert_called_once()
            await_mock = getattr(mock_acquire, "__await__", None)
            if await_mock is not None:
                assert not await_mock.called, (
                    "await _acquire() was called — this is the bug this test prevents"
                )

            assert isinstance(result, list)
