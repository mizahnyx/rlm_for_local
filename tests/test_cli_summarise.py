"""Tests for `rlm summarise` — the part that runs without a model.

The command's job is to spend *generation* on a hand-picked set, and the expensive part needs a
server. What can be tested here is everything that decides what will be spent: the plan is read
(tolerantly, and drops are reported), the selection comes from the head of the ranking, the
report is aggregates, and nothing is enqueued until the operator asks for it.
"""

from __future__ import annotations

import json
from pathlib import Path

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.enrich import Candidate, write_plan
from rlm_kernel.mine import PENDING, SUMMARISE, MineStore
from rlm_kernel.mounts import LocalTreeMount
from rlm_local.cli import main
from rlm_local.summarise import BACKEND_DEFAULT_TIMEOUT


def _corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.txt").write_text("A sentence. " * 200, encoding="utf-8")
    (corpus / "two.txt").write_text("Another sentence. " * 200, encoding="utf-8")
    index_path = tmp_path / "derived" / "corpus.sqlite"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    mount = LocalTreeMount(corpus)
    index = CorpusIndex.open_for(corpus, index_path)
    index.build(mount)
    index.classifications().ensure()
    classify_entries(mount, index.classifications())
    index.close()
    return corpus, index_path


def _plan(tmp_path: Path, rows: str | None = None) -> Path:
    path = tmp_path / "derived" / "enrich-plan.tsv"
    if rows is not None:
        path.write_text(rows, encoding="utf-8")
        return path
    write_plan(
        [Candidate("one.txt", "prose", 4, 2), Candidate("two.txt", "code", 9, 0)], path,
    )
    return path


def _run(tmp_path: Path, *extra: str) -> tuple[int, str]:
    corpus, index_path = _corpus(tmp_path)
    plan = _plan(tmp_path)
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main([
            "summarise",
            "--corpus-root", str(corpus),
            "--corpus-index", str(index_path),
            "--plan", str(plan),
            *extra,
        ])
    return code, buffer.getvalue()


def _pending(tmp_path: Path) -> int:
    index = CorpusIndex(tmp_path / "derived" / "corpus.sqlite")
    try:
        status = MineStore(index._conn).status()  # noqa: SLF001
    finally:
        index.close()
    return int(status["queued_by_task"].get(SUMMARISE, {}).get(PENDING, 0))


class TestTheDryRun:
    def test_it_reports_the_set_and_enqueues_nothing(self, tmp_path: Path) -> None:
        code, output = _run(tmp_path, "--limit", "2", "--dry-run")
        assert code == 0
        assert "2 document(s) to consider; 1 of them cited by an answer" in output
        assert "selected 2 of 2 document(s)" in output
        assert "dry run: nothing enqueued, no model called" in output
        assert _pending(tmp_path) == 0, "a dry run must not fill the queue"

    def test_the_default_limit_is_small_on_purpose(self, tmp_path: Path) -> None:
        """Each document costs minutes of a 4B model; a big default would spend a night."""
        code, output = _run(tmp_path, "--dry-run")
        assert code == 0
        assert "selected 2 of 2 document(s)" in output

    def test_cited_only_selects_from_the_cited_head(self, tmp_path: Path) -> None:
        code, output = _run(tmp_path, "--dry-run", "--cited-only", "--limit", "1")
        assert code == 0
        assert "selected 1 of 2 document(s), cited only" in output

    def test_a_limit_of_zero_says_so_rather_than_running(self, tmp_path: Path) -> None:
        code, output = _run(tmp_path, "--dry-run", "--limit", "0")
        assert code == 0
        assert "nothing selected: nothing to summarise" in output

    def test_the_cost_estimate_is_printed_with_the_set(self, tmp_path: Path) -> None:
        """The plan's own arithmetic, so the operator sees what the selection implies."""
        _code, output = _run(tmp_path, "--dry-run")
        assert "hours of inference" in output


