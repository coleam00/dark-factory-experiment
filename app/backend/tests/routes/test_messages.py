"""
Tests for POST /api/conversations/{conv_id}/messages.

Covers:
- retrieve() is called without explicit k (uses config.RETRIEVAL_TOP_K default)
- 404 when conversation not found
- 422 when message content is empty/whitespace
"""
from __future__ import annotations

# NOTE: conftest.py stubs docling_core before this file is imported, which
# allows backend.main (and the full route tree) to be imported cleanly in
# environments where the native docling_core/NumPy combination is broken.

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app():
    from backend.main import app as _app  # noqa: PLC0415
    return _app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


def test_create_message_uses_config_default_k(client):
    """retrieve() should be called without explicit k (uses config.RETRIEVAL_TOP_K default)."""
    conv_id = "test-conv-1"

    with (
        patch("backend.routes.messages.repository") as mock_repo,
        patch("backend.routes.messages.embed_text", return_value=[0.1] * 768),
        patch("backend.routes.messages.retrieve", new_callable=AsyncMock) as mock_retrieve,
        patch("backend.routes.messages.stream_chat") as mock_stream,
    ):
        mock_repo.get_conversation = AsyncMock(
            return_value={"id": conv_id, "title": "New Conversation"}
        )
        mock_repo.create_message = AsyncMock()
        mock_repo.list_messages = AsyncMock(return_value=[])
        mock_repo.update_conversation_title = AsyncMock()
        mock_retrieve.return_value = []

        async def fake_stream(*args, **kwargs):
            yield 'data: "Hello"\n\n'
            yield "data: [DONE]\n\n"

        mock_stream.return_value = fake_stream()

        response = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "What is RAG?"},
        )

    assert response.status_code == 200
    # Verify retrieve was called without explicit k keyword arg
    mock_retrieve.assert_called_once()
    _, kwargs = mock_retrieve.call_args
    assert "k" not in kwargs, (
        "k should not be passed explicitly; default from config should be used"
    )


def test_create_message_returns_404_when_conversation_missing(client):
    """Should return 404 when the conversation does not exist."""
    conv_id = "nonexistent-conv"

    with patch("backend.routes.messages.repository") as mock_repo:
        mock_repo.get_conversation = AsyncMock(return_value=None)

        response = client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "Hello"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


def test_create_message_rejects_empty_content(client):
    """Whitespace-only content should be rejected with 422."""
    conv_id = "any-conv"

    response = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"content": "   "},
    )

    assert response.status_code == 422
