"""Agent-oriented endpoint aliases.

These endpoints are thin wrappers over the path-based primary endpoints
that present a stable, deterministic shape for agent tools (e.g.
OpenClaw). They exist so an agent can be wired against ``/agent/*``
without depending on HTTP-method semantics it may not natively model.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_bearer_token
from ..config import Settings
from ..db import MetadataStore
from ..deps import get_embedder, get_indexer, get_settings_dep, get_store, get_vectors
from ..embeddings import CachingEmbedder
from ..indexer import Indexer
from ..schemas import (
    ContextRequest,
    ContextResponse,
    NoteCreate,
    NoteResponse,
    NoteUpdate,
    SearchRequest,
    SearchResponse,
)
from ..vector_store import VectorStore
from . import notes as notes_router
from . import search as search_router

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    dependencies=[Depends(require_bearer_token)],
    responses={401: {"description": "Missing or invalid bearer token"}},
)


@router.post("/search-notes", response_model=SearchResponse)
async def agent_search_notes(
    body: SearchRequest,
    embedder: CachingEmbedder = get_embedder,
    vectors: VectorStore = get_vectors,
) -> SearchResponse:
    return await search_router.search(body, embedder=embedder, vectors=vectors)


@router.post("/get-context", response_model=ContextResponse)
async def agent_get_context(
    body: ContextRequest,
    embedder: CachingEmbedder = get_embedder,
    vectors: VectorStore = get_vectors,
) -> ContextResponse:
    return await search_router.context(body, embedder=embedder, vectors=vectors)


@router.post("/create-note", response_model=NoteResponse)
async def agent_create_note(
    body: NoteCreate,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    return await notes_router.create_note(body, settings=settings, store=store, indexer=indexer)


@router.post("/update-note", response_model=NoteResponse)
async def agent_update_note(
    body: NoteUpdate,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    return await notes_router.patch_note(body, settings=settings, store=store, indexer=indexer)
