"""The privacy check: does a corpus-derived identifier reach this repository?

`AGENTS.md` §1.9 says identifiers never travel — and on 2026-09-19 one did: a question
id, a name out of the prose corpus, was committed in a dated record, in a test, and in a
commit message. The rule existed and nothing checked it. These tests hold the two things
that make the checker usable:

* it finds a planted token (a checker that cannot see the truth is worse than none);
* **its own output never quotes the token** — a failure message that printed the
  identifier would move the leak from the file to the terminal, the log and whatever
  pastes the log.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_privacy.py"
_spec = importlib.util.spec_from_file_location("check_privacy", SCRIPT)
assert _spec and _spec.loader
check_privacy = importlib.util.module_from_spec(_spec)
sys.modules["check_privacy"] = check_privacy
_spec.loader.exec_module(check_privacy)


class TestFindingAToken:
    def test_a_planted_token_in_the_tree_is_found(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "record.md").write_text(
            "The owner's question (`Zeta-Topic`) was re-run.\n", encoding="utf-8")
        (tmp_path / "clean.md").write_text("nothing here\n", encoding="utf-8")

        hits = check_privacy.scan_tree(["Zeta-Topic"], root=tmp_path)
        assert hits == [("docs/record.md", 1, 0)]

    def test_a_clean_tree_reports_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "record.md").write_text("aggregates only\n", encoding="utf-8")
        assert check_privacy.scan_tree(["Zeta-Topic"], root=tmp_path) == []

    def test_the_match_is_case_insensitive(self, tmp_path: Path) -> None:
        """A leak is not less of a leak for being capitalised differently."""
        (tmp_path / "a.md").write_text("zeta-topic appears here\n", encoding="utf-8")
        assert check_privacy.scan_tree(["Zeta-Topic"], root=tmp_path) == [("a.md", 1, 0)]

    def test_the_token_list_ignores_comments_and_blanks(self, tmp_path: Path) -> None:
        listing = tmp_path / "tokens.txt"
        listing.write_text("# a comment\n\nZeta-Topic\n  Other-Name  \n", encoding="utf-8")
        assert check_privacy.load_tokens(listing) == ["Zeta-Topic", "Other-Name"]


class TestWhatTheCheckPrints:
    def test_the_report_never_quotes_the_token(self, tmp_path: Path) -> None:
        """The failure message names a file, a line and a position — never the token."""
        (tmp_path / "record.md").write_text("mentions Zeta-Topic\n", encoding="utf-8")
        hits = check_privacy.scan_tree(["Zeta-Topic"], root=tmp_path)

        report = check_privacy.format_report(hits, [], token_count=1,
                                             checked_history=False)
        assert "Zeta-Topic" not in report, report
        assert "record.md:1" in report, report
        assert "token #1" in report, report

    def test_a_clean_report_says_so(self) -> None:
        report = check_privacy.format_report([], [], token_count=3,
                                             checked_history=True)
        assert "no occurrence" in report
        assert "3 token(s)" in report


class TestWithoutATokenList:
    def test_it_refuses_to_call_the_tree_clean(self, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture) -> None:
        """No list means the check cannot see the truth, so it says exactly that.

        Reporting "clean" here would be the `AGENTS.md` §1.8 corollary violated in the
        one place this project can least afford it.
        """
        monkeypatch.setattr(sys, "argv", ["check_privacy.py"])
        assert check_privacy.main() == 0
        out = capsys.readouterr().out
        assert "not run" in out
        assert "no occurrence" not in out, "it must not report a clean result"
