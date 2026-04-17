"""
Repository layer — all database access goes through this module.
No raw SQL lives in route handlers.
"""

import json
import uuid
from datetime import UTC, datetime

import aiosqlite

from backend.config import DB_PATH


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


async def create_video(
    *,
    title: str,
    description: str,
    url: str,
    transcript: str,
) -> dict:
    vid_id = _new_id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO videos (id, title, description, url, transcript, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid_id, title, description, url, transcript, now),
        )
        await db.commit()
    return {
        "id": vid_id,
        "title": title,
        "description": description,
        "url": url,
        "transcript": transcript,
        "created_at": now,
    }


async def get_video(video_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def list_videos() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, description, url, created_at FROM videos ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def count_videos() -> int:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT COUNT(*) FROM videos") as cursor,
    ):
        row = await cursor.fetchone()
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
) -> dict:
    chunk_id = _new_id()
    embedding_json = json.dumps(embedding)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chunks (id, video_id, content, embedding, chunk_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (chunk_id, video_id, content, embedding_json, chunk_index),
        )
        await db.commit()
    return {
        "id": chunk_id,
        "video_id": video_id,
        "content": content,
        "embedding": embedding,
        "chunk_index": chunk_index,
    }


async def list_chunks() -> list[dict]:
    """Return all chunks with their embeddings (deserialized)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, video_id, content, embedding, chunk_index FROM chunks"
        ) as cursor:
            rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding"])
        result.append(d)
    return result


async def list_chunks_for_video(video_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, video_id, content, embedding, chunk_index FROM chunks "
            "WHERE video_id = ? ORDER BY chunk_index",
            (video_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["embedding"] = json.loads(d["embedding"])
        result.append(d)
    return result


async def count_chunks() -> int:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute("SELECT COUNT(*) FROM chunks") as cursor,
    ):
        row = await cursor.fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


async def create_conversation(*, user_id: str, title: str = "New Conversation") -> dict:
    conv_id = _new_id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, user_id, title, now, now),
        )
        await db.commit()
    return {
        "id": conv_id,
        "user_id": user_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }


async def get_conversation(conv_id: str, user_id: str) -> dict | None:
    """Return the conversation only if it belongs to the given user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def list_conversations(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT c.*,
                   (SELECT content
                    FROM messages
                    WHERE conversation_id = c.id
                    ORDER BY created_at DESC
                    LIMIT 1) AS preview
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY c.updated_at DESC
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_conversation_title(conv_id: str, user_id: str, title: str) -> bool:
    """Rename a conversation. Returns False if it does not belong to the user."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, _now(), conv_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def touch_conversation(conv_id: str, user_id: str) -> None:
    """Update the updated_at timestamp (scoped to owner; silent no-op otherwise)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
            (_now(), conv_id, user_id),
        )
        await db.commit()


async def delete_conversation(conv_id: str, user_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        # Enable foreign keys so ON DELETE CASCADE removes associated messages
        await db.execute("PRAGMA foreign_keys=ON;")
        cursor = await db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def create_message(
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
) -> dict | None:
    """Insert a message. Returns None if the conversation does not belong to the user."""
    msg_id = _new_id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        # Verify ownership atomically — INSERT only succeeds if the conversation
        # row exists for this user. Prevents cross-user message injection even
        # if a route handler forgets to check.
        cursor = await db.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            SELECT ?, ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM conversations WHERE id = ? AND user_id = ?
            )
            """,
            (msg_id, conversation_id, role, content, now, conversation_id, user_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    await touch_conversation(conversation_id, user_id)
    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": now,
    }


async def list_messages(conversation_id: str, user_id: str) -> list[dict]:
    """Return messages only if the conversation belongs to the given user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT m.*
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = ? AND c.user_id = ?
            ORDER BY m.created_at ASC
            """,
            (conversation_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(r) for r in rows]
