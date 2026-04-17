# syntax=docker/dockerfile:1.7
#
# DynaChat single-container image.
#   Stage 1: build the frontend with bun.
#   Stage 2: python runtime that serves FastAPI + the built frontend.
#
# Build context is the repo root (see deploy/docker-compose.yml → build.context: ..).

# ----------------------------------------------------------------------------
# Stage 1 — frontend build
# ----------------------------------------------------------------------------
FROM oven/bun:1.2-alpine AS frontend-build

WORKDIR /src/frontend

# Install deps first for better layer caching.
COPY app/frontend/package.json app/frontend/bun.lock ./
RUN bun install --frozen-lockfile

# Now bring in the source and build.
COPY app/frontend/ ./
RUN bun run build
# Output: /src/frontend/dist

# ----------------------------------------------------------------------------
# Stage 2 — backend runtime
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Pull in the uv binary (pinned).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Minimal runtime deps. curl isn't strictly required (healthcheck uses urllib)
# but is useful for in-container debugging.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. Create /app ownership BEFORE volume mounts so the named
# volume inherits correct ownership on first creation.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home /app app

WORKDIR /app

# Install Python deps first (separate layer for cache).
COPY --chown=app:app app/backend/pyproject.toml app/backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev

# Copy backend source.
COPY --chown=app:app app/backend/ ./backend/

# Pull the built frontend from stage 1 into /app/frontend/dist.
COPY --from=frontend-build --chown=app:app /src/frontend/dist /app/frontend/dist

# Data dir for SQLite (mounted as a named volume in compose so blue/green
# swaps preserve state).
RUN mkdir -p /app/data && chown -R app:app /app

USER app

# Container serves FastAPI which in turn serves /api + static frontend.
ENV DB_PATH=/app/data/chat.db \
    FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Healthcheck hits /api/health (which does a DB query, so it validates the
# app is truly ready — not just that the process started).
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)" || exit 1

# Run from /app so the `backend.main:app` module path resolves.
CMD ["uv", "--project", "backend", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
