"""Vault path containment (S4 / R20).

`LocalVault.get/put/delete` used to compute `root / path` with no normalization
and no containment check, and frontmatter `name` had no charset validation, so
a proposed page named `../../foo` could be promoted to a path outside the vault
root. The `/docs` jail in `rlm_web/app.py` is the in-repo reference pattern for
what these guards should look like.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.schema import Frontmatter, Page, PageKind, parse_page
from rlm_kernel.vault import LocalVault

TRAVERSAL_PATHS = [
    "../escape.md",
    "../../escape.md",
    "a/../../escape.md",
    "a/b/../../../escape.md",
    "./../escape.md",
    "..",
    "helper/../../escape.md",
]


def _page(name: str = "safe-name") -> Page:
    return Page(
        Frontmatter(
            kind=PageKind.NOTE,
            name=name,
            title="T",
            summary="S",
        ),
        "body\n",
    )


class TestFrontmatterNameValidator:
    """`name` is model-controlled, so it must not be able to carry a path."""

    @pytest.mark.parametrize(
        "bad",
        [
            "../../foo",
            "..",
            ".",
            "/etc/passwd",
            "a/b",
            "a\\b",
            " leading-space",
            "trailing-space ",
            ".hidden",
            "",
            "x" * 129,
            "name\nwith-newline",
            "name\u0000",
        ],
    )
    def test_rejects_unsafe_names(self, bad):
        with pytest.raises(Exception) as exc:
            Frontmatter(kind=PageKind.NOTE, name=bad, title="T", summary="S")
        assert "name" in str(exc.value).lower() or "string" in str(exc.value).lower()

    @pytest.mark.parametrize(
        "good",
        [
            "my.helper-v2",
            "hello-world",
            "page_1",
            "A",
            "0abc",
            "x" * 128,
            "with.dots.and-dashes_underscores",
        ],
    )
    def test_allows_legitimate_names(self, good):
        fm = Frontmatter(kind=PageKind.NOTE, name=good, title="T", summary="S")
        assert fm.name == good

    def test_parse_page_rejects_traversing_name(self):
        content = (
            "---\n"
            "schema: 1\n"
            "id: 01HZZZZZZZZZZZZZZZZZZZZZZZ\n"
            "kind: note\n"
            "name: ../../foo\n"
            "title: T\n"
            "summary: S\n"
            "---\n"
            "body\n"
        )
        with pytest.raises(Exception):
            parse_page(content, "note/x.md")


class TestVaultPathContainment:
    @pytest.fixture
    def vault(self, temp_vault: Path) -> LocalVault:
        return LocalVault(temp_vault, init_git=False)

    @pytest.mark.parametrize("path", TRAVERSAL_PATHS)
    def test_get_rejects_traversal(self, vault, path):
        with pytest.raises(ValueError):
            vault.get(path)

    @pytest.mark.parametrize("path", TRAVERSAL_PATHS)
    def test_put_rejects_traversal(self, vault, path):
        with pytest.raises(ValueError):
            vault.put(_page(), path)

    @pytest.mark.parametrize("path", TRAVERSAL_PATHS)
    def test_delete_rejects_traversal(self, vault, path):
        with pytest.raises(ValueError):
            vault.delete(path)

    @pytest.mark.parametrize("path", TRAVERSAL_PATHS)
    def test_exists_rejects_traversal(self, vault, path):
        with pytest.raises(ValueError):
            vault.exists(path)

    @pytest.mark.parametrize("path", ["/etc/passwd", "C:/Windows/win.ini", "C:\\Windows\\win.ini"])
    def test_absolute_paths_rejected(self, vault, path):
        with pytest.raises(ValueError):
            vault.get(path)
        with pytest.raises(ValueError):
            vault.put(_page(), path)

    def test_nothing_is_written_outside_the_root(self, vault, temp_vault: Path):
        outside = temp_vault.parent / "escaped.md"
        if outside.exists():
            outside.unlink()
        with pytest.raises(ValueError):
            vault.put(_page(), "../escaped.md")
        assert not outside.exists()

    def test_legitimate_paths_still_work(self, vault, temp_vault: Path):
        vault.put(_page("my.helper-v2"), "helper/my.helper-v2.md")
        assert vault.exists("helper/my.helper-v2.md")
        page = vault.get("helper/my.helper-v2.md")
        assert page is not None
        assert page.frontmatter.name == "my.helper-v2"
        assert (temp_vault / "helper" / "my.helper-v2.md").is_file()
        vault.delete("helper/my.helper-v2.md")
        assert not vault.exists("helper/my.helper-v2.md")

    def test_dotfile_paths_inside_the_root_are_still_allowed(self, vault):
        """Containment is about escaping, not about leading dots in a path."""
        vault.put(_page("dotted"), ".index/dotted.md")
        assert vault.exists(".index/dotted.md")

    def test_list_skips_escapes_quietly(self, vault):
        # `list` walks the tree; it must never raise on a stored page.
        vault.put(_page("ok"), "note/ok.md")
        assert [p.frontmatter.name for p in vault.list(kind="note")] == ["ok"]
