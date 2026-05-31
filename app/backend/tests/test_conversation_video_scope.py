"""Tests for conversation video scoping (issue #279).

Mocks asyncpg at the repository boundary so no real Postgres is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.db import repository
from backend.rag import tools as tools_module
from backend.rag.tools import execute_tool


# ---------------------------------------------------------------------------
# Repository-level persistence
# ---------------------------------------------------------------------------


async def test_create_conversation_with_scoped_video_ids():
    """create_conversation persists scoped_video_ids and returns them."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="INSERT 0 1")

    mock_acquire = AsyncMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    with patch.object(repository, "_acquire", return_value=mock_acquire):
        result = await repository.create_conversation(
            user_id="u1",
            title="Scoped Chat",
            scoped_video_ids=["v1", "v2"],
        )

    assert result["scoped_video_ids"] == ["v1", "v2"]
    call_args = mock_conn.execute.call_args
    sql = call_args[0][0]
    assert "scoped_video_ids" in sql
    assert call_args[0][6] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# Tool executor wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_passes_video_id_whitelist_to_search_tools(monkeypatch):
    """When a conversation has scoped_video_ids, execute_tool must forward
    the whitelist to all three search executors so retrieval is restricted."""
    captured = {}

    async def fake_hybrid(raw, embedding_cache=None, is_member=False, video_id_whitelist=None):
        captured["hybrid_whitelist"] = video_id_whitelist
        return {"ok": True, "text": "hi", "chunks": []}

    async def fake_keyword(raw, is_member=False, video_id_whitelist=None):
        captured["keyword_whitelist"] = video_id_whitelist
        return {"ok": True, "text": "hi", "chunks": []}

    async def fake_semantic(raw, embedding_cache=None, is_member=False, video_id_whitelist=None):
        captured["semantic_whitelist"] = video_id_whitelist
        return {"ok": True, "text": "hi", "chunks": []}

    monkeypatch.setattr(tools_module, "execute_search_hybrid", fake_hybrid)
    monkeypatch.setattr(tools_module, "execute_search_keyword", fake_keyword)
    monkeypatch.setattr(tools_module, "execute_search_semantic", fake_semantic)

    whitelist = {"v1", "v2"}

    await execute_tool("search_videos", {"query": "q"}, video_id_whitelist=whitelist)
    assert captured.get("hybrid_whitelist") == whitelist

    await execute_tool("keyword_search_videos", {"query": "q"}, video_id_whitelist=whitelist)
    assert captured.get("keyword_whitelist") == whitelist

    await execute_tool("semantic_search_videos", {"query": "q"}, video_id_whitelist=whitelist)
    assert captured.get("semantic_whitelist") == whitelist
