"""
Configuration module — loads environment variables from the project-root .env file.
The path is computed dynamically so it works on any machine.
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Dynamically locate the .env file:
# config.py lives at app/backend/config.py
# The .env file lives 3 levels up (app/ -> workspace/claude/ -> workspace/ ...
# actually at C:/Users/colem/open-source/adversarial-dev/.env)
# We traverse parents to find a .env that contains OPENROUTER_API_KEY


def _find_and_load_env() -> None:
    """Search parent directories for .env and load it.

    In containerized deploys there is no .env file on disk — env vars are
    injected by docker-compose. Missing .env is therefore not an error.
    """
    current = Path(__file__).resolve()
    # Try each parent directory up to the filesystem root
    for parent in current.parents:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            logger.info(f"Loaded .env from {candidate}")
            return
    logger.info("No .env file found on disk; assuming env vars are injected (container deploy).")


_find_and_load_env()

# Expose configuration constants
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    print(
        "WARNING: OPENROUTER_API_KEY is not set or empty. "
        "Embedding and LLM features will not work.",
        file=sys.stderr,
    )

SUPADATA_API_KEY: str = os.environ.get("SUPADATA_API_KEY", "")
if not SUPADATA_API_KEY:
    print(
        "WARNING: SUPADATA_API_KEY is not set or empty. Ingest-by-URL will not work in production.",
        file=sys.stderr,
    )

# MISSION §Administrative surface: "A logged-in admin view … identified by a
# hardcoded user identifier, not by a role system." Empty = no admin configured,
# every /api/admin/* endpoint returns 403 fail-safe.
ADMIN_USER_EMAIL: str = os.environ.get("ADMIN_USER_EMAIL", "")
if not ADMIN_USER_EMAIL:
    print(
        "WARNING: ADMIN_USER_EMAIL is not set. All /api/admin/* endpoints "
        "will return 403 until it is configured.",
        file=sys.stderr,
    )

# Circle membership verification (issue #147). Without these, paid-content
# gating fails closed — every user is treated as a non-member and only sees
# YouTube chunks. The app keeps working; users just don't see Dynamous course
# content until the env is configured.
CIRCLE_ADMIN_TOKEN: str = os.environ.get("CIRCLE_ADMIN_TOKEN", "")
CIRCLE_PAID_ACCESS_GROUP_ID: int = int(os.environ.get("CIRCLE_PAID_ACCESS_GROUP_ID", "0") or "0")
if not CIRCLE_ADMIN_TOKEN or not CIRCLE_PAID_ACCESS_GROUP_ID:
    print(
        "WARNING: Circle membership verification disabled "
        "(CIRCLE_ADMIN_TOKEN or CIRCLE_PAID_ACCESS_GROUP_ID missing). "
        "All users will be treated as non-members.",
        file=sys.stderr,
    )

# Membership refresh staleness window. /me re-verifies a user against Circle
# when member_verified_at is NULL or older than this many seconds.
MEMBERSHIP_REFRESH_SECONDS: int = int(os.environ.get("MEMBERSHIP_REFRESH_SECONDS", "3600"))

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
# OpenRouter slug for the chat model. Defaults to Sonnet 4.6 for prod; can be
# overridden per-deploy to canary a different model (e.g. google/gemini-3-flash-preview).
CHAT_MODEL: str = os.environ.get("CHAT_MODEL", "anthropic/claude-sonnet-4.6")
# When the model is a reasoning model (Gemini 3 Flash, OpenAI o-series, etc.)
# we can disable thinking to maximize tokens/sec on workloads that don't need
# long chain-of-thought (e.g. RAG with explicit tool guidance). "minimal" maps
# to disabled-or-near-zero thinking budget across providers via OpenRouter.
LLM_REASONING_EFFORT: str = os.environ.get("LLM_REASONING_EFFORT", "").strip().lower()

# Postgres — required for all data (chat + auth). The app fails fast without it.
# In prod, docker-compose injects DATABASE_URL from the POSTGRES_* vars.
# Locally, set it manually (e.g. in .env at the app/ root).
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. DynaChat now requires Postgres for all data "
        "(no SQLite fallback). Set DATABASE_URL in your environment before starting."
    )

# JWT signing secret. Required whenever auth is active. 32+ random bytes in prod.
# A local dev value is used only when JWT_SECRET is unset AND DATABASE_URL is unset
# (i.e. auth-off local mode). In any environment with DATABASE_URL set, the real
# secret must come from the environment.
JWT_SECRET: str = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET and DATABASE_URL:
    print(
        "WARNING: DATABASE_URL is set but JWT_SECRET is not. Authentication will fail.",
        file=sys.stderr,
    )
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_SECONDS: int = 7 * 24 * 60 * 60  # 7 days

# RAG settings
RETRIEVAL_TOP_K: int = 5
HYBRID_CHUNKER_MAX_TOKENS: int = 512
RETRIEVAL_EXPANSION_WINDOW: int = int(os.environ.get("RETRIEVAL_EXPANSION_WINDOW", "1"))

# Hybrid retrieval (RRF) constants
HYBRID_K_CONSTANT: int = 60
HYBRID_OVERFETCH_FACTOR: int = 2
KEYWORD_LANGUAGE: str = "english"

# Per-video diversity cap applied after each search-tool call. Prevents one
# long video from monopolizing the retrieved context on broad questions.
# Set to a very large value (e.g. 999) to effectively disable.
RETRIEVAL_MAX_PER_VIDEO: int = int(os.environ.get("RETRIEVAL_MAX_PER_VIDEO", "3"))

# Cap on non-cited citations (issue #176); cited chunks always pass through.
CITATIONS_MAX_COUNT: int = int(os.environ.get("CITATIONS_MAX_COUNT", "10"))

# RAG tool-based retrieval — the LLM drives retrieval via tool calls
# (search_videos, keyword_search_videos, semantic_search_videos,
# get_video_transcript) rather than receiving pre-retrieved chunks. Disabled
# falls back to a tools-off LLM call with no context (model answers from
# training or refuses) — useful only for diagnostic rollback. Per-turn cap
# protects the OpenRouter budget from runaway loops.
LLM_TOOLS_ENABLED: bool = os.environ.get("LLM_TOOLS_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LLM_TOOLS_MAX_PER_TURN: int = int(os.environ.get("LLM_TOOLS_MAX_PER_TURN", "6"))

# Prompt-caching: inject a video catalog block into the system prompt so
# Anthropic can cache the static content between requests.  Opt-in because
# the catalog adds tokens (even on cache hits the input tokens are counted).
CATALOG_ENABLED: bool = os.environ.get("CATALOG_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
CATALOG_TIER: str = (
    os.environ.get("CATALOG_TIER", "standard").strip().lower()
)  # "standard" or "extended"
# TTL in seconds for the extended prompt-cache tier (Anthropic API requires an integer).
CATALOG_CACHE_TTL_SECONDS: int = int(os.environ.get("CATALOG_CACHE_TTL_SECONDS", "3600"))

# Cap on how many characters the get_video_transcript tool returns to the
# model. Long videos can produce 40K+ tokens of transcript; beyond ~30K
# tokens the cost per call gets uncomfortable even on Sonnet's 200K window.
# ~120K chars ≈ 30K tokens on English prose.
TRANSCRIPT_TOOL_MAX_CHARS: int = int(os.environ.get("TRANSCRIPT_TOOL_MAX_CHARS", "120000"))

# YouTube channel to sync from (used by POST /api/channels/sync)
YOUTUBE_CHANNEL_ID: str = os.environ.get("YOUTUBE_CHANNEL_ID", "")

# Content type filter for channel sync: 'all', 'video', 'short', 'live'
CHANNEL_SYNC_TYPE: str = os.environ.get("CHANNEL_SYNC_TYPE", "video")

# YouTube Data API v3 key — required for real video descriptions via videos.list?part=snippet
# Optional: if unset, video descriptions fall back to placeholder strings
YOUTUBE_API_KEY: str = os.environ.get("YOUTUBE_API_KEY", "")

# Whether startup should auto-seed the 10 mock videos bundled in data/seed.py.
# Defaults to OFF — the seed fixtures use synthesised YouTube IDs
# (AgntBld001a, etc.) which break the citation modal in production. Set
# SEED_ENABLE=true only for local development when you want the mock library
# without running a channel sync. Production gets its data via
# POST /api/channels/sync against YOUTUBE_CHANNEL_ID.
SEED_ENABLE: bool = os.environ.get("SEED_ENABLE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Frontend dist directory (built static assets from Vite build)
# When set, the SPA catch-all serves static files from this directory
FRONTEND_DIST: str = os.environ.get("FRONTEND_DIST", "")

# Server ports
BACKEND_PORT: int = 8000
FRONTEND_PORT: int = 5173

# CORS — comma-separated list of allowed origins; defaults to localhost + 127.0.0.1
# on the configured FRONTEND_PORT so the default dev setup works without any env vars.
_cors_raw: str = os.environ.get(
    "CORS_ORIGINS",
    f"http://localhost:{FRONTEND_PORT},http://127.0.0.1:{FRONTEND_PORT}",
)
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]
