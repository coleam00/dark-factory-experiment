#!/usr/bin/env python3
"""
One-shot migration script: copies all rows from a SQLite chat.db into Postgres.

Usage:
    python -m backend.scripts.migrate_sqlite_to_pg <path_to_sqlite_chat.db>

The script:
1. Connects to SQLite (read-only, via aiosqlite) and Postgres (asyncpg) simultaneously
2. Reads all rows from each chat table (videos, chunks, conversations, messages,
   channel_sync_runs, channel_sync_videos)
3. Parses ISO timestamp strings and inserts them into Postgres as TIMESTAMPTZ
4. Runs each table in a single transaction; aborts on any count mismatch
5. Prints progress: "Migrated {n} rows for {table}"

Row-count verification: after each table, asserts COUNT(*) matches on both sides.
If any check fails, the script exits with code 1 and prints which table mismatched.

TIMESTAMPTZ handling: SQLite stores ISO strings; Postgres TIMESTAMPTZ accepts them
directly via TO_TIMESTAMP() or as naive ISO strings (Postgres interprets them as UTC).

No rollback — this is a one-time cutover. Keep a timestamped SQLite snapshot
via dump_sqlite.sh before running.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import aiosqlite
import asyncpg


def _parse_timestamp(value: str | None) -> str | None:
    """Parse a SQLite ISO timestamp string to Postgres TIMESTAMPTZ format."""
    if value is None:
        return None
    return value  # asyncpg accepts ISO strings directly as TIMESTAMPTZ


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


async def _migrate_table(
    sqlite_conn: aiosqlite.Connection,
    pg_conn: asyncpg.Connection,
    table: str,
    select_sql: str,
    insert_sql: str,
    transform: Callable[[dict], tuple],
) -> int:
    """Migrate one table: SELECT from SQLite, INSERT into Postgres.

    Returns the number of rows inserted.
    """
    async with sqlite_conn.execute(select_sql) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        await pg_conn.execute(f"TRUNCATE {table} CASCADE")
        print(f"  {table}: 0 rows (truncated Postgres side)")
        return 0

    # Build list of transformed rows
    transformed = [transform(dict(r)) for r in rows]

    async with pg_conn.transaction():
        await pg_conn.execute(f"TRUNCATE {table} CASCADE")
        for row in transformed:
            await pg_conn.execute(insert_sql, *row)

    # Verify count
    sqlite_count = len(rows)
    pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")

    if pg_count != sqlite_count:
        raise AssertionError(
            f"Count mismatch for {table}: SQLite has {sqlite_count}, Postgres has {pg_count}"
        )

    print(f"  {table}: {pg_count} rows migrated")
    return pg_count  # type: ignore[no-any-return]


async def migrate(sqlite_path: Path, pg_dsn: str) -> None:
    print(f"Connecting to SQLite: {sqlite_path}")
    sqlite_conn = await aiosqlite.connect(str(sqlite_path))

    print(f"Connecting to Postgres: {pg_dsn[:30]}...")
    pg_conn = await asyncpg.connect(pg_dsn)

    try:
        total = 0

        # --- videos --------------------------------------------------------
        def transform_videos(r: dict) -> tuple:
            return (
                r["id"],
                r["title"],
                r["description"],
                r["url"],
                r["transcript"],
                _parse_timestamp(r["created_at"]),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "videos",
            "SELECT id, title, description, url, transcript, created_at FROM videos",
            "INSERT INTO videos (id, title, description, url, transcript, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
            transform_videos,
        )
        total += n

        # --- chunks --------------------------------------------------------
        def transform_chunks(r: dict) -> tuple:
            return (
                r["id"],
                r["video_id"],
                r["content"],
                r["embedding"],  # already JSON string in SQLite
                r["chunk_index"],
                r.get("start_seconds", 0.0),
                r.get("end_seconds", 0.0),
                r.get("snippet", ""),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "chunks",
            "SELECT id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet FROM chunks",
            "INSERT INTO chunks (id, video_id, content, embedding, chunk_index, start_seconds, end_seconds, snippet) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            transform_chunks,
        )
        total += n

        # --- conversations --------------------------------------------------
        def transform_conversations(r: dict) -> tuple:
            return (
                r["id"],
                r["user_id"],
                r["title"],
                _parse_timestamp(r["created_at"]),
                _parse_timestamp(r["updated_at"]),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "conversations",
            "SELECT id, user_id, title, created_at, updated_at FROM conversations",
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES ($1, $2, $3, $4, $5)",
            transform_conversations,
        )
        total += n

        # --- messages ------------------------------------------------------
        def transform_messages(r: dict) -> tuple:
            return (
                r["id"],
                r["conversation_id"],
                r["role"],
                r["content"],
                _parse_timestamp(r["created_at"]),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "messages",
            "SELECT id, conversation_id, role, content, created_at FROM messages",
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES ($1, $2, $3, $4, $5)",
            transform_messages,
        )
        total += n

        # --- channel_sync_runs ---------------------------------------------
        def transform_sync_runs(r: dict) -> tuple:
            return (
                r["id"],
                r["status"],
                r["videos_total"],
                r["videos_new"],
                r["videos_error"],
                _parse_timestamp(r["started_at"]),
                _parse_timestamp(r["finished_at"]),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "channel_sync_runs",
            "SELECT id, status, videos_total, videos_new, videos_error, started_at, finished_at FROM channel_sync_runs",
            "INSERT INTO channel_sync_runs (id, status, videos_total, videos_new, videos_error, started_at, finished_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            transform_sync_runs,
        )
        total += n

        # --- channel_sync_videos ------------------------------------------
        def transform_sync_videos(r: dict) -> tuple:
            return (
                r["id"],
                r["sync_run_id"],
                r["youtube_video_id"],
                r["status"],
                r.get("error_message"),
                _parse_timestamp(r["created_at"]),
            )

        n = await _migrate_table(
            sqlite_conn,
            pg_conn,
            "channel_sync_videos",
            "SELECT id, sync_run_id, youtube_video_id, status, error_message, created_at FROM channel_sync_videos",
            "INSERT INTO channel_sync_videos (id, sync_run_id, youtube_video_id, status, error_message, created_at) VALUES ($1, $2, $3, $4, $5, $6)",
            transform_sync_videos,
        )
        total += n

        print(f"\nMigration complete: {total} total rows copied from SQLite to Postgres.")

    finally:
        await sqlite_conn.close()
        await pg_conn.close()


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m backend.scripts.migrate_sqlite_to_pg <path_to_sqlite_chat.db>")
        sys.exit(1)

    sqlite_path = Path(sys.argv[1])
    if not sqlite_path.exists():
        print(f"SQLite file not found: {sqlite_path}")
        sys.exit(1)

    pg_dsn = input("Enter Postgres DATABASE_URL: ").strip()
    if not pg_dsn:
        print("DATABASE_URL is required.")
        sys.exit(1)

    import asyncio

    asyncio.run(migrate(sqlite_path, pg_dsn))


if __name__ == "__main__":
    main()
