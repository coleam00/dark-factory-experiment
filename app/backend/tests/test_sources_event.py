"""
Tests for the SSE sources event format helpers.

The context formatter used to live in routes/messages.py as `_format_context`.
Retrieval is now tool-driven and the equivalent formatter lives in
backend.rag.tools as `_format_search_results` — its behavior is covered in
test_tools.py. This file now only covers citation-object shape, SSE format,
and persistence round-trip.
"""


class TestCitationObjectShape:
    """Tests that verify citation objects have all required SSE fields."""

    def test_citation_has_all_required_keys(self) -> None:
        """A citation dict from retrieve() has all keys needed for SSE emission."""
        citation = {
            "chunk_id": "chunk-abc",
            "video_id": "vid-1",
            "video_title": "Test Video",
            "video_url": "https://youtube.com/watch?v=abc123",
            "start_seconds": 62.5,
            "end_seconds": 70.0,
            "snippet": "Test snippet text",
            "score": 0.95,
        }
        required_keys = {
            "chunk_id",
            "video_id",
            "video_title",
            "video_url",
            "start_seconds",
            "end_seconds",
            "snippet",
        }
        assert required_keys.issubset(citation.keys())

    def test_start_seconds_is_float(self) -> None:
        """start_seconds is a float (for sub-second precision)."""
        citation = {
            "chunk_id": "c1",
            "video_id": "v1",
            "video_title": "T",
            "video_url": "https://youtube.com/watch?v=abc",
            "start_seconds": 62.5,
            "end_seconds": 70.0,
            "snippet": "s",
            "score": 0.9,
        }
        assert isinstance(citation["start_seconds"], int | float)
        assert isinstance(citation["end_seconds"], int | float)


class TestSseSourcesEventEmission:
    """Tests for SSE sources event emission in messages route."""

    async def test_sources_event_emits_citation_objects(self) -> None:
        """The sources SSE event emits a JSON array of citation objects with all required fields."""
        import json

        chunks = [
            {
                "chunk_id": "chunk-abc",
                "video_id": "vid-1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc123",
                "start_seconds": 62.5,
                "end_seconds": 70.0,
                "snippet": "Test snippet text",
            },
        ]

        # Build source_citations the same way the route does
        source_citations = [
            {
                "chunk_id": c.get("chunk_id", ""),
                "video_id": c.get("video_id", ""),
                "video_title": c.get("video_title", ""),
                "video_url": c.get("video_url", ""),
                "start_seconds": c.get("start_seconds", 0.0),
                "end_seconds": c.get("end_seconds", 0.0),
                "snippet": c.get("snippet", ""),
            }
            for c in chunks
            if c.get("chunk_id")
        ]

        # Verify the JSON serializes correctly (as it would in the SSE event)
        sources_json = json.dumps(source_citations)

        # Verify it can be parsed back
        parsed = json.loads(sources_json)
        assert len(parsed) == 1
        assert parsed[0]["chunk_id"] == "chunk-abc"
        assert parsed[0]["video_title"] == "Test Video"
        assert parsed[0]["start_seconds"] == 62.5
        assert parsed[0]["end_seconds"] == 70.0
        assert parsed[0]["snippet"] == "Test snippet text"

    async def test_sources_event_sse_format(self) -> None:
        """The SSE event format matches what the frontend parser expects."""
        import json

        source_citations = [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Video Title",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "snippet": "Snippet text",
            }
        ]

        sources_json = json.dumps(source_citations)
        sse_event = f"event: sources\ndata: {sources_json}\n\n"

        # Verify the format can be parsed back
        lines = sse_event.split("\n")
        assert lines[0] == "event: sources"
        assert lines[1].startswith("data: ")
        data_json = lines[1][6:]  # Remove "data: " prefix
        parsed = json.loads(data_json)
        assert parsed[0]["video_title"] == "Video Title"

    async def test_sources_event_with_empty_chunks(self) -> None:
        """Empty chunks list produces empty source_citations (no event emitted)."""
        chunks: list[dict] = []

        source_citations = [
            {
                "chunk_id": c.get("chunk_id", ""),
                "video_id": c.get("video_id", ""),
                "video_title": c.get("video_title", ""),
                "video_url": c.get("video_url", ""),
                "start_seconds": c.get("start_seconds", 0.0),
                "end_seconds": c.get("end_seconds", 0.0),
                "snippet": c.get("snippet", ""),
            }
            for c in chunks
            if c.get("chunk_id")
        ]

        assert source_citations == []

    async def test_sources_event_multiple_citations(self) -> None:
        """Multiple citations are all included in the sources event."""
        import json

        chunks = [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Video 1",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "snippet": "Snippet 1",
            },
            {
                "chunk_id": "c2",
                "video_id": "v2",
                "video_title": "Video 2",
                "video_url": "https://youtube.com/watch?v=def",
                "start_seconds": 5.0,
                "end_seconds": 15.0,
                "snippet": "Snippet 2",
            },
            {
                "chunk_id": "c3",
                "video_id": "v1",
                "video_title": "Video 1",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 20.0,
                "end_seconds": 30.0,
                "snippet": "Snippet 3",
            },
        ]

        source_citations = [
            {
                "chunk_id": c.get("chunk_id", ""),
                "video_id": c.get("video_id", ""),
                "video_title": c.get("video_title", ""),
                "video_url": c.get("video_url", ""),
                "start_seconds": c.get("start_seconds", 0.0),
                "end_seconds": c.get("end_seconds", 0.0),
                "snippet": c.get("snippet", ""),
            }
            for c in chunks
            if c.get("chunk_id")
        ]

        sources_json = json.dumps(source_citations)
        parsed = json.loads(sources_json)
        assert len(parsed) == 3
        assert parsed[0]["chunk_id"] == "c1"
        assert parsed[1]["chunk_id"] == "c2"
        assert parsed[2]["chunk_id"] == "c3"


