"""Path-based notes CRUD + move."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import require_bearer_token
from ..config import Settings
from ..db import MetadataStore
from ..deps import get_indexer, get_settings_dep, get_store
from ..indexer import Indexer
from ..markdown import parse_markdown, render_markdown
from ..paths import InvalidPathError, normalize_note_path, resolve_in_vault
from ..schemas import NoteCreate, NoteMove, NoteResponse, NoteUpdate

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notes",
    tags=["notes"],
    dependencies=[Depends(require_bearer_token)],
    responses={401: {"description": "Missing or invalid bearer token"}},
)


def _validate_path(path: str) -> str:
    try:
        return normalize_note_path(path)
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _build_response(
    *,
    settings: Settings,
    store: MetadataStore,
    path: str,
) -> NoteResponse:
    record = store.get_note_by_path(path)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    abs_path = resolve_in_vault(settings.obsidian_vault_path, path)
    raw = abs_path.read_text(encoding="utf-8") if abs_path.is_file() else ""
    parsed = parse_markdown(raw)
    return NoteResponse(
        path=record.path,
        title=record.title,
        frontmatter=record.frontmatter,
        content=parsed.body,
        tags=store.get_tags(record.note_id),
        headings=store.get_headings(record.note_id),
        links=store.get_links(record.note_id),
        backlinks=store.get_backlinks(record.path),
        last_modified=record.last_modified,
        content_hash=record.content_hash,
        note_id=record.note_id,
    )


@router.get("/by-path", response_model=NoteResponse)
async def get_note(
    path: str = Query(..., description="Vault-relative note path"),
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
) -> NoteResponse:
    path = _validate_path(path)
    return _build_response(settings=settings, store=store, path=path)


def _write_note(
    *,
    settings: Settings,
    indexer: Indexer,
    path: str,
    frontmatter: dict | None,
    content: str,
    title: str | None,
) -> None:
    abs_path = resolve_in_vault(settings.obsidian_vault_path, path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    fm = dict(frontmatter or {})
    if title is not None:
        fm["title"] = title

    rendered = render_markdown(fm or None, content)
    # Atomic write: tmp + replace.
    tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(abs_path)

    indexer.index_path(path)


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreate,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    path = _validate_path(body.path)
    abs_path = resolve_in_vault(settings.obsidian_vault_path, path)
    if abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Note already exists"
        )
    _write_note(
        settings=settings,
        indexer=indexer,
        path=path,
        frontmatter=body.frontmatter,
        content=body.content,
        title=body.title,
    )
    return _build_response(settings=settings, store=store, path=path)


@router.put("/by-path", response_model=NoteResponse)
async def replace_note(
    body: NoteUpdate,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    path = _validate_path(body.path)
    _write_note(
        settings=settings,
        indexer=indexer,
        path=path,
        frontmatter=body.frontmatter or {},
        content=body.content or "",
        title=body.title,
    )
    return _build_response(settings=settings, store=store, path=path)


@router.patch("/by-path", response_model=NoteResponse)
async def patch_note(
    body: NoteUpdate,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    path = _validate_path(body.path)
    abs_path = resolve_in_vault(settings.obsidian_vault_path, path)
    if not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    raw = abs_path.read_text(encoding="utf-8")
    existing = parse_markdown(raw)

    new_fm = dict(existing.frontmatter)
    if body.frontmatter is not None:
        new_fm.update(body.frontmatter)
    if body.title is not None:
        new_fm["title"] = body.title

    new_body = body.content if body.content is not None else existing.body

    _write_note(
        settings=settings,
        indexer=indexer,
        path=path,
        frontmatter=new_fm,
        content=new_body,
        title=None,  # already merged into new_fm
    )
    return _build_response(settings=settings, store=store, path=path)


@router.delete("/by-path", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_note(
    path: str = Query(...),
    settings: Settings = get_settings_dep,
    indexer: Indexer = get_indexer,
) -> None:
    path = _validate_path(path)
    abs_path = resolve_in_vault(settings.obsidian_vault_path, path)
    if not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    abs_path.unlink()
    indexer.delete_path(path)


@router.post("/move", response_model=NoteResponse)
async def move_note(
    body: NoteMove,
    settings: Settings = get_settings_dep,
    store: MetadataStore = get_store,
    indexer: Indexer = get_indexer,
) -> NoteResponse:
    src = _validate_path(body.from_path)
    dst = _validate_path(body.to_path)
    if src == dst:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_path and to_path must differ",
        )
    src_abs = resolve_in_vault(settings.obsidian_vault_path, src)
    dst_abs = resolve_in_vault(settings.obsidian_vault_path, dst)
    if not src_abs.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source note not found")
    if dst_abs.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Destination already exists"
        )
    dst_abs.parent.mkdir(parents=True, exist_ok=True)
    src_abs.replace(dst_abs)
    indexer.move_path(src, dst)
    # Belt-and-braces: ensure the destination is fully indexed even if move_path
    # had to fall back (e.g. source was missing from the index).
    indexer.index_path(dst)
    return _build_response(settings=settings, store=store, path=dst)
