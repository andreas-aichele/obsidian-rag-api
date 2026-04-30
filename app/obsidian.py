"""Obsidian Headless process management.

The Obsidian Headless container/binary syncs a remote Obsidian vault into
a local directory using the user's account credentials. This module starts
that process from inside our application container, restarts it on crash,
and stops it cleanly on shutdown.

The actual Obsidian Headless binary is not vendored with this repository
(it is provided at deploy time via the Docker image). To accommodate
environments where the binary is unavailable (CI, tests, local dev with a
manually-populated vault), the manager can be disabled via the
``OBSIDIAN_HEADLESS_ENABLED`` environment variable, and falls back to a
no-op if no binary is on ``PATH``.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time

from .config import Settings

log = logging.getLogger(__name__)

# Candidate executable names the manager will look for.
_CANDIDATES = ("obsidian-headless", "obsidian", "obsidian-sync")
_RESTART_BACKOFF_SECONDS = 5.0


class ObsidianHeadlessManager:
    """Supervises an external Obsidian Headless sync process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._proc: subprocess.Popen[bytes] | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def _resolve_binary(self) -> str | None:
        for name in _CANDIDATES:
            path = shutil.which(name)
            if path:
                return path
        return None

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Pass credentials through the environment; never log them.
        env["OBSIDIAN_EMAIL"] = self._settings.obsidian_email
        env["OBSIDIAN_PASSWORD"] = self._settings.obsidian_password
        env["OBSIDIAN_VAULT_NAME"] = self._settings.obsidian_vault_name
        env["OBSIDIAN_VAULT_PATH"] = str(self._settings.obsidian_vault_path)
        return env

    def _spawn(self, binary: str) -> subprocess.Popen[bytes]:
        # The exact CLI shape varies between Obsidian Headless distributions;
        # most accept env-based config with no positional args.
        return subprocess.Popen(  # noqa: S603 - inputs are env-controlled
            [binary],
            env=self._build_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )

    def _supervise(self, binary: str) -> None:
        while not self._stop_event.is_set():
            try:
                self._proc = self._spawn(binary)
                log.info(
                    "obsidian_headless_started",
                    extra={"pid": self._proc.pid, "vault": self._settings.obsidian_vault_name},
                )
                rc = self._proc.wait()
                log.warning("obsidian_headless_exited", extra={"returncode": rc})
            except FileNotFoundError:
                log.error("obsidian_headless_binary_missing")
                return
            except Exception:
                log.exception("obsidian_headless_supervise_error")
            if self._stop_event.is_set():
                break
            time.sleep(_RESTART_BACKOFF_SECONDS)

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._settings.obsidian_headless_enabled:
            log.info("obsidian_headless_disabled")
            return
        if not (self._settings.obsidian_email and self._settings.obsidian_password
                and self._settings.obsidian_vault_name):
            log.warning("obsidian_headless_credentials_missing")
            return
        binary = self._resolve_binary()
        if binary is None:
            log.warning("obsidian_headless_no_binary_on_path", extra={"candidates": _CANDIDATES})
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._supervise, args=(binary,), daemon=True, name="obsidian-headless"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    log.exception("obsidian_headless_kill_failed")
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        log.info("obsidian_headless_stopped")
