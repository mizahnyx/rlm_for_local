"""Tests for `rlm trace render` — the owner's trace viewer (RO10).

The command has one job that must not slip: it writes the pages where the corpus
is, prints only aggregates, and refuses to write the rendered directory inside the
corpus root. A viewer that leaked the question, an address or a passage into the
terminal would defeat the privacy rule it exists to respect (AGENTS.md §1.9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rlm_local.cli import main as cli_main

T0 = 1_760_000_000.0

QUESTION = "Who ratified the Zxqvarn protocol?"
ADDRESS = "notes/song.txt#L0-21"
PASSAGE = "Vantrel sang it first"


def _trajectory(path: Path, *, with_served: bool = True) -> Path:
    events = [
        {"event": "start", "timestamp": T0, "query": QUESTION,
         "context_len": 12, "config": {"name": "laptop", "root_model": "stub-4b"}},
        {"event": "turn_start", "timestamp": T0 + 1, "turn": 1, "max_turns": 1},
        {"event": "root_message", "timestamp": T0 + 2, "turn": 1,
         "role": "assistant",
         "content": "```repl\nprint(corpus_search('cuicani'))\n```"},
        {"event": "repl_result", "timestamp": T0 + 3, "turn": 1,
         "stdout": f"{ADDRESS}  [raw, covers 1/1 (strong)]", "stderr": "",
         "final_answer": None, "warnings": []},
        {"event": "end", "timestamp": T0 + 4, "elapsed_s": 4.5,
         "final_answer": f"See {ADDRESS}", "turns_used": 1, "subcalls_used": 0,
         "forced": False},
    ]
    if with_served:
        events.insert(3, {
            "event": "corpus_served", "timestamp": T0 + 2, "turn": 1,
            "verb": "corpus_search", "query": "cuicani",
            "addresses": [{"address": ADDRESS, "band": "strong"}],
            "chars": 120, "ok": True,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "song.txt").write_text(PASSAGE, encoding="utf-8")
    return root


class TestTraceRender:
    def test_it_writes_a_page_per_run_and_an_index(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        traj = _trajectory(tmp_path / "live-ask-1.jsonl")
        out = tmp_path / "traces"
        rc = cli_main(["trace", "render", str(traj), "--out-dir", str(out)])
        captured = capsys.readouterr()

        assert rc == 0, captured.err
        assert (out / "index.md").exists()
        pages = [p for p in out.glob("*.md") if p.name != "index.md"]
        assert len(pages) == 1
        assert ADDRESS in pages[0].read_text(encoding="utf-8")
        assert "1 run(s)" in (out / "index.md").read_text(encoding="utf-8")

    def test_the_terminal_gets_aggregates_and_never_corpus_text(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """The page is local; what the terminal shows is safe to paste."""
        traj = _trajectory(tmp_path / "live-ask-2.jsonl")
        out = tmp_path / "traces"
        cli_main(["trace", "render", str(traj), "--out-dir", str(out),
                  "--summary"])
        text = capsys.readouterr().out

        assert str(out) in text
        assert "live-ask-2.jsonl" in text
        assert "audit=" in text and "answers=" in text
        assert QUESTION not in text
        assert ADDRESS not in text
        assert PASSAGE not in text

    def test_summary_alone_does_not_write_pages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """`--summary` is also usable as a pure read: no directory, no pages."""
        traj = _trajectory(tmp_path / "live-ask-3.jsonl")
        out = tmp_path / "traces"
        rc = cli_main(["trace", "summary", str(traj)])
        text = capsys.readouterr().out

        assert rc == 0
        assert "live-ask-3.jsonl" in text
        assert not out.exists()

    def test_a_directory_of_trajectories_is_rendered_together(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        logs = tmp_path / "logs"
        _trajectory(logs / "a.jsonl")
        _trajectory(logs / "b.jsonl")
        (logs / "notes.txt").write_text("not a trajectory", encoding="utf-8")
        out = tmp_path / "traces"
        cli_main(["trace", "render", str(logs), "--out-dir", str(out)])
        capsys.readouterr()

        index = (out / "index.md").read_text(encoding="utf-8")
        assert "2 run(s)" in index
        assert len([p for p in out.glob("*.md") if p.name != "index.md"]) == 2

    def test_a_write_target_inside_the_corpus_is_refused(
        self, tmp_path: Path, corpus: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """Layer 3, enforced by the command rather than by the operator's care."""
        traj = _trajectory(tmp_path / "live-ask-4.jsonl")
        rc = cli_main(["trace", "render", str(traj), "--out-dir",
                       str(corpus / "traces"), "--corpus-root", str(corpus)])
        assert rc == 2
        assert "derived state would live inside the corpus" in capsys.readouterr().err

    def test_a_missing_trajectory_is_an_error_not_an_empty_page(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = cli_main(["trace", "render", str(tmp_path / "absent.jsonl"),
                       "--out-dir", str(tmp_path / "traces")])
        assert rc == 2
        assert "no trajectories" in capsys.readouterr().err

    def test_no_subcommand_prints_usage(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["trace"]) == 2
        assert "Usage: rlm trace" in capsys.readouterr().out


class TestTraceRenderWithACorpus:
    """With a corpus configured, the page carries the passage behind the address."""

    @pytest.fixture
    def mined(self, corpus: Path, tmp_path: Path,
              capsys: pytest.CaptureFixture) -> Path:
        index = tmp_path / "derived" / "corpus.sqlite"
        index.parent.mkdir()
        cli_main(["corpus", "index", "--corpus-root", str(corpus),
                  "--corpus-index", str(index)])
        cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                  "--corpus-index", str(index)])
        cli_main(["mine", "plan", "--corpus-index", str(index)])
        cli_main(["mine", "run", "--corpus-root", str(corpus),
                  "--corpus-index", str(index), "--tasks", "index_text"])
        capsys.readouterr()
        return index

    def test_the_page_embeds_the_passage(
        self, tmp_path: Path, corpus: Path, mined: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        traj = _trajectory(tmp_path / "live-ask-5.jsonl")
        out = tmp_path / "traces"
        rc = cli_main(["trace", "render", str(traj), "--out-dir", str(out),
                       "--corpus-root", str(corpus), "--corpus-index", str(mined)])
        captured = capsys.readouterr()
        assert rc == 0, captured.err

        page = next(p for p in out.glob("*.md") if p.name != "index.md")
        text = page.read_text(encoding="utf-8")
        assert PASSAGE in text, "the passage behind a cited address must be on the page"
        # And the terminal never saw it.
        assert PASSAGE not in captured.out

    def test_no_passages_keeps_the_addresses_and_drops_the_text(
        self, tmp_path: Path, corpus: Path, mined: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        traj = _trajectory(tmp_path / "live-ask-6.jsonl")
        out = tmp_path / "traces"
        cli_main(["trace", "render", str(traj), "--out-dir", str(out),
                  "--corpus-root", str(corpus), "--corpus-index", str(mined),
                  "--no-passages"])
        capsys.readouterr()

        page = next(p for p in out.glob("*.md") if p.name != "index.md")
        text = page.read_text(encoding="utf-8")
        assert ADDRESS in text
        assert PASSAGE not in text
