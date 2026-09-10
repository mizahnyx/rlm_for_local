"""Tests for rlm_kernel.seed — canonical seeding, layout convention, idempotency.

R13: seeded pages must live in the SINGULAR ``<kind>/`` directory that
``gate.promote`` writes to (``helper/hello.md``, ``fewshot/example.md``) — the
pre-R13 seed used plural directories (``helpers/``, ``fewshots/``), so a vault
ended up with two conventions at once and kind-filtered listing missed the
seeded pages.
"""

from __future__ import annotations

from pathlib import Path

from rlm_kernel.index import rebuild_index
from rlm_kernel.seed import seed_vault
from rlm_kernel.vault import LocalVault


HELPER_NAMES = ["peek", "grep", "chunk", "map_query", "show_vars"]


class TestSeedLayout:
    """R13: one directory convention — singular <kind>/ (gate.promote's)."""

    def test_helpers_seeded_into_singular_directory(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        for name in HELPER_NAMES:
            assert vault.exists(f"helper/{name}.md"), (
                f"helper '{name}' not seeded at helper/{name}.md"
            )

    def test_no_plural_helper_directory(self, temp_vault):
        """Guard: the plural directory must not be produced by seeding."""
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        stray = [p.path for p in vault.list() if p.path.startswith("helpers/")]
        assert stray == [], f"seeded pages still use helpers/: {stray}"

    def test_fewshot_seeded_into_singular_directory(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        assert vault.exists("fewshot/example.md")
        stray = [p.path for p in vault.list() if p.path.startswith("fewshots/")]
        assert stray == [], f"seeded pages still use fewshots/: {stray}"

    def test_seeded_helper_and_fewshot_paths_match_promote_convention(self, temp_vault):
        """Seeded paths equal what gate.promote would choose: <kind>/<name>.md."""
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        checked = 0
        for page in vault.list():
            if page.kind.value in ("helper", "fewshot"):
                assert page.path == f"{page.kind.value}/{page.name}.md", (
                    f"{page.path!r} diverges from the gate convention "
                    f"{page.kind.value}/{page.name}.md"
                )
                checked += 1
        assert checked >= 6, f"expected 5 helpers + 1 fewshot, saw {checked}"

    def test_index_lists_seeded_helpers_by_kind(self, temp_vault):
        """R13 acceptance: Index.list_paths(kind='helper') finds all seeded helpers."""
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = rebuild_index(vault, idx_path)
        try:
            paths = idx.list_paths(kind="helper")
        finally:
            idx.close()

        assert sorted(Path(p).stem for p in paths) == sorted(HELPER_NAMES)
        assert all(p.startswith("helper/") for p in paths), paths


class TestSeedIdempotency:
    def test_seed_twice_changes_nothing(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)
        first = {p.path: p.body for p in vault.list()}
        first_ids = {p.path: p.frontmatter.id for p in vault.list()}

        seed_vault(vault)
        second = {p.path: p.body for p in vault.list()}
        second_ids = {p.path: p.frontmatter.id for p in vault.list()}

        assert sorted(first) == sorted(second)
        assert first == second, "re-seeding must not rewrite existing page bodies"
        assert first_ids == second_ids, "re-seeding must not replace page identities"

    def test_seed_skips_existing_page_but_fills_gaps(self, temp_vault):
        """Idempotency is per-page: an edited page survives, a deleted one returns."""
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)

        page = vault.get("contract/how-to-work.md")
        page.body = "HUMAN EDIT"
        vault.put(page, "contract/how-to-work.md")
        vault.delete("contract/templates/prologue.md")

        seed_vault(vault)

        assert vault.get("contract/how-to-work.md").body == "HUMAN EDIT"
        assert vault.exists("contract/templates/prologue.md")

    def test_seed_creates_expected_page_count(self, temp_vault):
        """2 contracts + 9 templates + 5 helpers + 1 fewshot = 17 pages."""
        vault = LocalVault(temp_vault, init_git=False)
        seed_vault(vault)
        assert len(vault.list()) == 17
