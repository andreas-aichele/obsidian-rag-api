"""Shared test fixtures.

The fixtures here build a fully-wired FastAPI app against a temporary
vault, an in-memory vector store, and the deterministic ``fake``
embedding provider, so the test suite runs offline with no Docker.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    return d


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def api_token() -> str:
    return "test-token-123"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, vault_dir: Path, data_dir: Path, api_token: str) -> None:
    """Configure environment variables for an isolated, offline test run."""
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("SQLITE_PATH", str(data_dir / "test.sqlite"))
    monkeypatch.setenv("API_TOKEN", api_token)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_DIM", "32")
    monkeypatch.setenv("VECTOR_BACKEND", "memory")
    monkeypatch.setenv("OBSIDIAN_HEADLESS_ENABLED", "false")
    monkeypatch.setenv("WATCHER_ENABLED", "false")
    monkeypatch.setenv("LOG_LEVEL", "warning")

    # Ensure no .env from CWD leaks in.
    monkeypatch.chdir(data_dir.parent)

    # Reset settings + app module so new env is picked up.
    from app import config as cfg

    cfg.reset_settings_cache()


@pytest.fixture
def client(env: None) -> Iterator[TestClient]:
    # Import after env is set.
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(api_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token}"}