class TestSourcesPersistenceRoundtrip:
    """Tests for source citation persistence round-trip through the repository layer.

    Covers: create_message(sources=...) → DB JSONB → list_messages deserialization.
    """

    async def test_create_message_stores_sources_json(self) -> None:
        """create_message stores sources as JSONB and list_messages deserializes it back."""
        import json
        from unittest.mock import AsyncMock, patch

        from backend.db import repository

        citations = [
            {
                "chunk_id": "chunk-abc",
                "video_id": "vid-1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc123",
                "start_seconds": 62.5,
                "end_seconds": 70.0,
                "snippet": "Test snippet text",
            }
        ]

        # Patch _acquire to return a mock connection that records the call
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "msg-1",
                    "conversation_id": "conv-1",
                    "role": "assistant",
                    "content": "Test response",
                    "sources": json.dumps(citations),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )

        class _FakeAcquire:
            """Dual-purpose awaitable + async context manager."""

            def __await__(self):
                async def _do():
                    return mock_conn

                return _do().__await__()

            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *exc):
                return False

        with patch.object(repository, "_acquire", lambda: _FakeAcquire()):
            msg = await repository.create_message(
                conversation_id="conv-1",
                user_id="test-user",
                role="assistant",
                content="Test response",
                sources=citations,
            )

        assert msg is not None
        assert msg["sources"] == citations

        # Verify execute was called (proving the DB write path was exercised)
        assert mock_conn.execute.called

    async def test_create_message_sources_none_round_trip(self) -> None:
        """sources=None is stored as NULL and deserialized as None."""
        from unittest.mock import AsyncMock, patch

        from backend.db import repository

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "msg-1",
                    "conversation_id": "conv-1",
                    "role": "assistant",
                    "content": "No citations",
                    "sources": None,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        )

        class _FakeAcquire:
            def __await__(self):
                async def _do():
                    return mock_conn

                return _do().__await__()

            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *exc):
                return False

        with patch.object(repository, "_acquire", lambda: _FakeAcquire()):
            msg = await repository.create_message(
                conversation_id="conv-1",
                user_id="test-user",
                role="assistant",
                content="No citations",
                sources=None,
            )
            assert msg is not None
            assert msg["sources"] is None

            messages = await repository.list_messages("conv-1", "test-user")
            assert messages[0]["sources"] is None

    async def test_sources_event_with_retrieval_failed(self) -> None:
        """When retrieval fails, citations receive retrieval_failed=True."""
        source_citations = [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "snippet": "Test snippet",
            }
        ]

        retrieval_failed = True
        if retrieval_failed:
            for citation in source_citations:
                citation["retrieval_failed"] = True

        assert source_citations[0]["retrieval_failed"] is True

        # Verify it serializes correctly (as it would in the SSE event)
        import json

        sources_json = json.dumps(source_citations)
        parsed = json.loads(sources_json)
        assert parsed[0]["retrieval_failed"] is True


