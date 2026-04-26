"""
FastAPI application entry point.
Handles lifespan startup (DB init + seeding) and route registration.
"""

import logging
import os
import subprocess
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.auth.dependencies import get_current_admin, get_current_user
from backend.config import CORS_ORIGINS, FRONTEND_DIST
from backend.data.seed import seed_if_empty
from backend.db.postgres import close_pg_pool, init_pg_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: run Alembic migrations, init Postgres pool, then seed if empty."""
    logger.info("Starting up — running Alembic migrations…")

    # Run alembic upgrade head. alembic.ini lives at app/backend/alembic.ini
    # and its script_location ("backend/alembic") is resolved relative to the
    # working directory — which must be the parent of backend/ (i.e. /app in
    # the container, or app/ in the repo). Force cwd so behaviour is the same
    # regardless of where uvicorn was started from.
    backend_dir = Path(__file__).resolve().parent
    alembic_cfg = backend_dir / "alembic.ini"
    alembic_cwd = backend_dir.parent
    result = subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "--config",
            str(alembic_cfg),
            "upgrade",
            "head",
        ],
        capture_output=True,
        text=True,
        cwd=str(alembic_cwd),
    )
    if result.returncode != 0:
        logger.error(
            "Alembic migration failed. stdout=%s stderr=%s",
            result.stdout,
            result.stderr,
        )
        raise RuntimeError(
            f"Alembic upgrade head failed: stdout={result.stdout} stderr={result.stderr}"
        )
    logger.info("Alembic migrations applied.")

    # Initialise the Postgres pool (used by all repository calls)
    await init_pg_pool()
    logger.info("Postgres pool initialised.")

    logger.info("Checking seed data…")
    await seed_if_empty()

    # Ingest paid Dynamous course/workshop transcripts (issue #147). The
    # private `dynamous-content` repo is git-cloned into the container at
    # deploy time; this picks up whatever is on disk. Idempotent — files
    # whose SHA matches what's in the DB are skipped. Failures here never
    # crash startup; YouTube content keeps working regardless.
    try:
        from backend.ingest.dynamous import ingest_dynamous_content

        content_dir = Path(os.environ.get("DYNAMOUS_CONTENT_DIR", "/app/content/dynamous"))
        counts = await ingest_dynamous_content(content_dir)
        logger.info("Dynamous ingest counts: %s", counts)
    except Exception:
        logger.exception("Dynamous content ingest failed; continuing without it")

    logger.info("Startup complete.")
    yield
    logger.info("Shutting down.")
    await close_pg_pool()


app = FastAPI(title="RAG YouTube Chat API", lifespan=lifespan)

# Allow the Vite dev server to reach the API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # was hardcoded list
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes (imported here to keep main.py clean)
# ---------------------------------------------------------------------------
from backend.routes import admin, auth, channels, conversations, ingest, messages  # noqa: E402

# Auth routes are public (signup/login don't require a session; /me and /logout
# rely on their own dependency/cookie behaviour).
app.include_router(auth.router, prefix="/api")

# User-scoped routes require authentication — MISSION.md §10 invariant:
# "All chat access requires authentication — there is no anonymous mode."
_auth_required = [Depends(get_current_user)]
app.include_router(conversations.router, prefix="/api", dependencies=_auth_required)
app.include_router(messages.router, prefix="/api", dependencies=_auth_required)

# Library-mutation routes (ingest a video, backfill the whole channel) and
# admin routes — all gated on get_current_admin. These endpoints write to the
# shared video library or burn paid API budget (Supadata, embeddings), so they
# are not safe to expose to arbitrary authenticated users. get_current_admin
# chains through get_current_user, so an unauthenticated caller still gets 401
# (not 403).
_admin_required = [Depends(get_current_admin)]
app.include_router(ingest.router, prefix="/api", dependencies=_admin_required)
app.include_router(channels.router, prefix="/api", dependencies=_admin_required)
app.include_router(admin.router, prefix="/api", dependencies=_admin_required)


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
        "db_type": "postgres",
    }


@app.get("/api/version")
async def version() -> dict[str, str]:
    try:
        return {"version": get_version("dynachat-backend")}
    except PackageNotFoundError:
        raise HTTPException(status_code=503, detail="Package metadata unavailable") from None


# ---------------------------------------------------------------------------
# Frontend static assets / SPA catch-all
#
# When FRONTEND_DIST env var points at a built `dist/`, serve static files
# from it and fall back to index.html for any path that isn't an API route.
# In dev (FRONTEND_DIST unset), Caddy proxies / to Vite on 5173, so this
# block is never reached — but the catch-all is harmless in that case.
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def serve_root():
    """Serve index.html for the root path (/) which doesn't match /{path:path})."""
    index_path = Path(FRONTEND_DIST) / "index.html" if FRONTEND_DIST else Path("index.html")
    if not index_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"index.html not found. FRONTEND_DIST={FRONTEND_DIST!r}, cwd={os.getcwd()}. "
            "Set FRONTEND_DIST environment variable or ensure index.html exists at current directory.",
        )
    return FileResponse(str(index_path))


@app.get("/{path:path}", include_in_schema=False)
async def serve_spa_or_static(path: str):
    """Serve index.html for any non-API path (SPA catch-all).

    When FRONTEND_DIST is set, also serve actual static files (JS/CSS/assets)
    from that directory so hashed build artifacts are served correctly.
    """
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404)

    if FRONTEND_DIST:
        try:
            dist_dir = Path(FRONTEND_DIST).resolve()
            requested_path = (dist_dir / path).resolve()
            # Guard against path traversal (e.g. path=../../etc/passwd)
            if not requested_path.is_relative_to(dist_dir):
                raise HTTPException(status_code=404)
            if requested_path.is_file():
                return FileResponse(str(requested_path))
        except OSError as exc:
            logger.error(
                "Static file error for path=%s frontend_dist=%s: %s", path, FRONTEND_DIST, exc
            )
            raise HTTPException(status_code=500, detail="Static file error") from exc

    index_path = Path(FRONTEND_DIST) / "index.html" if FRONTEND_DIST else Path("index.html")
    if not index_path.exists():
        # Pre-fix behavior: 404 JSON for unknown non-API paths when FRONTEND_DIST is unset
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(index_path))
