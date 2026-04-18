"""
Tests for create_sync_run and update_sync_run repository functions.

Verifies datetime handling: both functions accept datetime | str inputs
and handle them correctly for the database and return values.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from backend.db import repository


class _FakeConn:
    """Minimal connection mock for repository tests."""

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


class _SyncAcquire:
    """A synchronous acquire context manager (not awaitable)."""

    async def __aenter__(self):
        return _FakeConn()

    async def __aexit__(self, *exc):
        return False


def _make_sync_acquire():
    return _SyncAcquire()


class TestCreateSyncRun:
    """Tests for create_sync_run() datetime handling."""

    async def test_create_sync_run_with_datetime_input(self):
        """create_sync_run accepts datetime and returns ISO-formatted started_at string."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)

        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            dt = datetime(2026, 4, 18, 10, 30, 0, tzinfo=UTC)
            result = await repository.create_sync_run(
                sync_run_id="test-run-1",
                started_at=dt,
            )

        # Verify DB insert was called with the datetime directly (asyncpg accepts datetime)
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        # The second arg is started_at, should be the datetime object
        assert call_args[1] == "test-run-1"
        assert call_args[2] == dt  # asyncpg accepts datetime for TIMESTAMPTZ

        # Verify return value has ISO string
        assert result["id"] == "test-run-1"
        assert result["started_at"] == dt.isoformat()
        assert isinstance(result["started_at"], str)

    async def test_create_sync_run_with_string_input(self):
        """create_sync_run accepts string and passes it through unchanged."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)

        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            iso_str = "2026-04-18T10:30:00+00:00"
            result = await repository.create_sync_run(
                sync_run_id="test-run-2",
                started_at=iso_str,
            )

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert call_args[2] == iso_str  # string passed directly

        assert result["id"] == "test-run-2"
        assert result["started_at"] == iso_str
        assert isinstance(result["started_at"], str)

    async def test_create_sync_run_returns_expected_shape(self):
        """create_sync_run returns a dict with all expected keys."""
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=_FakeConn())
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_sync_run(
                sync_run_id="test-run-3",
                started_at=datetime.now(UTC),
            )

        assert set(result.keys()) == {
            "id",
            "status",
            "videos_total",
            "videos_new",
            "videos_error",
            "started_at",
            "finished_at",
        }
        assert result["status"] == "running"
        assert result["finished_at"] is None


class TestUpdateSyncRun:
    """Tests for update_sync_run() datetime handling."""

    async def test_update_sync_run_with_datetime_finished_at(self):
        """update_sync_run accepts datetime and returns True on success."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            dt = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
            result = await repository.update_sync_run(
                sync_run_id="test-run-1",
                status="completed",
                finished_at=dt,
                videos_total=10,
                videos_new=5,
                videos_error=0,
            )

        assert result is True
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        # Verify the finished_at datetime was passed
        assert dt in call_args

    async def test_update_sync_run_with_string_finished_at(self):
        """update_sync_run accepts string finished_at and passes it through."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            iso_str = "2026-04-18T12:00:00+00:00"
            result = await repository.update_sync_run(
                sync_run_id="test-run-2",
                status="completed",
                finished_at=iso_str,
            )

        assert result is True
        call_args = mock_conn.execute.call_args[0]
        assert iso_str in call_args

    async def test_update_sync_run_with_none_finished_at(self):
        """update_sync_run accepts None for finished_at."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")

        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.update_sync_run(
                sync_run_id="test-run-3",
                status="running",
                finished_at=None,
            )

        assert result is True
