"""
FastAPI application entry point.
Handles lifespan startup (DB init + seeding) and route registration.
"""

import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.config import CORS_ORIGINS, DB_PATH
from backend.data.seed import seed_if_empty
from backend.db.schema import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise DB tables, then seed if empty."""
    logger.info("Starting up — initialising database…")
    await init_db()
    logger.info("Database ready. Checking seed data…")
    await seed_if_empty()
    logger.info("Startup complete.")
    yield
    # Shutdown (nothing to clean up for SQLite)
    logger.info("Shutting down.")


app = FastAPI(title="RAG YouTube Chat API", lifespan=lifespan)

# Allow the Vite dev server to reach the API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes (imported here to keep main.py clean)
# ---------------------------------------------------------------------------
from backend.routes import conversations, ingest, messages  # noqa: E402

app.include_router(conversations.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")


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
