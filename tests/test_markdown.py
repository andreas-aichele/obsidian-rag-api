from __future__ import annotations

from app.markdown import (
    extract_headings,
    extract_links,
    extract_tags,
    parse_markdown,
    render_markdown,
    split_frontmatter,
)


def test_split_frontmatter() -> None:
    text = "---\ntitle: Hello\ntags: [a, b]\n---\n# Body\n"
    fm, body = split_frontmatter(text)
    assert fm == {"title": "Hello", "tags": ["a", "b"]}
    assert body == "# Body\n"


def test_split_frontmatter_missing() -> None:
    fm, body = split_frontmatter("# only body\n")
    assert fm == {}
    assert body == "# only body\n"


def test_split_frontmatter_invalid_yaml_is_passthrough() -> None:
    fm, body = split_frontmatter("---\n: : bad : :\n---\nhi")
    assert fm == {}
    assert "hi" in body


def test_extract_headings_ignores_code_fences() -> None:
    body = "# Real\n```\n# fake\n```\n## Sub\n"
    assert extract_headings(body) == ["Real", "Sub"]


def test_extract_links() -> None:
    body = "See [[Other Note]] and [[Folder/Note|alias]] and [[Other Note]] again."
    assert extract_links(body) == ["Other Note", "Folder/Note"]


def test_extract_tags_combines_sources() -> None:
    fm = {"tags": ["frontmatter-tag", "shared"]}
    body = "Some prose with #inline and #nested/tag and #shared and code: `not#tag`."
    tags = extract_tags(fm, body)
    assert tags[:2] == ["frontmatter-tag", "shared"]
    assert "inline" in tags
    assert "nested/tag" in tags
    # No duplicates
    assert len(tags) == len(set(tags))


def test_parse_markdown_title_fallback() -> None:
    text = "# First Heading\n\nbody"
    parsed = parse_markdown(text)
    assert parsed.title == "First Heading"


def test_parse_markdown_title_from_frontmatter() -> None:
    text = "---\ntitle: From FM\n---\n# Heading\n"
    parsed = parse_markdown(text)
    assert parsed.title == "From FM"


def test_render_markdown_round_trip() -> None:
    rendered = render_markdown({"title": "T", "tags": ["a", "b"]}, "# Body\n\nLine")
    fm, body = split_frontmatter(rendered)
    assert fm == {"title": "T", "tags": ["a", "b"]}
    assert body.endswith("\n")
    assert "# Body" in body


def test_render_markdown_no_frontmatter() -> None:
    out = render_markdown(None, "no fm")
    assert out == "no fm\n"


def test_render_markdown_normalizes_line_endings() -> None:
    out = render_markdown(None, "a\r\nb\rc")
    assert out == "a\nb\nc\n"
