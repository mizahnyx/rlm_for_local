"""Tests for rlm_kernel.gate — proposal, validation, lifecycle management."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.gate import (
    ValidationReport,
    demote,
    promote,
    propose,
    reject,
    search_quarantine,
    validate,
    verify_quarantine_isolation,
)
from rlm_kernel.index import Index, rebuild_index
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import LocalVault


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_helper(name: str, body: str) -> str:
    """Build a complete helper page body with Signature + Implementation sections."""
    code = body.strip()
    return (
        f"## Signature\n\n```python\ndef {name}():\n```\n\n"
        f"## Implementation\n\n```python\n{code}\n```\n"
    )


# ── propose → validate → promote cycle ──────────────────────────────────────

class TestProposeValidatePromote:
    def test_full_cycle_with_valid_helper(self, temp_vault):
        """propose → validate → promote cycle with a valid helper."""
        vault = LocalVault(temp_vault, init_git=False)
        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = rebuild_index(vault, idx_path)

        body = _make_helper("hello", "def hello():\n    return 'world'\n")
        path = propose(vault, PageKind.HELPER, "hello", body, "test rationale")

        assert path.startswith("quarantine/")
        page = vault.get(path)
        assert page is not None
        assert page.frontmatter.status == PageStatus.PENDING

        report = validate(page, vault=vault)
        assert report.passed, f"Validation errors: {report.errors}"

        new_path = promote(vault, page, index=idx)
        assert new_path == "helper/hello.md"
        assert vault.exists(new_path)
        assert not vault.exists(path)

        promoted_page = vault.get(new_path)
        assert promoted_page.frontmatter.status == PageStatus.ACTIVE
        idx.close()

    def test_propose_validate_fails_with_blocked_import(self, temp_vault):
        """validate fails when helper code uses blocked imports (subprocess)."""
        vault = LocalVault(temp_vault, init_git=False)

        body = _make_helper(
            "bad_helper",
            "import subprocess\n\ndef bad_helper():\n    subprocess.run(['ls'])\n",
        )
        path = propose(vault, PageKind.HELPER, "bad_helper", body)
        page = vault.get(path)
        assert page is not None

        report = validate(page, vault=vault)
        assert not report.passed
        assert any("subprocess" in e.lower() for e in report.errors), (
            f"Expected subprocess error in: {report.errors}"
        )

    def test_propose_validate_reject_deletes_page(self, temp_vault):
        """reject deletes a quarantined page."""
        vault = LocalVault(temp_vault, init_git=False)

        body = _make_helper("temp", "def temp():\n    return 1\n")
        path = propose(vault, PageKind.HELPER, "temp", body)
        page = vault.get(path)
        assert page is not None

        reject(vault, page)
        assert not vault.exists(path)

    def test_promote_refuses_invalid_page(self, temp_vault):
        """promote raises ValueError when validation fails (import blocked)."""
        vault = LocalVault(temp_vault, init_git=False)

        body = _make_helper(
            "dangerous",
            "import ctypes\n\ndef dangerous():\n    ctypes.cdll.LoadLibrary('evil.so')\n",
        )
        path = propose(vault, PageKind.HELPER, "dangerous", body)
        page = vault.get(path)
        assert page is not None

        with pytest.raises(ValueError, match="Cannot promote: validation failed"):
            promote(vault, page)

        # Page should still be in quarantine
        assert vault.exists(path)

    def test_promote_refuses_non_quarantine_path(self, temp_vault):
        """promote raises ValueError for pages not in quarantine."""
        vault = LocalVault(temp_vault, init_git=False)

        fm = Frontmatter(
            kind=PageKind.HELPER,
            name="outside",
            title="outside",
            summary="test",
            tags=[],
            status=PageStatus.PENDING,
        )
        page = Page(frontmatter=fm, body="# test", path="helper/outside.md")

        with pytest.raises(ValueError, match="not in quarantine"):
            promote(vault, page)

    def test_promote_refuses_non_pending_status(self, temp_vault):
        """promote raises ValueError when page is not pending."""
        vault = LocalVault(temp_vault, init_git=False)

        body = _make_helper("active_helper", "def active_helper():\n    return 1\n")
        path = propose(vault, PageKind.HELPER, "active_helper", body)
        page = vault.get(path)

        # Manually set status to active (simulates pre-promoted)
        page.frontmatter.status = PageStatus.ACTIVE
        vault.put(page, path)

        with pytest.raises(ValueError, match="expected 'pending'"):
            promote(vault, page)


# ── Demote lifecycle ─────────────────────────────────────────────────────────

class TestDemote:
    def test_active_to_deprecated(self, temp_vault):
        """active → deprecated transition."""
        vault = LocalVault(temp_vault, init_git=False)

        fm = Frontmatter(
            kind=PageKind.HELPER,
            name="old_helper",
            title="old_helper",
            summary="test",
            tags=[],
            status=PageStatus.ACTIVE,
        )
        page = Page(frontmatter=fm, body="# old", path="helper/old_helper.md")
        vault.put(page, page.path)

        demote(vault, page)
        assert page.frontmatter.status == PageStatus.DEPRECATED

    def test_deprecated_to_superseded(self, temp_vault):
        """deprecated → superseded transition."""
        vault = LocalVault(temp_vault, init_git=False)

        fm = Frontmatter(
            kind=PageKind.HELPER,
            name="old_helper",
            title="old_helper",
            summary="test",
            tags=[],
            status=PageStatus.DEPRECATED,
        )
        page = Page(frontmatter=fm, body="# old", path="helper/old_helper.md")
        vault.put(page, page.path)

        demote(vault, page, superseded_by="helper/new_helper.md")
        assert page.frontmatter.status == PageStatus.SUPERSEDED
        assert page.frontmatter.superseded_by == "helper/new_helper.md"

    def test_superseded_unchanged(self, temp_vault):
        """superseded pages are left unchanged."""
        vault = LocalVault(temp_vault, init_git=False)

        fm = Frontmatter(
            kind=PageKind.HELPER,
            name="old_helper",
            title="old_helper",
            summary="test",
            tags=[],
            status=PageStatus.SUPERSEDED,
        )
        page = Page(frontmatter=fm, body="# old", path="helper/old_helper.md")
        vault.put(page, page.path)

        demote(vault, page)
        assert page.frontmatter.status == PageStatus.SUPERSEDED


# ── Quarantine isolation ─────────────────────────────────────────────────────

class TestQuarantineIsolation:
    def test_isolation_holds_after_propose(self, temp_vault):
        """verify_quarantine_isolation returns True after propose (no leak)."""
        vault = LocalVault(temp_vault, init_git=False)
        idx_path = temp_vault / ".index" / "meta.sqlite"

        # Build initial index
        rebuild_index(vault, idx_path)

        body = _make_helper("isolated", "def isolated():\n    return 42\n")
        propose(vault, PageKind.HELPER, "isolated", body)

        # Isolation should hold — quarantine pages are not indexed
        assert verify_quarantine_isolation(vault, idx_path)

    def test_isolation_false_after_promote(self, temp_vault):
        """After promote, quarantine is empty — isolation still returns True."""
        vault = LocalVault(temp_vault, init_git=False)
        idx_path = temp_vault / ".index" / "meta.sqlite"

        rebuild_index(vault, idx_path)

        body = _make_helper("move_me", "def move_me():\n    return 1\n")
        path = propose(vault, PageKind.HELPER, "move_me", body)
        page = vault.get(path)

        promote(vault, page)

        # After promote, no quarantine pages remain — isolation holds
        assert verify_quarantine_isolation(vault, idx_path)

    def test_search_quarantine_finds_pending(self, temp_vault):
        """search_quarantine returns proposed pages without query filter."""
        vault = LocalVault(temp_vault, init_git=False)

        body = _make_helper("find_me", "def find_me():\n    return 1\n")
        propose(vault, PageKind.HELPER, "find_me", body)

        results = search_quarantine(vault)
        assert len(results) >= 1
        assert any("find_me" in p.name for p in results)

    def test_search_quarantine_with_query(self, temp_vault):
        """search_quarantine filters by query substring."""
        vault = LocalVault(temp_vault, init_git=False)

        body1 = _make_helper("alpha", "def alpha():\n    return 1\n")
        body2 = _make_helper("beta", "def beta():\n    return 2\n")
        propose(vault, PageKind.HELPER, "alpha", body1)
        propose(vault, PageKind.HELPER, "beta", body2)

        results = search_quarantine(vault, query="alpha")
        assert len(results) == 1
        assert results[0].name == "alpha"


# ── ValidationReport ─────────────────────────────────────────────────────────

class TestValidationReport:
    def test_report_passed_when_no_errors(self):
        report = ValidationReport(passed=True, warnings=["just a warning"])
        assert report.passed
        assert len(report.errors) == 0
        assert len(report.warnings) == 1

    def test_report_failed_with_errors(self):
        report = ValidationReport(passed=False, errors=["bad import"])
        assert not report.passed
        assert len(report.errors) == 1
