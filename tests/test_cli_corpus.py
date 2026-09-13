"""Tests for `rlm corpus` — the operator's read-only corpus commands (RO3/RO4).

The command group has one job beyond convenience: it must never invent a corpus,
and it must never accept an index path inside the corpus. Both are tested here
because both are silent failures — a guessed root returns plausible nonsense, and
an index inside the corpus writes into the thing the mount exists to protect.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from rlm_local.cli import main as cli_main


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "papers" / "2021").mkdir(parents=True)
    (root / "papers" / "notes.md").write_text("# Notes\n\nText.\n", encoding="utf-8")
    (root / "papers" / "2021" / "memo.txt").write_text("memo\n", encoding="utf-8")
    (root / "archive.zip").write_bytes(b"PK\x03\x04not really")
    return root


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    d = tmp_path / "derived"
    d.mkdir()
    return d / "corpus.sqlite"


class TestCorpusIndex:
    def test_index_builds_and_reports_aggregates(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "index", "--corpus-root", str(corpus),
                       "--corpus-index", str(index_path), "--progress-every", "100"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Indexed 5 entries" in out
        # The report is aggregates only: counts and totals, no paths.
        report = json.loads(out[out.index("{"):])
        assert report["by_kind"] == {"dir": 2, "file": 3}
        assert "notes.md" not in out

    def test_index_requires_both_paths(self, corpus: Path,
                                       capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["corpus", "index", "--corpus-root", str(corpus)]) == 2
        assert "required" in capsys.readouterr().err

    def test_index_inside_the_corpus_is_refused(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "index", "--corpus-root", str(corpus),
                       "--corpus-index", str(corpus / "corpus.sqlite")])
        assert rc == 2
        assert "derived state would live inside the corpus" in capsys.readouterr().err

    def test_index_refuses_a_root_it_cannot_read(
        self, tmp_path: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "index", "--corpus-root", str(tmp_path / "absent"),
                       "--corpus-index", str(index_path)])
        assert rc == 2
        assert "not a directory" in capsys.readouterr().err


class TestCorpusQueries:
    @pytest.fixture(autouse=True)
    def _built(self, corpus: Path, index_path: Path) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])

    def test_find_prints_hits(self, corpus: Path, index_path: Path,
                              capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "find", "memo", "--corpus-index", str(index_path)])
        assert rc == 0
        assert "papers/2021/memo.txt" in capsys.readouterr().out

    def test_find_with_no_hits_says_so(self, index_path: Path,
                                       capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "find", "nothing-here",
                       "--corpus-index", str(index_path)])
        assert rc == 0
        assert "(no matches)" in capsys.readouterr().out

    def test_count_reports_totals(self, index_path: Path,
                                  capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "count", "--corpus-index", str(index_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "5 entries" in out
        assert "3 files" in out

    def test_count_under_a_subtree(self, index_path: Path,
                                   capsys: pytest.CaptureFixture) -> None:
        cli_main(["corpus", "count", "--corpus-index", str(index_path),
                  "--under", "papers"])
        assert "3 entries" in capsys.readouterr().out

    def test_status_prints_aggregates_only(self, index_path: Path,
                                           capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "status", "--corpus-index", str(index_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out)["entries"] == 5

    def test_read_prints_content(self, corpus: Path, index_path: Path,
                                 capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "read", "papers/notes.md",
                       "--corpus-root", str(corpus), "--corpus-index", str(index_path)])
        assert rc == 0
        assert "Text." in capsys.readouterr().out

    def test_read_refuses_an_escaping_path(self, corpus: Path, index_path: Path,
                                           capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "read", "../outside.txt",
                       "--corpus-root", str(corpus), "--corpus-index", str(index_path)])
        assert rc == 0  # a refusal is an answer, not a crash
        assert "refused" in capsys.readouterr().out

    def test_a_missing_index_is_an_error_not_an_empty_answer(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "status",
                       "--corpus-index", str(tmp_path / "absent.sqlite")])
        assert rc == 2
        assert "no index at" in capsys.readouterr().err

    def test_queries_require_an_index_path(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["corpus", "find", "x"]) == 2
        assert "required" in capsys.readouterr().err

    def test_no_subcommand_prints_usage(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["corpus"]) == 2
        assert "Usage: rlm corpus" in capsys.readouterr().out


class TestCorpusVerify:
    """`rlm corpus verify` — the read-only proof, produced by the harness."""

    HOUR = 3600.0

    @staticmethod
    def _age_everything(corpus: Path, marker: Path, when: float) -> None:
        """Give the whole fixture — and the marker — one unambiguous mtime."""
        for path in corpus.rglob("*"):
            os.utime(path, (when, when))
        marker.write_text("", encoding="utf-8")
        os.utime(marker, (when, when))

    def test_a_clean_corpus_exits_zero_with_a_proof(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        marker = corpus.parent / "marker"
        self._age_everything(corpus, marker, time.time() - self.HOUR)
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                       "--since-file", str(marker), "--sample", "0"])
        out = capsys.readouterr().out
        assert rc == 0
        report = json.loads(out)
        assert report["verdict"].startswith("PROOF: nothing")
        assert report["scan_complete"] is True
        assert report["newer"] == 0

    def test_a_write_inside_the_run_window_exits_one(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        marker = corpus.parent / "marker"
        when = time.time() - self.HOUR
        self._age_everything(corpus, marker, when)
        os.utime(corpus / "papers" / "notes.md", None)  # written "now"
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                       "--since-file", str(marker), "--sample", "0",
                       "--run-started", str(when)])
        report = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert report["verdict"].startswith("BREACH")
        assert report["newer_inside_run_window"] == 1

    def test_a_future_dated_file_is_reported_as_not_ours(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        marker = corpus.parent / "marker"
        when = time.time() - self.HOUR
        self._age_everything(corpus, marker, when)
        future = time.time() + 86400 * 30
        os.utime(corpus / "archive.zip", (future, future))
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                       "--since-file", str(marker), "--sample", "0"])
        report = json.loads(capsys.readouterr().out)
        assert report["verdict"].startswith("NO WRITES BY THIS RUN")
        assert report["newer_dated_in_the_future"] == 1
        assert report["newer_inside_run_window"] == 0
        assert rc == 1  # not clean, and it says why rather than pretending

    def test_the_report_contains_no_paths(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        marker = corpus.parent / "marker"
        marker.write_text("", encoding="utf-8")
        cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                  "--since-file", str(marker), "--sample", "0"])
        out = capsys.readouterr().out
        assert "readme.md" not in out
        assert "archive.zip" not in out

    def test_a_missing_marker_is_an_error(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus)])
        assert rc == 2
        assert "marker" in capsys.readouterr().err

    def test_a_bad_marker_file_is_an_error(
        self, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                       "--since-file", str(tmp_path / "absent")])
        assert rc == 2
        assert "cannot use" in capsys.readouterr().err

    def test_an_epoch_marker_is_accepted(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "verify", "--corpus-root", str(corpus),
                       "--since", "2000000000", "--sample", "0"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["newer"] == 0


class TestCorpusDigestCommand:
    """`rlm corpus digest` — the snapshot that a before/after proof compares."""

    def test_a_walk_digest_prints_aggregates_only(
        self, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "digest", "--corpus-root", str(corpus)])
        out = capsys.readouterr().out
        assert rc == 0
        snapshot = json.loads(out)
        assert snapshot["source"] == "walk"
        assert snapshot["entries"] == 5
        assert len(snapshot["digest"]) == 128
        assert "notes.md" not in out

    def test_an_index_digest_is_available_without_a_walk(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        rc = cli_main(["corpus", "digest", "--from-index",
                       "--corpus-index", str(index_path)])
        snapshot = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert snapshot["source"] == "index"

    def test_index_and_walk_digests_of_the_same_corpus_match(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        cli_main(["corpus", "digest", "--from-index", "--corpus-index", str(index_path)])
        from_index = json.loads(capsys.readouterr().out)
        cli_main(["corpus", "digest", "--corpus-root", str(corpus)])
        from_walk = json.loads(capsys.readouterr().out)
        assert from_index["digest"] == from_walk["digest"]

    def test_compare_reports_a_match_and_exits_zero(
        self, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        snapshot = tmp_path / "before.json"
        cli_main(["corpus", "digest", "--corpus-root", str(corpus),
                  "--out", str(snapshot)])
        capsys.readouterr()
        rc = cli_main(["corpus", "digest", "--corpus-root", str(corpus),
                       "--compare", str(snapshot)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "PROOF: the two snapshots are identical" in out

    def test_compare_reports_a_change_and_exits_one(
        self, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        snapshot = tmp_path / "before.json"
        cli_main(["corpus", "digest", "--corpus-root", str(corpus),
                  "--out", str(snapshot)])
        capsys.readouterr()
        (corpus / "papers" / "notes.md").write_text("changed\n", encoding="utf-8")
        rc = cli_main(["corpus", "digest", "--corpus-root", str(corpus),
                       "--compare", str(snapshot)])
        assert rc == 1
        assert "DIFFERENT" in capsys.readouterr().out

    def test_from_index_without_an_index_is_an_error(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        assert cli_main(["corpus", "digest", "--from-index"]) == 2
        assert "--corpus-index" in capsys.readouterr().err

    def test_a_walk_digest_needs_a_root(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["corpus", "digest"]) == 2
        assert "--corpus-root" in capsys.readouterr().err

    def test_an_unreadable_snapshot_is_an_error(
        self, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "digest", "--corpus-root", str(corpus),
                       "--compare", str(tmp_path / "absent.json")])
        assert rc == 2
        assert "cannot read" in capsys.readouterr().err


class TestCorpusClassifyCommand:
    """`rlm corpus classify` — Stage 1 from the operator's side."""

    def test_it_classifies_and_reports_aggregates(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        rc = cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                       "--corpus-index", str(index_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "files classified" in out or "classified:" in out
        assert "dedup" in out
        # Aggregates only: the report must never name a file.
        assert "notes.md" not in out
        assert "archive.zip" not in out

    def test_a_second_run_says_nothing_new(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        assert "this pass: 0 files read" in capsys.readouterr().out

    def test_limit_pilots(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path), "--limit", "2"])
        assert "this pass: 2 files read" in capsys.readouterr().out

    def test_it_needs_both_paths(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["corpus", "classify", "--corpus-root", "/tmp"]) == 2
        assert "required" in capsys.readouterr().err

    def test_a_bad_root_is_an_error(
        self, tmp_path: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "classify", "--corpus-root", str(tmp_path / "absent"),
                       "--corpus-index", str(index_path)])
        assert rc == 2
        assert "not a directory" in capsys.readouterr().err


