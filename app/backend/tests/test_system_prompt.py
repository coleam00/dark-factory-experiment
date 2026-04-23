"""Regression tests for the RAG system prompt.

Pin two behaviors the factory rules care about:
  1. The prompt forbids raw ids in the assistant's prose (issue #93).
  2. The prompt no longer injects pre-retrieved context — retrieval is
     entirely tool-driven now — and it tells the model to use its tools.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.llm.openrouter import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


def _blocks_text(blocks: list[dict]) -> str:
    """Concatenate all text blocks into a single string for easy assertions."""
    return "\n".join(b["text"] for b in blocks)


class TestSystemPromptForbidsRawIds:
    def test_template_forbids_raw_ids(self) -> None:
        assert "video IDs" in SYSTEM_PROMPT_TEMPLATE or "video id" in SYSTEM_PROMPT_TEMPLATE.lower()
        assert "title only" in SYSTEM_PROMPT_TEMPLATE.lower()

    async def test_built_prompt_contains_rule(self) -> None:
        with patch("backend.llm.openrouter.CATALOG_ENABLED", False):
            blocks = await build_system_prompt(max_tool_calls=6)
        prompt = _blocks_text(blocks).lower()
        assert "never write youtube video ids" in prompt
        assert "title only" in prompt


class TestSystemPromptToolBased:
    async def test_prompt_has_no_context_placeholder(self) -> None:
        with patch("backend.llm.openrouter.CATALOG_ENABLED", False):
            blocks = await build_system_prompt(max_tool_calls=0)
        prompt = _blocks_text(blocks)
        assert "{context}" not in prompt
        assert "Context:" not in prompt

    async def test_prompt_mentions_all_tools_when_enabled(self) -> None:
        with patch("backend.llm.openrouter.CATALOG_ENABLED", False):
            blocks = await build_system_prompt(max_tool_calls=6)
        prompt = _blocks_text(blocks)
        for tool_name in (
            "search_videos",
            "keyword_search_videos",
            "semantic_search_videos",
            "get_video_transcript",
        ):
            assert tool_name in prompt
        assert "6 tool calls" in prompt or "6 " in prompt  # cap echoed in guidance

    async def test_prompt_omits_tool_guidance_when_cap_zero(self) -> None:
        with patch("backend.llm.openrouter.CATALOG_ENABLED", False):
            blocks = await build_system_prompt(max_tool_calls=0)
        prompt = _blocks_text(blocks)
        assert "search_videos" not in prompt
