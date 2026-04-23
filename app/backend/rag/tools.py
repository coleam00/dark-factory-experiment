"""LLM retrieval tools.

All RAG retrieval is exposed as LLM tool calls — no pre-retrieval happens
before the model runs. The model chooses which strategy fits each question:

  - `search_videos`           — hybrid (keyword + vector via RRF). Default.
  - `keyword_search_videos`   — tsvector FTS only. Best for exact terms.
  - `semantic_search_videos`  — pgvector cosine only. Best for paraphrases.
  - `get_video_transcript`    — full timestamped transcript of one video.

Executors return a dict of shape
    {"ok": True, "text": <LLM-facing string>, "chunks": <citation-shaped list>}
on success, or {"ok": False, "error": <str>} on any failure. The caller
accumulates `chunks` into the SSE `sources` event so citation chips reflect
whatever the model actually read.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from backend.db import repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

SEARCH_VIDEOS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_videos",
        "description": (
            "Hybrid search over the video library, combining keyword and semantic "
            "retrieval via Reciprocal Rank Fusion. This is the default and "
            "recommended strategy for most questions. Returns the most relevant "
            "chunks across all videos with timestamp-anchored citations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — a question, phrase, or keyword.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max chunks to return (default 10, range 1-30).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

KEYWORD_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "keyword_search_videos",
        "description": (
            "Keyword/full-text search (Postgres tsvector). Best when the user "
            "uses exact terminology, proper nouns, acronyms, or technical terms "
            "likely to appear verbatim in transcripts. Prefer `search_videos` "
            "unless you specifically need literal-term matching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to match."},
                "top_k": {"type": "integer", "description": "Max chunks to return (default 10)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

SEMANTIC_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "semantic_search_videos",
        "description": (
            "Semantic/vector search (pgvector cosine). Best for conceptual or "
            "paraphrased questions where the user's wording may not match the "
            "transcripts literally. Prefer `search_videos` unless you know "
            "terminology will diverge and need pure semantic matching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question or concept to search for."},
                "top_k": {"type": "integer", "description": "Max chunks to return (default 10)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

GET_VIDEO_TRANSCRIPT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_video_transcript",
        "description": (
            "Read the full timestamped transcript of one video. Call this when "
            "a search returned relevant-but-insufficient chunks and you need the "
            "full arc of a specific video to answer well. Expensive — cap 2 "
            "per turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "video_id": {
                    "type": "string",
                    "description": "Internal video_id. Must come from a prior search result or the catalog.",
                }
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    SEARCH_VIDEOS_TOOL,
    KEYWORD_SEARCH_TOOL,
    SEMANTIC_SEARCH_TOOL,
    GET_VIDEO_TRANSCRIPT_TOOL,
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_args(raw: str | dict) -> dict | None:
    """Parse tool arguments. Returns None on invalid JSON."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp_top_k(value: Any, default: int = 10, maximum: int = 30) -> int:
    """Coerce top_k to a sane integer in [1, maximum]."""
    try:
        k = int(value) if value is not None else default
    except (TypeError, ValueError):
        k = default
    return max(1, min(maximum, k))


async def _hydrate_chunks(raw_chunks: list[dict]) -> list[dict]:
    """Enrich raw repository chunks (id, video_id, content, ts...) with video
    title/url and reshape to the canonical citation-chunk dict.

    Fetches video metadata for all unique video_ids concurrently to avoid
    serial DB round-trips when a search spans many videos.
    """
    if not raw_chunks:
        return []

    unique_ids = list({c.get("video_id", "") for c in raw_chunks if c.get("video_id")})

    async def _load(vid: str) -> tuple[str, dict[str, str]]:
        try:
            video = await repository.get_video(vid)
        except Exception as exc:
            logger.warning("hydrate: get_video failed for %s: %s", vid, exc, exc_info=True)
            video = None
        info = video or {}
        return vid, {
            "title": info.get("title", "Unknown Video"),
            "url": info.get("url", ""),
        }

    video_cache: dict[str, dict[str, str]] = dict(
        await asyncio.gather(*(_load(v) for v in unique_ids))
    )

    return [
        {
            "chunk_id": c.get("id", c.get("chunk_id", "")),
            "content": c.get("content", ""),
            "video_id": c.get("video_id", ""),
            "video_title": video_cache.get(c.get("video_id", ""), {}).get("title", "Unknown Video"),
            "video_url": video_cache.get(c.get("video_id", ""), {}).get("url", ""),
            "start_seconds": c.get("start_seconds", 0.0),
            "end_seconds": c.get("end_seconds", 0.0),
            "snippet": c.get("snippet", ""),
        }
        for c in raw_chunks
    ]


