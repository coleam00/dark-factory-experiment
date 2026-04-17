"""
FastAPI application entry point.
Handles lifespan startup (DB init + seeding) and route registration.
"""

import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.dependencies import get_current_user
from backend.config import DATABASE_URL, DB_PATH
from backend.data.seed import seed_if_empty
from backend.db.postgres import close_pg_pool, init_users_schema
from backend.db.schema import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise DB tables (SQLite + Postgres), then seed if empty."""
    logger.info("Starting up — initialising database…")
    await init_db()
    if DATABASE_URL:
        logger.info("DATABASE_URL set — initialising Postgres users schema…")
        await init_users_schema()
    else:
        logger.warning("DATABASE_URL not set; auth endpoints will fail until configured.")
    logger.info("Database ready. Checking seed data…")
    await seed_if_empty()
    logger.info("Startup complete.")
    yield
    logger.info("Shutting down.")
    if DATABASE_URL:
        await close_pg_pool()


app = FastAPI(title="RAG YouTube Chat API", lifespan=lifespan)

# Allow the Vite dev server to reach the API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes (imported here to keep main.py clean)
# ---------------------------------------------------------------------------
from backend.routes import auth, conversations, ingest, messages  # noqa: E402

# Auth routes are public (signup/login don't require a session; /me and /logout
# rely on their own dependency/cookie behaviour).
app.include_router(auth.router, prefix="/api")

# All other API routes require authentication — MISSION.md §10 invariant:
# "All chat access requires authentication — there is no anonymous mode."
_auth_required = [Depends(get_current_user)]
app.include_router(conversations.router, prefix="/api", dependencies=_auth_required)
app.include_router(messages.router, prefix="/api", dependencies=_auth_required)
app.include_router(ingest.router, prefix="/api", dependencies=_auth_required)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
from backend.db import repository  # noqa: E402


@app.get("/api/health")
async def health():
    video_count = await repository.count_videos()
    chunk_count = await repository.count_chunks()

    return {
        "status": "ok",
        "video_count": video_count,
        "chunk_count": chunk_count,
        "db_path": str(DB_PATH),
    }


@app.get("/api/version")
async def version() -> dict[str, str]:
    try:
        return {"version": get_version("dynachat-backend")}
    except PackageNotFoundError:
        raise HTTPException(status_code=503, detail="Package metadata unavailable") from None


@app.post("/api/stream-test")
async def stream_test():
    """
    Test route that streams a short LLM response as SSE to verify streaming format.
    """
    from backend.llm.openrouter import stream_chat

    async def generator():
        async for chunk in stream_chat(
            messages=[{"role": "user", "content": "Say hello in exactly 3 words."}],
            context="",
        ):
            yield chunk

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Frontend static assets (production only — Vite serves them in dev)
#
# When FRONTEND_DIST env var points at a built `dist/`, mount it at `/` so
# FastAPI serves index.html + hashed JS/CSS from the same origin as `/api/*`.
# This mount is registered LAST so the `/api/*` routes above take precedence.
# ---------------------------------------------------------------------------
_frontend_dist = os.environ.get("FRONTEND_DIST", "")
if _frontend_dist and Path(_frontend_dist).is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