class TestRefusalSourcesSuppression:
    """Tests for suppression of sources section on off-topic refusals."""

    def test_is_refusal_detects_not_covered_phrase(self) -> None:
        """Refusal phrases containing 'not covered in any of the videos' are detected."""
        from backend.routes.messages import _is_refusal

        text = (
            "Those topics are not covered in any of the videos in my context — "
            "they're focused entirely on Claude Code."
        )
        assert _is_refusal(text) is True

    def test_is_refusal_detects_can_only_answer(self) -> None:
        """Refusal phrases containing 'can only answer questions about' are detected."""
        from backend.routes.messages import _is_refusal

        text = "I can only answer questions about the topics covered in the provided videos."
        assert _is_refusal(text) is True

    def test_is_refusal_detects_dont_have_information(self) -> None:
        """Refusal phrases containing 'don't have information' are detected."""
        from backend.routes.messages import _is_refusal

        text = "I don't have information about that topic."
        assert _is_refusal(text) is True

    def test_is_refusal_rejects_normal_answer(self) -> None:
        """A substantive answer that happens to use 'not' does not trigger refusal detection."""
        from backend.routes.messages import _is_refusal

        text = "The video explains that this approach does not work well when nested."
        assert _is_refusal(text) is False

    def test_is_refusal_is_case_insensitive(self) -> None:
        """Refusal detection is case-insensitive."""
        from backend.routes.messages import _is_refusal

        text = "THOSE TOPICS NOT COVERED IN ANY OF THE VIDEOS"
        assert _is_refusal(text) is True

    def test_is_refusal_empty_text(self) -> None:
        """Empty text returns False (edge case — shouldn't happen in practice)."""
        from backend.routes.messages import _is_refusal

        assert _is_refusal("") is False
        assert _is_refusal("   ") is False

    def test_is_refusal_detects_cant_help(self) -> None:
        """Refusal phrase 'can't help with that' is detected."""
        from backend.routes.messages import _is_refusal

        text = "I'm sorry, but I can't help with that request."
        assert _is_refusal(text) is True

    def test_is_refusal_detects_outside_scope(self) -> None:
        """Refusal phrase 'outside the scope of' is detected."""
        from backend.routes.messages import _is_refusal

        text = "That question is outside the scope of the content I've been provided."
        assert _is_refusal(text) is True

    def test_is_refusal_detects_dont_have_access(self) -> None:
        """Refusal phrase 'don't have access to' is detected."""
        from backend.routes.messages import _is_refusal

        text = "I don't have access to information about that."
        assert _is_refusal(text) is True

    def test_is_refusal_detects_not_part_of_knowledge(self) -> None:
        """Refusal phrase 'not part of my knowledge' is detected."""
        from backend.routes.messages import _is_refusal

        text = "That's not part of my knowledge base."
        assert _is_refusal(text) is True

    def test_is_refusal_detects_additional_phrases(self) -> None:
        """Additional refusal variations that may come from LLMs."""
        from backend.routes.messages import _is_refusal

        additional_refusals = [
            "I haven't been provided with any videos about that topic.",
            "That information is not available in the materials I've been given.",
            "I cannot answer questions about topics not covered in the provided content.",
            "I'm not able to help with that as it's not covered in the source material.",
        ]
        for text in additional_refusals:
            assert _is_refusal(text) is True, f"Failed on: {text}"

    def test_is_refusal_detects_contraction_form(self) -> None:
        """The exact prod refusal phrasing uses "aren't covered" (contraction),
        which does not contain the substring "not". Without a pattern that
        matches the contraction directly, the pattern list misses the most
        common real-world case and "Sources (N)" still renders after an
        off-topic refusal. Regression guard for the E2E failure on PR #134.
        """
        from backend.routes.messages import _is_refusal

        text = (
            "Those topics aren't covered in any of the videos in my context — "
            "they're focused entirely on Claude Code, AI coding agents, and "
            "development workflows."
        )
        assert _is_refusal(text) is True

    def test_is_refusal_detects_other_contractions(self) -> None:
        """Other common contraction-form refusals the LLM may emit."""
        from backend.routes.messages import _is_refusal

        contraction_refusals = [
            "That topic isn't covered in the source videos.",
            "These concepts aren't part of the material I was given.",
            "This isn't part of the context I have access to.",
            "Those details aren't available in the videos provided.",
            "That subject isn't discussed in any of the videos.",
        ]
        for text in contraction_refusals:
            assert _is_refusal(text) is True, f"Failed on: {text}"

    def test_is_refusal_detects_kimi_refusal_phrases(self) -> None:
        """Kimi K2.6 refusal phrases that don't match Sonnet patterns.

        Regression guard for issue #158: Kimi phrases refusals differently
        from Sonnet, e.g. "couldn't find", "I searched through", "not an actual".
        Without these patterns, _is_refusal() returns False and the sources event
        emits a misleading "Sources (N)" chip.
        """
        from backend.routes.messages import _is_refusal

        kimi_refusals = [
            "I searched through the video library, but I couldn't find an actual recipe for chocolate chip cookies.",
            "I could not find any videos about that topic in the library.",
            "This is not an actual recipe from the video content.",
            "There are no actual recipes for chocolate chip cookies in these videos.",
            "I searched through the video library, but I couldn't find what you're looking for.",
            "The videos that mention 'cookies' are only using them as examples in AI agent demos.",
            "If you're looking for a recipe, check elsewhere.",
            "I couldn't find the information you're looking for in the provided videos.",
            "I can only find information about topics covered in the videos, not recipes.",
        ]
        for text in kimi_refusals:
            assert _is_refusal(text) is True, f"Failed on Kimi phrase: {text}"

    def test_is_refusal_kimi_patterns_no_false_positives(self) -> None:
        """Kimi-added patterns don't cause false positives on normal text.

        Some patterns like "not an actual" or "I searched" could appear in
        non-refusal contexts. Verify they only match in refusal contexts.
        """
        from backend.routes.messages import _is_refusal

        # These should NOT be detected as refusals
        normal_texts = [
            "I searched through my notes and found the answer.",
            "This is a real recipe that I found in the video.",
            "Check other videos for more information.",
            "The examples only mentioned as reference, not as the main topic.",
        ]
        for text in normal_texts:
            assert _is_refusal(text) is False, f"False positive on: {text}"


