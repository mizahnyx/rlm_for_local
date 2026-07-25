"""Tests for rlm_kernel.index — SQLite meta + FTS5 index."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.index import Index, rebuild_index
from rlm_kernel.schema import Frontmatter, Page
from rlm_kernel.vault import LocalVault


@pytest.fixture
def populated_vault(temp_vault):
    """Vault with several pages across kinds."""
    vault = LocalVault(temp_vault, init_git=False)

    pages = [
        ("contract/repl-contract.md", Frontmatter(
            schema=1, kind="contract", name="repl-contract",
            title="REPL Contract", summary="The REPL contract rules.",
        ), "# REPL Contract\n\nThe rules."),
        ("definitions/llm-concept.md", Frontmatter(
            schema=1, kind="definition", name="llm-concept",
            title="LLM Concept", summary="What LLMs are.",
            tags=["ai", "llm"],
        ), "# LLM Concept\n\nLarge language models."),
        ("helpers/grep.md", Frontmatter(
            schema=1, kind="helper", name="grep",
            title="grep — regex search", summary="Search context with regex.",
            tags=["builtin", "search"],
        ), "## Signature\n```python\ndef grep(p): ...\n```\n\n## Implementation\n```python\ndef grep(p):\n    pass\n```"),
        ("helpers/peek.md", Frontmatter(
            schema=1, kind="helper", name="peek",
            title="peek — context preview", summary="Preview first N chars.",
            tags=["builtin"],
        ), "## Signature\n```python\ndef peek(n=2000): ...\n```\n\n## Implementation\n```python\ndef peek(n=2000):\n    pass\n```"),
        ("fewshots/example.md", Frontmatter(
            schema=1, kind="fewshot", name="example",
            title="Example Few-Shot", summary="A worked example.",
        ), "# Example\n\nWorked transcript."),
    ]
    for path, fm, body in pages:
        vault.put(Page(fm, body), path)
    return vault


class TestIndexBuild:
    def test_build_from_vault(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        assert idx.page_count() == 5

    def test_rebuild_is_idempotent(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)
        count1 = idx.page_count()

        idx.build(populated_vault)
        count2 = idx.page_count()
        assert count1 == count2 == 5

    def test_get_page_by_path(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        row = idx.get_page("helpers/grep.md")
        assert row is not None
        assert row["kind"] == "helper"
        assert row["name"] == "grep"

    def test_get_page_nonexistent(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        assert idx.get_page("nonexistent.md") is None


class TestIndexFTS:
    def test_fts_search_finds_page(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        results = idx.fts_search("regex search")
        assert any("grep" in r["name"] for r in results)

    def test_fts_search_ranks_by_relevance(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        results = idx.fts_search("grep")
        assert len(results) > 0
        # grep should rank highest
        assert "grep" in results[0]["name"]


class TestIncrementalUpdate:
    def test_new_page_indexed(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        fm = Frontmatter(schema=1, kind="definition", name="new-one",
                         title="New One", summary="A new definition.")
        populated_vault.put(Page(fm, "# New"), "definitions/new-one.md")

        idx.reindex_delta(populated_vault)
        assert idx.get_page("definitions/new-one.md") is not None

    def test_changed_page_reindexed(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        fm = Frontmatter(schema=1, kind="definition", name="llm-concept",
                         title="LLM Concept Updated", summary="Updated summary.",
                         tags=["ai", "llm", "updated"], version=2)
        populated_vault.put(Page(fm, "# Updated\n\nNew body."),
                           "definitions/llm-concept.md")

        idx.reindex_delta(populated_vault)
        row = idx.get_page("definitions/llm-concept.md")
        assert row is not None
        assert row["title"] == "LLM Concept Updated"

    def test_deleted_page_removed(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        idx = Index(index_path)
        idx.build(populated_vault)

        populated_vault.delete("definitions/llm-concept.md")
        idx.reindex_delta(populated_vault)

        assert idx.get_page("definitions/llm-concept.md") is None


class TestRebuildIndex:
    def test_rebuild_index_helper(self, populated_vault):
        index_path = populated_vault.root / ".index" / "meta.sqlite"
        rebuild_index(populated_vault, index_path)
        idx = Index(index_path)
        assert idx.page_count() == 5
