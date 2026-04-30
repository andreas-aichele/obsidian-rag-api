"""Search and RAG context endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_bearer_token
from ..deps import get_embedder, get_vectors
from ..embeddings import CachingEmbedder
from ..markdown import content_hash
from ..schemas import (
    ContextRequest,
    ContextResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from ..vector_store import VectorStore

router = APIRouter(
    tags=["search"],
    dependencies=[Depends(require_bearer_token)],
    responses={401: {"description": "Missing or invalid bearer token"}},
)


def _run_search(
    *,
    query: str,
    top_k: int,
    filters_dict: dict | None,
    embedder: CachingEmbedder,
    vectors: VectorStore,
) -> list[SearchHit]:
    qvec = embedder.embed_with_hashes([(content_hash(query), query)])[0]
    matches = vectors.search(qvec, top_k=top_k, filters=filters_dict)
    return [
        SearchHit(
            score=m.score,
            path=m.payload.get("path", ""),
            title=m.payload.get("title"),
            chunk_text=m.payload.get("text", ""),
            heading_context=list(m.payload.get("heading_path") or []),
            tags=list(m.payload.get("tags") or []),
            last_modified=float(m.payload.get("last_modified") or 0.0),
        )
        for m in matches
    ]


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    embedder: CachingEmbedder = get_embedder,
    vectors: VectorStore = get_vectors,
) -> SearchResponse:
    filters_dict = body.filters.model_dump() if body.filters else None
    hits = _run_search(
        query=body.query,
        top_k=body.top_k,
        filters_dict=filters_dict,
        embedder=embedder,
        vectors=vectors,
    )
    return SearchResponse(query=body.query, hits=hits)


@router.post("/context", response_model=ContextResponse)
async def context(
    body: ContextRequest,
    embedder: CachingEmbedder = get_embedder,
    vectors: VectorStore = get_vectors,
) -> ContextResponse:
    filters_dict = body.filters.model_dump() if body.filters else None
    hits = _run_search(
        query=body.query,
        top_k=body.top_k,
        filters_dict=filters_dict,
        embedder=embedder,
        vectors=vectors,
    )
    blocks: list[str] = []
    total = 0
    used: list[SearchHit] = []
    for hit in hits:
        header = f"## {hit.path}"
        if hit.heading_context:
            header += " — " + " › ".join(hit.heading_context)
        block = f"{header}\n{hit.chunk_text.strip()}"
        if total + len(block) > body.max_chars and used:
            break
        blocks.append(block)
        used.append(hit)
        total += len(block) + 2
    return ContextResponse(query=body.query, context="\n\n".join(blocks), sources=used)
