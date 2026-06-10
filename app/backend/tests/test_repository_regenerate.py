"""
Repository tests for delete_last_assistant_message (issue #280).

Postgres isn't available in the test environment, so these follow the
mocked-connection pattern from test_sources_event.py: a fake _acquire()
returns a recording connection and we assert on the SQL shape, parameter
binding, and return-value handling.
"""

from unittest.mock import AsyncMock, patch

from backend.db import repository


class _FakeAcquire:
    """Dual-purpose awaitable + async context manager wrapping a mock conn."""

    def __init__(self, conn):
        self._conn = conn

    def __await__(self):
        async def _do():
            return self._conn

        return _do().__await__()

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class TestDeleteLastAssistantMessage:
    async def test_returns_deleted_id(self) -> None:
        """When the DELETE matches a row, the deleted id is returned."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "msg-assistant-2"})

        with patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)):
            deleted = await repository.delete_last_assistant_message("conv-1", "user-1")

        assert deleted == "msg-assistant-2"

    async def test_returns_none_when_no_assistant_or_not_owner(self) -> None:
        """No matching row (no assistant message, or conversation belongs to
        another user) → None, never an exception."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)):
            deleted = await repository.delete_last_assistant_message("conv-1", "intruder")

        assert deleted is None

    async def test_query_is_owner_scoped_and_targets_latest_assistant_row_only(self) -> None:
        """The SQL must enforce ownership via a JOIN on conversations.user_id,
        filter to role='assistant', and delete at most the single most recent
        row (ORDER BY created_at DESC LIMIT 1) — user messages are untouched."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "msg-1"})

        with patch.object(repository, "_acquire", lambda: _FakeAcquire(mock_conn)):
            await repository.delete_last_assistant_message("conv-1", "user-1")

        assert mock_conn.fetchrow.await_count == 1
        args = mock_conn.fetchrow.call_args.args
        sql = args[0]
        # Parameterized binding — conversation id then user id, no f-strings.
        assert args[1] == "conv-1"
        assert args[2] == "user-1"
        assert "$1" in sql and "$2" in sql
        # Owner scoping through the conversations table.
        assert "JOIN conversations" in sql
        assert "user_id = $2" in sql
        # Only assistant rows are eligible.
        assert "role = 'assistant'" in sql
        # Only the single most recent one is deleted.
        assert "ORDER BY m.created_at DESC" in sql
        assert "LIMIT 1" in sql
        assert sql.strip().startswith("DELETE FROM messages")
        assert "RETURNING id" in sql
