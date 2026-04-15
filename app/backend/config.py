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
    """Search parent directories for .env and load it."""
    current = Path(__file__).resolve()
    # Try each parent directory up to the filesystem root
    for parent in current.parents:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            logger.info(f"Loaded .env from {candidate}")
            return
    # Fallback: try the standard absolute path mentioned in the spec
    fallback = Path(__file__).resolve().parents[4] / ".env"
    if fallback.exists():
        load_dotenv(dotenv_path=fallback, override=False)
        logger.info(f"Loaded .env from fallback {fallback}")
    else:
        print("WARNING: No .env file found in parent directories.", file=sys.stderr)


_find_and_load_env()

# Expose configuration constants
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    print(
        "WARNING: OPENROUTER_API_KEY is not set or empty. "
        "Embedding and LLM features will not work.",
        file=sys.stderr,
    )

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
CHAT_MODEL: str = "anthropic/claude-sonnet-4.6"

# Database
DB_PATH: Path = Path(__file__).resolve().parent / "data" / "chat.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# RAG settings
RETRIEVAL_TOP_K: int = 5
HYBRID_CHUNKER_MAX_TOKENS: int = 512

# Server ports
BACKEND_PORT: int = 8000
FRONTEND_PORT: int = 5173

# CORS — comma-separated list of allowed origins
# Default includes localhost variants at the configured frontend port
_cors_raw: str = os.environ.get(
    "CORS_ORIGINS",
    f"http://localhost:{FRONTEND_PORT},http://127.0.0.1:{FRONTEND_PORT}",
)
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]
