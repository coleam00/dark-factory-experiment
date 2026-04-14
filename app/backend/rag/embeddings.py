"""
Embeddings service — wraps the OpenRouter embeddings API via the openai SDK.

Exposes:
  embed_text(text: str) -> list[float]
  embed_batch(texts: list[str]) -> list[list[float]]

Uses model: openai/text-embedding-3-small (dimensionality: 1536)
"""

from __future__ import annotations

import logging

from openai import OpenAI

from backend.config import EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client (module-level singleton — re-used across calls)
# ---------------------------------------------------------------------------

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
        )
    return _client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed_text(text: str) -> list[float]:
    """
    Embed a single text string via OpenRouter.

    Args:
        text: A non-empty string to embed.

    Returns:
        A list of 1536 floats representing the embedding vector.

    Raises:
        ValueError: If *text* is empty or whitespace-only.
        Exception: If the OpenRouter API call fails.
    """
    if not text or not text.strip():
        raise ValueError(
            "embed_text() requires a non-empty string; got an empty or whitespace-only value."
        )

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
        )
    except Exception as exc:
        logger.error("OpenRouter embeddings API call failed: %s", exc)
        raise RuntimeError(f"Embeddings API request failed: {exc}") from exc

    embedding = response.data[0].embedding
    return list(embedding)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of text strings via OpenRouter in a single batched API call.

    Args:
        texts: A list of strings. May be empty (returns [] immediately).

    Returns:
        A list of embedding vectors, one per input text, in the same order.
        Returns [] if *texts* is empty without making any API call.

    Raises:
        ValueError: If any text in the list is empty or whitespace-only.
        Exception: If the OpenRouter API call fails.
    """
    if not texts:
        return []

    # Validate all texts before making the API call
    for i, text in enumerate(texts):
        if not text or not text.strip():
            raise ValueError(
                f"embed_batch() requires all texts to be non-empty; got empty string at index {i}."
            )

    client = _get_client()
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
    except Exception as exc:
        logger.error("OpenRouter embeddings batch API call failed: %s", exc)
        raise RuntimeError(f"Embeddings batch API request failed: {exc}") from exc

    # The API guarantees results in the same order as inputs
    # but we sort by index just to be safe
    sorted_data = sorted(response.data, key=lambda d: d.index)
    return [list(d.embedding) for d in sorted_data]
