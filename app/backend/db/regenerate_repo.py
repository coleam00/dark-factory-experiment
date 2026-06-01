"""
Repository helper for regenerating the last assistant message.
Lives outside repository.py to avoid touching the protected path.
"""

from __future__ import annotations

from backend.db.postgres import get_pg_pool


async def delete_last_assistant_message(conversation_id: str, user_id: str) -> bool:
    """Delete the most recent assistant message in a conversation.

    Returns True if a row was deleted, False otherwise.
    Ownership is verified by joining through the conversations table.
    """
    pool = get_pg_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM messages
            WHERE id = (
                SELECT m.id
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.conversation_id = $1
                  AND c.user_id = $2
                  AND m.role = 'assistant'
                ORDER BY m.created_at DESC
                LIMIT 1
            )
            """,
            conversation_id,
            user_id,
        )
        return result != "DELETE 0"
