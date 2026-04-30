"""Heading-aware chunking with size-based fallback.

The chunker walks the body of a parsed Markdown note and produces an
ordered list of :class:`Chunk` objects. Each chunk preserves the chain of
ancestor headings ("heading context") so the search response can show a
human-meaningful breadcrumb without re-parsing the source file.

Token estimation is intentionally heuristic and dependency-free
(``len(text) // 4``). It matches the typical rule-of-thumb ratio between
characters and tokens for English prose and avoids pulling a tokenizer
into the runtime image. The exact boundaries are not security-sensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, get_settings
from .markdown import ParsedNote

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    """A single chunk of a note ready for embedding."""

    order: int
    text: str
    heading_path: list[str]

    @property
    def heading_context(self) -> str:
        return " › ".join(self.heading_path)


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token."""
    return max(1, len(text) // 4)


def _split_oversized(text: str, max_tokens: int) -> list[str]:
    """Split a too-long block into ~``max_tokens`` chunks at paragraph or
    sentence boundaries, falling back to hard character slicing."""
    if estimate_tokens(text) <= max_tokens:
        return [text]

    max_chars = max_tokens * 4
    parts: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)
    buf = ""
    for para in paragraphs:
        candidate = (buf + "\n\n" + para).strip() if buf else para
        if estimate_tokens(candidate) <= max_tokens:
            buf = candidate
            continue
        if buf:
            parts.append(buf)
            buf = ""
        if estimate_tokens(para) <= max_tokens:
            buf = para
            continue
        # Paragraph itself too large: split on sentence boundaries.
        sentences = re.split(r"(?<=[.!?])\s+", para)
        sbuf = ""
        for sent in sentences:
            cand = (sbuf + " " + sent).strip() if sbuf else sent
            if estimate_tokens(cand) <= max_tokens:
                sbuf = cand
                continue
            if sbuf:
                parts.append(sbuf)
            if estimate_tokens(sent) <= max_tokens:
                sbuf = sent
            else:
                # Hard slice as a last resort.
                for i in range(0, len(sent), max_chars):
                    parts.append(sent[i:i + max_chars])
                sbuf = ""
        if sbuf:
            buf = sbuf
    if buf:
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def chunk_note(note: ParsedNote, settings: Settings | None = None) -> list[Chunk]:
    """Chunk ``note`` by heading, falling back to size-based splitting."""
    cfg = settings or get_settings()
    target = cfg.chunk_target_tokens
    max_tokens = cfg.chunk_max_tokens
    min_tokens = cfg.chunk_min_tokens

    # Walk the body and group lines under the active heading stack.
    sections: list[tuple[list[str], str]] = []
    current_stack: list[str] = []
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((list(current_stack), text))

    for line in note.body.splitlines():
        m = _HEADING_LINE_RE.match(line)
        if m:
            flush()
            current_lines = []
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            # Trim the stack to the parent level, then push this heading.
            current_stack = current_stack[: level - 1]
            while len(current_stack) < level - 1:
                current_stack.append("")
            current_stack.append(heading_text)
        else:
            current_lines.append(line)
    flush()

    if not sections:
        body_text = note.body.strip()
        if not body_text:
            return []
        sections = [([], body_text)]

    # Apply size policy: split oversized sections, merge undersized neighbours
    # that share the same heading path.
    sized: list[tuple[list[str], str]] = []
    for stack, text in sections:
        if estimate_tokens(text) > max_tokens:
            for piece in _split_oversized(text, target):
                sized.append((stack, piece))
        else:
            sized.append((stack, text))

    merged: list[tuple[list[str], str]] = []
    for stack, text in sized:
        if (
            merged
            and merged[-1][0] == stack
            and estimate_tokens(merged[-1][1]) < min_tokens
            and estimate_tokens(merged[-1][1]) + estimate_tokens(text) <= max_tokens
        ):
            prev_stack, prev_text = merged.pop()
            merged.append((prev_stack, prev_text + "\n\n" + text))
        else:
            merged.append((stack, text))

    return [
        Chunk(order=i, text=text, heading_path=stack)
        for i, (stack, text) in enumerate(merged)
    ]
