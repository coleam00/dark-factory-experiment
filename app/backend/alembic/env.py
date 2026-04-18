"""
Alembic async environment for DynaChat.

Uses SQLAlchemy 2.0 async engine (backed by asyncpg) to run migrations.
The application uses asyncpg directly for data access; Alembic uses SQLAlchemy
async for migration lifecycle only — both connect to the same DATABASE_URL.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model's MetaData object, used by `autogenerate` support.
# We don't use autogenerate (lift-and-shift migration), so target_metadata
# is left empty. It must still be set so Alembic's context can compare.
target_metadata: dict | None = None


def get_database_url() -> str:
    """Resolve DATABASE_URL, preferring the environment over the ini value.

    alembic.ini ships with `sqlalchemy.url = env(DATABASE_URL)`, but Alembic
    does NOT natively expand `env(...)` — it returns the literal string, which
    SQLAlchemy then fails to parse. So: read os.environ first, and only fall
    back to the ini if the env var is unset AND the ini value looks like a
    real URL (not the unresolved `env(...)` placeholder).
    """
    import os

    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    ini_url = config.get_main_option("sqlalchemy.url", "")
    if ini_url and not ini_url.startswith("env("):
        return ini_url
    return ""


run_async_engine: AsyncEngine | None = None


def set_async_engine(engine: AsyncEngine) -> None:
    """Allow main.py to inject the shared pool's engine for migration runs."""
    global run_async_engine
    run_async_engine = engine


async def run_async_migrations() -> None:
    """Run migrations in async mode using the injected async engine."""
    global run_async_engine
    if run_async_engine is None:
        url = get_database_url()
        if not url:
            raise RuntimeError("DATABASE_URL is not set. Cannot run Alembic migrations.")
        # Create a dedicated pool for migrations (separate from app pool).
        # NullPool so each check-out is a fresh connection.
        # SQLAlchemy's async engine needs an explicit async driver.
        # The app's asyncpg code accepts the plain postgresql:// URL, so we
        # normalize here rather than forcing users to maintain two URLs.
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        run_async_engine = create_async_engine(
            url,
            poolclass=pool.NullPool,
            echo=False,
        )

    async with run_async_engine.connect() as connection:
        await connection.begin()
        await connection.run_sync(do_run_migrations)
        await connection.commit()


def do_run_migrations(connection) -> None:
    """Synchronous wrapper called by connection.run_sync."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,  # type: ignore[arg-type]
        compare_type=True,
        render_as_batch=True,  # Handles SERial/Serial mismatches gracefully
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Entry point for 'alembic upgrade head' when running online."""
    await run_async_migrations()


# Alembic's offline mode is not supported (we always run online with asyncpg).
# The offline context manager is defined but raises if invoked.
def run_migrations_offline() -> None:
    raise NotImplementedError(
        "Offline migration mode is not supported. "
        "DATABASE_URL is required; all migrations run online via asyncpg."
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Run migrations synchronously within the async event loop.
    # get_event_loop() is deprecated in 3.10+ but required for older Python.
    # Use asyncio.run() when available or fall back to get_event_loop().
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(run_async_migrations())
