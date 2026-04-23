"""
Tests for the video catalog cache (app/backend/rag/catalog.py).

Verifies:
  - Catalog block returns non-empty string with all seeded video ids and titles
  - CATALOG_TIER=minimal omits description text
  - CATALOG_TIER=standard includes description text
  - invalidate_catalog_cache() followed by get_catalog_block() re-fetches from DB
  - Empty library returns empty string
  - Catalog block passed to LLM includes cache_control: {"type": "ephemeral"}
  - CATALOG_ENABLED=False skips fetch (passes None to LLM)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.rag.catalog import (
    _catalog_cache,
    _render_catalog,
    get_catalog_block,
    invalidate_catalog_cache,
)


# -------------------------------------------------------------------------- #
# Fixtures
# -------------------------------------------------------------------------- #

_SEEDED_VIDEOS = [
    {
        "id": "vid-001",
        "title": "Getting Started with Dark Factory",
        "description": "An introduction to the Archon workflow system.",
        "url": "https://youtube.com/watch?v=aaa",
        "created_at": "2026-01-01T00:00:00Z",
        "channel_id": "UC_test",
        "channel_title": "Test Channel",
    },
    {
        "id": "vid-002",
        "title": "Advanced MCP Server Setup",
        "description": "Deep dive into model context protocol configuration.",
        "url": "https://youtube.com/watch?v=bbb",
        "created_at": "2026-01-02T00:00:00Z",
        "channel_id": "UC_test",
        "channel_title": "Test Channel",
    },
]


# -------------------------------------------------------------------------- #
# Reset cache between tests
# -------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def reset_catalog_cache():
    """Ensure the module-level catalog cache is clean before and after every test."""
    global _catalog_cache
    _catalog_cache = None
    yield
    _catalog_cache = None


# -------------------------------------------------------------------------- #
# Tests
# -------------------------------------------------------------------------- #


class TestRenderCatalog:
    """Tests for _render_catalog()."""

    def test_renders_all_video_ids_and_titles(self):
        """Output contains every video id and title from the input list."""
        result = _render_catalog(_SEEDED_VIDEOS, tier="minimal")
        for video in _SEEDED_VIDEOS:
            assert video["id"] in result
            assert video["title"] in result

    def test_minimal_tier_omits_description(self):
        """CATALOG_TIER=minimal output does not contain description text."""
        result = _render_catalog(_SEEDED_VIDEOS, tier="minimal")
        assert "introduction" not in result.lower()
        assert "Deep dive" not in result

    def test_standard_tier_includes_description(self):
        """CATALOG_TIER=standard output contains description text."""
        result = _render_catalog(_SEEDED_VIDEOS, tier="standard")
        assert "introduction" in result.lower() or "Deep dive" in result

    def test_empty_library_returns_empty_string(self):
        """Zero videos in DB returns empty string."""
        result = _render_catalog([], tier="minimal")
        assert result == ""

    def test_wraps_in_video_library_tags(self):
        """Output contains the opening and closing library tags."""
        result = _render_catalog(_SEEDED_VIDEOS, tier="minimal")
        assert "<video_library>" in result
        assert "</video_library>" in result


class TestGetCatalogBlock:
    """Tests for get_catalog_block()."""

    async def test_returns_non_empty_with_seeded_data(self):
        """With seeded data, get_catalog_block() returns a non-empty string."""
        with patch(
            "backend.rag.catalog.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_SEEDED_VIDEOS,
        ):
            result = await get_catalog_block()
        assert result is not None
        assert len(result) > 0
        assert "<video_library>" in result

    async def test_invalidate_then_get_refetches(self):
        """invalidate_catalog_cache() followed by get_catalog_block() re-fetches from DB."""
        # First call populates the cache
        with patch(
            "backend.rag.catalog.repository.list_videos",
            new_callable=AsyncMock,
            return_value=_SEEDED_VIDEOS,
        ) as mock_list:
            first = await get_catalog_block()
            assert mock_list.call_count == 1

        # Cache is now warm; second call should NOT re-fetch
        global _catalog_cache
        assert _catalog_cache is not None
        second = await get_catalog_block()
        assert mock_list.call_count == 1  # still 1, cache was hit

        # Invalidate then call again — should re-fetch
        invalidate_catalog_cache()
        assert _catalog_cache is None
        third = await get_catalog_block()
        assert mock_list.call_count == 2

    async def test_empty_library_returns_empty_string(self):
        """Zero videos in DB returns empty string (not None)."""
        with patch(
            "backend.rag.catalog.repository.list_videos",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await get_catalog_block()
        assert result == ""


class TestCatalogBlockInLlmRequestPayload:
    """Tests for catalog block with cache_control in LLM request payload."""

    async def test_catalog_block_includes_cache_control_ephemeral(self):
        """build_system_prompt_blocks with catalog returns blocks with cache_control ephemeral."""
        from backend.llm.openrouter import build_system_prompt_blocks

        catalog_text = "<video_library>\n  - [v1] Test Video\n</video_library>"
        blocks = build_system_prompt_blocks(max_tool_calls=6, catalog_block=catalog_text)

        # Block 1: base system prompt
        assert blocks[0]["type"] == "text"
        assert "cache_control" in blocks[0]
        assert blocks[0]["cache_control"]["type"] == "ephemeral"

        # Block 2: catalog block
        assert len(blocks) == 2
        assert blocks[1]["type"] == "text"
        assert "cache_control" in blocks[1]
        assert blocks[1]["cache_control"]["type"] == "ephemeral"
        assert catalog_text in blocks[1]["text"]

    async def test_no_catalog_block_when_empty_string(self):
        """Empty catalog_block produces only the base system prompt block."""
        from backend.llm.openrouter import build_system_prompt_blocks

        blocks = build_system_prompt_blocks(max_tool_calls=6, catalog_block="")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    async def test_no_extra_empty_blocks(self):
        """No catalog block added when catalog_block is None."""
        from backend.llm.openrouter import build_system_prompt_blocks

        blocks = build_system_prompt_blocks(max_tool_calls=6, catalog_block=None)
        assert len(blocks) == 1


class TestCatalogEnabledFalse:
    """Tests for CATALOG_ENABLED=False safe rollback path."""

    async def test_catalog_enabled_false_skips_fetch(self, monkeypatch):
        """CATALOG_ENABLED=False bypasses catalog fetch (returns None directly)."""
        import backend.config
        monkeypatch.setattr(backend.config, "CATALOG_ENABLED", False)

        with patch(
            "backend.rag.catalog.repository.list_videos",
            new_callable=AsyncMock,
        ) as mock_list:
            result = await get_catalog_block()

        assert result is None
        mock_list.assert_not_called()
