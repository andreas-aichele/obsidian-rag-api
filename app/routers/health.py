"""Health endpoint (unauthenticated)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import Settings, get_settings
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


_settings_dep_singleton = Depends(get_settings)


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = _settings_dep_singleton) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        vector_backend=settings.vector_backend,
        embedding_provider=settings.embedding_provider,
    )
