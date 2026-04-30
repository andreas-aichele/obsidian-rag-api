"""FastAPI dependency wiring.

Application-wide singletons (settings, metadata store, vector store,
embedder, indexer) are stored on ``app.state`` during the lifespan
context. The ``Depends`` helpers in this module pull them off ``request``.
"""

from __future__ import annotations

from fastapi import Depends, Request

from .config import Settings, get_settings
from .db import MetadataStore
from .embeddings import CachingEmbedder
from .indexer import Indexer
from .vector_store import VectorStore


def settings_dep() -> Settings:
    return get_settings()


def store_dep(request: Request) -> MetadataStore:
    return request.app.state.metadata_store


def vectors_dep(request: Request) -> VectorStore:
    return request.app.state.vector_store


def embedder_dep(request: Request) -> CachingEmbedder:
    return request.app.state.embedder


def indexer_dep(request: Request) -> Indexer:
    return request.app.state.indexer


# Re-exported names so routers can write `Depends(get_indexer)` etc.
get_settings_dep = Depends(settings_dep)
get_store = Depends(store_dep)
get_vectors = Depends(vectors_dep)
get_embedder = Depends(embedder_dep)
get_indexer = Depends(indexer_dep)
