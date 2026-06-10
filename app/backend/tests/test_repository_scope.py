"""
Tests for per-conversation video scope (issue #279) at the repository layer.

Covers:
  - create_conversation round-trips scoped_video_ids and normalizes empty→NULL.
  - keyword_search / vector_search_pg apply the video_id_filter as an additive
    `video_id = ANY(...)` clause, and leave the unscoped path unchanged.

All DB access is mocked via the same _acquire pattern used in
test_repository_hybrid_search.py — no live Postgres.
"""

from unittest.mock import AsyncMock, patch

from backend.db import repository


def _mock_acquire(fetch_return=None):
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])
    mock_conn.execute = AsyncMock(return_value=None)

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    return mock_acquire, mock_conn


class TestCreateConversationScope:
    async def test_persists_scoped_video_ids(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(
                user_id="u1",
                title="Scoped chat",
                scoped_video_ids=["v1", "v2"],
            )

        # The INSERT carries the scoped list as the 6th positional arg ($6).
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "scoped_video_ids" in sql
        assert call_args[0][6] == ["v1", "v2"]
        # The returned dict surfaces the scope and preserves ownership fields.
        assert result["scoped_video_ids"] == ["v1", "v2"]
        assert result["user_id"] == "u1"
        assert result["title"] == "Scoped chat"

    async def test_empty_list_normalized_to_none(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(
                user_id="u1",
                scoped_video_ids=[],
            )

        # Empty list stores NULL so retrieval treats it as "search everything".
        assert mock_conn.execute.call_args[0][6] is None
        assert result["scoped_video_ids"] is None

    async def test_default_is_none(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.create_conversation(user_id="u1")

        assert mock_conn.execute.call_args[0][6] is None
        assert result["scoped_video_ids"] is None


class TestKeywordSearchScope:
    async def test_unscoped_path_unchanged(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("hello", top_k=5)

        sql = mock_conn.fetch.call_args[0][0]
        args = mock_conn.fetch.call_args[0]
        # No video filter clause and no 4th param when filter is None.
        assert "video_id = ANY" not in sql
        assert len(args) == 4  # sql, query, top_k, allowed_source_types

    async def test_filter_adds_clause_and_arg(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("hello", top_k=5, video_id_filter=["v1", "v2"])

        sql = mock_conn.fetch.call_args[0][0]
        args = mock_conn.fetch.call_args[0]
        assert "video_id = ANY($4::text[])" in sql
        assert args[4] == ["v1", "v2"]


class TestVectorSearchScope:
    async def test_unscoped_path_unchanged(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.vector_search_pg([0.1] * 1536, top_k=5)

        sql = mock_conn.fetch.call_args[0][0]
        args = mock_conn.fetch.call_args[0]
        assert "video_id = ANY" not in sql
        assert len(args) == 4  # sql, embedding_json, top_k, allowed_source_types

    async def test_filter_adds_clause_and_arg(self):
        mock_acquire, mock_conn = _mock_acquire()
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.vector_search_pg([0.1] * 1536, top_k=5, video_id_filter=["v1"])

        sql = mock_conn.fetch.call_args[0][0]
        args = mock_conn.fetch.call_args[0]
        assert "video_id = ANY($4::text[])" in sql
        assert args[4] == ["v1"]
