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
#
# We invoke the app via ``python -m app.main`` (which calls ``app.main.run``)
# so that uvicorn is configured programmatically with ``log_config=None`` and
# ``access_log=False``. This lets the app's own ``configure_logging()`` install
# the JSON formatter without uvicorn first overwriting it from a config file.

set -eu

mkdir -p "${OBSIDIAN_VAULT_PATH:-/vault}"
mkdir -p "$(dirname "${SQLITE_PATH:-/data/obsidian-rag-api.sqlite}")"

exec python -m app.main