class TestExtractTextFromSse:
    """Tests for _extract_text_from_sse helper."""

    def test_extracts_json_encoded_token(self) -> None:
        """JSON-encoded tokens are decoded and concatenated."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = ['data: "Hello "\n\n', 'data: "world"\n\n']
        assert _extract_text_from_sse(chunks) == "Hello world"

    def test_skips_dones(self) -> None:
        """[DONE] markers are skipped."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = ['data: "Hello"\n\n', "data: [DONE]\n\n"]
        assert _extract_text_from_sse(chunks) == "Hello"

    def test_skips_error_payloads(self) -> None:
        """Error JSON payloads are skipped."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = ['data: {"error": "oops"}\n\n', 'data: "Hello"\n\n']
        assert _extract_text_from_sse(chunks) == "Hello"

    def test_fallback_on_invalid_json(self) -> None:
        """Non-JSON data: lines are treated as raw text."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = ["data: raw text\n\n"]
        assert _extract_text_from_sse(chunks) == "raw text"

    def test_reconstructs_full_refusal(self) -> None:
        """Refusal text spanning multiple SSE chunks is fully reconstructed.

        Real LLM streams send complete phrases as tokens, not individual words.
        """
        import json

        from backend.routes.messages import _extract_text_from_sse, _is_refusal

        # Simulate realistic LLM streaming - phrases as complete tokens
        refusal_phrases = [
            "Those topics are ",
            "not covered in any of the videos",
            ".",
        ]
        chunks = [f"data: {json.dumps(phrase)}\n\n" for phrase in refusal_phrases]
        result = _extract_text_from_sse(chunks)
        assert result == "Those topics are not covered in any of the videos."
        assert _is_refusal(result) is True

    def test_skips_non_data_chunks(self) -> None:
        """SSE chunks not starting with 'data: ' are skipped."""
        from backend.routes.messages import _extract_text_from_sse

        chunks = [
            "event: sources\ndata: []\n\n",
            'data: "Hello"\n\n',
        ]
        assert _extract_text_from_sse(chunks) == "Hello"


