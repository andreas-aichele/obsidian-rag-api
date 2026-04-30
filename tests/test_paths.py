from __future__ import annotations

from pathlib import Path

import pytest

from app.paths import InvalidPathError, normalize_note_path, resolve_in_vault, to_vault_relative


class TestNormalizeNotePath:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Projects/Test.md", "Projects/Test.md"),
            ("Daily/2026-04-30.md", "Daily/2026-04-30.md"),
            ("Infrastructure/Cloudflare Zero Trust.md", "Infrastructure/Cloudflare Zero Trust.md"),
            ("Projects\\Test.md", "Projects/Test.md"),       # windows separators
            ("./Projects/Test.md", "Projects/Test.md"),       # leading "./"
            ("Projects//Test.md", "Projects/Test.md"),        # empty segment
            ("a/b/c.MD", "a/b/c.MD"),                          # case-insensitive suffix
        ],
    )
    def test_valid_paths(self, raw: str, expected: str) -> None:
        assert normalize_note_path(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "/etc/passwd.md",
            "/abs/Projects/Test.md",
            "C:/Windows/notes.md",
            "../escape.md",
            "Projects/../../escape.md",
            "Projects/Test.txt",
            "no-extension",
            ".hidden.md",
            "Projects/.md",
            "with\x00null.md",
        ],
    )
    def test_invalid_paths(self, raw: str) -> None:
        with pytest.raises(InvalidPathError):
            normalize_note_path(raw)


class TestResolveInVault:
    def test_resolves_inside_vault(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        resolved = resolve_in_vault(vault, "Projects/Test.md")
        assert resolved == (vault / "Projects/Test.md").resolve()

    def test_rejects_traversal(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        with pytest.raises(InvalidPathError):
            resolve_in_vault(vault, "../../escape.md")

    def test_round_trip(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        (vault / "Projects").mkdir(parents=True)
        f = vault / "Projects" / "Test.md"
        f.write_text("hello")
        rel = to_vault_relative(vault, f)
        assert rel == "Projects/Test.md"