def _format_search_results(chunks: list[dict]) -> str:
    """Format chunks as the LLM-facing tool result text with [mm:ss] markers."""
    if not chunks:
        return "No relevant chunks found. Try a different query or strategy."
    parts = []
    for c in chunks:
        title = c.get("video_title") or "Unknown Video"
        start = int(c.get("start_seconds") or 0.0)
        mins, secs = divmod(start, 60)
        parts.append(f"[Source: {title} at {mins:02d}:{secs:02d}]\n{c.get('content', '')}")
    return "\n\n---\n\n".join(parts)


_CANONICAL_CHUNK_KEYS = (
    "chunk_id",
    "content",
    "video_id",
    "video_title",
    "video_url",
    "start_seconds",
    "end_seconds",
    "snippet",
)

_FLOAT_CHUNK_KEYS = frozenset(("start_seconds", "end_seconds"))


def _normalize_chunk_shape(chunk: dict) -> dict:
    """Project a chunk dict onto the canonical citation shape.

    Different retrieval paths produce slightly different dicts — hybrid
    retrieval adds an RRF ``score`` that the frontend doesn't use, while
    hydrate-produced chunks don't have it. Normalizing before the dedup/
    merge in ``routes/messages.py`` keeps the ``sources`` SSE payload
    consistent regardless of which tool the model called.
    """
    return {
        key: chunk.get(key, 0.0 if key in _FLOAT_CHUNK_KEYS else "")
        for key in _CANONICAL_CHUNK_KEYS
    }


def _apply_per_video_cap(chunks: list[dict], max_per_video: int) -> list[dict]:
    """Limit how many chunks from any single video reach the final context.

    Walks chunks in their input ranking order and drops a chunk once its
    video has already contributed ``max_per_video`` chunks. Preserves the
    relative ordering of the chunks that are kept. A very large cap value
    effectively disables the filter. Passing ``max_per_video <= 0`` is a
    no-op and returns the input list unchanged.

    Chunks missing a ``video_id`` key (or with a falsy value) are always
    passed through without counting against any cap bucket.
    """
    if max_per_video <= 0 or not chunks:
        return chunks

    per_video: dict[str, int] = defaultdict(int)
    kept: list[dict] = []
    for c in chunks:
        vid = c.get("video_id")
        if not vid:
            # No video_id — pass through unconditionally; don't group under "".
            kept.append(c)
            continue
        if per_video[vid] >= max_per_video:
            continue
        kept.append(c)
        per_video[vid] += 1
    return kept


