"""Tests for rlm_kernel.search — BM25 search over the vault."""

from __future__ import annotations

import pytest

from rlm_kernel.index import rebuild_index
from rlm_kernel.schema import Frontmatter, Page
from rlm_kernel.search import SearchBackend, search_vault
from rlm_kernel.vault import LocalVault


@pytest.fixture
def searchable_vault(temp_vault):
    """Vault with pages for search testing."""
    vault = LocalVault(temp_vault, init_git=False)

    pages_data = [
        ("contract/repl-contract.md", Frontmatter(
            schema=1, kind="contract", name="repl-contract",
            title="REPL Contract",
            summary="The contract governing REPL behavior and answer format.",
        ), "# REPL Contract\n\nRules for the REPL."),
        ("helpers/grep.md", Frontmatter(
            schema=1, kind="helper", name="grep",
            title="grep — regex search over context",
            summary="Search context lines with a regex pattern.",
            tags=["builtin", "search"],
        ), "# grep\n\nRegex search helper."),
        ("helpers/peek.md", Frontmatter(
            schema=1, kind="helper", name="peek",
            title="peek — context preview",
            summary="Preview the first N characters of context.",
            tags=["builtin"],
        ), "# peek\n\nPreview helper."),
        ("definitions/blue-widget.md", Frontmatter(
            schema=1, kind="definition", name="blue-widget",
            title="Blue Widget",
            summary="A widget that is blue in color.",
            tags=["widget", "color"],
        ), "# Blue Widget\n\nDocumentation about blue widgets."),
        ("definitions/red-widget.md", Frontmatter(
            schema=1, kind="definition", name="red-widget",
            title="Red Widget",
            summary="A widget that is red in color.",
            tags=["widget", "color"],
        ), "# Red Widget\n\nDocumentation about red widgets."),
    ]
    for path, fm, body in pages_data:
        vault.put(Page(fm, body), path)
    return vault


class TestSearch:
    def test_search_finds_by_keyword(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "regex", k=3)
        assert len(results) > 0
        assert any("grep" in r["name"] for r in results)

    def test_search_filters_by_kind(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "widget",
                               kinds=["definition"], k=5)
        assert len(results) == 2  # blue-widget and red-widget
        assert all(r["kind"] == "definition" for r in results)

    def test_search_returns_cards(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "preview", k=3)
        for r in results:
            assert "path" in r
            assert "kind" in r
            assert "name" in r
            assert "title" in r
            assert "summary" in r
            assert "score" in r

    def test_search_card_within_budget(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "blue", k=3)
        for r in results:
            card = str(r)
            assert len(card) <= 400, f"Card too long: {len(card)} chars"

    def test_search_empty_query_returns_empty(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "", k=3)
        assert len(results) == 0

    def test_search_nonexistent_term(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "xyznonexistent123", k=3)
        assert len(results) == 0

    def test_search_k_limits_results(self, searchable_vault):
        idx_path = searchable_vault.root / ".index" / "meta.sqlite"
        rebuild_index(searchable_vault, idx_path)

        results = search_vault(searchable_vault, idx_path, "widget", k=1)
        assert len(results) <= 1


class TestSearchBackendProtocol:
    def test_search_backend_is_protocol(self):
        assert isinstance(SearchBackend, type)
