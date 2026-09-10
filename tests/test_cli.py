"""Tests for rlm CLI frontend (D1)."""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rlm_local.cli import main as cli_main


class TestAsk:
    def test_ask_with_context_file(self, monkeypatch):
        """rlm ask with --context-file runs completion and prints answer."""
        import rlm_local
        monkeypatch.setattr(rlm_local, "completion",
                            lambda q, c, **kw: "The answer is blue.")

        with tempfile.NamedTemporaryFile(suffix=".md", mode="w",
                                         encoding="utf-8", delete=False) as f:
            f.write("# Test\n\nThe sky is blue.")
            fpath = f.name

        try:
            rc = cli_main(["ask", "What color?", "--context-file", fpath])
            assert rc == 0
        finally:
            Path(fpath).unlink()

    def test_ask_no_context_fails(self):
        """rlm ask without context returns error code 2."""
        rc = cli_main(["ask", "What color?"])
        assert rc == 2

    def test_ask_with_stdin(self, monkeypatch):
        """rlm ask --stdin reads from stdin."""
        import rlm_local
        monkeypatch.setattr(rlm_local, "completion",
                            lambda q, c, **kw: "answer")

        saved_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("Some context from stdin.")
            rc = cli_main(["ask", "What?", "--stdin"])
            assert rc == 0
        finally:
            sys.stdin = saved_stdin


class TestSearch:
    def test_search_no_index_reports_rebuild(self, tmp_path, capsys):
        """rlm search on a vault with no index exits 1 and says what to run."""
        rc = cli_main(["search", "xyzzy", "--vault", str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "Index not found" in out
        assert "rlm vault index --rebuild" in out

    def test_search_no_results(self, tmp_path, capsys):
        """rlm search over a real index prints '(no results)' and exits 0."""
        from rlm_kernel.index import rebuild_index
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        vault_path = tmp_path / "vault"
        vault = LocalVault(vault_path, init_git=False)
        seed_vault(vault)
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        rc = cli_main(["search", "xyzzyplugh", "--vault", str(vault_path)])
        assert rc == 0
        assert "(no results)" in capsys.readouterr().out


class TestGet:
    def test_get_nonexistent(self):
        """rlm get for missing page returns error."""
        rc = cli_main(["get", "nonexistent/page.md", "--vault",
                       str(Path.home() / ".local/share/rlm-kernel/vault")])
        assert rc == 1


class TestIngest:
    def test_ingest_creates_page(self, monkeypatch):
        """rlm ingest creates a vault page from a markdown file."""
        import tempfile
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="cli_ingest_",
                                         ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        # Init vault with git disabled
        vault = LocalVault(vault_path, init_git=False)
        from rlm_kernel.seed import seed_vault
        seed_vault(vault)
        # Build index
        from rlm_kernel.index import rebuild_index
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        # Create a test markdown file
        test_md = vault_path / "test_doc.md"
        test_md.write_text("# Hello World\n\nThis is a test document.",
                           encoding="utf-8")

        rc = cli_main(["ingest", str(test_md), "--vault", str(vault_path),
                       "--kind", "note"])
        assert rc == 0

        # Verify the page was created
        notes = vault.list(kind="note")
        assert len(notes) >= 1
        titles = [p.frontmatter.title for p in notes]
        assert any("Hello" in t for t in titles)

        td.cleanup()

    def test_ingest_duplicate_skipped(self, monkeypatch):
        """Re-ingesting the same file is skipped."""
        import tempfile
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="cli_ingest2_",
                                         ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        vault = LocalVault(vault_path, init_git=False)
        from rlm_kernel.seed import seed_vault
        seed_vault(vault)
        from rlm_kernel.index import rebuild_index
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        test_md = vault_path / "dup_test.md"
        test_md.write_text("# Duplicate\n\nSame content.", encoding="utf-8")

        # First ingest
        rc1 = cli_main(["ingest", str(test_md), "--vault", str(vault_path)])
        assert rc1 == 0

        # Second ingest — should skip
        rc2 = cli_main(["ingest", str(test_md), "--vault", str(vault_path)])
        assert rc2 == 0

        # Only one note should exist
        notes = vault.list(kind="note")
        dup_titles = [p for p in notes if p.frontmatter.title == "Duplicate"]
        assert len(dup_titles) == 1

        td.cleanup()


class TestVaultPassThrough:
    def test_vault_init(self, tmp_path, capsys):
        """rlm vault init delegates to rlm-kernel and seeds a real vault.

        This test used to be a literal `pass`. It now asserts the pass-through
        actually reaches the kernel command: exit code 0, the kernel's
        confirmation line, and pages on disk afterwards.
        """
        from rlm_kernel.vault import LocalVault

        vault_path = tmp_path / "vault"
        rc = cli_main(["vault", "init", "--vault", str(vault_path)])

        assert rc == 0
        assert f"Vault seeded at {vault_path}" in capsys.readouterr().out

        vault = LocalVault(vault_path, init_git=False)
        assert vault.list(), "vault init reported success but seeded no pages"
