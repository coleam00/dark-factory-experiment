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

    async def test_keyword_search_binds_language_into_both_tsquery_calls(self):
        """Both plainto_tsquery calls carry the bound regconfig ($4), none are bare."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("hello world", top_k=5)

        call_args = mock_conn.fetch.call_args
        sql = call_args[0][0]

        # The bound-regconfig form must appear in BOTH the ts_rank and WHERE clauses.
        # Guards against fixing only the @@ clause and forgetting ts_rank (or vice-versa).
        assert sql.count("plainto_tsquery($4::regconfig, $1)") == 2
        # No bare plainto_tsquery($1) — that would fall back to the server GUC.
        assert "plainto_tsquery($1)" not in sql

    async def test_keyword_search_passes_language_arg(self):
        """language is bound as the 4th positional arg, defaulting to 'english'."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_acquire = AsyncMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)

        # Default language
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("hello world", top_k=5)
        assert mock_conn.fetch.call_args[0][4] == "english"

        # Explicit non-default language is honored, not hardcoded
        with patch.object(repository, "_acquire", return_value=mock_acquire):
            await repository.keyword_search("hello world", top_k=5, language="simple")
        assert mock_conn.fetch.call_args[0][4] == "simple"

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
