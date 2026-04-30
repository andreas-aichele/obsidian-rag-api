"""Bearer-token authentication.

A single static token (``API_TOKEN``) protects all non-health endpoints.
The dependency uses :func:`hmac.compare_digest` for constant-time
comparison and never includes the token in error responses or logs.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings


def _extract_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": 'Bearer realm="obsidian-rag-api"'},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": 'Bearer realm="obsidian-rag-api"'},
        )
    return token.strip()


_settings_dep = Depends(get_settings)


async def require_bearer_token(
    authorization: str | None = Header(default=None),
    settings: Settings = _settings_dep,
) -> None:
    """FastAPI dependency that enforces ``Authorization: Bearer <API_TOKEN>``."""
    token = _extract_token(authorization)
    expected = settings.api_token
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": 'Bearer realm="obsidian-rag-api"'},
        )
