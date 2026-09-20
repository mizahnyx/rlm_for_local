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

    def test_two_questions_cannot_share_one_trajectory(self) -> None:
        """A repeated id would otherwise overwrite the first question's evidence.

        Note whose id moves: the *second* `dup` becomes `dup-3` rather than `dup-2`,
        because `dup-2` is written explicitly on the next line and an id the owner
        wrote down keeps its name. Every question survives, under a distinct name.
        """
        parsed = parse_questions(
            "dup\tWhat is the first thing?\n"
            "dup\tWhat is the second thing?\n"
            "dup-2\tAnd a third?\n"
            "dup\tAnd a fourth?\n"
        )
        assert [q.id for q in parsed] == ["dup", "dup-3", "dup-2", "dup-4"]
        assert len({q.id for q in parsed}) == 4
        assert "second" in parsed[1].question, "no question may be dropped"

    def test_an_id_separated_by_spaces_is_refused_not_misread(self) -> None:
        """A TAB separates the id from the question; spaces would hide the mistake.

        Read as a question, `how-many-files  How many files?` would file itself under
        an automatic id and the error would surface only as a trajectory named `q3`.
        The refusal names the fix, and the shape it refuses is narrow enough that a
        question beginning with a short lowercase word is still a question (below).
        """
        with pytest.raises(ValueError) as raised:
            parse_questions("how-many-files  How many files are there?\n")
        assert "TAB" in str(raised.value)

    def test_a_question_that_merely_contains_spaces_is_still_a_question(self) -> None:
        for line in ("what  is this about?\n", "Note  two spaces after a capital.\n"):
            parsed = parse_questions(line)
            assert len(parsed) == 1, line
            assert parsed[0].id == "q1"
            assert parsed[0].question == line.strip()

    def test_an_empty_set_is_empty_rather_than_an_error(self) -> None:
        assert parse_questions("# nothing here\n") == []


class TestSelectingQuestions:
    """`--only` must say what it searched, not just that it found nothing.

    Measured 2026-09-19: the first attempt to re-run one of the owner's questions
    without `--questions` printed `No questions to run.` and looked like a broken
    filter. The set it had searched — the three built-in aggregate questions — was
    never mentioned.
    """

    def test_a_filter_that_matches_nothing_names_what_the_set_holds(self) -> None:
        from rlm_local.question_probe import select_questions

        questions = [Question(id="how-many-entries", question="How many?")]
        with pytest.raises(ValueError) as raised:
            select_questions(questions, "no-such-topic", source="the built-in set")
        message = str(raised.value)
        assert "no-such-topic" in message
        assert "the built-in set" in message, message
        assert "how-many-entries" in message, "it must say what the set does hold"
        assert "--questions" in message, "it must name the likely omission"

    def test_a_filter_selects_every_matching_id_case_insensitively(self) -> None:
        from rlm_local.question_probe import select_questions

        # Ids here are invented on purpose: a test that used a real question's id
        # would put a corpus-derived name in a public repository (AGENTS.md §1.9).
        questions = [Question(id="Gamma-Topic", question="a"),
                     Question(id="delta-topic", question="b"),
                     Question(id="q3", question="c")]
        assert [q.id for q in select_questions(questions, "GAMMA",
                                               source="s")] == ["Gamma-Topic"]
        assert [q.id for q in select_questions(questions, "topic", source="s")] == [
            "Gamma-Topic", "delta-topic"]

    def test_no_filter_runs_the_whole_set(self) -> None:
        from rlm_local.question_probe import select_questions

        questions = [Question(id="a", question="?"), Question(id="b", question="?")]
        assert select_questions(questions, None, source="s") == questions


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

    def test_the_shipped_example_is_a_valid_question_set(self) -> None:
        """The example the owner is handed must parse — and to exactly the defaults.

        A documented format that no longer parses is worse than no documentation, and
        `--example` writes this text straight to a file the operator then edits.
        """
        from rlm_local.question_probe import example_question_set_text

        expected = [Question(id=name, question=text)
                    for name, text in DEFAULT_QUESTIONS]
        assert parse_questions(example_question_set_text()) == expected
        # ...and its header must state the two rules a hand-editor gets wrong.
        example = example_question_set_text()
        assert "TAB" in example and "unique" in example


@pytest.mark.parametrize("questions", [DEFAULT_QUESTIONS])
def test_the_default_set_is_not_empty(questions) -> None:
    """A vacuity guard: an empty shipped set would make the probe do nothing."""
    assert len(questions) >= 3
