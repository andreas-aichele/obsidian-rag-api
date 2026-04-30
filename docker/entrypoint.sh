#!/usr/bin/env sh
# Container entrypoint.
#
# Boot order:
#   1. Ensure /vault and /data exist (mounted as volumes in production).
#   2. Hand off to the FastAPI app, which itself supervises:
#        - the Obsidian Headless sync process (if installed and configured),
#        - the filesystem watcher,
#        - the indexer.
#
# We deliberately let the Python process own the Obsidian Headless lifecycle
# so that crashes are restarted with backoff and shutdowns are graceful.

set -eu

mkdir -p "${OBSIDIAN_VAULT_PATH:-/vault}"
mkdir -p "$(dirname "${SQLITE_PATH:-/data/obsidian-rag-api.sqlite}")"

exec uvicorn app.main:app \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    --log-config /dev/null \
    --no-access-log
