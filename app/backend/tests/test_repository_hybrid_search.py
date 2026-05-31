"""
Tests for repository keyword_search and vector_search_pg functions.

Verifies SQL query construction, parameter passing, and return shape
using mocked asyncpg connections.
"""

from unittest.mock import AsyncMock, patch

from backend.db import repository


class TestKeywordSearch:
    """Tests for keyword_search() SQL execution."""

    async def test_keyword_search_calls_fetch_with_correct_params(self):
        """keyword_search passes query and top_k as $1, $2 to the SQL query."""
        mock_conn = AsyncMock()
        # Return plain dicts (asyncpg rows are dict-like)
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "chunk1",
                    "video_id": "v1",
                    "content": "hello world test content",
                    "chunk_index": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "snippet": "hello world",
                    "rank": 0.9,
                }
            ]
        )

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.keyword_search("hello world", top_k=5)

        mock_conn.fetch.assert_called_once()
        call_args = mock_conn.fetch.call_args

        # Verify SQL contains plainto_tsquery and ts_rank
        sql = call_args[0][0]
        assert "plainto_tsquery" in sql
        assert "ts_rank" in sql
        assert "search_vector" in sql
        assert "@@" in sql  # tsvector match operator

        # Verify positional args: query string and top_k
        args = call_args[0]
        # args is (sql_string, query_string, top_k_int)
        assert args[1] == "hello world"
        assert args[2] == 5

        # Verify result shape
        assert len(result) == 1
        assert result[0]["id"] == "chunk1"
        assert result[0]["rank"] == 0.9

    async def test_keyword_search_returns_empty_list_when_no_matches(self):
        """keyword_search returns empty list when DB returns no rows."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.keyword_search("nonexistent query xyz", top_k=5)

        assert result == []

    async def test_keyword_search_respects_top_k_limit(self):
        """keyword_search passes top_k as LIMIT $2."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("test", top_k=3)

        call_args = mock_conn.fetch.call_args
        sql = call_args[0][0]
        # LIMIT $2 means second parameter is top_k
        assert "LIMIT $2" in sql
        args = call_args[0]
        assert args[2] == 3


class TestVectorSearchPg:
    """Tests for vector_search_pg() SQL execution."""

    async def test_vector_search_pg_calls_fetch_with_explicit_cast(self):
        """vector_search_pg uses embedding::vector <=> $1::vector for cosine distance."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "chunk1",
                    "video_id": "v1",
                    "content": "test content",
                    "chunk_index": 0,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "snippet": "test snippet",
                    "distance": 0.15,
                }
            ]
        )

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.vector_search_pg([0.1] * 1536, top_k=5)

        mock_conn.fetch.assert_called_once()
        call_args = mock_conn.fetch.call_args

        # Verify SQL uses embedding::vector (explicit cast) for cosine distance
        sql = call_args[0][0]
        assert "embedding::vector" in sql
        assert "<=>" in sql  # cosine distance operator
        assert "$1::vector" in sql  # parameterized query embedding

        # Verify args: (sql, embedding_json, top_k)
        args = call_args[0]
        import json

        parsed = json.loads(args[1])
        assert parsed == [0.1] * 1536
        assert args[2] == 5

        assert len(result) == 1
        assert result[0]["distance"] == 0.15

    async def test_vector_search_pg_returns_empty_list_when_no_matches(self):
        """vector_search_pg returns empty list when DB returns no rows."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            result = await repository.vector_search_pg([0.1] * 1536, top_k=5)

        assert result == []

    async def test_vector_search_pg_orders_by_distance_ascending(self):
        """vector_search_pg SQL orders by cosine distance ascending (nearest first)."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.vector_search_pg([0.1] * 1536, top_k=5)

        call_args = mock_conn.fetch.call_args
        sql = call_args[0][0]
        assert "ORDER BY distance" in sql

    async def test_vector_search_pg_json_encodes_embedding(self):
        """vector_search_pg JSON-serializes the embedding list before passing to DB."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            embedding = [0.123] * 1536
            await repository.vector_search_pg(embedding, top_k=5)

        call_args = mock_conn.fetch.call_args
        args = call_args[0]
        import json

        parsed = json.loads(args[1])
        assert parsed == embedding


class TestVideoIdsFilter:
    """Tests for video_ids filter in keyword_search and vector_search_pg (issue #279)."""

    async def test_keyword_search_adds_video_id_clause_when_video_ids_set(self):
        """keyword_search adds AND video_id = ANY($4::text[]) when video_ids is provided."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("test", top_k=5, video_ids=["v1", "v2"])

        call_args = mock_conn.fetch.call_args
        sql, *args = call_args[0]
        assert "video_id = ANY($4::text[])" in sql
        assert "source_type = ANY($3::text[])" in sql
        assert args[3] == ["v1", "v2"]

    async def test_keyword_search_no_video_id_clause_when_video_ids_none(self):
        """keyword_search does NOT add video_id clause when video_ids is None."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("test", top_k=5, video_ids=None)

        call_args = mock_conn.fetch.call_args
        sql, *args = call_args[0]
        assert "video_id = ANY" not in sql
        assert len(args) == 3  # query, top_k, allowed_source_types

    async def test_keyword_search_no_video_id_clause_for_empty_list(self):
        """keyword_search treats empty video_ids same as None — no filter clause."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("test", top_k=5, video_ids=[])

        call_args = mock_conn.fetch.call_args
        sql = call_args[0][0]
        assert "video_id = ANY" not in sql

    async def test_vector_search_pg_adds_video_id_clause_when_video_ids_set(self):
        """vector_search_pg adds AND video_id = ANY($4::text[]) when video_ids is provided."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.vector_search_pg([0.1] * 1536, top_k=5, video_ids=["v1"])

        call_args = mock_conn.fetch.call_args
        sql, *args = call_args[0]
        assert "video_id = ANY($4::text[])" in sql
        assert "source_type = ANY($3::text[])" in sql
        assert args[3] == ["v1"]

    async def test_vector_search_pg_no_video_id_clause_when_video_ids_none(self):
        """vector_search_pg does NOT add video_id clause when video_ids is None."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.vector_search_pg([0.1] * 1536, top_k=5, video_ids=None)

        call_args = mock_conn.fetch.call_args
        sql, *args = call_args[0]
        assert "video_id = ANY" not in sql
        assert len(args) == 3  # embedding_json, top_k, allowed_source_types

    async def test_source_type_acl_preserved_alongside_video_ids_filter(self):
        """Both source_type ACL and video_ids filter appear in keyword_search SQL (AND semantics)."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search(
                "test",
                top_k=5,
                allowed_source_types=["youtube"],
                video_ids=["some_dynamous_id"],
            )

        call_args = mock_conn.fetch.call_args
        sql, *args = call_args[0]
        # Both clauses present — intersection guards non-member from dynamous chunks
        assert "source_type = ANY($3::text[])" in sql
        assert "video_id = ANY($4::text[])" in sql
        assert "youtube" in args[2]
        assert args[3] == ["some_dynamous_id"]
