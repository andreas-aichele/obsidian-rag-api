"""SQLite metadata store.

Stores one row per note plus auxiliary rows for chunks, tags, links and an
embedding cache keyed by ``(provider, model, content_hash)``.

The vault filesystem is the *single source of truth*; everything in this
database can be rebuilt from disk via :meth:`MetadataStore.rebuild`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id        TEXT PRIMARY KEY,
    path           TEXT NOT NULL UNIQUE,
    title          TEXT,
    frontmatter    TEXT NOT NULL DEFAULT '{}',
    content_hash   TEXT NOT NULL,
    last_modified  REAL NOT NULL,
    size_bytes     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    note_id         TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    chunk_order     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    heading_path    TEXT NOT NULL DEFAULT '[]',
    content_hash    TEXT NOT NULL,
    UNIQUE(note_id, chunk_order)
);
CREATE INDEX IF NOT EXISTS idx_chunks_note ON chunks(note_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash);

CREATE TABLE IF NOT EXISTS tags (
    note_id  TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE TABLE IF NOT EXISTS links (
    note_id  TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    target   TEXT NOT NULL,
    PRIMARY KEY (note_id, target)
);
CREATE INDEX IF NOT EXISTS idx_links_target ON links(target);

CREATE TABLE IF NOT EXISTS headings (
    note_id  TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text     TEXT NOT NULL,
    PRIMARY KEY (note_id, position)
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector       BLOB NOT NULL,
    PRIMARY KEY (provider, model, content_hash)
);
"""


@dataclass
class NoteRecord:
    note_id: str
    path: str
    title: str | None
    frontmatter: dict[str, Any]
    content_hash: str
    last_modified: float
    size_bytes: int


@dataclass
class ChunkRecord:
    chunk_id: str
    note_id: str
    chunk_order: int
    text: str
    heading_path: list[str]
    content_hash: str


