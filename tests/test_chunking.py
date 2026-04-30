from __future__ import annotations

from app.chunking import chunk_note
from app.config import Settings
from app.markdown import parse_markdown


def _settings(**kw) -> Settings:
    base = dict(
        api_token="x",
        embedding_provider="fake",
        vector_backend="memory",
        obsidian_headless_enabled=False,
        watcher_enabled=False,
        chunk_target_tokens=100,
        chunk_max_tokens=120,
        chunk_min_tokens=10,
    )
    base.update(kw)
    return Settings(**base)


def test_chunk_by_headings_preserves_hierarchy() -> None:
    text = (
        "# Top\n"
        "intro line\n\n"
        "## A\n"
        "section a body\n\n"
        "## B\n"
        "section b body\n\n"
        "### B.1\n"
        "subsection body\n"
    )
    chunks = chunk_note(parse_markdown(text), _settings())
    paths = [c.heading_path for c in chunks]
    assert ["Top"] in paths
    assert ["Top", "A"] in paths
    assert ["Top", "B"] in paths
    assert ["Top", "B", "B.1"] in paths
    # Order is preserved
    assert [c.order for c in chunks] == list(range(len(chunks)))


def test_chunk_size_fallback_for_oversized_section() -> None:
    big = ("word " * 600).strip()
    text = f"# Big\n{big}\n"
    chunks = chunk_note(parse_markdown(text), _settings(chunk_target_tokens=50, chunk_max_tokens=60))
    assert len(chunks) > 1
    assert all(c.heading_path == ["Big"] for c in chunks)


def test_chunk_empty_note() -> None:
    assert chunk_note(parse_markdown(""), _settings()) == []


def test_chunk_no_headings_returns_single_chunk() -> None:
    chunks = chunk_note(parse_markdown("just some prose"), _settings())
    assert len(chunks) == 1
    assert chunks[0].heading_path == []
    assert "just some prose" in chunks[0].text
