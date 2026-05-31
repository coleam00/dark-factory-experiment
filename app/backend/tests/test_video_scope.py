"""Tests for conversation video-scoping (issue #279).

A conversation can be restricted to a subset of videos. When a scope is set,
both keyword and vector search must filter chunks to `video_id = ANY(scope)`;
when it's None, retrieval searches the whole library (today's behavior).

The test DB is mocked (no real Postgres in this environment — see
test_repository_hybrid_search.py for the same pattern), so these assert SQL
construction, parameter binding, and pass-through wiring rather than real
query results.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.db import repository
from backend.rag import tools as tools_module


def _mock_acquire(mock_conn: AsyncMock) -> AsyncMock:
    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    return mock_acquire


# ---------------------------------------------------------------------------
# Repository: keyword_search / vector_search_pg scope filter
# ---------------------------------------------------------------------------


class TestKeywordSearchScope:
    async def test_scope_filter_in_sql_and_bound_as_param4(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            await repository.keyword_search("hello", top_k=5, allowed_video_ids=["vid-a", "vid-b"])
        sql, *args = mock_conn.fetch.call_args[0]
        # The scope guard is injected and parameterized as $4.
        assert "$4::text[] IS NULL OR video_id = ANY($4::text[])" in sql
        # args: (query, top_k, allowed_source_types, allowed_video_ids)
        assert args[3] == ["vid-a", "vid-b"]

    async def test_no_scope_binds_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            await repository.keyword_search("hello", top_k=5)
        # Positional args: (sql, query/embedding, top_k, source_types, video_ids).
        # $4 is None → the SQL guard short-circuits to "search everything".
        assert mock_conn.fetch.call_args[0][4] is None


class TestVectorSearchScope:
    async def test_scope_filter_in_sql_and_bound_as_param4(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            await repository.vector_search_pg([0.1] * 1536, top_k=5, allowed_video_ids=["vid-a"])
        sql, *args = mock_conn.fetch.call_args[0]
        assert "$4::text[] IS NULL OR video_id = ANY($4::text[])" in sql
        # args: (embedding_json, top_k, allowed_source_types, allowed_video_ids)
        assert args[3] == ["vid-a"]

    async def test_no_scope_binds_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            await repository.vector_search_pg([0.1] * 1536, top_k=5)
        assert mock_conn.fetch.call_args[0][4] is None


# ---------------------------------------------------------------------------
# Repository: create_conversation + update_conversation_scope
# ---------------------------------------------------------------------------


class TestConversationScopePersistence:
    async def test_create_conversation_inserts_scope(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            result = await repository.create_conversation(
                user_id="u1", title="t", scoped_video_ids=["vid-a"]
            )
        sql, *args = mock_conn.execute.call_args[0]
        assert "scoped_video_ids" in sql
        # The scope list is bound and echoed back in the returned dict.
        assert ["vid-a"] in args
        assert result["scoped_video_ids"] == ["vid-a"]

    async def test_create_conversation_defaults_scope_none(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            result = await repository.create_conversation(user_id="u1")
        assert result["scoped_video_ids"] is None

    async def test_update_scope_issues_scoped_update(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            ok = await repository.update_conversation_scope(
                "c1", user_id="u1", scoped_video_ids=["vid-a", "vid-b"]
            )
        assert ok is True
        sql, *args = mock_conn.execute.call_args[0]
        assert "UPDATE conversations SET scoped_video_ids" in sql
        # Owner scoping is preserved — user_id is part of the WHERE clause.
        assert "user_id" in sql
        assert ["vid-a", "vid-b"] in args
        assert "c1" in args
        assert "u1" in args

    async def test_update_scope_clear_passes_none(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            await repository.update_conversation_scope("c1", user_id="u1", scoped_video_ids=None)
        args = mock_conn.execute.call_args[0]
        assert None in args

    async def test_update_scope_returns_false_when_not_owner(self):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        with patch.object(repository, "_acquire", return_value=_mock_acquire(mock_conn)):
            ok = await repository.update_conversation_scope(
                "c1", user_id="other", scoped_video_ids=["vid-a"]
            )
        assert ok is False


# ---------------------------------------------------------------------------
# Tools: search executors forward the scope to the repository / retriever
# ---------------------------------------------------------------------------


class TestToolsForwardScope:
    async def test_hybrid_forwards_scope_to_retriever(self):
        scope = ["vid-a", "vid-b"]
        with (
            patch.object(tools_module, "_embed_query", AsyncMock(return_value=[0.1] * 4)),
            patch(
                "backend.rag.retriever_hybrid.retrieve_hybrid",
                AsyncMock(return_value=[]),
            ) as mock_retrieve,
        ):
            result = await tools_module.execute_search_hybrid(
                {"query": "q"}, allowed_video_ids=scope
            )
        assert result["ok"] is True
        assert mock_retrieve.call_args.kwargs["allowed_video_ids"] == scope

    async def test_keyword_forwards_scope_to_repository(self):
        scope = ["vid-a"]
        with (
            patch.object(
                tools_module.repository, "keyword_search", AsyncMock(return_value=[])
            ) as mock_kw,
            patch.object(tools_module, "_hydrate_chunks", AsyncMock(return_value=[])),
        ):
            result = await tools_module.execute_search_keyword(
                {"query": "q"}, allowed_video_ids=scope
            )
        assert result["ok"] is True
        assert mock_kw.call_args.kwargs["allowed_video_ids"] == scope

    async def test_semantic_forwards_scope_to_repository(self):
        scope = ["vid-a"]
        with (
            patch.object(tools_module, "_embed_query", AsyncMock(return_value=[0.1] * 4)),
            patch.object(
                tools_module.repository, "vector_search_pg", AsyncMock(return_value=[])
            ) as mock_vec,
            patch.object(tools_module, "_hydrate_chunks", AsyncMock(return_value=[])),
        ):
            result = await tools_module.execute_search_semantic(
                {"query": "q"}, allowed_video_ids=scope
            )
        assert result["ok"] is True
        assert mock_vec.call_args.kwargs["allowed_video_ids"] == scope

    async def test_dispatcher_forwards_scope(self):
        scope = ["vid-a"]
        with patch.object(
            tools_module, "execute_search_hybrid", AsyncMock(return_value={"ok": True})
        ) as mock_hybrid:
            await tools_module.execute_tool(
                "search_videos", {"query": "q"}, allowed_video_ids=scope
            )
        assert mock_hybrid.call_args.kwargs["allowed_video_ids"] == scope


# ---------------------------------------------------------------------------
# Retriever: retrieve_hybrid forwards scope to both search functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_hybrid_forwards_scope_to_both_searches(monkeypatch):
    from backend import config
    from backend.rag import retriever_hybrid

    # retrieve_hybrid does a local `from backend.config import DATABASE_URL`,
    # so the truthiness check reads backend.config at call time. conftest
    # already sets a test URL, but pin it here so the test is self-contained.
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test", raising=False)
    mock_kw = AsyncMock(return_value=[])
    mock_vec = AsyncMock(return_value=[])
    monkeypatch.setattr(retriever_hybrid.repository, "keyword_search", mock_kw)
    monkeypatch.setattr(retriever_hybrid.repository, "vector_search_pg", mock_vec)

    scope = ["vid-a", "vid-b"]
    await retriever_hybrid.retrieve_hybrid("q", [0.1] * 4, top_k=5, allowed_video_ids=scope)

    assert mock_kw.call_args.kwargs["allowed_video_ids"] == scope
    assert mock_vec.call_args.kwargs["allowed_video_ids"] == scope
