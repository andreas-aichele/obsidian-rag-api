"""Index admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_bearer_token
from ..deps import get_indexer
from ..indexer import Indexer
from ..schemas import IndexOpResponse

router = APIRouter(
    prefix="/index",
    tags=["index"],
    dependencies=[Depends(require_bearer_token)],
    responses={401: {"description": "Missing or invalid bearer token"}},
)


@router.post("/sync", response_model=IndexOpResponse)
async def sync(indexer: Indexer = get_indexer) -> IndexOpResponse:
    counts = indexer.sync()
    return IndexOpResponse(**counts)


@router.post("/rebuild", response_model=IndexOpResponse)
async def rebuild(indexer: Indexer = get_indexer) -> IndexOpResponse:
    counts = indexer.rebuild()
    return IndexOpResponse(**counts)
