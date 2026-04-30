"""Obsidian Headless process management.

Wraps the official Obsidian Headless CLI (``ob``, shipped by the
``obsidian-headless`` npm package — https://github.com/obsidianmd/obsidian-headless),
which logs in to an Obsidian account and syncs a remote vault to a local
directory. This module logs in, sets up the local vault for sync (once),
and then supervises the long-running ``ob sync --continuous`` process,
restarting it on crash and stopping it cleanly on shutdown.

The Docker image installs ``ob`` by default, but the manager also gracefully
no-ops when the binary is absent (CI, tests, locally-populated vaults) or
when ``OBSIDIAN_HEADLESS_ENABLED`` is false. For backward compatibility with
custom images, bare ``obsidian-headless`` / ``obsidian`` / ``obsidian-sync``
binaries on ``PATH`` are still detected and invoked with env-only config.
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

# Candidate executable names the manager will look for, in priority order.
# ``ob`` is the official Obsidian Inc. CLI shipped via the ``obsidian-headless``
# npm package; the others are kept for backward compatibility with custom
# images that ship a pre-built binary under a different name.
_CANDIDATES = ("ob", "obsidian-headless", "obsidian", "obsidian-sync")
_RESTART_BACKOFF_SECONDS = 5.0
_LOGIN_TIMEOUT_SECONDS = 60
_SETUP_TIMEOUT_SECONDS = 120
_STATUS_TIMEOUT_SECONDS = 30


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

    @staticmethod
    def _is_ob_cli(binary: str) -> bool:
        return os.path.basename(binary) == "ob"

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Pass credentials through the environment as well; never log them.
        # The ``ob`` CLI doesn't read these directly (it uses flags + a
        # credential store under $HOME), but custom binaries may.
        env["OBSIDIAN_EMAIL"] = self._settings.obsidian_email
        env["OBSIDIAN_PASSWORD"] = self._settings.obsidian_password
        env["OBSIDIAN_VAULT_NAME"] = self._settings.obsidian_vault_name
        env["OBSIDIAN_VAULT_PATH"] = str(self._settings.obsidian_vault_path)
        return env

    def _setup_ob(self, binary: str) -> bool:
        """Idempotent ``ob login`` + ``ob sync-setup`` for the official CLI.

        Returns ``True`` if the vault is configured for continuous sync.
        Credentials are passed via flags; this is acceptable in a container
        where ``/proc`` is only visible to the same user, but should not be
        used on shared hosts.
        """
        env = self._build_env()
        vault_path = str(self._settings.obsidian_vault_path)

        # Login is idempotent: if already logged in to the same account, it
        # is a no-op; otherwise it (re-)authenticates.
        try:
            subprocess.run(  # noqa: S603 - inputs are env-controlled
                [
                    binary, "login",
                    "--email", self._settings.obsidian_email,
                    "--password", self._settings.obsidian_password,
                ],
                check=True,
                timeout=_LOGIN_TIMEOUT_SECONDS,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("obsidian_headless_login_ok")
        except Exception:
            log.exception("obsidian_headless_login_failed")
            return False

        # If the vault is already set up for sync at this path, sync-status
        # exits 0; otherwise we run sync-setup.
        try:
            status = subprocess.run(  # noqa: S603 - inputs are env-controlled
                [binary, "sync-status", "--path", vault_path],
                env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_STATUS_TIMEOUT_SECONDS,
            )
            already_configured = status.returncode == 0
        except Exception:
            log.exception("obsidian_headless_sync_status_failed")
            already_configured = False

        if not already_configured:
            try:
                subprocess.run(  # noqa: S603 - inputs are env-controlled
                    [
                        binary, "sync-setup",
                        "--vault", self._settings.obsidian_vault_name,
                        "--path", vault_path,
                    ],
                    check=True,
                    timeout=_SETUP_TIMEOUT_SECONDS,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("obsidian_headless_sync_setup_ok",
                         extra={"vault": self._settings.obsidian_vault_name})
            except Exception:
                log.exception("obsidian_headless_sync_setup_failed")
                return False
        return True

    def _spawn(self, binary: str) -> subprocess.Popen[bytes]:
        if self._is_ob_cli(binary):
            argv = [
                binary, "sync", "--continuous",
                "--path", str(self._settings.obsidian_vault_path),
            ]
        else:
            # Legacy/custom binaries: env-based config, no positional args.
            argv = [binary]
        return subprocess.Popen(  # noqa: S603 - inputs are env-controlled
            argv,
            env=self._build_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )

    def _supervise(self, binary: str) -> None:
        if self._is_ob_cli(binary) and not self._setup_ob(binary):
            # Setup failed and we can't proceed; the rest of the service
            # continues to run against whatever is already on disk.
            return
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
