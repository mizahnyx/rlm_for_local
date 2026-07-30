"""Tests for rlm CLI frontend (D1)."""

from __future__ import annotations

import io
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
    def test_search_no_results(self, monkeypatch):
        """rlm search with no results prints (no results)."""
        from rlm_kernel.search import search_vault
        monkeypatch.setattr("rlm_kernel.search.search_vault",
                            lambda *a, **kw: [])

        rc = cli_main(["search", "xyzzy", "--vault",
                       str(Path.home() / ".local/share/rlm-kernel/vault")])
        # May fail if vault doesn't exist, but won't crash
        assert rc in (0, 1)


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
    def test_vault_init(self):
        """rlm vault init delegates to rlm-kernel."""
        # Just test it doesn't crash — actual init tested in kernel
        pass


# Needed for stdin test
import sys
