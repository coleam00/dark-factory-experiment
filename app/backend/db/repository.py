"""
Repository layer — all database access goes through this module.
No raw SQL lives in route handlers.

All tables now live in Postgres via asyncpg. The pool is accessed via
`get_pg_pool()` from `backend.db.postgres`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import asyncpg

from backend.db.postgres import get_pg_pool

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    """Return an aware UTC datetime.

    All created_at / updated_at columns are TIMESTAMPTZ in Postgres. asyncpg
    requires a datetime.datetime for those — strings raise DataError at
    bind-time. Return a datetime rather than an ISO string so every repository
    call site Just Works.
    """
    return datetime.now(UTC)


def _acquire() -> asyncpg.pool.PoolAcquireContext:
    """Return the pool's acquire context manager.

    Callers must use it as `async with _acquire() as conn:` — NOT
    `async with await _acquire()`. asyncpg's pool.acquire() already returns
    an async context manager, and awaiting it yields a PoolConnectionProxy
    which is not itself a context manager.
    """
    return get_pg_pool().acquire()


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


async def create_video(
    *,
    title: str,
    description: str,
    url: str,
    transcript: str,
    channel_id: str | None = None,
    channel_title: str | None = None,
) -> dict:
    vid_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO videos (id, title, description, url, transcript, channel_id, channel_title, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            vid_id,
            title,
            description,
            url,
            transcript,
            channel_id,
            channel_title,
            now,
        )
    return {
        "id": vid_id,
        "title": title,
        "description": description,
        "url": url,
        "transcript": transcript,
        "channel_id": channel_id,
        "channel_title": channel_title,
        "created_at": now,
    }


async def get_video(video_id: str) -> dict | None:
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM videos WHERE id = $1",
            video_id,
        )
    return dict(row) if row else None


async def delete_video(video_id: str) -> None:
    """Delete a video and all its associated chunks (FK cascade handles chunks)."""
    async with _acquire() as conn:
        await conn.execute("DELETE FROM videos WHERE id = $1", video_id)


async def list_videos() -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, description, url, created_at, channel_id, channel_title FROM videos ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


async def count_videos() -> int:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM videos")
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


async def create_chunk(
    *,
    video_id: str,
    content: str,
    embedding: list[float],
    chunk_index: int,
    start_seconds: float = 0.0,
    end_seconds: float = 0.0,
    snippet: str = "",
) -> dict:
    chunk_id = _new_id()
    embedding_json = json.dumps(embedding)
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chunks (id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            chunk_id,
            video_id,
            content,
            embedding_json,
            chunk_index,
            start_seconds,
            end_seconds,
            snippet,
        )
    return {
        "id": chunk_id,
        "video_id": video_id,
        "content": content,
        "embedding": embedding,
        "chunk_index": chunk_index,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "snippet": snippet,
    }


async def list_chunks() -> list[dict]:
    """Return all chunks with their embeddings (deserialized)."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet FROM chunks"
        )
    result = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding"])
        result.append(d)
    return result


async def list_chunks_for_video(video_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet
            FROM chunks
            WHERE video_id = $1
            ORDER BY chunk_index
            """,
            video_id,
        )
    result = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding"])
        result.append(d)
    return result


async def get_chunk_neighbors(
    video_id: str,
    chunk_index: int,
    window: int = 1,
) -> list[dict]:
    """
    Return chunks within [chunk_index - window, chunk_index + window] for a video.

    Used by the expansion step to fetch surrounding context for a retrieved chunk.
    Returns chunks ordered by chunk_index ascending.
    """
    min_index = max(0, chunk_index - window)
    max_index = chunk_index + window
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.video_id, c.content, c.chunk_index,
                   c.start_seconds, c.end_seconds, c.snippet,
                   v.title AS video_title, v.url AS video_url
            FROM chunks c
            JOIN videos v ON v.id = c.video_id
            WHERE c.video_id = $1 AND c.chunk_index >= $2 AND c.chunk_index <= $3
            ORDER BY c.chunk_index ASC
            """,
            video_id,
            min_index,
            max_index,
        )
    return [dict(r) for r in rows]


async def count_chunks() -> int:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM chunks")
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Hybrid retrieval helpers (tsvector + pgvector via RRF)
# ---------------------------------------------------------------------------


