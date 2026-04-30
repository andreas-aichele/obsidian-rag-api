"""Vault path validation, normalization and resolution.

Notes are *primarily identified by their vault-relative file path*. This
module is the single source of truth for what counts as a valid note path
and converts between vault-relative paths and absolute filesystem paths.

Rules enforced (matching the design contract):

* Paths must end with ``.md`` (case-insensitive on the suffix).
* Absolute paths are forbidden.
* ``..`` traversal segments are forbidden.
* Backslashes are normalized to forward slashes.
* Empty segments and ``.`` segments are collapsed.
* Resolved absolute paths must remain inside ``OBSIDIAN_VAULT_PATH``.

Case sensitivity
----------------
Path comparison is case-sensitive. On case-insensitive filesystems
(macOS default, Windows) Obsidian itself may resolve paths differently;
this service treats ``Foo/Bar.md`` and ``foo/bar.md`` as distinct logical
notes. Callers should preserve the casing chosen at create time.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class InvalidPathError(ValueError):
    """Raised when a supplied note path is not acceptable."""


def normalize_note_path(path: str) -> str:
    """Validate ``path`` and return a normalized vault-relative POSIX path.

    Raises :class:`InvalidPathError` if the path is unsafe or malformed.
    """
    if path is None:
        raise InvalidPathError("path is required")
    if not isinstance(path, str):
        raise InvalidPathError("path must be a string")

    raw = path.strip()
    if not raw:
        raise InvalidPathError("path must not be empty")

    # Reject NUL bytes and embedded control characters.
    if "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise InvalidPathError("path contains control characters")

    # Normalize Windows separators.
    candidate = raw.replace("\\", "/")

    # Reject absolute paths (Unix or Windows-drive style).
    if candidate.startswith("/") or (len(candidate) >= 2 and candidate[1] == ":"):
        raise InvalidPathError("absolute paths are not allowed")

    pure = PurePosixPath(candidate)

    # Collapse "." and reject ".." segments.
    parts: list[str] = []
    for segment in pure.parts:
        if segment in ("", "."):
            continue
        if segment == "..":
            raise InvalidPathError("path traversal ('..') is not allowed")
        parts.append(segment)

    if not parts:
        raise InvalidPathError("path must not be empty")

    normalized = "/".join(parts)

    if not normalized.lower().endswith(".md"):
        raise InvalidPathError("path must end with '.md'")

    # Disallow notes whose final segment is just an extension (e.g. ".md").
    if parts[-1].lower() == ".md" or parts[-1].startswith("."):
        raise InvalidPathError("note filename must not start with '.'")

    return normalized


def resolve_in_vault(vault_root: Path, note_path: str) -> Path:
    """Return the absolute filesystem path for ``note_path`` inside ``vault_root``.

    Raises :class:`InvalidPathError` if the resolved path escapes the vault.
    The vault root itself need not exist on disk for validation, but its
    parent must be resolvable so we can compare absolute prefixes safely.
    """
    normalized = normalize_note_path(note_path)
    root = vault_root.resolve(strict=False)
    candidate = (root / normalized).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise InvalidPathError("path escapes the vault root") from exc
    return candidate


def to_vault_relative(vault_root: Path, absolute_path: Path) -> str:
    """Convert an absolute filesystem path back to a normalized vault-relative path."""
    rel = absolute_path.resolve(strict=False).relative_to(vault_root.resolve(strict=False))
    return normalize_note_path(rel.as_posix())
