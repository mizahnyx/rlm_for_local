"""Promote-path occupancy guards and quarantine ordering (R16).

Two related defects: `promote`'s name-conflict guard only protected ACTIVE
occupants (a deprecated/superseded page at the target path was silently
overwritten, destroying the record that a page had been replaced), and
`search_quarantine`'s docstring promised newest-first while `vault.list`
returns lexicographic order (ULIDs sort oldest-first).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.gate import promote, propose, search_quarantine
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import LocalVault


def _note(name: str, body: str = "body\n", status: PageStatus = PageStatus.ACTIVE) -> Page:
    return Page(
        Frontmatter(
            kind=PageKind.NOTE,
            name=name,
            title=name,
            summary="summary",
            status=status,
        ),
        body,
        path=f"note/{name}.md",
    )


@pytest.fixture
def vault(temp_vault: Path) -> LocalVault:
    return LocalVault(temp_vault, init_git=False)


class TestPromoteRefusesNonActiveOccupants:
    def _proposal(self, vault: LocalVault, name: str = "shared-name") -> Page:
        path = propose(vault, PageKind.NOTE, name, "## Example\n\nnew body\n")
        page = vault.get(path)
        assert page is not None
        return page

    def test_active_occupant_is_refused(self, vault):
        vault.put(_note("shared-name"), "note/shared-name.md")
        page = self._proposal(vault)
        with pytest.raises(ValueError, match="Active page already exists"):
            promote(vault, page)

    @pytest.mark.parametrize("status", [PageStatus.DEPRECATED, PageStatus.SUPERSEDED])
    def test_non_active_occupant_is_refused_without_force(self, vault, status):
        vault.put(_note("shared-name", status=status), "note/shared-name.md")
        page = self._proposal(vault)
        with pytest.raises(ValueError, match="force"):
            promote(vault, page)

    @pytest.mark.parametrize("status", [PageStatus.DEPRECATED, PageStatus.SUPERSEDED])
    def test_non_active_occupant_is_overwritten_with_force(self, vault, status):
        vault.put(_note("shared-name", body="old body\n", status=status),
                  "note/shared-name.md")
        page = self._proposal(vault)
        final = promote(vault, page, force=True)
        assert final == "note/shared-name.md"
        written = vault.get(final)
        assert written is not None
        assert written.frontmatter.status == PageStatus.ACTIVE
        assert "new body" in written.body

    def test_refusal_leaves_both_pages_intact(self, vault):
        vault.put(_note("shared-name", body="precious old body\n",
                        status=PageStatus.SUPERSEDED), "note/shared-name.md")
        page = self._proposal(vault)
        quarantine_path = page.path
        with pytest.raises(ValueError):
            promote(vault, page)

        survivor = vault.get("note/shared-name.md")
        assert survivor is not None
        assert "precious old body" in survivor.body
        assert survivor.frontmatter.status == PageStatus.SUPERSEDED
        # The proposal is still pending, not lost.
        assert vault.exists(quarantine_path)

    def test_explicit_target_path_bypasses_the_guard(self, vault):
        """The optimizer replaces incumbents deliberately (D-K4-1a)."""
        vault.put(_note("incumbent"), "note/incumbent.md")
        page = propose(vault, PageKind.NOTE, "candidate", "## Example\n\nx\n")
        cand = vault.get(page)
        assert cand is not None
        final = promote(vault, cand, target_path="note/incumbent.md")
        assert final == "note/incumbent.md"

    def test_empty_target_path_is_unaffected(self, vault):
        page = propose(vault, PageKind.NOTE, "brand-new", "## Example\n\nx\n")
        fresh = vault.get(page)
        assert fresh is not None
        assert promote(vault, fresh) == "note/brand-new.md"


class TestSearchQuarantineOrdering:
    def test_newest_first(self, vault):
        """ULID filenames sort oldest-first, so the promised order needs a sort."""
        names = ["alpha", "beta", "gamma"]
        for n in names:
            propose(vault, PageKind.NOTE, n, "## Example\n\nx\n")

        results = search_quarantine(vault)
        assert len(results) == 3
        stems = [Path(p.path).stem for p in results]
        assert stems == sorted(stems, reverse=True), (
            "search_quarantine must return newest first"
        )

    def test_query_filter_keeps_the_order(self, vault):
        propose(vault, PageKind.NOTE, "match-one", "## Example\n\nneedle\n")
        propose(vault, PageKind.NOTE, "other", "## Example\n\nnothing\n")
        propose(vault, PageKind.NOTE, "match-two", "## Example\n\nneedle\n")

        results = search_quarantine(vault, query="needle")
        assert len(results) == 2
        stems = [Path(p.path).stem for p in results]
        assert stems == sorted(stems, reverse=True)

    def test_kind_filter_still_applies(self, vault):
        propose(vault, PageKind.NOTE, "a-note", "## Example\n\nx\n")
        propose(vault, PageKind.HELPER, "a-helper",
                "## Signature\n```python\ndef a_helper():\n    ...\n```\n\n"
                "## Implementation\n```python\ndef a_helper():\n    return 1\n```\n")
        notes = search_quarantine(vault, kind="note")
        assert [p.name for p in notes] == ["a-note"]
