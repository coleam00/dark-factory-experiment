"""Tests for backend.rag.tools — the full-transcript tool and its executor."""

from __future__ import annotations

import json

import pytest

from backend.llm.openrouter import build_system_prompt
from backend.rag import tools as tools_module
from backend.rag.tools import (
    GET_VIDEO_TRANSCRIPT_TOOL,
    TOOL_SCHEMAS,
    _format_timestamped_transcript,
    execute_get_video_transcript,
    execute_tool,
    serialize_tool_result,
)

# --- Tool schema -----------------------------------------------------------


def test_schema_is_openai_function_format() -> None:
    schema = GET_VIDEO_TRANSCRIPT_TOOL
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "get_video_transcript"
    params = fn["parameters"]
    assert params["properties"]["video_id"]["type"] == "string"
    assert params["required"] == ["video_id"]
    assert GET_VIDEO_TRANSCRIPT_TOOL in TOOL_SCHEMAS


# --- Argument validation ---------------------------------------------------


@pytest.mark.parametrize(
    "bad_args,reason",
    [
        ({}, "video_id"),
        ({"video_id": "   "}, "video_id"),
        ({"video_id": 123}, "video_id"),
        ("{not valid json", "invalid"),
    ],
)
@pytest.mark.asyncio
async def test_bad_arguments_return_error(bad_args, reason) -> None:
    result = await execute_get_video_transcript(bad_args)
    assert result["ok"] is False
    assert reason in result["error"].lower() or "json" in result["error"].lower()


@pytest.mark.asyncio
async def test_unknown_tool_name_returns_error() -> None:
    result = await execute_tool("not_a_real_tool", {})
    assert result["ok"] is False
    assert "unknown tool" in result["error"].lower()


@pytest.mark.asyncio
async def test_id_outside_whitelist_rejected() -> None:
    result = await execute_get_video_transcript(
        {"video_id": "hallucinated"}, video_id_whitelist={"real-1"}
    )
    assert result["ok"] is False
    assert "library" in result["error"].lower() or "catalog" in result["error"].lower()


# --- Execution paths -------------------------------------------------------


def _fake_repo(monkeypatch, video, chunks):
    async def _get(_vid):
        return video

    async def _list(_vid):
        return chunks

    monkeypatch.setattr(tools_module.repository, "get_video", _get)
    monkeypatch.setattr(tools_module.repository, "list_chunks_for_video", _list)


@pytest.mark.asyncio
async def test_video_not_found(monkeypatch) -> None:
    _fake_repo(monkeypatch, None, [])
    result = await execute_get_video_transcript({"video_id": "v1"}, video_id_whitelist={"v1"})
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_no_chunks(monkeypatch) -> None:
    _fake_repo(monkeypatch, {"id": "v1", "title": "T", "url": "u"}, [])
    result = await execute_get_video_transcript({"video_id": "v1"}, video_id_whitelist={"v1"})
    assert result["ok"] is False
    assert "chunks" in result["error"].lower()


@pytest.mark.asyncio
async def test_happy_path_text_and_chunks(monkeypatch) -> None:
    _fake_repo(
        monkeypatch,
        {"id": "v1", "title": "How RAG Works", "url": "https://youtu.be/abc"},
        [
            {
                "id": "c1",
                "content": "First.",
                "chunk_index": 0,
                "start_seconds": 0.0,
                "end_seconds": 30.0,
                "snippet": "First",
                "embedding": [0.0],
            },
            {
                "id": "c2",
                "content": "Second.",
                "chunk_index": 1,
                "start_seconds": 30.0,
                "end_seconds": 65.0,
                "snippet": "Second",
                "embedding": [0.0],
            },
        ],
    )
    # JSON-encoded string arguments also work (OpenAI streams it this way)
    result = await execute_tool(
        "get_video_transcript",
        json.dumps({"video_id": "v1"}),
        video_id_whitelist={"v1"},
    )
    assert result["ok"] is True
    assert "How RAG Works" in result["text"]
    assert "[00:00]" in result["text"]
    assert "[00:30]" in result["text"]
    assert len(result["chunks"]) == 2
    first = result["chunks"][0]
    assert first["chunk_id"] == "c1"
    assert first["video_id"] == "v1"
    assert first["video_title"] == "How RAG Works"


# --- Formatting + serialization -------------------------------------------


def test_formatter_renders_mmss_timestamps() -> None:
    text = _format_timestamped_transcript(
        {"title": "Demo"},
        [
            {"start_seconds": 0.0, "content": "A"},
            {"start_seconds": 75.5, "content": "B"},
            {"start_seconds": 3600.0, "content": "C"},
        ],
    )
    assert "Demo" in text
    assert "[00:00]" in text
    assert "[01:15]" in text
    assert "[60:00]" in text


def test_serialize_ok_returns_text() -> None:
    assert serialize_tool_result({"ok": True, "text": "hello"}) == "hello"


def test_serialize_error_returns_error_line() -> None:
    payload = serialize_tool_result({"ok": False, "error": "boom"})
    assert payload.startswith("Error:")
    assert "boom" in payload


def test_serialize_malformed_returns_generic_error() -> None:
    assert serialize_tool_result({}).startswith("Error:")


# --- System prompt tool guidance ------------------------------------------


def test_prompt_includes_tool_guidance_when_cap_positive() -> None:
    prompt = build_system_prompt(context="", tool_guidance_max_per_turn=2)
    assert "get_video_transcript" in prompt
    assert "at most 2 times" in prompt


@pytest.mark.parametrize("cap", [None, 0])
def test_prompt_omits_tool_guidance_when_disabled(cap) -> None:
    prompt = build_system_prompt(context="", tool_guidance_max_per_turn=cap)
    assert "get_video_transcript" not in prompt
