"""Tests for rlm_kernel.repl_bridge — helper payload and proxy handlers (R24).

The bridge is what makes vault pages reach the REPL worker at runtime: if
`get_helper_definitions` silently returns nothing, kernel-defined helpers are
inert and every completion falls back to hardcoded behaviour. These tests
exercise both listing paths (index-backed and vault-walk), the status filter,
and the three proxy handlers the worker can call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.index import rebuild_index
from rlm_kernel.repl_bridge import KernelBridge
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.seed import seed_vault
from rlm_kernel.vault import LocalVault


HELPER_NAMES = ["chunk", "grep", "map_query", "peek", "show_vars"]


@pytest.fixture
def seeded_bridge(temp_vault):
    """Seeded vault + built index + bridge over them."""
    vault = LocalVault(temp_vault, init_git=False)
    seed_vault(vault)
    idx_path = temp_vault / ".index" / "meta.sqlite"
    rebuild_index(vault, idx_path).close()
    return KernelBridge(vault=vault, index_path=idx_path), vault, idx_path


def _add_helper(vault, name: str, status: PageStatus = PageStatus.ACTIVE) -> str:
    """Write a helper page with a valid Signature/Implementation body."""
    body = (
        f"## Signature\n\n```python\ndef {name}(x):\n```\n\n"
        f"## Implementation\n\n```python\ndef {name}(x):\n"
        f"    return '{name}:' + str(x)\n```\n"
    )
    fm = Frontmatter(
        schema=1, kind=PageKind.HELPER, name=name, title=name,
        summary=f"The {name} helper.", status=status, tags=["builtin"],
    )
    path = f"helper/{name}.md"
    vault.put(Page(fm, body), path)
    return path


class TestHelperDefinitions:
    def test_index_backed_listing_returns_all_seeded_helpers(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        defs = bridge.get_helper_definitions()

        assert sorted(d["name"] for d in defs) == HELPER_NAMES
        for d in defs:
            assert set(d) == {"name", "code"}
            assert d["code"].strip(), f"{d['name']} has empty code"
        grep = next(d for d in defs if d["name"] == "grep")
        assert "def grep(" in grep["code"]

    def test_vault_walk_fallback_returns_the_same_helpers(self, seeded_bridge):
        """Without an index the bridge must still find every helper."""
        bridge, _, idx_path = seeded_bridge
        indexed = sorted(d["name"] for d in bridge.get_helper_definitions())

        idx_path.unlink()  # force the slow path
        fallback = sorted(d["name"] for d in bridge.get_helper_definitions())

        assert fallback == indexed == HELPER_NAMES

    def test_deprecated_helper_excluded_from_index_backed_listing(self, seeded_bridge):
        bridge, vault, idx_path = seeded_bridge
        _add_helper(vault, "old-helper", status=PageStatus.DEPRECATED)
        rebuild_index(vault, idx_path).close()

        names = [d["name"] for d in bridge.get_helper_definitions()]

        assert "old-helper" not in names
        assert sorted(names) == HELPER_NAMES

    def test_deprecated_helper_excluded_from_vault_walk(self, seeded_bridge):
        bridge, vault, idx_path = seeded_bridge
        _add_helper(vault, "old-helper", status=PageStatus.DEPRECATED)
        idx_path.unlink()

        names = [d["name"] for d in bridge.get_helper_definitions()]

        assert "old-helper" not in names

    def test_pending_helper_in_quarantine_is_not_injected(self, seeded_bridge):
        """Quarantined proposals must never reach the REPL namespace."""
        bridge, vault, idx_path = seeded_bridge
        body = "## Implementation\n\n```python\ndef sneaky():\n    return 1\n```\n"
        fm = Frontmatter(schema=1, kind=PageKind.HELPER, name="sneaky",
                         title="sneaky", summary="Proposed helper.",
                         status=PageStatus.PENDING)
        vault.put(Page(fm, body), "quarantine/01SNEAKYSNEAKYSNEAKYSNEAKY.md")
        rebuild_index(vault, idx_path).close()

        names = [d["name"] for d in bridge.get_helper_definitions()]

        assert "sneaky" not in names

    def test_helper_summaries_are_one_line_per_active_helper(self, seeded_bridge):
        bridge, vault, idx_path = seeded_bridge
        _add_helper(vault, "old-helper", status=PageStatus.DEPRECATED)
        rebuild_index(vault, idx_path).close()

        lines = bridge.get_helper_summaries()

        assert len(lines) == len(HELPER_NAMES)
        assert all(line.startswith("  ") and ":" in line for line in lines)
        assert not any("old-helper" in line for line in lines)

    def test_helper_summaries_are_capped(self, seeded_bridge):
        """Progressive disclosure: the helper section must not crowd out the contract."""
        bridge, vault, idx_path = seeded_bridge
        for i in range(40):
            _add_helper(vault, f"extra-helper-{i}")
        rebuild_index(vault, idx_path).close()

        assert len(bridge.get_helper_summaries(limit=5)) == 5
        assert len(bridge.get_helper_summaries()) == 30

    def test_helper_summaries_use_the_index_when_one_exists(
        self, seeded_bridge, monkeypatch
    ):
        """The index-backed path is what keeps a large vault off the hot path (C1).

        Asserted by making the vault walk fail: if the summaries still come back,
        they did not come from walking the vault.
        """
        bridge, _, _ = seeded_bridge

        def _no_walk(*args, **kwargs):
            raise AssertionError("vault.list() used despite an index existing")

        monkeypatch.setattr(bridge.vault, "list", _no_walk)

        lines = bridge.get_helper_summaries()

        assert len(lines) == len(HELPER_NAMES)

    def test_helper_summaries_fall_back_to_the_walk_without_an_index(self, temp_vault):
        """No index yet (a fresh vault) still advertises its helpers."""
        from rlm_kernel.repl_bridge import KernelBridge

        vault = LocalVault(temp_vault, init_git=False)
        for name in ("alpha", "beta"):
            _add_helper(vault, name)
        bridge = KernelBridge(vault=vault, index_path=temp_vault / ".index" / "meta.sqlite")

        lines = bridge.get_helper_summaries()

        assert [line.split(":")[0].strip() for line in lines] == ["alpha", "beta"]


class TestSearchProxy:
    def test_search_returns_numbered_lines(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        out = bridge.handle_search("regex")

        assert out != "(no results)"
        assert out.startswith("[1] ")
        assert "grep" in out

    def test_search_without_match_reports_no_results(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        # Compared against the shared constant, not a literal: the worker and the
        # bridge are two sides of one protocol and the text must be one string.
        from rlm_local.templates import WORKER_SEARCH_NO_RESULTS

        assert bridge.handle_search("xyzzynotpresent") == WORKER_SEARCH_NO_RESULTS

    def test_search_detail_full_includes_body(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        out = bridge.handle_search("regex", k=1, detail="full")

        assert out.startswith("[1] ")
        assert "def grep(" in out

    def test_search_kind_filter(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        out = bridge.handle_search("contract", k=5, kinds=["helper"])

        assert out == "(no results)"

    def test_search_failure_returns_error_string(self, seeded_bridge, monkeypatch):
        """A backend failure must degrade to text, not kill the REPL cell."""
        bridge, _, _ = seeded_bridge
        import rlm_kernel.search as search_mod

        def boom(*args, **kwargs):
            raise RuntimeError("index is locked")

        monkeypatch.setattr(search_mod, "search_vault", boom)

        out = bridge.handle_search("regex")

        assert out.startswith("Error: search failed:")
        assert "index is locked" in out


class TestProposeProxy:
    def test_propose_writes_quarantined_pending_page(self, seeded_bridge):
        bridge, vault, _ = seeded_bridge
        body = "## Implementation\n\n```python\ndef extract(x):\n    return x\n```\n"

        out = bridge.handle_propose("helper", "extract-dates", body,
                                    "Extracts ISO dates")

        assert out.startswith("Proposed: quarantine/")
        path = out.split("Proposed: ", 1)[1]
        page = vault.get(path)
        assert page is not None
        assert page.frontmatter.status == PageStatus.PENDING
        assert page.frontmatter.kind == PageKind.HELPER
        assert page.frontmatter.name == "extract-dates"
        assert "extract-dates" in page.frontmatter.summary or \
            "ISO dates" in page.frontmatter.summary
        assert body in page.body

    def test_propose_rejects_unknown_kind(self, seeded_bridge):
        bridge, vault, _ = seeded_bridge

        out = bridge.handle_propose("unicorn", "nope", "body")

        assert out.startswith("Error: invalid kind 'unicorn'")
        assert vault.list(prefix="quarantine") == []

    def test_proposed_page_is_not_returned_by_normal_search(self, seeded_bridge):
        """The proxy is a gate entrance, not a shortcut into the vault."""
        from rlm_kernel.search import search_vault

        bridge, vault, idx_path = seeded_bridge
        body = "## Implementation\n\n```python\ndef unique_name_xyz(x):\n    return x\n```\n"
        out = bridge.handle_propose("helper", "unique-name-xyz", body)
        path = out.split("Proposed: ", 1)[1]

        results = search_vault(vault, idx_path, "unique_name_xyz", k=5)

        assert all(r["path"] != path for r in results)


class TestCoreMemorySummary:
    def test_returns_none_when_absent(self, seeded_bridge):
        bridge, _, _ = seeded_bridge

        assert bridge.get_core_memory_summary() is None

    def test_returns_summary_when_written(self, seeded_bridge):
        from rlm_kernel.memory import MemoryManager

        bridge, vault, _ = seeded_bridge
        MemoryManager(vault=vault).write_core(
            vault, "This instance specializes in log analysis."
        )

        summary = bridge.get_core_memory_summary()

        assert summary is not None
        assert "log analysis" in summary
