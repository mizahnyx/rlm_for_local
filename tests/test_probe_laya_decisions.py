"""Tests for the Laya probe runner's scoring (Gate 6, phase B1).

The properties that matter: a case with no expected action is *reported, not scored* (scoring it
would manufacture precision), an error is kept out of accuracy (a transport failure is not a wrong
answer), and the report never carries a card — because the same runner is meant to be pointed at real
cards later, where printing one leaks a document.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.probe_laya_decisions import load_cases, main, render, score

CASES = [
    {"id": "a", "question": "q", "card": "Zanzibaricum sprocket notes.", "expected": "expand"},
    {"id": "b", "question": "q", "card": "A long manual.", "expected": "summarise"},
    {"id": "c", "question": "q", "card": "Ambiguous.", "expected": None},
]


def _decision(action: str | None = None, confidence: float = 0.5, error: str | None = None) -> dict:
    if error is not None:
        return {"error": error}
    return {"action": action, "action_confidence": confidence, "relevant": 0.7,
            "importance": 2.0, "seconds": 0.1}


class TestTheCaseFile:
    def test_the_shipped_set_loads_and_is_self_consistent(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "laya_decision_cases.json"
        cases = load_cases(path)
        assert len(cases) >= 10
        assert len({case["id"] for case in cases}) == len(cases), "ids identify rows in the report"
        assert any(case["expected"] is None for case in cases), (
            "the set must contain cases the probe declines to score, or it is pretending to know"
        )

    def test_a_case_without_a_card_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(json.dumps({"cases": [{"id": "x", "question": "q", "card": ""}]}),
                        encoding="utf-8")
        with pytest.raises(ValueError):
            load_cases(path)

    def test_an_expectation_outside_the_vocabulary_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(json.dumps({"cases": [
            {"id": "x", "question": "q", "card": "c", "expected": "summarise_and_expand"}]}),
            encoding="utf-8")
        with pytest.raises(ValueError):
            load_cases(path)


class TestTheScoring:
    def test_a_case_without_an_expectation_is_reported_not_scored(self) -> None:
        report = score(CASES, [_decision("expand"), _decision("drop"), _decision("one_line")])
        assert report["scored"] == 2
        assert report["unscored"] == 1
        assert report["accuracy"] == 0.5, "one right, one wrong, one unscored"

    def test_an_error_is_not_counted_as_a_wrong_answer(self) -> None:
        report = score(CASES, [_decision("expand"), _decision(error="ReadTimeout"),
                               _decision("drop")])
        assert report["errored"] == 1
        assert report["scored"] == 1
        assert report["accuracy"] == 1.0, "a transport failure says nothing about the decision"

    def test_calibration_is_the_confidence_split(self) -> None:
        report = score(CASES, [_decision("expand", 0.9), _decision("drop", 0.3),
                               _decision("drop", 0.1)])
        assert report["confidence_when_correct"] == 0.9
        assert report["confidence_when_wrong"] == 0.3

    def test_the_confusion_matrix_counts_what_was_chosen(self) -> None:
        report = score(CASES, [_decision("one_line"), _decision("summarise"), _decision("drop")])
        assert report["confusion"]["expand"]["one_line"] == 1
        assert report["confusion"]["summarise"]["summarise"] == 1

    def test_no_scorable_case_gives_no_accuracy(self) -> None:
        """`None`, not 0.0: with nothing scored there is no accuracy to report."""
        report = score([CASES[2]], [_decision("drop")])
        assert report["accuracy"] is None


class TestTheReport:
    def test_it_carries_ids_and_labels_and_no_card_text(self) -> None:
        text = render(score(CASES, [_decision("expand"), _decision("summarise"),
                                    _decision("drop")]))
        assert "answer" in text or "a" in text
        for word in ("zanzibaricum", "sprocket", "manual"):
            assert word not in text.lower(), "a report that quoted a card could not be used on real ones"

    def test_it_names_the_unscored_and_errored_counts(self) -> None:
        text = render(score(CASES, [_decision("expand"), _decision(error="ReadTimeout"),
                                    _decision("drop")]))
        assert "without an expectation" in text
        assert "1 errored" in text


class TestTheRunnerFailsHonestly:
    def test_an_unreachable_server_gives_error_rows_not_a_crash(self, tmp_path: Path) -> None:
        """Phase B1 must be runnable before the model is: the report is the artefact, not a traceback."""
        cases = tmp_path / "cases.json"
        cases.write_text(json.dumps({"cases": CASES}), encoding="utf-8")
        out = tmp_path / "report.json"
        code = main([
            "--cases", str(cases), "--endpoint", "http://127.0.0.1:9", "--out", str(out),
        ])
        assert code == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["errored"] == len(CASES), "every case should be an error row"
        assert report["accuracy"] is None
