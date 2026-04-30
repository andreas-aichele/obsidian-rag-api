"""Filesystem watcher that streams vault changes into the indexer.

The watcher debounces rapid bursts of events (e.g. editor saves that
trigger create+modify+rename in quick succession) and batches them per
absolute path. All exceptions are caught and logged so the watcher
thread never dies silently.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import Settings
from .indexer import Indexer
from .paths import InvalidPathError, to_vault_relative

log = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 0.5


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, vault_root: Path, indexer: Indexer) -> None:
        self._vault_root = vault_root
        self._indexer = indexer
        self._lock = threading.Lock()
        self._pending: dict[Path, tuple[str, float]] = {}
        self._timer: threading.Timer | None = None

    # --- watchdog callbacks --------------------------------------------------
    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path, "upsert")

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path, "upsert")

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        # Treat a move as delete-of-source + upsert-of-dest. The indexer
        # tolerates either ordering.
        self._enqueue(event.src_path, "delete")
        dest = getattr(event, "dest_path", None)
        if dest:
            self._enqueue(dest, "upsert")

    # ------------------------------------------------------------------------
    def _enqueue(self, raw_path: str, action: str) -> None:
        if not raw_path or not raw_path.endswith(".md"):
            return
        path = Path(raw_path)
        with self._lock:
            self._pending[path] = (action, time.time())
            if self._timer is None or not self._timer.is_alive():
                self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            batch = self._pending
            self._pending = {}
            self._timer = None
        for path, (action, _ts) in batch.items():
            try:
                rel = self._safe_relative(path)
                if rel is None:
                    continue
                if action == "delete":
                    self._indexer.delete_path(rel)
                else:
                    self._indexer.index_path(rel)
            except Exception:
                log.exception("watcher_handle_failed", extra={"path": str(path)})

    def _safe_relative(self, path: Path) -> str | None:
        try:
            return to_vault_relative(self._vault_root, path)
        except (InvalidPathError, ValueError):
            return None


class VaultWatcher:
    """Convenience wrapper around watchdog's :class:`Observer`."""

    def __init__(self, settings: Settings, indexer: Indexer) -> None:
        self._settings = settings
        self._indexer = indexer
        self._observer: Observer | None = None

    def start(self) -> None:
        vault = self._settings.obsidian_vault_path
        vault.mkdir(parents=True, exist_ok=True)
        handler = _VaultEventHandler(vault, self._indexer)
        observer = Observer()
        observer.schedule(handler, str(vault), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("watcher_started", extra={"path": str(vault)})

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            log.info("watcher_stopped")
