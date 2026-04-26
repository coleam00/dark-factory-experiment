"""
Async Postgres connection pool.

The pool is a module-level singleton created in the FastAPI lifespan handler;
routes and repos fetch it via `get_pg_pool()`. All schema management is handled
by Alembic migrations.
"""

from __future__ import annotations

import logging

import asyncpg

from backend.config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup run by asyncpg before the pool hands it to a caller.

    Currently sets pgvector's iterative-scan mode to `relaxed_order`. Without
    this, filtered ANN queries (chunks WHERE source_type IN (...)) silently
    fall back to exact scan and lose the HNSW index speedup. See pgvector
    0.8.0 release notes. Failing this SET should not crash the app — older
    pgvector versions don't have the GUC, and the fallback (exact scan) is
    correct, just slower.
    """
    try:
        await conn.execute("SET hnsw.iterative_scan = 'relaxed_order'")
    except asyncpg.PostgresError as exc:
        logger.warning(
            "Could not set hnsw.iterative_scan (pgvector < 0.8.0?): %s; "
            "filtered ANN queries will fall back to exact scan",
            exc,
        )


async def init_pg_pool() -> asyncpg.Pool:
    """Create the asyncpg pool if not already created. Idempotent."""
    global _pool
    if _pool is not None:
        return _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set; cannot initialise Postgres pool.")
    logger.info("Connecting to Postgres…")
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=10,
        init=_init_connection,
    )
    logger.info("Postgres pool ready.")
    return _pool


async def close_pg_pool() -> None:
    """Close the pool on shutdown. Idempotent."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pg_pool() -> asyncpg.Pool:
    """Return the live pool. Raises if `init_pg_pool` was not called."""
    if _pool is None:
        raise RuntimeError(
            "Postgres pool is not initialised. Call init_pg_pool() in the "
            "FastAPI lifespan before using any Postgres-backed repository."
        )
    return _pool
