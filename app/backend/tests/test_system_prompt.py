"""
Pin the RAG system prompt's "no raw IDs in prose" rule (issue #93).

The LLM was emitting `(Source: Video <11-char-id>)` inline in responses;
source chips already render that info below the message. We added an
explicit instruction to suppress it — if someone rewrites the prompt and
drops the rule, these tests fail so the regression is caught in CI.
"""

from __future__ import annotations

from backend.llm.openrouter import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


class TestSystemPromptForbidsRawIds:
    def test_template_forbids_raw_ids(self) -> None:
        assert "video IDs" in SYSTEM_PROMPT_TEMPLATE or "video id" in SYSTEM_PROMPT_TEMPLATE.lower()
        assert "title only" in SYSTEM_PROMPT_TEMPLATE.lower()

    def test_built_prompt_contains_rule(self) -> None:
        prompt = build_system_prompt(context="[Source: Some Video at 00:10]\nSome transcript.")
        lowered = prompt.lower()
        assert "never write youtube video ids" in lowered
        assert "title only" in lowered

    def test_built_prompt_still_injects_context(self) -> None:
        ctx = "[Source: Claude Code Walkthrough at 01:23]\nHello world."
        prompt = build_system_prompt(context=ctx)
        assert ctx in prompt
