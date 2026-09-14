"""Tests for `rlm mine` — the operator's side of the mining queue (RO3).

What matters here is the *control surface*, because the owner's windows are
variable: `plan` must be idempotent, `run` must stop for a budget, a deadline or
a pause and leave the rest queued, a second worker must be refused rather than
allowed to fight for the same machine, and every line of output must be an
aggregate — the mining logs are the kind of thing that gets pasted into a chat.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from rlm_local.cli import main as cli_main


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "notes.txt").write_text("plain text\n", encoding="utf-8")
    (root / "docs" / "report.pdf").write_bytes(b"%PDF-1.4\nplain\n")
    (root / "docs" / "manual.docx").write_bytes(_docx_bytes("Godot is the engine."))
    with zipfile.ZipFile(root / "bundle.zip", "w") as archive:
        archive.writestr("inner/one.txt", "x" * 10)
    return root


def _docx_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f"<w:t>{text}</w:t>")
    return buffer.getvalue()


@pytest.fixture
def built(corpus: Path, tmp_path: Path) -> Path:
    """An index built and classified, as Stage 1 would leave it."""
    index = tmp_path / "derived" / "corpus.sqlite"
    index.parent.mkdir()
    assert cli_main(["corpus", "index", "--corpus-root", str(corpus),
                     "--corpus-index", str(index)]) == 0
    assert cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                     "--corpus-index", str(index)]) == 0
    return index


class TestPlan:
    def test_plan_queues_from_the_map(self, built: Path,
                                      capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["mine", "plan", "--corpus-index", str(built)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "queued" in out
        assert "list_archive" in out and "extract_text" in out

    def test_planning_twice_adds_nothing(self, built: Path,
                                         capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        capsys.readouterr()
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        assert "queued 0 new items" in capsys.readouterr().out

    def test_the_report_names_no_file(self, built: Path,
                                      capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        out = capsys.readouterr().out
        for name in ("notes.txt", "bundle.zip", "manual.docx", "report.pdf"):
            assert name not in out


class TestStatus:
    def test_status_reports_depth_and_cache(self, built: Path,
                                            capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        capsys.readouterr()
        rc = cli_main(["mine", "status", "--corpus-index", str(built)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "mining queue:" in out
        assert "archive members recorded" in out
        assert "cache/list_archive" in out

    def test_an_empty_queue_says_so(self, built: Path,
                                    capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "status", "--corpus-index", str(built)])
        assert "rlm mine plan" in capsys.readouterr().out

    def test_a_missing_index_is_an_error(self, tmp_path: Path,
                                         capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["mine", "status", "--corpus-index", str(tmp_path / "no.sqlite")])
        assert rc == 2
        assert "no index at" in capsys.readouterr().err

    def test_no_index_flag_is_an_error(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_main(["mine", "status"]) == 2
        assert "required" in capsys.readouterr().err


class TestRun:
    def test_a_bounded_run_works_and_stops(self, corpus: Path, built: Path,
                                           capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        capsys.readouterr()
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--max-items", "2",
                       "--for", "1h", "--progress-every", "1"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "stopped: max_items" in out
        assert "done" in out

    def test_the_budget_accepts_minutes(self, corpus: Path, built: Path,
                                        capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        capsys.readouterr()
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--for", "0s"])
        assert rc == 0
        assert "stopped: budget" in capsys.readouterr().out

    def test_a_bad_duration_is_an_error(self, corpus: Path, built: Path,
                                        capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--for", "soon"])
        assert rc == 2
        assert "cannot read --for" in capsys.readouterr().err

    def test_a_bad_deadline_is_an_error(self, corpus: Path, built: Path,
                                        capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--until", "teatime"])
        assert rc == 2
        assert "cannot read --until" in capsys.readouterr().err

    def test_an_unknown_task_is_an_error(self, corpus: Path, built: Path,
                                         capsys: pytest.CaptureFixture) -> None:
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--tasks", "daydream"])
        assert rc == 2
        assert "unknown task" in capsys.readouterr().err

    def test_a_second_worker_is_refused(self, corpus: Path, built: Path,
                                        capsys: pytest.CaptureFixture) -> None:
        """Two miners on a 15 GiB box is how swapping starts."""
        lock = built.parent / "mine.lock"
        lock.write_text("12345\n", encoding="utf-8")  # fresh mtime: held
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--max-items", "1"])
        assert rc == 2
        assert "another mining worker holds" in capsys.readouterr().err
        assert lock.exists(), "the refused worker must not steal the lock"

    def test_the_lock_is_released_after_a_run(self, corpus: Path, built: Path,
                                              capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        cli_main(["mine", "run", "--corpus-root", str(corpus),
                  "--corpus-index", str(built), "--max-items", "1"])
        capsys.readouterr()
        assert not (built.parent / "mine.lock").exists()

    def test_a_paused_worker_does_nothing(self, corpus: Path, built: Path,
                                          capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        cli_main(["mine", "pause", "--corpus-index", str(built)])
        capsys.readouterr()
        rc = cli_main(["mine", "run", "--corpus-root", str(corpus),
                       "--corpus-index", str(built), "--max-items", "5"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "stopped: paused" in out
        assert "done 0," in out

    def test_resume_clears_the_flag(self, built: Path,
                                    capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "pause", "--corpus-index", str(built)])
        capsys.readouterr()
        assert cli_main(["mine", "resume", "--corpus-index", str(built)]) == 0
        assert "resumed" in capsys.readouterr().out
        # Idempotent: resuming when nothing is paused is not an error.
        assert cli_main(["mine", "resume", "--corpus-index", str(built)]) == 0

    def test_status_shows_the_pause(self, built: Path,
                                    capsys: pytest.CaptureFixture) -> None:
        cli_main(["mine", "pause", "--corpus-index", str(built)])
        capsys.readouterr()
        cli_main(["mine", "status", "--corpus-index", str(built)])
        assert "PAUSED" in capsys.readouterr().out


class TestRetry:
    def test_retry_requeues_failures(self, corpus: Path, built: Path,
                                     capsys: pytest.CaptureFixture) -> None:
        """A damaged container must be re-queueable once the code improves."""
        (corpus / "broken.zip").write_bytes(b"PK\x03\x04 not a zip")
        assert cli_main(["corpus", "index", "--corpus-root", str(corpus),
                         "--corpus-index", str(built)]) == 0
        assert cli_main(["corpus", "classify", "--corpus-root", str(corpus),
                         "--corpus-index", str(built)]) == 0
        cli_main(["mine", "plan", "--corpus-index", str(built)])
        cli_main(["mine", "run", "--corpus-root", str(corpus),
                  "--corpus-index", str(built), "--tasks", "list_archive"])
        capsys.readouterr()
        rc = cli_main(["mine", "retry", "--corpus-index", str(built),
                       "--task", "list_archive"])
        assert rc == 0
        assert "re-queued 1 failed item(s)" in capsys.readouterr().out


class TestDurations:
    def test_seconds_minutes_hours(self) -> None:
        from rlm_local.cli import _parse_duration

        assert _parse_duration("90") == 90
        assert _parse_duration("90s") == 90
        assert _parse_duration("30m") == 1800
        assert _parse_duration("2h") == 7200
        assert _parse_duration("") is None
        assert _parse_duration(None) is None
        assert _parse_duration("soon") is None
        assert _parse_duration("-5") == 0.0
