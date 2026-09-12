"""Tests for rlm_kernel.index — SQLite meta + FTS5 index."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.index import Index, rebuild_index
from rlm_kernel.schema import Frontmatter, Page, PageStatus
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


class TestSearchStatusPredicate:
    """CL1 — retired pages used to keep answering queries.

    `fts_search` had no status predicate, so a page the vault had explicitly
    deprecated or superseded matched exactly like an active one, and a superseded
    page could outrank its own replacement.
    """

    @pytest.fixture
    def vault_with_retired_pages(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        vault.put(Page(Frontmatter(
            schema=1, kind="helper", name="current-qr",
            title="qr — current implementation",
            summary="The helper that is in use.",
        ), "## Signature\n```python\ndef qr(p): ...\n```\n\n## Implementation\n```python\ndef qr(p):\n    pass\n```"),
            "helper/current-qr.md")
        vault.put(Page(Frontmatter(
            schema=1, kind="helper", name="old-qr",
            title="qr — retired implementation",
            summary="Superseded by current-qr.",
            status=PageStatus.DEPRECATED,
        ), "## Signature\n```python\ndef qr(p): ...\n```\n\n## Implementation\n```python\ndef qr(p):\n    pass\n```"),
            "helper/old-qr.md")
        vault.put(Page(Frontmatter(
            schema=1, kind="helper", name="ancient-qr",
            title="qr — ancient implementation",
            summary="Superseded long ago.",
            status=PageStatus.SUPERSEDED,
        ), "## Signature\n```python\ndef qr(p): ...\n```\n\n## Implementation\n```python\ndef qr(p):\n    pass\n```"),
            "helper/ancient-qr.md")

        idx = Index(temp_vault / ".index" / "meta.sqlite")
        idx.build(vault)
        return idx, vault

    def test_a_deprecated_page_is_not_returned_by_default(self, vault_with_retired_pages):
        idx, _ = vault_with_retired_pages
        names = [r["name"] for r in idx.fts_search("qr")]
        assert "current-qr" in names
        assert "old-qr" not in names
        assert "ancient-qr" not in names

    def test_the_history_is_available_on_request(self, vault_with_retired_pages):
        idx, _ = vault_with_retired_pages
        names = [
            r["name"] for r in idx.fts_search(
                "qr", statuses=["active", "deprecated", "superseded"],
            )
        ]
        assert set(names) == {"current-qr", "old-qr", "ancient-qr"}

    def test_one_status_at_a_time(self, vault_with_retired_pages):
        idx, _ = vault_with_retired_pages
        assert [r["name"] for r in idx.fts_search("qr", statuses=["deprecated"])] == [
            "old-qr"
        ]

    def test_results_carry_their_status(self, vault_with_retired_pages):
        idx, _ = vault_with_retired_pages
        results = idx.fts_search("qr", statuses=["active", "deprecated"])
        by_name = {r["name"]: r["status"] for r in results}
        assert by_name == {"current-qr": "active", "old-qr": "deprecated"}

    def test_kind_and_status_filters_compose(self, vault_with_retired_pages):
        idx, _ = vault_with_retired_pages
        assert idx.fts_search("qr", kinds=["definition"]) == []
        names = [r["name"] for r in idx.fts_search("qr", kinds=["helper"])]
        assert names == ["current-qr"]


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


class TestFtsQuerySemantics:
    """R14: a multi-word query must recall pages where the terms are not adjacent."""

    @staticmethod
    def _vault_with_scattered_page(temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        fm = Frontmatter(
            schema=1, kind="definition", name="scattered", title="Scattered Notes",
            summary="Two terms far apart in one body.",
        )
        body = (
            "# Scattered Notes\n\n"
            "The alpha reading appears at the very start of this page, and "
            "after several intervening sentences the beta reading appears near "
            "the end of the body."
        )
        vault.put(Page(fm, body), "definitions/scattered.md")
        return vault

    def test_multi_word_query_matches_non_adjacent_terms(self, temp_vault):
        """Quoting the whole query turned it into an FTS5 *phrase* query."""
        vault = self._vault_with_scattered_page(temp_vault)
        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = Index(idx_path)
        idx.build(vault)
        try:
            results = idx.fts_search("alpha beta")
        finally:
            idx.close()

        assert any(r["name"] == "scattered" for r in results), (
            "multi-word query lost recall: terms are present but not adjacent"
        )

    def test_single_term_query_still_matches(self, temp_vault):
        """Control: OR-expansion must not break the single-token case."""
        vault = self._vault_with_scattered_page(temp_vault)
        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = Index(idx_path)
        idx.build(vault)
        try:
            assert any(r["name"] == "scattered" for r in idx.fts_search("alpha"))
        finally:
            idx.close()

    def test_absent_term_still_excluded(self, temp_vault):
        """OR-expansion must not turn into match-anything."""
        vault = self._vault_with_scattered_page(temp_vault)
        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = Index(idx_path)
        idx.build(vault)
        try:
            assert idx.fts_search("xyzzynotpresent") == []
        finally:
            idx.close()

    def test_empty_and_whitespace_queries_return_empty(self, temp_vault):
        vault = self._vault_with_scattered_page(temp_vault)
        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = Index(idx_path)
        idx.build(vault)
        try:
            assert idx.fts_search("") == []
            assert idx.fts_search("   ") == []
        finally:
            idx.close()


class TestReindexDeltaIdentity:
    """R14: the delta key must include the page id, not just the content hash."""

    def test_id_change_is_reindexed_and_tags_do_not_orphan(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        index_path = temp_vault / ".index" / "meta.sqlite"
        path = "definitions/identity.md"

        fm = Frontmatter(
            schema=1, kind="definition", name="identity", title="Identity",
            summary="Identical content.", tags=["alpha", "beta"],
        )
        body = "# Identity\n\nBody that does not change."
        vault.put(Page(fm, body), path)
        old_id = fm.id

        idx = Index(index_path)
        idx.build(vault)
        assert idx.get_page(path)["id"] == old_id

        # Rewrite the same content under a NEW ULID (e.g. page regenerated).
        replacement = Frontmatter(
            schema=1, kind="definition", name="identity", title="Identity",
            summary="Identical content.", tags=["alpha", "beta"],
        )
        assert replacement.id != old_id
        # Precondition that makes this a real regression test: the content hash
        # is id-independent, so a hash-only delta key cannot see the change.
        assert (Page(replacement, body).content_hash
                == Page(fm, body).content_hash)
        vault.put(Page(replacement, body), path)

        idx.reindex_delta(vault)

        row = idx.get_page(path)
        assert row["id"] == replacement.id, "index kept the stale ULID"
        orphan_rows = idx.conn.execute(
            "SELECT COUNT(*) FROM tags WHERE page_id = ?", (old_id,)
        ).fetchone()[0]
        assert orphan_rows == 0, "tags stayed keyed to the old ULID"
        new_rows = idx.conn.execute(
            "SELECT COUNT(*) FROM tags WHERE page_id = ?", (replacement.id,)
        ).fetchone()[0]
        assert new_rows == 2
        idx.close()

    def test_rebuild_equals_delta_after_id_rewrite(self, temp_vault):
        """Same invariant, stated as rebuild/delta equivalence."""
        vault = LocalVault(temp_vault, init_git=False)
        index_path = temp_vault / ".index" / "meta.sqlite"
        path = "definitions/identity.md"

        fm = Frontmatter(schema=1, kind="definition", name="identity",
                         title="Identity", summary="Same.", tags=["t1"])
        body = "# Identity\n\nBody."
        vault.put(Page(fm, body), path)

        idx = Index(index_path)
        idx.build(vault)
        replacement = Frontmatter(schema=1, kind="definition", name="identity",
                                  title="Identity", summary="Same.", tags=["t1"])
        vault.put(Page(replacement, body), path)
        idx.reindex_delta(vault)
        delta_tags = sorted(
            tuple(r) for r in idx.conn.execute("SELECT page_id, tag FROM tags")
        )
        delta_pages = sorted(
            tuple(r) for r in idx.conn.execute("SELECT id, path FROM pages")
        )
        idx.close()

        idx2 = Index(index_path)
        idx2.build(vault)
        rebuild_tags = sorted(
            tuple(r) for r in idx2.conn.execute("SELECT page_id, tag FROM tags")
        )
        rebuild_pages = sorted(
            tuple(r) for r in idx2.conn.execute("SELECT id, path FROM pages")
        )
        idx2.close()

        assert delta_pages == rebuild_pages
        assert delta_tags == rebuild_tags


class TestPropertyRebuildDeltaEquivalence:
    """rebuild and reindex_delta must produce identical index state."""

    @staticmethod
    def _snapshot_index(idx: Index) -> dict[str, list[tuple]]:
        """Collect all rows from every index table as sorted tuples."""
        pages = [tuple(r) for r in idx.conn.execute(
            "SELECT id, path, kind, name, title, summary, hash, idx_hash, version, status, updated FROM pages ORDER BY path"
        ).fetchall()]
        fts = [tuple(r) for r in idx.conn.execute(
            "SELECT path, kind, name, title, summary, body FROM fts_pages ORDER BY path"
        ).fetchall()]
        links = [tuple(r) for r in idx.conn.execute(
            "SELECT src, dst FROM links ORDER BY src, dst"
        ).fetchall()]
        tags = [tuple(r) for r in idx.conn.execute(
            "SELECT page_id, tag FROM tags ORDER BY page_id, tag"
        ).fetchall()]
        return {"pages": pages, "fts": fts, "links": links, "tags": tags}

    def test_rebuild_equals_delta_reindex(self, temp_vault):
        """After edits, a rebuild and a delta-reindex produce identical state."""
        import random as _random
        from rlm_kernel.schema import Frontmatter, Page
        from rlm_kernel.vault import LocalVault

        vault = LocalVault(temp_vault, init_git=False)
        index_path = temp_vault / ".index" / "meta.sqlite"
        rng = _random.Random(42)

        # --- Create 10 pages with wikilinks ---
        kinds = ["contract", "definition", "helper", "fewshot", "note", "topic", "cache"]
        page_names = [f"page-{i:02d}" for i in range(10)]
        page_paths: list[str] = []

        for i in range(10):
            kind = kinds[i % len(kinds)]
            path = f"{kind}s/{page_names[i]}.md"
            page_paths.append(path)
            # Add wikilinks to 1-2 other pages
            links_out = []
            for j in range(rng.randint(1, 2)):
                target_idx = (i + j + 1) % 10
                target_name = page_names[target_idx]
                links_out.append(f"[[{target_name}]]")
            body = f"# Page {i}\n\nContent for page {i}.\n" + "\n".join(links_out)
            fm = Frontmatter(
                schema=1, kind=kind, name=page_names[i],
                title=f"Page {i:02d}", summary=f"Summary of page {i}.",
                tags=[f"tag-{i%3}"],
            )
            vault.put(Page(fm, body), path)

        # --- Build fresh index ---
        idx1 = Index(index_path)
        idx1.build(vault)
        snapshot1 = self._snapshot_index(idx1)
        idx1.close()

        # --- Mutate vault: add 2, modify 3, delete 1 ---
        # Add 2 new pages
        for i in range(10, 12):
            kind = kinds[i % len(kinds)]
            path = f"{kind}s/page-{i:02d}.md"
            page_paths.append(path)
            fm = Frontmatter(
                schema=1, kind=kind, name=f"page-{i:02d}",
                title=f"Page {i:02d}", summary=f"Summary of page {i}.",
                tags=[f"tag-{i%3}"],
            )
            body = f"# Page {i}\n\nNew page content.\n[[page-00]]"
            vault.put(Page(fm, body), path)

        # Modify 3 existing pages
        for idx_mod in [2, 5, 7]:
            path = page_paths[idx_mod]
            old_page = vault.get(path)
            assert old_page is not None
            new_fm = Frontmatter(
                schema=1, kind=old_page.frontmatter.kind.value,
                name=old_page.frontmatter.name,
                title=f"Modified {old_page.frontmatter.title}",
                summary=f"Updated summary for page {idx_mod}.",
                tags=old_page.frontmatter.tags + ["modified"],
            )
            new_body = old_page.body + "\n\nModified content."
            vault.put(Page(new_fm, new_body), path)

        # Delete 1 page
        del_path = page_paths[9]
        vault.delete(del_path)

        # --- Delta-reindex ---
        idx2 = Index(index_path)
        idx2.reindex_delta(vault)
        snapshot_delta = self._snapshot_index(idx2)
        idx2.close()

        # --- Fresh rebuild ---
        idx3 = Index(index_path)
        idx3.build(vault)
        snapshot_rebuild = self._snapshot_index(idx3)
        idx3.close()

        # --- Assert equivalence ---
        for table in ["pages", "fts", "links", "tags"]:
            assert snapshot_delta[table] == snapshot_rebuild[table], (
                f"Mismatch in {table}:\n"
                f"  delta:   {snapshot_delta[table]}\n"
                f"  rebuild: {snapshot_rebuild[table]}"
            )

    def test_no_duplicate_fts_rows_after_edits(self, temp_vault):
        """10 sequential edits to the same page produce exactly 1 FTS row."""
        from rlm_kernel.schema import Frontmatter, Page
        from rlm_kernel.vault import LocalVault

        vault = LocalVault(temp_vault, init_git=False)
        index_path = temp_vault / ".index" / "meta.sqlite"

        # Create one page
        fm = Frontmatter(schema=1, kind="definition", name="single-page",
                         title="Single Page", summary="A single page.")
        vault.put(Page(fm, "# Single\n\nInitial body."), "definitions/single-page.md")

        idx = Index(index_path)
        idx.build(vault)

        # 10 sequential edits
        for i in range(10):
            new_fm = Frontmatter(schema=1, kind="definition", name="single-page",
                                 title=f"Single Page v{i}",
                                 summary=f"Revision {i}.")
            vault.put(Page(new_fm, f"# Revision {i}\n\nBody {i}."),
                     "definitions/single-page.md")
            idx.reindex_delta(vault)
            # Verify exactly one FTS row
            fts_rows = idx.conn.execute(
                "SELECT COUNT(*) FROM fts_pages WHERE path = ?",
                ("definitions/single-page.md",)
            ).fetchone()[0]
            assert fts_rows == 1, f"After edit {i}: expected 1 FTS row, got {fts_rows}"

        idx.close()

    def test_delta_per_page_no_worse_than_10x_fresh(self, temp_vault):
        """F5: scaling assertion — delta per-page ≤ 10× fresh per-page.

        Catches quadratics in the update path (e.g. full-scan FTS DELETEs).
        """
        import time
        from rlm_kernel.schema import Frontmatter, Page
        from rlm_kernel.vault import LocalVault

        vault = LocalVault(temp_vault, init_git=False)
        index_path = temp_vault / ".index" / "meta.sqlite"

        # Create 500 pages (enough to expose O(n) per-update costs)
        for i in range(500):
            kind = ["definition", "helper", "note"][i % 3]
            dir_name = f"{kind}s"
            fm = Frontmatter(
                schema=1, kind=kind, name=f"page-{i:05d}",
                title=f"Page {i}", summary=f"Summary {i}.",
            )
            body = f"# Page {i}\n\n" + "lorem ipsum " * 20
            vault.put(Page(fm, body), f"{dir_name}/page-{i:05d}.md")

        # Fresh build
        idx = Index(index_path)
        t0 = time.perf_counter()
        idx.build(vault)
        fresh_elapsed = time.perf_counter() - t0
        fresh_per_page_ms = (fresh_elapsed / 500) * 1000

        # 50 sequential edits
        for edit_i in range(50):
            i = edit_i * 2  # edit every other page
            kind = ["definition", "helper", "note"][i % 3]
            dir_name = f"{kind}s"
            new_fm = Frontmatter(
                schema=1, kind=kind, name=f"page-{i:05d}",
                title=f"Page {i} v2", summary=f"Updated summary {i}.",
            )
            vault.put(Page(new_fm, f"# Page {i} v2\n\nupdated " * 10),
                     f"{dir_name}/page-{i:05d}.md")

        # Time reindex_delta — one call processing all 50 changed pages (W2: was vacuous)
        t0 = time.perf_counter()
        idx.reindex_delta(vault)
        delta_elapsed = time.perf_counter() - t0
        delta_per_page_ms = (delta_elapsed / 50) * 1000

        idx.close()

        ratio = delta_per_page_ms / max(fresh_per_page_ms, 0.001)
        print(f"  Fresh: {fresh_per_page_ms:.1f} ms/page, Delta: {delta_per_page_ms:.1f} ms/page, ratio: {ratio:.1f}x")
        assert ratio <= 10.0, (
            f"Delta per-page ({delta_per_page_ms:.1f} ms) is {ratio:.1f}x "
            f"fresh per-page ({fresh_per_page_ms:.1f} ms) — exceeds 10x limit. "
            f"Possible quadratic in update path."
        )
