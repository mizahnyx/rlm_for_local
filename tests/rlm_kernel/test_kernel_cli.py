"""Tests for rlm_kernel.cli — the `rlm-kernel` command surface (R24).

Each test drives the real `main(argv)` entry point (argparse → command
implementation → vault), with `--vault` pointed at a temp directory so nothing
touches the operator's real vault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.cli import main
from rlm_kernel.memory import MemoryManager
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import LocalVault


SEEDED_PAGE_COUNT = 17  # 2 contracts + 9 templates + 5 helpers + 1 fewshot


def _helper_body(name: str, code: str | None = None) -> str:
    impl = code or f"def {name}():\n    return '{name}'\n"
    return (
        f"## Signature\n\n```python\ndef {name}():\n```\n\n"
        f"## Implementation\n\n```python\n{impl}```\n"
    )


class TestInit:
    def test_init_seeds_vault_and_reports(self, tmp_path, capsys):
        rc = main(["init", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "Vault seeded" in out

        vault = LocalVault(tmp_path, init_git=False)
        assert len(vault.list()) == SEEDED_PAGE_COUNT
        assert vault.exists("contract/how-to-work.md")
        assert vault.exists("helper/grep.md")

    def test_init_is_idempotent(self, tmp_path, capsys):
        assert main(["init", "--vault", str(tmp_path)]) == 0
        capsys.readouterr()

        assert main(["init", "--vault", str(tmp_path)]) == 0

        vault = LocalVault(tmp_path, init_git=False)
        assert len(vault.list()) == SEEDED_PAGE_COUNT


class TestIndex:
    def test_index_rebuild_reports_page_count(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["index", "--rebuild", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert f"Index rebuilt: {SEEDED_PAGE_COUNT} pages indexed" in out
        assert (tmp_path / ".index" / "meta.sqlite").exists()


class TestSearch:
    def test_search_finds_seeded_helper(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["search", "regex", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "[helper] grep: grep" in out

    def test_search_without_match_prints_no_results(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["search", "xyzzynotpresent", "--vault", str(tmp_path)])

        assert rc == 0
        assert "(no results)" in capsys.readouterr().out

    def test_search_without_index_prints_no_results(self, tmp_path, capsys):
        """No index file → nothing to search, but not an error."""
        rc = main(["search", "anything", "--vault", str(tmp_path)])

        assert rc == 0
        assert "(no results)" in capsys.readouterr().out

    def test_search_kind_filter_excludes_other_kinds(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        capsys.readouterr()

        # "regex" appears in the grep helper AND in the repl-contract body, so
        # the kind filter is what decides which of the two is reported.
        rc = main(["search", "regex", "--kind", "helper", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "[helper] grep: grep" in out
        assert "[contract]" not in out


class TestSearchStatusFilter:
    """CL1 — a retired page stops answering queries, but stays reachable."""

    @staticmethod
    def _vault_with_a_deprecated_helper(tmp_path):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        # Demote the seeded grep helper: it stays in the vault and the index.
        main(["demote", "helper/grep.md", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])

    def test_a_deprecated_page_is_hidden_from_its_own_query(self, tmp_path, capsys):
        self._vault_with_a_deprecated_helper(tmp_path)
        capsys.readouterr()

        rc = main(["search", "grep", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        # Other pages may mention grep; the demoted helper itself must not appear.
        assert "[helper] grep" not in out

    def test_an_empty_result_explains_the_default(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["search", "xyzzynotpresent", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "(no results)" in out
        assert "active pages by default" in out

    def test_status_widens_the_search_to_the_history(self, tmp_path, capsys):
        self._vault_with_a_deprecated_helper(tmp_path)
        capsys.readouterr()

        rc = main(["search", "grep", "--status", "deprecated",
                   "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "[helper] [deprecated] grep: grep" in out

    def test_an_active_page_shows_without_a_marker(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        main(["index", "--rebuild", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["search", "grep", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "[helper] grep: grep" in out
        assert "[deprecated]" not in out


class TestReview:
    def test_review_with_no_proposals(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["review", "--vault", str(tmp_path)])

        assert rc == 0
        assert "No pending proposals." in capsys.readouterr().out

    def test_review_reports_pass_and_fail(self, tmp_path, capsys):
        vault = LocalVault(tmp_path, init_git=False)
        from rlm_kernel.gate import propose

        # Page name must equal the defined callable — validation checks that
        # the helper page actually defines the function it claims to.
        good = propose(vault, PageKind.HELPER, "goodhelper",
                       _helper_body("goodhelper"), "valid helper")
        bad = propose(vault, PageKind.HELPER, "badhelper",
                      _helper_body("badhelper",
                                   "import subprocess\n\ndef badhelper():\n    return 1\n"),
                      "invalid helper")

        rc = main(["review", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert good in out and bad in out
        assert "[PASS]" in out
        assert "[FAIL]" in out
        assert "ERROR:" in out
        assert "subprocess" in out


class TestPromoteDemote:
    def test_promote_moves_page_into_kind_directory(self, tmp_path, capsys):
        vault = LocalVault(tmp_path, init_git=False)
        from rlm_kernel.gate import propose

        qpath = propose(vault, PageKind.HELPER, "hello",
                        _helper_body("hello"), "a helper")
        capsys.readouterr()

        rc = main(["promote", qpath, "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert f"Promoted: {qpath} -> helper/hello.md" in out
        assert vault.exists("helper/hello.md")
        assert not vault.exists(qpath)
        promoted = vault.get("helper/hello.md")
        assert promoted.frontmatter.status == PageStatus.ACTIVE

    def test_promote_missing_page_exits_1(self, tmp_path, capsys):
        rc = main(["promote", "quarantine/nope.md", "--vault", str(tmp_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Page not found" in captured.err

    def test_demote_marks_page_deprecated(self, tmp_path, capsys):
        main(["init", "--vault", str(tmp_path)])
        capsys.readouterr()

        rc = main(["demote", "contract/how-to-work.md", "--vault", str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "Demoted: contract/how-to-work.md" in out
        page = LocalVault(tmp_path, init_git=False).get("contract/how-to-work.md")
        assert page.frontmatter.status == PageStatus.DEPRECATED

    def test_demote_missing_page_exits_1(self, tmp_path, capsys):
        rc = main(["demote", "contract/nope.md", "--vault", str(tmp_path)])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Page not found" in captured.err


class TestCompact:
    @staticmethod
    def _vault_with_duplicate_notes(tmp_path) -> LocalVault:
        vault = LocalVault(tmp_path, init_git=False)
        mm = MemoryManager(vault=vault)
        # Titles must clear the CLI's default 0.85 similarity threshold.
        mm.add(text="## Python Tips\n\nUse list comprehensions.")
        mm.add(text="## Python Tip\n\nUse generator expressions.")
        return vault

    def test_compact_dry_run_then_confirm(self, tmp_path, capsys):
        self._vault_with_duplicate_notes(tmp_path)

        rc = main(["compact", "--vault", str(tmp_path)])
        dry_out = capsys.readouterr().out
        assert rc == 0
        assert "Dry run: 1 merge candidate(s):" in dry_out
        assert "Run with --confirm to perform merges." in dry_out

        rc = main(["compact", "--confirm", "--vault", str(tmp_path)])
        confirm_out = capsys.readouterr().out
        assert rc == 0
        assert "Compacted: 1 note(s) merged." in confirm_out

    def test_compact_without_candidates(self, tmp_path, capsys):
        LocalVault(tmp_path, init_git=False)
        MemoryManager(vault=LocalVault(tmp_path, init_git=False)).add(
            text="## Only Note\n\nNothing to merge with."
        )

        rc = main(["compact", "--vault", str(tmp_path)])

        assert rc == 0
        assert "No merge candidates found." in capsys.readouterr().out


class TestOptimize:
    def test_optimize_dispatches_to_runner(self, tmp_path, monkeypatch):
        calls: list[dict] = []

        def fake_run(vault, **kwargs):
            calls.append(kwargs)
            return {"status": "no_improvement"}

        monkeypatch.setattr("rlm_kernel.optimize.run_optimization", fake_run)

        rc = main(["optimize", "--target", "nudges", "--vault", str(tmp_path)])

        assert rc == 0
        assert calls == [{"target": "nudges"}]

    def test_optimize_rejects_unknown_target(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["optimize", "--target", "not-a-target", "--vault", str(tmp_path)])


class TestBareInvocation:
    def test_no_command_prints_help(self, capsys):
        rc = main([])

        out = capsys.readouterr().out
        assert rc == 0
        assert "usage: rlm-kernel" in out
        assert "review" in out