class TestAskWithACorpus:
    def test_ask_with_only_a_corpus_uses_the_stub_context(
        self, corpus: Path, index_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import rlm_local

        captured: dict = {}

        def fake_completion(query, context, **kwargs):
            captured["context"] = context
            captured["corpus_bridge"] = kwargs.get("corpus_bridge")
            return "answered"

        monkeypatch.setattr(rlm_local, "completion", fake_completion)
        assert cli_main(["corpus", "index", "--corpus-root", str(corpus),
                         "--corpus-index", str(index_path)]) == 0
        rc = cli_main(["ask", "how many papers?", "--corpus-root", str(corpus),
                       "--corpus-index", str(index_path)])
        assert rc == 0
        assert "read-only corpus" in captured["context"]
        assert captured["corpus_bridge"] is not None
        assert captured["corpus_bridge"].index is not None

    def test_ask_without_a_corpus_passes_no_bridge(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        import rlm_local

        captured: dict = {}

        def fake_completion(query, context, **kwargs):
            captured["corpus_bridge"] = kwargs.get("corpus_bridge")
            return "answered"

        monkeypatch.setattr(rlm_local, "completion", fake_completion)
        ctx = tmp_path / "ctx.md"
        ctx.write_text("plain context\n", encoding="utf-8")
        rc = cli_main(["ask", "what?", "--context-file", str(ctx)])
        assert rc == 0
        assert captured["corpus_bridge"] is None
