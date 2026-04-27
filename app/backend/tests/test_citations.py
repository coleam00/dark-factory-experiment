"""Tests for the two-tier citation marker module (issue #176)."""

from __future__ import annotations

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.auth.tokens import encode_token
from backend.main import app
from backend.rag.citations import (
    CitationMarkerStripper,
    extract_cited_chunk_ids,
    strip_citation_markers,
)

# Pure parsing -----------------------------------------------------------


class TestParsing:
    def test_extracts_dedupes_dashes_and_ignores_malformed(self) -> None:
        text = "[c:abc] [c:550e8400-e29b-41d4] [c:abc] [c:] [c:open  see [docs](url)"
        assert extract_cited_chunk_ids(text) == {"abc", "550e8400-e29b-41d4"}

    def test_strip_removes_markers_preserves_other_brackets(self) -> None:
        assert strip_citation_markers("see [docs](url) [c:a][c:b] end") == ("see [docs](url)  end")


# Stream-safe stripping --------------------------------------------------


class TestFormatterMarkerRoundTrip:
    """The LLM-facing tool result must contain `[c:<chunk_id>]` markers in the
    same form the citation parser extracts — that contract is what makes
    marker emission possible at all (issue #176 follow-up).
    """

    def test_search_format_emits_marker_extractable_by_parser(self) -> None:
        from backend.rag.tools import _format_search_results

        chunks = [
            {
                "chunk_id": "abc-123",
                "video_title": "T",
                "start_seconds": 0.0,
                "content": "x",
            },
            {
                "chunk_id": "def_456",
                "video_title": "T",
                "start_seconds": 60.0,
                "content": "y",
            },
        ]
        text = _format_search_results(chunks)
        assert extract_cited_chunk_ids(text) == {"abc-123", "def_456"}

    def test_transcript_format_emits_marker_extractable_by_parser(self) -> None:
        from backend.rag.tools import _format_transcript

        # Transcript path uses raw chunks where the id field is "id" not "chunk_id".
        chunks = [{"id": "raw-id-1", "start_seconds": 0.0, "content": "hello"}]
        text = _format_transcript({"title": "Demo"}, chunks, max_chars=None)
        assert extract_cited_chunk_ids(text) == {"raw-id-1"}


class TestStreamStripper:
    def _run(self, tokens: list[str]) -> str:
        s = CitationMarkerStripper()
        return "".join(s.feed(t) for t in tokens) + s.flush()

    @pytest.mark.parametrize(
        ("tokens", "expected"),
        [
            (["A ", "B ", "C"], "A B C"),
            (["Hi [c:abc] there."], "Hi  there."),
            (["start [c:a", "bc] tail"], "start  tail"),
            (["end [", "c:", "xyz", "]", " tail"], "end  tail"),
            (["x [c:a][c:b] y"], "x  y"),
        ],
    )
    def test_round_trip(self, tokens: list[str], expected: str) -> None:
        assert self._run(tokens) == expected

    def test_partial_at_eof_emits_as_plain(self) -> None:
        # Stream ended mid-marker → user sees the literal text;
        # extract_cited_chunk_ids() won't match it, so is_cited stays False.
        assert self._run(["end [c:abc"]) == "end [c:abc"


# Full SSE integration through the messages route -----------------------


