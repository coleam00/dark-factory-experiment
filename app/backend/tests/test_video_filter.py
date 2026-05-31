"""
Tests for video_filter conversation scoping (issue #279).

Verifies create_conversation / get_conversation / list_conversations handle
the video_filter JSONB column correctly: persistence, deserialization, and
empty-list normalization.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.db import repository


class TestCreateConversationVideoFilter:
    """video_filter persistence in create_conversation."""

    async def test_normalizes_empty_list_to_none(self):
        """video_filter=[] is treated as no filter — NULL inserted and None returned."""
        inserted: dict = {}

        mock_conn = AsyncMock()

        async def capture_execute(sql, *args):
            inserted["args"] = args

        mock_conn.execute = capture_execute
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(user_id="u1", video_filter=[])

        assert result["video_filter"] is None
        # args: conv_id, user_id, title, vf_json, now, now
        assert inserted["args"][3] is None

    async def test_serializes_video_filter_ids(self):
        """video_filter with IDs is JSON-serialized for DB and returned as a list."""
        inserted: dict = {}

        mock_conn = AsyncMock()

        async def capture_execute(sql, *args):
            inserted["args"] = args

        mock_conn.execute = capture_execute
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(
                user_id="u1", video_filter=["v1", "v2"]
            )

        assert result["video_filter"] == ["v1", "v2"]
        vf_json = inserted["args"][3]
        assert vf_json is not None
        assert json.loads(vf_json) == ["v1", "v2"]

    async def test_default_none_inserts_null(self):
        """Default video_filter=None inserts NULL and returns None."""
        inserted: dict = {}

        mock_conn = AsyncMock()

        async def capture_execute(sql, *args):
            inserted["args"] = args

        mock_conn.execute = capture_execute
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(user_id="u1")

        assert result["video_filter"] is None
        assert inserted["args"][3] is None


class TestGetConversationVideoFilter:
    """JSONB deserialization in get_conversation."""

    async def test_deserializes_json_string_to_list(self):
        """get_conversation parses the JSONB string asyncpg returns into a Python list."""
        raw_row = {
            "id": "conv1",
            "user_id": "u1",
            "title": "Test",
            "video_filter": json.dumps(["v1", "v2"]),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=raw_row)
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.get_conversation("conv1", user_id="u1")

        assert result is not None
        assert result["video_filter"] == ["v1", "v2"]
        assert isinstance(result["video_filter"], list)

    async def test_null_video_filter_returns_none(self):
        """get_conversation returns None for NULL video_filter (no scope)."""
        raw_row = {
            "id": "conv1",
            "user_id": "u1",
            "title": "Test",
            "video_filter": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=raw_row)
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.get_conversation("conv1", user_id="u1")

        assert result is not None
        assert result["video_filter"] is None

    async def test_missing_conv_returns_none(self):
        """get_conversation returns None when the conversation doesn't exist."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.get_conversation("nonexistent", user_id="u1")

        assert result is None


class TestListConversationsVideoFilter:
    """JSONB deserialization in list_conversations (for Sidebar badge)."""

    async def test_deserializes_video_filter_in_list(self):
        """list_conversations parses video_filter JSONB strings in every row."""
        raw_rows = [
            {
                "id": "c1",
                "user_id": "u1",
                "title": "Scoped",
                "video_filter": json.dumps(["v1"]),
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "preview": None,
            },
            {
                "id": "c2",
                "user_id": "u1",
                "title": "Unscoped",
                "video_filter": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "preview": None,
            },
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=raw_rows)
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            results = await repository.list_conversations(user_id="u1")

        assert results[0]["video_filter"] == ["v1"]
        assert isinstance(results[0]["video_filter"], list)
        assert results[1]["video_filter"] is None
