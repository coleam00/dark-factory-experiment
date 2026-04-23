"""Tests for backend.rag.catalog — video catalog cache and prompt block builder."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from backend.rag import catalog

# ---------------------------------------------------------------------------
# Autouse fixture: reset module-level cache between tests for isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_catalog_cache() -> Generator[None, None, None]:
    """Ensure _catalog_cache is None before and after every test."""
    catalog._catalog_cache = None
    yield
    catalog._catalog_cache = None


# ---------------------------------------------------------------------------
# get_catalog / invalidate_catalog
# ---------------------------------------------------------------------------


async def test_get_catalog_populates_cache() -> None:
    fake = [{"id": "1", "title": "T", "url": "https://youtube.com/watch?v=abc"}]
    with patch("backend.rag.catalog.repository.list_videos", new_callable=AsyncMock) as mock_lv:
        mock_lv.return_value = fake
        result = await catalog.get_catalog()
        mock_lv.assert_called_once()
        assert result == fake


async def test_get_catalog_uses_cache() -> None:
    fake = [{"id": "1", "title": "T", "url": "https://youtube.com/watch?v=abc"}]
    catalog._catalog_cache = fake
    with patch("backend.rag.catalog.repository.list_videos", new_callable=AsyncMock) as mock_lv:
        result = await catalog.get_catalog()
        mock_lv.assert_not_called()
        assert result is fake


def test_invalidate_catalog_clears_cache() -> None:
    catalog._catalog_cache = [{"id": "1"}]
    catalog.invalidate_catalog()
    assert catalog._catalog_cache is None


# ---------------------------------------------------------------------------
# build_catalog_block
# ---------------------------------------------------------------------------


def test_build_catalog_block_standard() -> None:
    videos = [{"title": "My Video", "url": "https://youtube.com/watch?v=abc"}]
    block = catalog.build_catalog_block(videos, "standard")
    assert block["type"] == "text"
    assert "My Video" in block["text"]
    assert "https://youtube.com/watch?v=abc" in block["text"]
    assert block["cache_control"] == {"type": "ephemeral"}


def test_build_catalog_block_extended() -> None:
    videos = [{"title": "My Video", "url": "https://youtube.com/watch?v=abc"}]
    block = catalog.build_catalog_block(videos, "extended")
    assert block["cache_control"] == {"type": "ephemeral", "ttl": 3600}


def test_build_catalog_block_untitled_fallback() -> None:
    videos = [{"title": None, "url": "https://youtube.com/watch?v=abc"}]
    block = catalog.build_catalog_block(videos, "standard")
    assert "Untitled" in block["text"]


def test_build_catalog_block_numbering() -> None:
    videos = [
        {"title": "A", "url": "u1"},
        {"title": "B", "url": "u2"},
        {"title": "C", "url": "u3"},
    ]
    block = catalog.build_catalog_block(videos, "standard")
    assert "1. A" in block["text"]
    assert "2. B" in block["text"]
    assert "3. C" in block["text"]


# ---------------------------------------------------------------------------
# build_system_prompt integration
# ---------------------------------------------------------------------------


async def test_build_system_prompt_with_catalog() -> None:
    from backend.llm.openrouter import build_system_prompt

    fake = [{"title": "Vid", "url": "https://youtu.be/x"}]
    with (
        patch("backend.llm.openrouter.CATALOG_ENABLED", True),
        patch("backend.rag.catalog.get_catalog", new_callable=AsyncMock, return_value=fake),
    ):
        blocks = await build_system_prompt(max_tool_calls=6)
    assert len(blocks) == 2
    assert "cache_control" in blocks[-1]
    assert "cache_control" not in blocks[0]


async def test_build_system_prompt_without_catalog() -> None:
    from backend.llm.openrouter import build_system_prompt

    with patch("backend.llm.openrouter.CATALOG_ENABLED", False):
        blocks = await build_system_prompt(max_tool_calls=0)
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


async def test_build_system_prompt_catalog_empty_library() -> None:
    from backend.llm.openrouter import build_system_prompt

    with (
        patch("backend.llm.openrouter.CATALOG_ENABLED", True),
        patch("backend.rag.catalog.get_catalog", new_callable=AsyncMock, return_value=[]),
    ):
        blocks = await build_system_prompt(max_tool_calls=6)
    # Empty library: no catalog block, cache anchor on base block
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Additional coverage: CATALOG_TIER wiring, missing url, DB failure
# ---------------------------------------------------------------------------


def test_build_catalog_block_extended_ttl_is_integer() -> None:
    """CATALOG_TIER='extended' must produce an integer ttl, not a string."""
    with patch("backend.rag.catalog.CATALOG_CACHE_TTL_SECONDS", 3600):
        videos = [{"title": "Vid", "url": "https://youtu.be/x"}]
        block = catalog.build_catalog_block(videos, "extended")
    assert isinstance(block["cache_control"]["ttl"], int)
    assert block["cache_control"]["ttl"] == 3600


def test_build_catalog_block_missing_url() -> None:
    """Video dict without 'url' key must not raise a KeyError."""
    videos = [{"title": "My Video"}]
    block = catalog.build_catalog_block(videos, "standard")
    assert "My Video" in block["text"]
    # url defaults to empty string — no crash
    assert block["type"] == "text"


async def test_get_catalog_returns_empty_list_on_db_error() -> None:
    """DB failure in list_videos must return [] and not raise, preventing retry storm."""
    catalog._catalog_cache = None
    with patch(
        "backend.rag.catalog.repository.list_videos",
        new_callable=AsyncMock,
        side_effect=RuntimeError("DB unavailable"),
    ):
        result = await catalog.get_catalog()
    assert result == []
    # Cache is set to [] so the next call skips the DB entirely
    assert catalog._catalog_cache == []
