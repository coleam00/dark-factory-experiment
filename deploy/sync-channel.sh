#!/usr/bin/env bash
# Run a YouTube channel sync inside the live DynaChat app container. Picks
# the active color from upstream.conf so we always exec into whichever
# container Caddy is currently routing to.
#
# Designed to be invoked from a systemd timer — see deploy/systemd/.
# Output is a single JSON line on success (parsed by journald) plus the
# usual log lines from the FastAPI app on stderr.
set -euo pipefail

UPSTREAM=/opt/dynachat/app/deploy/upstream.conf
if [ ! -f "$UPSTREAM" ]; then
    echo "[$(date -Iseconds)] $UPSTREAM not found — has the initial deploy run?" >&2
    exit 1
fi

ACTIVE=$(grep -oE 'app-(blue|green)' "$UPSTREAM" | head -1 | sed 's/app-//')
if [ -z "$ACTIVE" ]; then
    echo "[$(date -Iseconds)] could not parse active color from $UPSTREAM" >&2
    exit 1
fi

CONTAINER="dynachat-app-$ACTIVE"
echo "[$(date -Iseconds)] running channel sync inside $CONTAINER"

# `uv` may not be in $PATH inside the container; use the venv's interpreter
# directly so we don't depend on a particular install location. The repo lays
# the venv out at /app/backend/.venv (see Dockerfile).
docker exec "$CONTAINER" /app/backend/.venv/bin/python /app/backend/scripts/sync_channel.py "$@"
