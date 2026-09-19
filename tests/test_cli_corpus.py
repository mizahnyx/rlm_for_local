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


class TestCorpusSearchCommand:
    """`rlm corpus search` — words inside the corpus, end to end."""

    @pytest.fixture
    def mined(self, corpus: Path, index_path: Path,
              capsys: pytest.CaptureFixture) -> Path:
        (corpus / "papers" / "needle.md").write_text(
            "The engine was Godot, and Cuicani sang.\n", encoding="utf-8"
        )
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["mine", "plan", "--corpus-index", str(index_path)])
        cli_main(["mine", "run", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path), "--tasks", "index_text"])
        capsys.readouterr()
        return index_path

    def test_a_word_inside_a_file_is_found(self, corpus: Path, mined: Path,
                                           capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "search", "Godot", "--corpus-root", str(corpus),
                       "--corpus-index", str(mined)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "needle.md#L" in out
        assert "Godot" in out

    def test_count_only_never_prints_content(self, corpus: Path, mined: Path,
                                             capsys: pytest.CaptureFixture) -> None:
        """The form that is safe to paste: counts and coverage, no path, no text."""
        rc = cli_main(["corpus", "search", "Godot", "--corpus-root", str(corpus),
                       "--corpus-index", str(mined), "--count-only"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "matches: 1" in out
        assert "text_coverage:" in out
        assert "needle.md" not in out
        assert "Godot" not in out

    def test_coverage_alone(self, corpus: Path, mined: Path,
                            capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "search", "x", "--corpus-index", str(mined),
                       "--coverage"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "sources_indexed" in out
        assert "text_files_in_map" in out

    def test_a_missing_word_reports_no_matches(self, corpus: Path, mined: Path,
                                               capsys: pytest.CaptureFixture) -> None:
        cli_main(["corpus", "search", "helicopter", "--corpus-root", str(corpus),
                  "--corpus-index", str(mined)])
        assert "(no matches)" in capsys.readouterr().out

    def test_an_empty_text_index_says_so(self, corpus: Path, index_path: Path,
                                         capsys: pytest.CaptureFixture) -> None:
        """Nothing indexed yet: the search must report coverage, not silence."""
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        cli_main(["corpus", "search", "anything", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        out = capsys.readouterr().out
        assert "(no matches)" in out
        assert "coverage:" in out

    def test_it_needs_an_index(self, tmp_path: Path,
                               capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "search", "x",
                       "--corpus-index", str(tmp_path / "absent.sqlite")])
        assert rc == 2
        assert "no index at" in capsys.readouterr().err


class TestCorpusCountersCommand:
    """`rlm corpus counters` — the snapshot a search quotes, shown or recomputed.

    Two verbs in one command on purpose: reading is instant, refreshing takes ~16
    minutes on the live index. A search inside a 120 s REPL cell can afford the
    first and never the second, so the interface has to make the difference
    visible rather than hiding a scan behind a read.
    """

    def test_without_a_snapshot_it_says_unknown_not_zero(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        rc = cli_main(["corpus", "counters", "--corpus-index", str(index_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "unknown" in out.lower()
        assert "It is not zero" in out

    def test_refresh_publishes_and_then_reads_back(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()
        assert cli_main(["corpus", "counters", "--corpus-index", str(index_path),
                         "--refresh"]) == 0
        refreshed = capsys.readouterr().out
        assert "sources_indexed:" in refreshed

        assert cli_main(["corpus", "counters",
                         "--corpus-index", str(index_path)]) == 0
        read_back = capsys.readouterr().out
        assert "sources_indexed:" in read_back

    def test_it_needs_an_index(self, tmp_path: Path,
                               capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "counters",
                       "--corpus-index", str(tmp_path / "absent.sqlite")])
        assert rc == 2
        assert "no index at" in capsys.readouterr().err


class TestCorpusSampleCommand:
    """`rlm corpus sample` — random passages with their addresses (2026-09-18).

    The owner's tool for devising questions: it hands back real text blocks from
    the live corpus, each with the address that re-reads it, so a question set can
    be written against the corpus instead of against a fixture that sleeps. It is
    also the one corpus command whose *whole output* is corpus text, which is why
    the header says so: the output is readable only where the corpus is
    (`AGENTS.md` §1.9).
    """

    @pytest.fixture
    def sampled(self, corpus: Path, index_path: Path,
                capsys: pytest.CaptureFixture) -> Path:
        (corpus / "papers" / "long.md").write_text(
            ("The engine was Godot, and Cuicani sang the archive awake. " * 14) + "\n",
            encoding="utf-8",
        )
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["mine", "plan", "--corpus-index", str(index_path)])
        cli_main(["mine", "run", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path), "--tasks", "index_text"])
        capsys.readouterr()
        return index_path

    @staticmethod
    def _addresses(out: str) -> list[str]:
        return [line.split()[-1] for line in out.splitlines()
                if line.startswith("=== ")]

    def test_a_passage_arrives_with_an_address_that_can_re_read_it(
        self, corpus: Path, sampled: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["corpus", "sample", "--n", "3", "--seed", "5",
                       "--corpus-root", str(corpus), "--corpus-index", str(sampled)])
        out = capsys.readouterr().out
        assert rc == 0
        addresses = self._addresses(out)
        assert addresses, out
        assert all("#L" in address for address in addresses)
        # The text really is the text: this command's job is to show it.
        assert "Cuicani" in out

    def test_the_same_seed_hands_back_the_same_passages(
        self, corpus: Path, sampled: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """A question set must be re-runnable, which needs a reproducible draw."""
        def draw(seed: str) -> tuple[str, list[str]]:
            cli_main(["corpus", "sample", "--n", "3", "--seed", seed,
                      "--corpus-root", str(corpus), "--corpus-index", str(sampled)])
            out = capsys.readouterr().out
            return out, self._addresses(out)

        header, first = draw("1234")
        _, again = draw("1234")
        assert first == again, "the same seed must hand back the same passages"
        assert "seed=1234" in header, header
        assert len(first) == 3

    def test_the_header_says_the_output_is_corpus_text(
        self, corpus: Path, sampled: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "sample", "--n", "1", "--seed", "1",
                  "--corpus-root", str(corpus), "--corpus-index", str(sampled)])
        out = capsys.readouterr().out
        header = "\n".join(line for line in out.splitlines()
                           if line.startswith("#"))
        assert "seed=1" in header
        assert "corpus text" in header.lower()
        assert "1.9" in header, "the header names the rule that keeps it local"

    def test_a_long_passage_is_clipped_and_says_so(
        self, corpus: Path, sampled: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        cli_main(["corpus", "sample", "--n", "4", "--seed", "2", "--chars", "60",
                  "--corpus-root", str(corpus), "--corpus-index", str(sampled)])
        out = capsys.readouterr().out
        blocks = out.split("=== ")[1:]
        assert blocks, out
        for block in blocks:
            first_line = block.split("\n", 1)[1].split("\n")[0]
            assert "clipped at 60" in block or len(first_line) <= 60, block
            assert len(first_line) <= 60, (
                f"the cap is a cap: {len(first_line)} characters printed"
            )

    def test_it_needs_an_index(self, tmp_path: Path,
                               capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["corpus", "sample", "--n", "1",
                       "--corpus-index", str(tmp_path / "absent.sqlite")])
        assert rc == 2
        assert "no index at" in capsys.readouterr().err


class TestReindexEncodingsCommand:
    """`rlm corpus reindex-encodings` — the repair pass for RO19.

    The forward fix (index a source with the encoding the sniffer recorded) does
    nothing for a source indexed *before* it: those chunks carry NULL in the encoding
    column, which is what "unknown" means here. This command is what makes the 46,735
    files recorded as cp1252 or latin-1 searchable again — so the test builds exactly
    that stale state by hand rather than pretending a fresh index is one.
    """

    WORD = "canción"
    BODY = "El comité aprobó la canción de María y el niño.\n"

    def _stale_index(self, corpus: Path, index_path: Path) -> bytes:
        """A cp1252 file, classified, and indexed the way the old code did it."""
        import sqlite3

        from rlm_kernel.corpus import CorpusIndex

        legacy = corpus / "papers" / "legacy.txt"
        legacy.write_bytes(self.BODY.encode("cp1252"))
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        # `index`/`classify` do not touch the text index — mining creates it — so the
        # tables are made here, exactly as an old mine window left them.
        owner = CorpusIndex(index_path)
        try:
            owner.text().ensure()
        finally:
            owner.close()
        raw = b"papers/legacy.txt"
        body = legacy.read_bytes()

        conn = sqlite3.connect(str(index_path))
        conn.execute(
            "INSERT INTO text_chunks (source, display, source_hash, origin, byte_start,"
            " byte_end, derived, vendored) VALUES (?, ?, ?, 'file', 0, ?, 0, 0)",
            (raw, "papers/legacy.txt", "h-legacy", len(body)),
        )
        # The old decode: UTF-8 with replacement characters. This is the damage.
        conn.execute("INSERT INTO text_fts (rowid, body) VALUES (?, ?)",
                     (1, body.decode("utf-8", "replace")))
        conn.commit()
        stored = conn.execute(
            "SELECT encoding FROM classification WHERE raw = ?", (raw,)
        ).fetchone()
        conn.close()
        assert stored is not None and stored[0] == "cp1252", stored
        return body

    def test_a_stale_source_is_repaired_and_then_searchable(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        body = self._stale_index(corpus, index_path)

        # Before: the word is not findable, because the tokens were destroyed.
        cli_main(["corpus", "search", self.WORD, "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        before = capsys.readouterr().out
        assert "no matches" in before, before

        rc = cli_main(["corpus", "reindex-encodings", "--corpus-root", str(corpus),
                       "--corpus-index", str(index_path)])
        report = capsys.readouterr().out
        assert rc == 0
        assert "re-indexed=1" in report, report

        cli_main(["corpus", "search", self.WORD, "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        after = capsys.readouterr().out
        assert "legacy.txt#L" in after, after
        # The address still names the raw bytes: cp1252 encodes the accented vowel in
        # one byte, so a decode before chunking would have moved this offset.
        address = after.split("legacy.txt#L", 1)[1].split()[0]
        start, _, end = address.partition("-")
        assert self.WORD in body[int(start):int(end)].decode("cp1252"), address

    def test_a_second_run_skips_what_is_already_current(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """Idempotent, and it does not re-read: a repair pass must be sliceable."""
        self._stale_index(corpus, index_path)
        cli_main(["corpus", "reindex-encodings", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        capsys.readouterr()

        cli_main(["corpus", "reindex-encodings", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        second = capsys.readouterr().out
        assert "re-indexed=0" in second, second
        assert "already-current=1" in second, second

    def test_one_unstorable_source_does_not_end_the_pass(
        self, corpus: Path, index_path: Path, capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The first live run died six seconds in, on a path whose name is not UTF-8.

        A surrogate cannot be stored as SQLite TEXT, so that source raises — and a
        pass that stops there repairs nothing at all. The failure is counted by
        exception type, and the other sources are still repaired.
        """
        from rlm_kernel.textindex import TextIndex

        first = corpus / "papers" / "legacy.txt"
        first.write_bytes(self.BODY.encode("cp1252"))
        second = corpus / "papers" / "other.txt"
        second.write_bytes(("Otra canción distinta.\n").encode("cp1252"))
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index_path)])

        from rlm_kernel.corpus import CorpusIndex

        owner = CorpusIndex(index_path)
        try:
            owner.text().ensure()
        finally:
            owner.close()

        original = TextIndex.add_text
        exploding = (b"papers/legacy.txt",)

        def sometimes(self, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs.get("raw") in exploding:
                raise UnicodeEncodeError("utf-8", "", 0, 1, "surrogates not allowed")
            return original(self, **kwargs)

        monkeypatch.setattr(TextIndex, "add_text", sometimes)
        rc = cli_main(["corpus", "reindex-encodings", "--corpus-root", str(corpus),
                       "--corpus-index", str(index_path)])
        report = capsys.readouterr().out

        assert rc == 0, report
        assert "re-indexed=1" in report, report
        assert "failed=1" in report, report
        assert "UnicodeEncodeError=1" in report, report


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
