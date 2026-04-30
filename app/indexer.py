"""Incremental indexer.

Public surface
--------------
* :meth:`Indexer.index_path` – (re)index a single note from its file.
* :meth:`Indexer.delete_path` – remove a deleted note from the indexes.
* :meth:`Indexer.move_path`   – rename within indexes (no re-embed).
* :meth:`Indexer.sync`        – walk the vault and reconcile both indexes.
* :meth:`Indexer.rebuild`     – drop derived data and re-index the vault.

The vault filesystem is the single source of truth; SQLite and the vector
store are derived. Re-embedding only happens for chunks whose
``content_hash`` actually changed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from .chunking import chunk_note
from .config import Settings
from .db import ChunkRecord, MetadataStore
from .embeddings import CachingEmbedder
from .markdown import content_hash, parse_markdown
from .paths import normalize_note_path, resolve_in_vault, to_vault_relative
from .vector_store import VectorPoint, VectorStore

log = logging.getLogger(__name__)


class Indexer:
    def __init__(
        self,
        *,
        settings: Settings,
        store: MetadataStore,
        embedder: CachingEmbedder,
        vector_store: VectorStore,
    ) -> None:
        self._settings = settings
        self._store = store
        self._embedder = embedder
        self._vectors = vector_store
        self._lock = threading.RLock()
        self._vectors.ensure_collection(self._embedder.dim)

    # ------------------------------------------------------------------
    # Single-note operations
    # ------------------------------------------------------------------
    def index_path(self, note_path: str) -> None:
        """Index (or re-index) a single note identified by its vault-relative path."""
        path = normalize_note_path(note_path)
        abs_path = resolve_in_vault(self._settings.obsidian_vault_path, path)
        if not abs_path.is_file():
            # File was removed between event and processing – treat as delete.
            self.delete_path(path)
            return

        raw = abs_path.read_text(encoding="utf-8")
        file_hash = content_hash(raw)
        stat = abs_path.stat()

        with self._lock:
            existing = self._store.get_note_by_path(path)
            if existing and existing.content_hash == file_hash:
                log.debug("index_skip_unchanged", extra={"path": path})
                return

            parsed = parse_markdown(raw)
            note = self._store.upsert_note(
                path=path,
                title=parsed.title,
                frontmatter=parsed.frontmatter,
                content_hash=file_hash,
                last_modified=stat.st_mtime,
                size_bytes=stat.st_size,
            )
            self._store.replace_tags(note.note_id, parsed.tags)
            self._store.replace_links(note.note_id, parsed.links)
            self._store.replace_headings(note.note_id, parsed.headings)

            chunks = chunk_note(parsed, self._settings)
            chunk_records: list[ChunkRecord] = []
            for ch in chunks:
                chunk_records.append(
                    ChunkRecord(
                        chunk_id=str(uuid.uuid4()),
                        note_id=note.note_id,
                        chunk_order=ch.order,
                        text=ch.text,
                        heading_path=ch.heading_path,
                        content_hash=content_hash(ch.text),
                    )
                )
            self._store.replace_chunks(note.note_id, chunk_records)

            # Refresh vectors. Cheap: cached vectors are reused; only changed
            # chunks hit the embedding provider.
            self._vectors.delete_by_note(note.note_id)
            if chunk_records:
                vectors = self._embedder.embed_with_hashes(
                    [(c.content_hash, c.text) for c in chunk_records]
                )
                points = [
                    VectorPoint(
                        id=c.chunk_id,
                        vector=vec,
                        payload={
                            "note_id": note.note_id,
                            "path": note.path,
                            "title": note.title,
                            "tags": parsed.tags,
                            "heading_path": c.heading_path,
                            "chunk_order": c.chunk_order,
                            "text": c.text,
                            "last_modified": note.last_modified,
                        },
                    )
                    for c, vec in zip(chunk_records, vectors, strict=True)
                ]
                self._vectors.upsert(points)

        log.info("index_upsert", extra={"path": path, "chunks": len(chunk_records)})

    def delete_path(self, note_path: str) -> None:
        path = normalize_note_path(note_path)
        with self._lock:
            note_id = self._store.delete_note_by_path(path)
            if note_id is None:
                return
            self._vectors.delete_by_note(note_id)
        log.info("index_delete", extra={"path": path})

    def move_path(self, from_path: str, to_path: str) -> None:
        src = normalize_note_path(from_path)
        dst = normalize_note_path(to_path)
        with self._lock:
            renamed = self._store.rename_note(src, dst)
            if renamed is None:
                # Source not in index; just index the destination.
                self.index_path(dst)
                return
            # Refresh vector payloads for the new path; vectors themselves
            # don't need recomputing because content is unchanged.
            chunks = self._store.get_chunks(renamed.note_id)
            if chunks:
                tags = self._store.get_tags(renamed.note_id)
                self._vectors.delete_by_note(renamed.note_id)
                # Embeddings are cached by content_hash so this is essentially free.
                vectors = self._embedder.embed_with_hashes(
                    [(c.content_hash, c.text) for c in chunks]
                )
                self._vectors.upsert(
                    VectorPoint(
                        id=c.chunk_id,
                        vector=vec,
                        payload={
                            "note_id": renamed.note_id,
                            "path": renamed.path,
                            "title": renamed.title,
                            "tags": tags,
                            "heading_path": c.heading_path,
                            "chunk_order": c.chunk_order,
                            "text": c.text,
                            "last_modified": renamed.last_modified,
                        },
                    )
                    for c, vec in zip(chunks, vectors, strict=True)
                )
        log.info("index_move", extra={"from": src, "to": dst})

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    def sync(self) -> dict[str, int]:
        """Reconcile indexes against the filesystem and return counts."""
        vault_root = self._settings.obsidian_vault_path
        if not vault_root.exists():
            log.warning("vault_missing", extra={"path": str(vault_root)})
            return {"upserted": 0, "deleted": 0, "skipped": 0}

        on_disk: set[str] = set()
        upserted = skipped = 0
        for abs_path in _walk_markdown(vault_root):
            try:
                rel = to_vault_relative(vault_root, abs_path)
            except Exception:
                log.exception("sync_skip_invalid_path", extra={"path": str(abs_path)})
                continue
            on_disk.add(rel)
            existing = self._store.get_note_by_path(rel)
            file_hash = content_hash(abs_path.read_text(encoding="utf-8"))
            if existing and existing.content_hash == file_hash:
                skipped += 1
                continue
            self.index_path(rel)
            upserted += 1

        # Remove notes that are no longer on disk.
        deleted = 0
        for known in self._store.list_paths():
            if known not in on_disk:
                self.delete_path(known)
                deleted += 1

        log.info("sync_done", extra={"upserted": upserted, "deleted": deleted, "skipped": skipped})
        return {"upserted": upserted, "deleted": deleted, "skipped": skipped}

    def rebuild(self) -> dict[str, int]:
        """Idempotent rebuild: drop derived data, re-walk the vault."""
        with self._lock:
            self._store.truncate_all()
            self._vectors.reset()
            self._vectors.ensure_collection(self._embedder.dim)
        return self.sync()


def _walk_markdown(root: Path) -> list[Path]:
    paths: list[Path] = []
    for p in root.rglob("*.md"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts):
            paths.append(p)
    return paths
