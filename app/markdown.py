"""Markdown utilities: frontmatter, headings, links, tags.

The parser is intentionally small and dependency-free: it understands just
enough of the Obsidian/CommonMark dialect to extract the structural data
the indexer needs. We deliberately avoid a heavyweight Markdown library to
keep the container image lean and the behaviour deterministic.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]\|\n]+)(?:\|[^\]\n]+)?\]\]")
# Tags: # followed by at least one allowed char, not preceded by a word char or '#',
# not part of a markdown heading. Allow nested tags via '/'.
_TAG_RE = re.compile(r"(?<![\w#])#([A-Za-z0-9_][\w/-]*)")
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


@dataclass
class ParsedNote:
    """Structured view of a parsed Markdown note."""

    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    headings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        """Return the note title from frontmatter, falling back to the first H1."""
        title = self.frontmatter.get("title") if isinstance(self.frontmatter, dict) else None
        if isinstance(title, str) and title.strip():
            return title.strip()
        for heading in self.headings:
            return heading
        return None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (frontmatter dict, body string)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        # Malformed frontmatter: treat as opaque text rather than failing the write.
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[match.end():]


def _strip_code_fences(text: str) -> str:
    return _FENCE_RE.sub("", text)


def extract_headings(body: str) -> list[str]:
    """Return heading texts in document order (H1–H6)."""
    cleaned = _strip_code_fences(body)
    return [m.group(2).strip() for m in _HEADING_RE.finditer(cleaned)]


def extract_links(body: str) -> list[str]:
    """Return Obsidian ``[[wikilink]]`` targets (without aliases)."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        if not target or target in seen:
            continue
        seen.add(target)
        out.append(target)
    return out


def extract_tags(frontmatter: dict[str, Any], body: str) -> list[str]:
    """Return the union of frontmatter tags and inline ``#tags``."""
    tags: list[str] = []
    seen: set[str] = set()

    fm_tags = frontmatter.get("tags") if isinstance(frontmatter, dict) else None
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.replace(",", " ").split() if t.strip()]
    if isinstance(fm_tags, list):
        for t in fm_tags:
            if isinstance(t, str) and t.strip():
                norm = t.strip().lstrip("#")
                if norm and norm not in seen:
                    seen.add(norm)
                    tags.append(norm)

    cleaned = _strip_code_fences(body)
    for match in _TAG_RE.finditer(cleaned):
        norm = match.group(1)
        if norm and norm not in seen:
            seen.add(norm)
            tags.append(norm)

    return tags


def parse_markdown(text: str) -> ParsedNote:
    """Parse a Markdown document into a :class:`ParsedNote`."""
    frontmatter, body = split_frontmatter(text)
    return ParsedNote(
        frontmatter=frontmatter,
        body=body,
        headings=extract_headings(body),
        tags=extract_tags(frontmatter, body),
        links=extract_links(body),
    )


def render_markdown(frontmatter: dict[str, Any] | None, content: str) -> str:
    """Render a Markdown document with optional YAML frontmatter.

    Ensures a single trailing newline and consistent ``\\n`` line endings.
    """
    body = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"

    if not frontmatter:
        return body

    dumped = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip("\n")
    return f"---\n{dumped}\n---\n{body}"


def content_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest for ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