def _format_transcript(video: dict, chunks: list[dict], max_chars: int | None = None) -> str:
    """Render chunks as [mm:ss]-annotated transcript.

    If ``max_chars`` is supplied and the rendered transcript would exceed it,
    truncate at the last complete chunk that fits and append a marker so the
    LLM knows content was dropped.
    """
    title = video.get("title", "Unknown Video")
    header = f"# {title}\n"
    parts: list[str] = [header]
    char_count = len(header)
    kept_chunks = 0
    total_chunks = 0
    for c in chunks:
        total_chunks += 1
        start = int(c.get("start_seconds") or 0.0)
        mins, secs = divmod(start, 60)
        content = (c.get("content") or "").strip()
        if not content:
            continue
        piece = f"[{mins:02d}:{secs:02d}] {content}"
        # Account for the "\n\n" separator we will join with.
        addition = len(piece) + 2
        if max_chars is not None and char_count + addition > max_chars:
            break
        parts.append(piece)
        char_count += addition
        kept_chunks += 1
    if max_chars is not None and kept_chunks < total_chunks:
        dropped = total_chunks - kept_chunks
        parts.append(
            f"\n[transcript truncated — {dropped} more chunks omitted to stay "
            f"within the {max_chars}-character cap for tool responses]"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


async def _embed_query(query: str, cache: dict[str, list[float]] | None) -> list[float]:
    """Embed a query, optionally memoizing the result within a turn.

    The same user turn can issue both ``search_videos`` and
    ``semantic_search_videos`` on identical query strings — without the
    cache each call pays an extra OpenRouter embedding round-trip.
    """
    from backend.rag.embeddings import embed_text

    if cache is not None and query in cache:
        return cache[query]
    embedding = await asyncio.to_thread(embed_text, query)
    if cache is not None:
        cache[query] = embedding
    return embedding


async def execute_search_hybrid(
    raw_arguments: str | dict,
    embedding_cache: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Hybrid (keyword + semantic via RRF) search."""
    from backend.config import RETRIEVAL_MAX_PER_VIDEO
    from backend.rag.retriever_hybrid import retrieve_hybrid

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    try:
        embedding = await _embed_query(query, embedding_cache)
        chunks = await retrieve_hybrid(query, embedding, top_k=top_k)
    except Exception as exc:
        logger.warning("search_hybrid failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_video_cap(chunks, RETRIEVAL_MAX_PER_VIDEO)
    chunks = [_normalize_chunk_shape(c) for c in chunks]
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_search_keyword(raw_arguments: str | dict) -> dict[str, Any]:
    """Keyword-only (tsvector FTS) search."""
    from backend.config import KEYWORD_LANGUAGE, RETRIEVAL_MAX_PER_VIDEO

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    try:
        raw = await repository.keyword_search(query, top_k=top_k, language=KEYWORD_LANGUAGE)
        chunks = await _hydrate_chunks(raw)
    except Exception as exc:
        logger.warning("search_keyword failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_video_cap(chunks, RETRIEVAL_MAX_PER_VIDEO)
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_search_semantic(
    raw_arguments: str | dict,
    embedding_cache: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Semantic-only (pgvector cosine) search."""
    from backend.config import RETRIEVAL_MAX_PER_VIDEO

    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "missing required parameter: query"}
    top_k = _clamp_top_k(args.get("top_k"))

    try:
        embedding = await _embed_query(query, embedding_cache)
        raw = await repository.vector_search_pg(embedding, top_k=top_k)
        chunks = await _hydrate_chunks(raw)
    except Exception as exc:
        logger.warning("search_semantic failed: %s", exc, exc_info=True)
        return {"ok": False, "error": f"search failed: {exc}"}

    chunks = _apply_per_video_cap(chunks, RETRIEVAL_MAX_PER_VIDEO)
    return {"ok": True, "text": _format_search_results(chunks), "chunks": chunks}


async def execute_get_video_transcript(
    raw_arguments: str | dict,
    video_id_whitelist: set[str] | None = None,
) -> dict[str, Any]:
    """Full transcript of one video. video_id_whitelist guards against the
    model hallucinating ids; None disables the check (tests)."""
    args = _parse_args(raw_arguments)
    if args is None:
        return {"ok": False, "error": "invalid JSON arguments"}
    video_id = args.get("video_id")
    if not isinstance(video_id, str) or not video_id.strip():
        return {"ok": False, "error": "missing required parameter: video_id"}
    video_id = video_id.strip()

    if video_id_whitelist is not None and video_id not in video_id_whitelist:
        return {
            "ok": False,
            "error": (
                f"video_id {video_id!r} is not in the current library. "
                "Only ids from prior search results are valid."
            ),
        }

    try:
        video = await repository.get_video(video_id)
    except Exception as exc:
        logger.warning("get_video_transcript: get_video failed for %s: %s", video_id, exc)
        return {"ok": False, "error": f"failed to look up video: {exc}"}
    if not video:
        return {"ok": False, "error": f"video not found: {video_id}"}

    try:
        raw_chunks = await repository.list_chunks_for_video(video_id)
    except Exception as exc:
        logger.warning("get_video_transcript: list_chunks failed for %s: %s", video_id, exc)
        return {"ok": False, "error": f"failed to load chunks: {exc}"}
    if not raw_chunks:
        return {"ok": False, "error": f"no chunks available for video: {video_id}"}

    chunks = [
        {
            "chunk_id": c.get("id", ""),
            "content": c.get("content", ""),
            "video_id": video_id,
            "video_title": video.get("title", ""),
            "video_url": video.get("url", ""),
            "start_seconds": c.get("start_seconds", 0.0),
            "end_seconds": c.get("end_seconds", 0.0),
            "snippet": c.get("snippet", ""),
        }
        for c in raw_chunks
    ]

    from backend.config import TRANSCRIPT_TOOL_MAX_CHARS

    return {
        "ok": True,
        "text": _format_transcript(video, raw_chunks, max_chars=TRANSCRIPT_TOOL_MAX_CHARS),
        "chunks": chunks,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def execute_tool(
    name: str,
    raw_arguments: str | dict,
    video_id_whitelist: set[str] | None = None,
    embedding_cache: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Dispatch by tool name. Unknown names return an error dict so the
    model sees the refusal and stops calling.

    ``embedding_cache`` is optional per-turn memoization — if the same query
    text is passed to hybrid and semantic search in one turn, we embed once.
    """
    if name == "search_videos":
        return await execute_search_hybrid(raw_arguments, embedding_cache=embedding_cache)
    if name == "keyword_search_videos":
        return await execute_search_keyword(raw_arguments)
    if name == "semantic_search_videos":
        return await execute_search_semantic(raw_arguments, embedding_cache=embedding_cache)
    if name == "get_video_transcript":
        return await execute_get_video_transcript(
            raw_arguments, video_id_whitelist=video_id_whitelist
        )
    return {"ok": False, "error": f"unknown tool: {name}"}


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Convert an executor result into the `role: tool` message content."""
    if result.get("ok"):
        return str(result.get("text", ""))
    return f"Error: {result.get('error') or 'tool execution failed'}"
