"""Post-retrieval LLM reranker.

Listwise reranker: sends all candidate chunks to a cheap LLM in a single
non-streaming call and asks it to return chunk indices ordered by relevance.
Falls back to the original RRF order on any error so retrieval always succeeds.
"""

from __future__ import annotations

import asyncio
import json
import logging

from openai import AsyncOpenAI

from backend.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, RERANKER_MODEL, RERANKER_TOP_N

logger = logging.getLogger(__name__)

_async_client: AsyncOpenAI | None = None


def _get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    return _async_client


def _build_rerank_prompt(query: str, chunks: list[dict], top_n: int) -> str:
    """Build the listwise ranking prompt.  Each chunk must have ``video_title``
    and at least one of ``content`` / ``snippet``; missing keys produce empty lines."""
    lines = [
        "Rank these transcript chunks by relevance to the query.",
        "Pay special attention to chunks where the author expresses a",
        "recommendation, preference, or personal opinion matching the query intent.",
        "",
        f'Query: "{query}"',
        "",
        "Chunks (each shown as [index] title snippet):",
    ]
    for i, c in enumerate(chunks):
        title = (c.get("video_title") or "")[:60]
        snippet = (c.get("content") or c.get("snippet") or "")[:200]
        lines.append(f"[{i}] {title} | {snippet}")
    lines.extend(
        [
            "",
            f"Return ONLY a JSON array of the top {top_n} chunk indices"
            " ordered from most to least relevant. Example: [2, 0, 4]",
            "Do not include any other text.",
        ]
    )
    return "\n".join(lines)


async def rerank_chunks(
    query: str,
    chunks: list[dict],
    top_n: int | None = None,
) -> list[dict]:
    """Re-order *chunks* by relevance to *query* using a single LLM call.

    Returns the top ``top_n`` chunks (default ``RERANKER_TOP_N``) in
    descending relevance order.  Falls back to the original order on any
    error so retrieval always returns something useful.
    """
    if not chunks:
        return chunks
    n = top_n if top_n is not None else RERANKER_TOP_N
    # Skip the LLM call for trivially small inputs — nothing to reorder.
    if len(chunks) <= 1:
        return chunks[:n]

    prompt = _build_rerank_prompt(query, chunks, n)
    try:
        client = _get_async_client()
        response = await client.chat.completions.create(
            model=RERANKER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=128,
            temperature=0,
        )
        raw = (response.choices[0].message.content or "").strip()
        indices: list[int] = json.loads(raw)
        # Validate: must be a list of ints within range.
        if not isinstance(indices, list):
            raise ValueError("reranker did not return a list")
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(chunks)]
        if not valid:
            raise ValueError("no valid indices returned")
        # Build result: reranked + any remaining chunks not in list (preserve coverage).
        seen: set[int] = set(valid)
        remainder = [i for i in range(len(chunks)) if i not in seen]
        ordered_indices = valid + remainder
        return [chunks[i] for i in ordered_indices[:n]]
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.warning("rerank_chunks failed (falling back to RRF order): %s", exc, exc_info=True)
        return chunks[:n]
