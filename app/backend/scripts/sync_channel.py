"""
CLI wrapper around the YouTube channel-sync route, for use from a systemd
timer (or any non-HTTP scheduler) that doesn't want to deal with admin
auth cookies. Imports the existing FastAPI handler and invokes it directly.

Usage:
    uv run python scripts/sync_channel.py             # full channel sync
    uv run python scripts/sync_channel.py --limit 50  # cap to N newest videos
    uv run python scripts/sync_channel.py --force     # also re-process
                                                       # already-ingested videos

Exits 0 on success, 1 on failure. Prints the run summary as a single JSON
line on stdout so a calling script (e.g. journald) can parse it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path

# scripts/ lives at app/backend/scripts/. Imports use `from backend.…`,
# so put app/ (parents[2]) on sys.path. Mirrors scripts/eval_retrieval.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.db.postgres import close_pg_pool, init_pg_pool
from backend.routes.channels import sync_channel


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a one-shot YouTube channel sync.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max videos to process. Omit for full channel.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process videos already in the DB (default: skip).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("sync_channel_cli")
    logger.info("starting channel sync via CLI (limit=%s, force=%s)", args.limit, args.force)

    # Stand the asyncpg pool up ourselves — the FastAPI lifespan does this
    # at uvicorn startup, but we're a separate Python process inside the same
    # container. Without this, the first repository call hits the
    # "Postgres pool is not initialised" guard.
    try:
        await init_pg_pool()
    except Exception as exc:
        logger.exception("Postgres pool init failed: %s", exc)
        return 1

    try:
        result = await sync_channel(limit=args.limit, force=args.force)
    except Exception as exc:
        # Mirror the route's HTTPException semantics: log and exit non-zero
        # so systemd marks the unit as failed and journalctl shows the cause.
        logger.exception("channel sync failed: %s", exc)
        return 1
    finally:
        # Ensure the asyncpg pool is closed cleanly; otherwise pending
        # connections trigger a "loop is closed" warning at process exit.
        with contextlib.suppress(Exception):
            await close_pg_pool()

    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    print(json.dumps(payload))
    logger.info(
        "done: total=%d new=%d errors=%d run_id=%s",
        payload.get("videos_total", 0),
        payload.get("videos_new", 0),
        payload.get("videos_error", 0),
        payload.get("sync_run_id", ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
