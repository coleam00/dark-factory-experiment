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

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
CHAT_MODEL: str = "anthropic/claude-sonnet-4.6"

# Database — env-overridable so containerized deploys can point at a mounted volume.
# Default preserves the existing local dev behaviour (app/backend/data/chat.db).
_default_db_path = Path(__file__).resolve().parent / "data" / "chat.db"
DB_PATH: Path = Path(os.environ.get("DB_PATH", str(_default_db_path)))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Postgres — required in prod for auth/users. Absent locally means auth endpoints
# will fail on first DB call; pick one of the existing chat routes for local dev.
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

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

# YouTube channel to sync from (used by POST /api/channels/sync)
YOUTUBE_CHANNEL_ID: str = os.environ.get("YOUTUBE_CHANNEL_ID", "")

# Content type filter for channel sync: 'all', 'video', 'short', 'live'
CHANNEL_SYNC_TYPE: str = os.environ.get("CHANNEL_SYNC_TYPE", "video")

# Server ports
BACKEND_PORT: int = 8000
FRONTEND_PORT: int = 5173
