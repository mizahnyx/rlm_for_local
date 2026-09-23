"""Tests for the event-based question-set summariser.

The point of this tool is that it reads *events*, because reading the probe's one-line
summary is what produced a wrong published claim on 2026-09-22. So the tests feed it event
rows shaped like the real ones — including the `guardrail` key the earlier hand-written check
looked for in the wrong place — and assert that what it reports is what the events say.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "summarise_question_set", SCRIPTS / "summarise_question_set.py")
module = importlib.util.module_from_spec(spec)
sys.modules["summarise_question_set"] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def _served(verb: str) -> dict:
    return {"event": "corpus_served", "verb": verb}


def _guardrail(kind: str, detail: str = "") -> dict:
    return {"event": "guardrail", "guardrail": kind, "detail": detail}


def _turn() -> dict:
    return {"event": "turn_start"}


class TestItReadsTheEventsNotTheSummary:
    def test_a_hard_timeout_is_reported_with_its_budget(self) -> None:
        rows = [
            _turn(), _turn(),
            _served("corpus_search"), _served("corpus_search"), _served("corpus_read"),
            _guardrail("cell_extended", "elapsed=84s soft=60s hard=1200s last_helper=corpus_search"),
            _guardrail("cell_timeout",
                       "block=1 limit=hard budget=1200s activity=5 "
                       "last_helper=corpus_search corpus_calls=8 cell_timeouts=1"),
            _guardrail("corpus_last_turn", "last turn reached with 13 corpus helper call(s)"),
            _guardrail("corpus_citation", "answers_with_address=False chars=777"),
        ]
        got = module.summarise(rows)
        assert got["turns"] == 2
        assert got["helpers"] == {"corpus_search": 2, "corpus_read": 1}
        assert got["helper_calls"] == 3
        assert got["cell_timeouts"] == 1 and got["cell_extended"] == 1
        assert got["answers"] == 1 and got["answers_with_address"] is False
        assert got["last_turn_warning"] is True
        assert "limit=hard" in got["budget_events"][1]["detail"]

    def test_a_last_turn_warning_is_not_a_verdict_on_submission(self) -> None:
        """Measured on the live set: the warning fired on a run whose `end` says forced=False.

        The model reached its last turn with nothing submitted and then submitted inside it,
        so the flag means "the last turn was reached empty-handed", not "the answer was not
        submitted". Reading it as a verdict would have produced exactly the kind of wrong
        published claim this whole tool exists to prevent.
        """
        got = module.summarise([
            _guardrail("corpus_last_turn", "last turn reached with 4 corpus helper call(s)"),
            {"event": "end", "forced": False},
            _guardrail("corpus_citation", "answers_with_address=False chars=207"),
        ])
        assert got["last_turn_warning"] is True
        assert got["answers"] == 1

    def test_an_answer_with_an_address_is_reported_as_such(self) -> None:
        got = module.summarise([
            _guardrail("corpus_citation", "answers_with_address=True chars=200")])
        assert got["answers_with_address"] is True

    def test_bands_are_summed_across_searches(self) -> None:
        """`citable` is the number that decides whether a missing citation is a finding."""
        rows = [
            _guardrail("corpus_search_quality", "served weak=5 chars=2510"),
            _guardrail("corpus_search_quality", "served partial=1 weak=4 chars=2393"),
            _guardrail("corpus_search_quality", "served none=4 weak=1 chars=2570"),
        ]
        got = module.summarise(rows)
        assert got["bands"] == {"weak": 10, "partial": 1, "none": 4}
        assert got["served"] == 15 and got["citable_served"] == 1

    def test_an_empty_trajectory_is_not_an_error(self) -> None:
        got = module.summarise([])
        assert got["turns"] == 0 and got["served"] == 0 and got["answers"] == 0
        assert got["answers_with_address"] is False

    def test_the_rendered_line_carries_the_numbers_a_record_needs(self) -> None:
        line = module.render(module.summarise([
            _turn(), _guardrail("cell_timeout", "limit=hard budget=1200s"),
            _guardrail("corpus_citation", "answers_with_address=False chars=9"),
        ]), label="q")
        assert line.startswith("q: turns=1")
        for fragment in ("hard_timeouts=1", "with_address=False", "last_turn_warning=False"):
            assert fragment in line, line
