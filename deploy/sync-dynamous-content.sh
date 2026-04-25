#!/usr/bin/env bash
# sync-dynamous-content.sh
#
# Pull the latest paid Dynamous course/workshop transcripts from the private
# coleam00/dynamous-content repo into the host directory that docker-compose
# mounts into the app containers (issue #147).
#
# Idempotent: clones on first run, pulls on subsequent runs. The container
# ingester (backend.ingest.dynamous) SHA-checks each markdown file so unchanged
# files are no-ops.
#
# Run this from the host's deploy.sh BEFORE bringing up the new color so the
# fresh container sees the latest content on its first health check.
#
# Required env (loaded from /opt/dynachat/.env in prod):
#   DYNAMOUS_CONTENT_DEPLOY_KEY_PATH  — path to the SSH private key (deploy
#                                        key on the dynamous-content repo)
#   DYNAMOUS_CONTENT_HOST_PATH        — local checkout dir (defaults to
#                                        /opt/dynachat/dynamous-content)

set -euo pipefail

KEY_PATH="${DYNAMOUS_CONTENT_DEPLOY_KEY_PATH:-/opt/dynachat/secrets/dynamous-content-deploy}"
DIR="${DYNAMOUS_CONTENT_HOST_PATH:-/opt/dynachat/dynamous-content}"
REPO="git@github.com:coleam00/dynamous-content.git"

if [ ! -f "$KEY_PATH" ]; then
    echo "WARN: deploy key missing at $KEY_PATH; skipping dynamous-content sync"
    exit 0
fi

# `-o IdentitiesOnly=yes` stops ssh from trying any other agent-loaded keys
# first (which would 403 against this repo's deploy key allowlist and could
# trigger GitHub's rate limit on bad attempts).
export GIT_SSH_COMMAND="ssh -i $KEY_PATH -o StrictHostKeyChecking=no -o IdentitiesOnly=yes"

if [ -d "$DIR/.git" ]; then
    echo "Pulling dynamous-content into $DIR"
    git -C "$DIR" fetch --depth 1 origin main
    git -C "$DIR" reset --hard origin/main
else
    echo "Cloning dynamous-content into $DIR"
    mkdir -p "$(dirname "$DIR")"
    git clone --depth 1 "$REPO" "$DIR"
fi

echo "Sync complete: $DIR ($(find "$DIR" -name '*.md' | wc -l) markdown files)"