async def _post_message(*, answer_tokens: list[str], retrieved_chunks: list[dict]) -> str:
    """Post one chat message and return the SSE body. Mocks LLM + DB."""
    test_user_id = str(uuid4())
    test_conv_id = str(uuid4())
    valid_token = encode_token(test_user_id)

    async def mock_stream_chat(
        messages,
        tools=None,
        tool_executor=None,
        max_tool_calls=0,
        final_text_out=None,
        **_kwargs,
    ):
        if tool_executor is not None:
            await tool_executor("search_videos", json.dumps({"query": "t"}))
        for tok in answer_tokens:
            yield f"data: {json.dumps(tok)}\n\n"
        if final_text_out is not None:
            final_text_out.append("".join(answer_tokens))
        yield "data: [DONE]\n\n"

    async def mock_execute_tool(
        name, raw_args, video_id_whitelist=None, embedding_cache=None, is_member=False
    ):
        return {"ok": True, "text": "ctx", "chunks": retrieved_chunks}

    async def get_user(*a, **kw):
        return {"id": test_user_id, "email": "t@e.com", "password_hash": "h", "created_at": "x"}

    async def get_conv(*a, **kw):
        return {"id": test_conv_id, "user_id": test_user_id, "title": "T", "created_at": "x"}

    async def create_msg(**kw):
        return {"id": str(uuid4()), **kw}

    async def list_msgs(*a, **kw):
        return []

    async def list_vids(*a, **kw):
        return [{"id": "v1", "title": "T", "url": "u"}]

    with (
        patch("backend.auth.dependencies.users_repo.get_user_by_id", get_user),
        patch("backend.db.repository.get_conversation", get_conv),
        patch("backend.db.repository.create_message", create_msg),
        patch("backend.db.repository.list_messages", list_msgs),
        patch("backend.db.repository.list_videos", list_vids),
        patch("backend.routes.messages.stream_chat", mock_stream_chat),
        patch("backend.routes.messages.execute_tool", mock_execute_tool),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/conversations/{test_conv_id}/messages",
                json={"content": "test"},
                headers={"Cookie": f"session={valid_token}"},
            )
    return resp.text


def _parse_sources(body: str) -> list[dict[str, object]]:
    idx = body.index("event: sources")
    result: list[dict[str, object]] = json.loads(body[idx:].split("\n", 2)[1][len("data: ") :])
    return result


def _chunk(cid: str, vid: str = "v1") -> dict:
    return {
        "chunk_id": cid,
        "video_id": vid,
        "video_title": "T",
        "video_url": "u",
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "snippet": "s",
    }


class TestSseIntegration:
    async def test_markers_stripped_and_is_cited_set(self) -> None:
        """Markers never reach the wire; is_cited reflects the marker set."""
        body = await _post_message(
            answer_tokens=["The framework is X [c:cit", "ed1]."],
            retrieved_chunks=[_chunk("cited1"), _chunk("consulted1", "v2")],
        )
        assert "[c:" not in body
        flags = {c["chunk_id"]: c["is_cited"] for c in _parse_sources(body)}
        assert flags == {"cited1": True, "consulted1": False}

    async def test_hallucinated_marker_id_silently_dropped(self) -> None:
        body = await _post_message(
            answer_tokens=["Cited [c:real1] hallucinated [c:fake]."],
            retrieved_chunks=[_chunk("real1")],
        )
        payload = _parse_sources(body)
        assert len(payload) == 1
        assert payload[0]["chunk_id"] == "real1"
        assert payload[0]["is_cited"] is True

    async def test_uncited_capped_at_citations_max_count(self) -> None:
        """No markers + retrieval > cap → SSE payload sliced to CITATIONS_MAX_COUNT."""
        from backend.config import CITATIONS_MAX_COUNT

        chunks = [_chunk(f"c{i}") for i in range(CITATIONS_MAX_COUNT + 5)]
        body = await _post_message(
            answer_tokens=["Plain answer with no markers."],
            retrieved_chunks=chunks,
        )
        assert len(_parse_sources(body)) == CITATIONS_MAX_COUNT

    async def test_cited_pass_through_uncited_capped(self) -> None:
        """Cited chunks always render (model's choice); only the consulted
        tier is capped. Verifies the cap targets the right list."""
        from backend.config import CITATIONS_MAX_COUNT

        chunks = [_chunk(f"c{i}") for i in range(CITATIONS_MAX_COUNT + 3)]
        body = await _post_message(
            answer_tokens=[f"Cited [c:c0][c:c{CITATIONS_MAX_COUNT + 1}]."],
            retrieved_chunks=chunks,
        )
        payload = _parse_sources(body)
        cited_ids = {c["chunk_id"] for c in payload if c["is_cited"]}
        assert cited_ids == {"c0", f"c{CITATIONS_MAX_COUNT + 1}"}
        # 2 cited + CITATIONS_MAX_COUNT non-cited = 12 total.
        assert len(payload) == 2 + CITATIONS_MAX_COUNT
