"""FastAPI application factory and lifespan management.

Startup wires up:

* Obsidian Headless supervisor (best-effort; logs and continues if unavailable)
* SQLite metadata store
* Vector store (Qdrant or in-memory)
* Embedding provider (OpenAI or fake)
* Indexer
* Filesystem watcher

Shutdown stops the watcher and the Obsidian Headless supervisor, then
closes the database. Lifespan is implemented with the modern
``@asynccontextmanager`` pattern so the same app can be reused in tests.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings, get_settings
from .db import MetadataStore
from .embeddings import CachingEmbedder, build_provider
from .indexer import Indexer
from .logging_config import configure_logging
from .obsidian import ObsidianHeadlessManager
from .paths import InvalidPathError
from .routers import agent, health, index, notes, search
from .vector_store import build_vector_store
from .watcher import VaultWatcher

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(settings.log_level)

    settings.obsidian_vault_path.mkdir(parents=True, exist_ok=True)
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    obsidian = ObsidianHeadlessManager(settings)
    obsidian.start()

    store = MetadataStore(settings.sqlite_path)
    vector_store = build_vector_store(settings)
    embedder = CachingEmbedder(build_provider(settings), store)
    indexer = Indexer(
        settings=settings,
        store=store,
        embedder=embedder,
        vector_store=vector_store,
    )

    watcher: VaultWatcher | None = None
    if settings.watcher_enabled:
        watcher = VaultWatcher(settings, indexer)
        watcher.start()

    app.state.settings = settings
    app.state.metadata_store = store
    app.state.vector_store = vector_store
    app.state.embedder = embedder
    app.state.indexer = indexer
    app.state.obsidian = obsidian
    app.state.watcher = watcher

    log.info("startup_complete", extra={"version": __version__})
    try:
        yield
    finally:
        if watcher is not None:
            watcher.stop()
        obsidian.stop()
        store.close()
        log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="obsidian-rag-api",
        version=__version__,
        description=(
            "Dockerized RAG API on top of an Obsidian vault. Notes are identified "
            "by their vault-relative path (e.g. 'Projects/Test.md')."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(notes.router)
    app.include_router(search.router)
    app.include_router(index.router)
    app.include_router(agent.router)

    @app.exception_handler(InvalidPathError)
    async def _invalid_path_handler(_request: Request, exc: InvalidPathError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        # Pass-through, but ensure no upstream exception text leaks secrets.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers or None,
        )

    return app


app = create_app()


def run() -> None:  # pragma: no cover - thin wrapper around uvicorn
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
