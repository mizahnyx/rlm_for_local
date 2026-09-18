"""Tests for the question probe: parsing, failure tolerance, and what it prints.

The probe's job is to run real questions and report *counts*. Two of these tests are
privacy guards rather than behaviour tests, and they are the important ones: the line
the probe prints must never carry the answer, and the question set that ships in this
repository must never carry a question devised from a passage (`AGENTS.md` §1.9).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rlm_local.config import load_config
from rlm_local.question_probe import (
    DEFAULT_QUESTIONS,
    Question,
    parse_questions,
    render_line,
    run_question,
    write_question_set,
)


class StubBackend:
    """A backend that returns scripted responses: no model, no server."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages: list[dict[str, str]], *, tier: str = "root",
             max_tokens: int = 1500, temperature: float = 0.0,
             response_schema: dict[str, Any] | None = None) -> str:
        if self.responses:
            return self.responses.pop(0)
        return "FINAL: no more scripted responses"


class TestParsingAQuestionSet:
    def test_one_question_per_line_and_comments_are_ignored(self) -> None:
        parsed = parse_questions(
            "# a question set\n"
            "\n"
            "how-many\tHow many files are there?\n"
            "What is it about?\n"
            "   \n"
        )
        assert [(q.id, q.question) for q in parsed] == [
            ("how-many", "How many files are there?"),
            ("q2", "What is it about?"),
        ]

    def test_a_bare_question_is_numbered_in_file_order(self) -> None:
        parsed = parse_questions("First?\nSecond?\n")
        assert [q.id for q in parsed] == ["q1", "q2"]

    def test_an_id_becomes_a_file_name(self) -> None:
        parsed = parse_questions("How many files?!\tHow many files are there?\n")
        assert parsed[0].id == "how-many-files"

    def test_an_id_cannot_escape_the_output_directory(self) -> None:
        """The id names a file under `--out-dir`, so it must not be able to walk out.

        A question file is something the owner edits by hand, and the id is written
        into a path in `run_question`. `../../somewhere` must come back as a plain
        name rather than as a directory traversal.
        """
        parsed = parse_questions("../../escape/me\tWhat is here?\n")
        assert parsed[0].id == "escape-me", parsed[0].id
        assert "/" not in parsed[0].id and ".." not in parsed[0].id

    def test_an_empty_set_is_empty_rather_than_an_error(self) -> None:
        assert parse_questions("# nothing here\n") == []


class TestWhatTheProbePrints:
    def test_the_line_carries_the_run_and_not_the_answer(self, tmp_path: Path) -> None:
        """The guard that keeps corpus text out of the terminal that reads the probe.

        A probe whose line carried the answer would put corpus text wherever the
        operator's stdout goes. The answer here is a phrase that exists nowhere else,
        so its absence from the line is checkable.
        """
        secret = "ZQ-7741 the archive access code is in this passage"
        cfg = load_config("tiny", max_turns=2)
        backend = StubBackend([
            "```repl\nprint('looking')\n```",
            f"```repl\nanswer['content'] = {secret!r}\nanswer['ready'] = True\n```",
        ])
        run = run_question(Question(id="q1", question="What is the code?"),
                           config=cfg, backend=backend, corpus_bridge=None,
                           out_dir=tmp_path)

        assert run.error is None, run.error
        line = render_line(run)
        assert secret not in line
        assert "ZQ-7741" not in line
        # The aggregates are what the operator is meant to read.
        assert "wall=" in line
        assert "turns=" in line
        # ...and the answer *is* kept, in the trajectory beside the corpus.
        events = [json.loads(l) for l in
                  run.trajectory.read_text(encoding="utf-8").splitlines() if l.strip()]
        end = next(e for e in events if e.get("event") == "end")
        assert "ZQ-7741" in str(end.get("final_answer", ""))

    def test_a_failing_question_is_recorded_and_does_not_take_the_probe_with_it(
        self, tmp_path: Path,
    ) -> None:
        class Exploding(StubBackend):
            def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
                raise ConnectionError("the router is down")

        cfg = load_config("tiny", max_turns=1)
        run = run_question(Question(id="q9", question="Anything?"),
                           config=cfg, backend=Exploding([]),
                           corpus_bridge=None, out_dir=tmp_path)
        assert run.error is not None
        assert "the router is down" in run.error
        assert "error=" in render_line(run)


class TestTheQuestionSetThatShipsHere:
    def test_the_shipped_questions_are_about_aggregates_only(self) -> None:
        """What may live in this repository: counts, never content.

        A question devised from a passage is corpus-derived and belongs beside the
        corpus. If someone adds one here, this is where it is caught: an address, a
        path or a quoted fragment has no business in a public question set.
        """
        for name, question in DEFAULT_QUESTIONS:
            assert name and question
            assert "#L" not in question, question
            assert "!" not in question, question
            assert "/" not in question, question
            assert '"' not in question and "'" not in question, question

    def test_the_parsed_default_set_round_trips_through_a_file(
        self, tmp_path: Path,
    ) -> None:
        questions = [Question(id=name, question=text)
                     for name, text in DEFAULT_QUESTIONS]
        path = tmp_path / "questions.txt"
        write_question_set(questions, path)
        parsed = parse_questions(path.read_text(encoding="utf-8"))
        assert parsed == questions


@pytest.mark.parametrize("questions", [DEFAULT_QUESTIONS])
def test_the_default_set_is_not_empty(questions) -> None:
    """A vacuity guard: an empty shipped set would make the probe do nothing."""
    assert len(questions) >= 3
