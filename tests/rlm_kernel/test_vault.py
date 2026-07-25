"""Tests for rlm_kernel.vault — page CRUD with atomic writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.schema import Frontmatter, Page, PageKind, parse_page
from rlm_kernel.vault import LocalVault, VaultStore


# ── VaultStore protocol ────────────────────────────────────────────────────

class TestVaultProtocol:
    def test_local_vault_implements_protocol(self):
        vault = LocalVault(Path("/tmp/test"))
        assert isinstance(vault, VaultStore)


# ── LocalVault CRUD ────────────────────────────────────────────────────────

class TestLocalVaultCRUD:
    def test_put_and_get(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(
            schema=1, kind="definition", name="test-concept",
            title="Test Concept", summary="A test concept.",
        )
        page = Page(fm, "# Test Concept\n\nBody text.")
        vault.put(page, "definitions/test-concept.md")

        got = vault.get("definitions/test-concept.md")
        assert got is not None
        assert got.frontmatter.name == "test-concept"
        assert got.body == "# Test Concept\n\nBody text."

    def test_put_assigns_path(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        page = Page(fm, "body")
        vault.put(page, "definitions/x.md")
        assert page.path == "definitions/x.md"

    def test_get_nonexistent(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        assert vault.get("nonexistent.md") is None

    def test_exists(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        vault.put(Page(fm, "body"), "definitions/x.md")
        assert vault.exists("definitions/x.md")
        assert not vault.exists("definitions/y.md")

    def test_delete(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        vault.put(Page(fm, "body"), "definitions/x.md")
        assert vault.exists("definitions/x.md")
        vault.delete("definitions/x.md")
        assert not vault.exists("definitions/x.md")

    def test_delete_nonexistent_no_error(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        vault.delete("nonexistent.md")  # should not raise

    def test_list_by_prefix(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        for name in ["a", "b", "c"]:
            fm = Frontmatter(schema=1, kind="definition", name=name, title=name, summary=".")
            vault.put(Page(fm, name), f"definitions/{name}.md")
        for name in ["x", "y"]:
            fm = Frontmatter(schema=1, kind="helper", name=name, title=name, summary=".")
            vault.put(Page(fm, name), f"helpers/{name}.md")

        defs = vault.list(prefix="definitions/")
        assert len(defs) == 3
        assert all(p.kind == PageKind.DEFINITION for p in defs)

    def test_list_by_kind(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        for name in ["a", "b"]:
            fm = Frontmatter(schema=1, kind="definition", name=name, title=name, summary=".")
            vault.put(Page(fm, name), f"definitions/{name}.md")
        fm = Frontmatter(schema=1, kind="helper", name="h", title="H", summary=".")
        vault.put(Page(fm, "body"), "helpers/h.md")

        helpers = vault.list(kind="helper")
        assert len(helpers) == 1
        assert helpers[0].kind == PageKind.HELPER

    def test_put_overwrites(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm1 = Frontmatter(schema=1, kind="definition", name="x", title="X1", summary=".", version=1)
        vault.put(Page(fm1, "body1"), "definitions/x.md")

        fm2 = Frontmatter(schema=1, kind="definition", name="x", title="X2", summary=".", version=2)
        vault.put(Page(fm2, "body2"), "definitions/x.md")

        got = vault.get("definitions/x.md")
        assert got is not None
        assert got.frontmatter.version == 2
        assert got.body == "body2"

    def test_put_creates_intermediate_dirs(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        vault.put(Page(fm, "body"), "definitions/nested/deep/x.md")
        assert vault.exists("definitions/nested/deep/x.md")

    def test_atomic_write_survives_crash(self, temp_vault):
        """Simulated: only the final file exists, no temp artifacts."""
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        vault.put(Page(fm, "body"), "definitions/x.md")

        # No .tmp files should remain
        tmp_files = list(temp_vault.rglob("*.tmp"))
        assert len(tmp_files) == 0

        # Exact file exists
        assert (temp_vault / "definitions" / "x.md").exists()


# ── Markdown round-trip ────────────────────────────────────────────────────

class TestRoundTrip:
    def test_write_then_read_is_identical(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        original = """---
schema: 1
kind: definition
name: test-rt
title: Round Trip Test
summary: Testing round trip fidelity.
tags:
- test
version: 1
---
# Round Trip

Body with **markdown** and `code`.

- item 1
- item 2
"""
        page = parse_page(original, "definitions/test-rt.md")
        vault.put(page, "definitions/test-rt.md")

        raw = (temp_vault / "definitions" / "test-rt.md").read_text(encoding="utf-8")
        reparsed = parse_page(raw, "definitions/test-rt.md")

        assert reparsed.frontmatter.name == page.frontmatter.name
        assert reparsed.frontmatter.kind == page.frontmatter.kind
        assert reparsed.frontmatter.title == page.frontmatter.title
        assert reparsed.frontmatter.summary == page.frontmatter.summary
        assert reparsed.frontmatter.tags == page.frontmatter.tags
        assert reparsed.body.strip() == page.body.strip()
        assert reparsed.content_hash == page.content_hash


# ── Wikilink resolution ────────────────────────────────────────────────────

class TestWikilinks:
    def test_resolve_wikilink(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="target-page",
                         title="Target Page", summary=".")
        vault.put(Page(fm, "body"), "definitions/target-page.md")

        resolved = vault.resolve_wikilink("target-page")
        assert resolved is not None
        assert resolved.name == "target-page"

    def test_resolve_wikilink_case_insensitive(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(schema=1, kind="definition", name="Target-Page",
                         title="Target", summary=".")
        vault.put(Page(fm, "body"), "definitions/Target-Page.md")

        resolved = vault.resolve_wikilink("target-page")
        assert resolved is not None
        assert resolved.name == "Target-Page"

    def test_resolve_nonexistent_wikilink(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        assert vault.resolve_wikilink("nonexistent") is None