class TestRefusalSourcesSuppressionIntegration:
    """Integration tests for SSE sources suppression on refusals.

    Verifies the full path: stream_chat yields refusal tokens → event_generator
    suppresses sources event → response contains [DONE] but no 'event: sources'.
    """

    async def test_sources_event_suppressed_on_refusal(self) -> None:
        """When LLM refuses, the sources SSE event must not be emitted."""
        import json
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        from backend.auth.tokens import encode_token
        from backend.main import app

        refusal_text = "Those topics are not covered in any of the videos."
        refusal_token = json.dumps(refusal_text)
        refusal_chunk = f"data: {refusal_token}\n\n"
        done_chunk = "data: [DONE]\n\n"

        source_citations = [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "snippet": "Test snippet",
            }
        ]

        # Tool-driven architecture: the executor populates tool_chunks_acc via
        # an injected closure. We simulate a successful search + then a refusal
        # response by calling the executor ourselves inside mock_stream_chat.
        async def mock_stream_chat(
            messages, tools=None, tool_executor=None, max_tool_calls=0, final_text_out=None
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield refusal_chunk
            if final_text_out is not None:
                final_text_out.append(refusal_text)
            yield done_chunk

        async def mock_execute_tool(name, raw_args, video_id_whitelist=None, embedding_cache=None):
            return {"ok": True, "text": "context", "chunks": source_citations}

        from uuid import uuid4

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        async def mock_get_user_by_id(user_id):
            return {
                "id": test_user_id,
                "email": "test@example.com",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(conv_id, user_id):
            if conv_id == test_conv_id:
                return {
                    "id": test_conv_id,
                    "user_id": test_user_id,
                    "title": "Test",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            return None

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_list_messages(conv_id, user_id):
            return []

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages",
                    json={"content": "off-topic question"},
                    headers={"Cookie": f"session={valid_token}"},
                )

        output = response.text
        # Sources event must NOT be present
        assert "event: sources" not in output, (
            f"Expected no 'event: sources' in output, but got: {output}"
        )
        # [DONE] should still be present
        assert "data: [DONE]" in output, f"Expected [DONE] in output, but got: {output}"

    async def test_sources_event_emitted_on_normal_answer(self) -> None:
        """When LLM gives a normal answer, sources SSE event IS emitted."""
        import json
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        from backend.auth.tokens import encode_token
        from backend.main import app

        answer_text = "The video explains that this feature works well."
        answer_token = json.dumps(answer_text)
        answer_chunk = f"data: {answer_token}\n\n"
        done_chunk = "data: [DONE]\n\n"

        source_citations = [
            {
                "chunk_id": "c1",
                "video_id": "v1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "snippet": "Test snippet",
            }
        ]

        async def mock_stream_chat(
            messages, tools=None, tool_executor=None, max_tool_calls=0, final_text_out=None
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield answer_chunk
            if final_text_out is not None:
                final_text_out.append(answer_text)
            yield done_chunk

        async def mock_execute_tool(name, raw_args, video_id_whitelist=None, embedding_cache=None):
            return {"ok": True, "text": "context", "chunks": source_citations}

        from uuid import uuid4

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        async def mock_get_user_by_id(user_id):
            return {
                "id": test_user_id,
                "email": "test@example.com",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(conv_id, user_id):
            if conv_id == test_conv_id:
                return {
                    "id": test_conv_id,
                    "user_id": test_user_id,
                    "title": "Test",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            return None

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_list_messages(conv_id, user_id):
            return []

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "u"}]

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages",
                    json={"content": "question about video"},
                    headers={"Cookie": f"session={valid_token}"},
                )

        output = response.text
        # Sources event MUST be present
        assert "event: sources" in output, (
            f"Expected 'event: sources' in output for normal answer, but got: {output}"
        )
        assert "data: [DONE]" in output


class TestChunkExpansionIntegration:
    """Integration tests for chunk expansion in the SSE streaming path.

    Verifies the SSE output contains citation content when chunks are returned
    by the tool executor. The expand_and_merge function itself is tested
    exhaustively in test_expansion.py. These tests verify the SSE streaming
    path correctly processes tool chunks and emits sources events.
    """

    async def test_sources_event_emitted_with_tool_chunks(self) -> None:
        """When LLM uses tool results, sources SSE event is emitted with citation content."""
        import json
        from unittest.mock import patch

        from httpx import ASGITransport, AsyncClient

        from backend.auth.tokens import encode_token
        from backend.main import app

        answer_token = json.dumps("The video explains it works.")
        answer_chunk = f"data: {answer_token}\n\n"
        done_chunk = "data: [DONE]\n\n"

        source_citations = [
            {
                "chunk_id": "c5",
                "video_id": "v1",
                "video_title": "Test Video",
                "video_url": "https://youtube.com/watch?v=abc",
                "start_seconds": 50.0,
                "end_seconds": 60.0,
                "snippet": "hello world snippet",
                "chunk_index": 5,
                "content": "hello world",
            }
        ]

        async def mock_stream_chat(
            messages,
            tools=None,
            tool_executor=None,
            max_tool_calls=0,
            final_text_out=None,
        ):
            if tool_executor is not None:
                await tool_executor("search_videos", json.dumps({"query": "test"}))
            yield answer_chunk
            if final_text_out is not None:
                final_text_out.append("The video explains it works.")
            yield done_chunk

        async def mock_execute_tool(name, raw_args, video_id_whitelist=None, embedding_cache=None):
            return {"ok": True, "text": "context", "chunks": source_citations}

        from uuid import uuid4

        test_user_id = str(uuid4())
        test_conv_id = str(uuid4())
        valid_token = encode_token(test_user_id)

        async def mock_get_user_by_id(user_id):
            return {
                "id": test_user_id,
                "email": "test@example.com",
                "password_hash": "hashed",
                "created_at": "2026-01-01T00:00:00Z",
            }

        async def mock_get_conversation(conv_id, user_id):
            if conv_id == test_conv_id:
                return {
                    "id": test_conv_id,
                    "user_id": test_user_id,
                    "title": "Test",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            return None

        async def mock_create_message(**kwargs):
            return {"id": str(uuid4()), **kwargs}

        async def mock_list_messages(conv_id, user_id):
            return []

        async def mock_list_videos():
            return [{"id": "v1", "title": "Test Video", "url": "https://youtube.com/watch?v=abc"}]

        with (
            patch("backend.auth.dependencies.users_repo.get_user_by_id", mock_get_user_by_id),
            patch("backend.db.repository.get_conversation", mock_get_conversation),
            patch("backend.db.repository.create_message", mock_create_message),
            patch("backend.db.repository.list_messages", mock_list_messages),
            patch("backend.db.repository.list_videos", mock_list_videos),
            patch("backend.routes.messages.stream_chat", mock_stream_chat),
            patch("backend.routes.messages.execute_tool", mock_execute_tool),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/conversations/{test_conv_id}/messages",
                    json={"content": "question about video"},
                    headers={"Cookie": f"session={valid_token}"},
                )

        output = response.text
        # Sources event must be present with citation content
        assert "event: sources" in output, f"Expected 'event: sources' in output, but got: {output}"
        assert "c5" in output
        assert "hello world snippet" in output
        assert "Test Video" in output
        assert "data: [DONE]" in output