async def keyword_search(query: str, top_k: int, language: str = "english") -> list[dict]:
    """
    Return top-K chunks matching a full-text query using tsvector.

    Args:
        query: Raw user query string (plainto_tsquery handles escaping)
        top_k: Maximum results to return
        language: tsvector language config (default 'english')

    Returns:
        List of chunk dicts with keys: id, video_id, content, chunk_index,
        start_seconds, end_seconds, snippet, rank (ts_rank score)
    """
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, video_id, content, chunk_index, start_seconds, end_seconds, snippet,
                   ts_rank(search_vector, plainto_tsquery($1)) AS rank
            FROM chunks
            WHERE search_vector @@ plainto_tsquery($1)
            ORDER BY rank DESC
            LIMIT $2
            """,
            query,
            top_k,
        )
    return [dict(r) for r in rows]


async def vector_search_pg(query_embedding: list[float], top_k: int) -> list[dict]:
    """
    Return top-K chunks by pgvector cosine similarity.

    Note: The embedding column is TEXT (JSON-encoded array). pgvector requires
    explicit cast to vector type for cosine distance (<=>) to work correctly.
    We use embedding::vector to ensure proper numeric vector comparison.
    For production, consider migrating embedding to vector(1536) type.

    Args:
        query_embedding: List of 1536 floats (text-embedding-3-small dimensions)
        top_k: Maximum results to return

    Returns:
        List of chunk dicts with keys: id, video_id, content, chunk_index,
        start_seconds, end_seconds, snippet, distance (cosine distance)
    """
    embedding_json = json.dumps(query_embedding)
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, video_id, content, chunk_index, start_seconds, end_seconds, snippet,
                   embedding::vector <=> $1::vector AS distance
            FROM chunks
            ORDER BY distance
            LIMIT $2
            """,
            embedding_json,
            top_k,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------


async def list_videos_admin() -> list[dict]:
    """Videos with chunk_count, newest first. Admin library listing."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.id, v.title, v.description, v.url, v.created_at, v.channel_id, v.channel_title,
                   (SELECT COUNT(*) FROM chunks c WHERE c.video_id = v.id) AS chunk_count
            FROM videos v
            ORDER BY v.created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def delete_video_cascade(video_id: str) -> bool:
    """Delete a video and its chunks (FK ON DELETE CASCADE). Returns False if not found."""
    async with _acquire() as conn:
        result = await conn.execute("DELETE FROM videos WHERE id = $1", video_id)
        return result != "DELETE 0"  # type: ignore[no-any-return]


async def replace_chunks_for_video(
    video_id: str,
    chunks: list[dict],
) -> None:
    """Atomically replace all chunks for *video_id*.

    Each entry in *chunks* must have keys: content, embedding, chunk_index.
    Caller is responsible for fetching/chunking/embedding BEFORE invoking this
    so a Supadata or OpenRouter failure cannot leave the video chunkless.
    """
    async with _acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM chunks WHERE video_id = $1", video_id)
        for c in chunks:
            await conn.execute(
                """
                    INSERT INTO chunks (id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                _new_id(),
                video_id,
                c["content"],
                json.dumps(c["embedding"]),
                c["chunk_index"],
                c.get("start_seconds", 0.0),
                c.get("end_seconds", 0.0),
                c.get("snippet", ""),
            )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict:
    conv_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO conversations (id, user_id, title, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            conv_id,
            user_id,
            title,
            now,
            now,
        )
    return {
        "id": conv_id,
        "user_id": user_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }


async def get_conversation(conv_id: str, user_id: str) -> dict | None:
    """Return the conversation only if it belongs to the given user."""
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM conversations WHERE id = $1 AND user_id = $2",
            conv_id,
            user_id,
        )
    return dict(row) if row else None


async def list_conversations(user_id: str) -> list[dict]:
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.*,
                   (SELECT content
                    FROM messages
                    WHERE conversation_id = c.id
                    ORDER BY created_at DESC
                    LIMIT 1) AS preview
            FROM conversations c
            WHERE c.user_id = $1
            ORDER BY c.updated_at DESC
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def update_conversation_title(conv_id: str, user_id: str, title: str) -> bool:
    """Rename a conversation. Returns False if it does not belong to the user."""
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE conversations SET title = $1, updated_at = $2 WHERE id = $3 AND user_id = $4",
            title,
            _now(),
            conv_id,
            user_id,
        )
        return result != "UPDATE 0"  # type: ignore[no-any-return]


async def touch_conversation(conv_id: str, user_id: str) -> None:
    """Update the updated_at timestamp (scoped to owner; silent no-op otherwise)."""
    async with _acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET updated_at = $1 WHERE id = $2 AND user_id = $3",
            _now(),
            conv_id,
            user_id,
        )


async def delete_conversation(conv_id: str, user_id: str) -> bool:
    async with _acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversations WHERE id = $1 AND user_id = $2",
            conv_id,
            user_id,
        )
        return result != "DELETE 0"  # type: ignore[no-any-return]


