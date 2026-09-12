"""Tests for `rlm corpus` — the operator's read-only corpus commands (RO3/RO4).

The command group has one job beyond convenience: it must never invent a corpus,
and it must never accept an index path inside the corpus. Both are tested here
because both are silent failures — a guessed root returns plausible nonsense, and
an index inside the corpus writes into the thing the mount exists to protect.
"""

from __future__ import annotations

import json
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
