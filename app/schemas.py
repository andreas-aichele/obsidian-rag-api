"""Pydantic request/response schemas.

Schemas are deterministic and stable: field names match the API contract
described in the README and never expose internal IDs unless explicitly
documented as such.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    vector_backend: str
    embedding_provider: str


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Vault-relative path, must end with '.md'")
    title: str | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    content: str = ""


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    title: str | None = None
    frontmatter: dict[str, Any] | None = None
    content: str | None = None


class NoteMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_path: str
    to_path: str


class NoteResponse(BaseModel):
    path: str
    title: str | None
    frontmatter: dict[str, Any]
    content: str
    tags: list[str]
    headings: list[str]
    links: list[str]
    backlinks: list[str]
    last_modified: float
    content_hash: str
    note_id: str = Field(..., description="Internal ID; do not rely on this for API operations")


# ---------------------------------------------------------------------------
# Search / RAG
# ---------------------------------------------------------------------------
class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    filters: SearchFilters | None = None


class SearchHit(BaseModel):
    score: float
    path: str
    title: str | None
    chunk_text: str
    heading_context: list[str]
    tags: list[str]
    last_modified: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    top_k: int = Field(default=8, ge=1, le=50)
    filters: SearchFilters | None = None
    max_chars: int = Field(default=8000, ge=200, le=64000)


class ContextResponse(BaseModel):
    query: str
    context: str
    sources: list[SearchHit]


# ---------------------------------------------------------------------------
# Index admin
# ---------------------------------------------------------------------------
class IndexOpResponse(BaseModel):
    upserted: int
    deleted: int
    skipped: int