async def search_conversations_by_title(user_id: str, query: str, limit: int = 20) -> list[dict]:
    """Return conversations owned by user where title contains substring (case-insensitive).

    Ported from the SQLite era: uses ILIKE in Postgres so the match is
    case-insensitive in one step (no need to lower() both sides).
    """
    pattern = f"%{query}%"
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = $1 AND title ILIKE $2
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            pattern,
            limit,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def create_message(
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
) -> dict | None:
    """Insert a message. Returns None if the conversation does not belong to the user."""
    msg_id = _new_id()
    now = _now()
    sources_json = json.dumps(sources) if sources is not None else None
    async with _acquire() as conn:
        # Verify ownership atomically — INSERT only succeeds if the conversation
        # row exists for this user. Prevents cross-user message injection even
        # if a route handler forgets to check.
        result = await conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, sources, created_at)
            SELECT $1, $2, $3, $4, $5::jsonb, $6
            WHERE EXISTS (
                SELECT 1 FROM conversations WHERE id = $7 AND user_id = $8
            )
            """,
            msg_id,
            conversation_id,
            role,
            content,
            sources_json,
            now,
            conversation_id,
            user_id,
        )
        if result == "INSERT 0 0":
            return None
    await touch_conversation(conversation_id, user_id)
    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "sources": sources,
        "created_at": now,
    }


async def list_messages(conversation_id: str, user_id: str) -> list[dict]:
    """Return messages only if the conversation belongs to the given user."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.*
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = $1 AND c.user_id = $2
            ORDER BY m.created_at ASC
            """,
            conversation_id,
            user_id,
        )
    results = []
    for r in rows:
        d = dict(r)
        # Deserialize sources JSONB to Citation array if present
        # asyncpg returns JSONB as a raw JSON string when no type codec is registered
        # (see db/postgres.py — no set_type_codec call). If a codec is ever added,
        # this json.loads() call must be removed.
        d["sources"] = json.loads(d["sources"]) if d.get("sources") else None
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Channel sync runs
# ---------------------------------------------------------------------------


async def create_sync_run(*, sync_run_id: str, started_at: datetime | str) -> dict:
    """Create a new channel sync run record."""
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO channel_sync_runs (id, status, videos_total, videos_new, videos_error, started_at)
            VALUES ($1, 'running', 0, 0, 0, $2)
            """,
            sync_run_id,
            started_at,
        )
    started_at_str = started_at.isoformat() if isinstance(started_at, datetime) else started_at
    return {
        "id": sync_run_id,
        "status": "running",
        "videos_total": 0,
        "videos_new": 0,
        "videos_error": 0,
        "started_at": started_at_str,
        "finished_at": None,
    }


async def update_sync_run(
    *,
    sync_run_id: str,
    status: str,
    finished_at: datetime | str | None = None,
    videos_total: int = 0,
    videos_new: int = 0,
    videos_error: int = 0,
) -> bool:
    """Update channel sync run counts and optionally mark as finished."""
    async with _acquire() as conn:
        result = await conn.execute(
            """
            UPDATE channel_sync_runs
            SET status = $1, finished_at = $2, videos_total = $3, videos_new = $4, videos_error = $5
            WHERE id = $6
            """,
            status,
            finished_at,
            videos_total,
            videos_new,
            videos_error,
            sync_run_id,
        )
        return result != "UPDATE 0"  # type: ignore[no-any-return]


async def list_sync_runs(limit: int = 10) -> list[dict]:
    """List recent channel sync runs."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM channel_sync_runs
            ORDER BY started_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Channel sync videos
# ---------------------------------------------------------------------------


async def create_sync_video(
    *,
    sync_run_id: str,
    youtube_video_id: str,
    status: str,
) -> dict:
    """Record a video within a sync run."""
    vid_id = _new_id()
    now = _now()
    async with _acquire() as conn:
        await conn.execute(
            """
            INSERT INTO channel_sync_videos (id, sync_run_id, youtube_video_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            vid_id,
            sync_run_id,
            youtube_video_id,
            status,
            now,
        )
    return {
        "id": vid_id,
        "sync_run_id": sync_run_id,
        "youtube_video_id": youtube_video_id,
        "status": status,
        "error_message": None,
        "created_at": now,
    }


async def update_sync_video_status(
    video_id: str, status: str, error_message: str | None = None
) -> bool:
    """Update a sync video's status, optionally recording an error."""
    async with _acquire() as conn:
        result = await conn.execute(
            "UPDATE channel_sync_videos SET status = $1, error_message = $2 WHERE id = $3",
            status,
            error_message,
            video_id,
        )
        return result != "UPDATE 0"  # type: ignore[no-any-return]


async def get_video_by_youtube_id(youtube_video_id: str) -> dict | None:
    """
    Check if a video has already been ingested.

    Looks up a video by searching for *youtube_video_id* in the URL column
    (assumes YouTube watch URL format ?v={id}). Returns None if not found.
    """
    async with _acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM videos WHERE url LIKE $1",
            f"%{youtube_video_id}%",
        )
    return dict(row) if row else None


async def list_sync_videos_for_run(sync_run_id: str) -> list[dict]:
    """List all sync video records for a given sync run."""
    async with _acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM channel_sync_videos WHERE sync_run_id = $1 ORDER BY created_at",
            sync_run_id,
        )
    return [dict(r) for r in rows]