class TestTheCommandWithAStubBackend:
    """The whole path minus the model: plan → enqueue → engine → cache → derived index → report.

    The backend is stubbed at the module the command imports it from, so no server is needed while
    the wiring that spends real minutes on real hardware is still exercised — including the
    derived timeout reaching the client, which is the bug the first live run found.
    """

    def test_one_document_is_described_logged_and_indexed(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        import io
        from contextlib import redirect_stdout

        corpus, index_path = _corpus(tmp_path)
        plan = _plan(tmp_path)
        seen: dict = {}
        reply = "A description of a text document, with words of its own."

        class StubBackend:
            def __init__(self, **kwargs):
                seen["kwargs"] = kwargs

            def chat(self, messages, **kwargs):
                seen["messages"] = messages
                return reply

            def close(self):
                seen["closed"] = True

        monkeypatch.setattr("rlm_local.model_backend.HTTPModelBackend", StubBackend)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([
                "summarise", "--corpus-root", str(corpus), "--corpus-index", str(index_path),
                "--plan", str(plan), "--limit", "1", "--cited-only",
                "--model", "Stub-Model", "--endpoint", "https://stub:1/v1",
            ])
        output = buffer.getvalue()
        assert code == 0
        assert "Stub-Model@stub:1" in output, "the named model must be the engine's identity"
        assert seen["closed"] is True, "the backend is closed even on the happy path"
        assert seen["kwargs"]["timeout"] > BACKEND_DEFAULT_TIMEOUT, (
            "the derived timeout must reach the client; 300 s is what killed the first live run"
        )
        assert reply not in output, (
            "the report is aggregates: a description is the document's own prose and may not "
            "be printed (AGENTS.md §1.9)"
        )

        records = [
            json.loads(line)
            for line in (tmp_path / "derived" / "summaries.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        assert len(records) == 1
        assert records[0]["model"] == "Stub-Model@stub:1"
        assert records[0]["output_chars"] == len(reply)
        assert _pending(tmp_path) == 0, "the item was worked, not left queued"

        index = CorpusIndex(index_path)
        try:
            derived = index._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM text_chunks WHERE derived = 1"
            ).fetchone()[0]
        finally:
            index.close()
        assert derived > 0, "a description nobody can search for is a description nobody reads"

    def test_a_second_run_costs_no_model_call(self, tmp_path: Path, monkeypatch) -> None:
        """A description already paid for is a cache hit — the whole point of a derivation key."""
        import io
        from contextlib import redirect_stdout

        corpus, index_path = _corpus(tmp_path)
        plan = _plan(tmp_path)
        calls: list[int] = []

        class StubBackend:
            def __init__(self, **kwargs):
                pass

            def chat(self, messages, **kwargs):
                calls.append(1)
                return "A description of a text document."

            def close(self):
                pass

        monkeypatch.setattr("rlm_local.model_backend.HTTPModelBackend", StubBackend)
        argv = [
            "summarise", "--corpus-root", str(corpus), "--corpus-index", str(index_path),
            "--plan", str(plan), "--limit", "1", "--cited-only",
            "--model", "Stub-Model", "--endpoint", "https://stub:1/v1",
        ]
        with redirect_stdout(io.StringIO()):
            assert main(argv) == 0
            assert main(argv) == 0
        assert len(calls) == 1, "the second run must be served from the cache"


class TestThePlanIsReadTolerantlyAndReported:
    def test_a_missing_plan_is_an_error_not_an_empty_run(self, tmp_path: Path) -> None:
        corpus, index_path = _corpus(tmp_path)
        import io
        from contextlib import redirect_stdout, redirect_stderr

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([
                "summarise", "--corpus-root", str(corpus), "--corpus-index", str(index_path),
                "--plan", str(tmp_path / "derived" / "absent.tsv"), "--dry-run",
            ])
        assert code == 2
        assert "no enrichment plan" in err.getvalue()

    def test_dropped_rows_are_reported(self, tmp_path: Path) -> None:
        corpus, index_path = _corpus(tmp_path)
        plan = _plan(tmp_path, rows="1\tbroken\n2\tone.txt\t1\t1\tprose\n")
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([
                "summarise", "--corpus-root", str(corpus), "--corpus-index", str(index_path),
                "--plan", str(plan), "--dry-run",
            ])
        assert code == 0
        assert "1 plan row(s) dropped as malformed" in buffer.getvalue()