class MetadataStore:
    """Thread-safe SQLite-backed metadata store."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Transaction helper
    # ------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Note CRUD
    # ------------------------------------------------------------------
    def get_note_by_path(self, path: str) -> NoteRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM notes WHERE path = ?", (path,)
            ).fetchone()
        return _row_to_note(row) if row else None

    def list_paths(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT path FROM notes ORDER BY path").fetchall()
        return [r["path"] for r in rows]

    def upsert_note(
        self,
        *,
        path: str,
        title: str | None,
        frontmatter: dict[str, Any],
        content_hash: str,
        last_modified: float,
        size_bytes: int,
    ) -> NoteRecord:
        with self.transaction() as conn:
            row = conn.execute("SELECT note_id FROM notes WHERE path = ?", (path,)).fetchone()
            if row is None:
                note_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO notes
                       (note_id, path, title, frontmatter, content_hash, last_modified, size_bytes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (note_id, path, title, json.dumps(frontmatter), content_hash,
                     last_modified, size_bytes),
                )
            else:
                note_id = row["note_id"]
                conn.execute(
                    """UPDATE notes
                       SET title=?, frontmatter=?, content_hash=?, last_modified=?, size_bytes=?
                       WHERE note_id=?""",
                    (title, json.dumps(frontmatter), content_hash, last_modified,
                     size_bytes, note_id),
                )
        return NoteRecord(
            note_id=note_id,
            path=path,
            title=title,
            frontmatter=frontmatter,
            content_hash=content_hash,
            last_modified=last_modified,
            size_bytes=size_bytes,
        )

    def rename_note(self, from_path: str, to_path: str) -> NoteRecord | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM notes WHERE path = ?", (from_path,)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE notes SET path=? WHERE note_id=?", (to_path, row["note_id"]))
            row = conn.execute("SELECT * FROM notes WHERE note_id=?", (row["note_id"],)).fetchone()
        return _row_to_note(row)

    def delete_note_by_path(self, path: str) -> str | None:
        """Delete the note row and return its note_id (for vector cleanup)."""
        with self.transaction() as conn:
            row = conn.execute("SELECT note_id FROM notes WHERE path = ?", (path,)).fetchone()
            if row is None:
                return None
            note_id = row["note_id"]
            conn.execute("DELETE FROM notes WHERE note_id = ?", (note_id,))
        return note_id

    # ------------------------------------------------------------------
    # Chunks / tags / links / headings
    # ------------------------------------------------------------------
    def replace_chunks(self, note_id: str, chunks: list[ChunkRecord]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))
            conn.executemany(
                """INSERT INTO chunks
                   (chunk_id, note_id, chunk_order, text, heading_path, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (c.chunk_id, note_id, c.chunk_order, c.text,
                     json.dumps(c.heading_path), c.content_hash)
                    for c in chunks
                ],
            )

    def get_chunks(self, note_id: str) -> list[ChunkRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE note_id=? ORDER BY chunk_order",
                (note_id,),
            ).fetchall()
        return [_row_to_chunk(r) for r in rows]

    def replace_tags(self, note_id: str, tags: list[str]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM tags WHERE note_id=?", (note_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?, ?)",
                [(note_id, t) for t in tags],
            )

    def get_tags(self, note_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (note_id,)
            ).fetchall()
        return [r["tag"] for r in rows]

    def replace_links(self, note_id: str, links: list[str]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM links WHERE note_id=?", (note_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO links (note_id, target) VALUES (?, ?)",
                [(note_id, t) for t in links],
            )

    def get_links(self, note_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT target FROM links WHERE note_id=? ORDER BY target", (note_id,)
            ).fetchall()
        return [r["target"] for r in rows]

    def get_backlinks(self, path_or_basename: str) -> list[str]:
        """Return the paths of notes whose outgoing links match the given target.

        Obsidian wikilinks omit the ``.md`` extension and may use either the
        full vault path or just the basename. We try both.
        """
        target = path_or_basename
        bare = target[:-3] if target.lower().endswith(".md") else target
        basename = bare.rsplit("/", 1)[-1]
        with self._lock:
            rows = self._conn.execute(
                """SELECT DISTINCT n.path FROM links l
                     JOIN notes n ON n.note_id = l.note_id
                    WHERE l.target IN (?, ?, ?)
                    ORDER BY n.path""",
                (bare, basename, target),
            ).fetchall()
        return [r["path"] for r in rows]

    def replace_headings(self, note_id: str, headings: list[str]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM headings WHERE note_id=?", (note_id,))
            conn.executemany(
                "INSERT INTO headings (note_id, position, text) VALUES (?, ?, ?)",
                [(note_id, i, h) for i, h in enumerate(headings)],
            )

    def get_headings(self, note_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM headings WHERE note_id=? ORDER BY position", (note_id,)
            ).fetchall()
        return [r["text"] for r in rows]

    # ------------------------------------------------------------------
    # Embedding cache
    # ------------------------------------------------------------------
    def get_cached_embedding(
        self, provider: str, model: str, content_hash: str
    ) -> list[float] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT vector FROM embedding_cache WHERE provider=? AND model=? AND content_hash=?",
                (provider, model, content_hash),
            ).fetchone()
        if row is None:
            return None
        return _deserialize_vector(row["vector"])

    def put_cached_embedding(
        self, provider: str, model: str, content_hash: str, vector: list[float]
    ) -> None:
        blob = _serialize_vector(vector)
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO embedding_cache (provider, model, content_hash, vector)
                   VALUES (?, ?, ?, ?)""",
                (provider, model, content_hash, blob),
            )

    # ------------------------------------------------------------------
    # Bulk ops
    # ------------------------------------------------------------------
    def truncate_all(self) -> None:
        """Drop all derived data. Used by ``/index/rebuild``."""
        with self.transaction() as conn:
            for tbl in ("chunks", "tags", "links", "headings", "notes"):
                conn.execute(f"DELETE FROM {tbl}")


def _row_to_note(row: sqlite3.Row) -> NoteRecord:
    return NoteRecord(
        note_id=row["note_id"],
        path=row["path"],
        title=row["title"],
        frontmatter=json.loads(row["frontmatter"] or "{}"),
        content_hash=row["content_hash"],
        last_modified=row["last_modified"],
        size_bytes=row["size_bytes"],
    )


def _row_to_chunk(row: sqlite3.Row) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=row["chunk_id"],
        note_id=row["note_id"],
        chunk_order=row["chunk_order"],
        text=row["text"],
        heading_path=json.loads(row["heading_path"] or "[]"),
        content_hash=row["content_hash"],
    )


def _serialize_vector(vec: list[float]) -> bytes:
    # Compact JSON; fine for our scale and avoids a numpy dependency.
    return json.dumps(vec).encode("utf-8")


def _deserialize_vector(blob: bytes) -> list[float]:
    return json.loads(blob.decode("utf-8"))
