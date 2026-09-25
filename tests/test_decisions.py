"""Tests for the admission-decision client (Gate 6).

The properties that matter are the refusals, not the happy path. The model's own documentation says
over-long input is *rejected*, so this module raises instead of shortening — a shortened card is a
decision about a different card. And a label outside our vocabulary is refused rather than mapped to
a default, because a confident answer to a question nobody asked is the failure this project ranks
below silence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rlm_local.decisions import (
    ACTIONS,
    ACTION_CRITERIA,
    MAX_QUESTION_TOKENS,
    CardTooLarge,
    MalformedDecision,
    UnknownAction,
    build_request,
    estimated_tokens,
    make_admission_engine,
    parse_response,
)

CARD = "Zanzibaricum sprocket calibration notes, revision nine: tolerance 0.02 millimetres."
QUESTION = "What is the sprocket tolerance?"


def _answers(action: str = "expand", noul: float = 0.8, score: float = 2.0) -> dict:
    return {
        "answers": {
            "action": {"type": "choice", "choice": action,
                       "probabilities": {a: 0.25 for a in ACTIONS}, "confidence": 0.4},
            "relevant": {"type": "noul", "noul": noul},
            "importance": {"type": "score", "score": score, "probabilities": [0.1, 0.2, 0.3, 0.4],
                           "legend": {"0": "no", "1": "background", "2": "part", "3": "the answer"}},
        }
    }


class FakeClient:
    """Records the payloads it was given, and can be made to fail."""

    def __init__(self, action: str = "expand", error: Exception | None = None):
        self.action = action
        self.error = error
        self.payloads: list[dict] = []

    def decide(self, payload: dict) -> dict:
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return _answers(self.action)


class TestTheRequestShape:
    def test_the_card_is_the_state_and_three_questions_ride_with_it(self) -> None:
        payload = build_request(question=QUESTION, card=CARD, turn=3, turns_left=5)
        assert payload["state"] == CARD
        assert set(payload["questions"]) == {"action", "relevant", "importance"}
        action = payload["questions"]["action"]
        assert action["type"] == "choice"
        assert list(action["criteria"]) == list(ACTIONS), "the order is the presented order"
        assert payload["questions"]["relevant"]["type"] == "noul"
        assert payload["questions"]["importance"]["type"] == "score"
        assert len(payload["questions"]["importance"]["levels"]) == 4

    def test_the_question_and_the_position_reach_the_instructions(self) -> None:
        payload = build_request(question=QUESTION, card=CARD, turn=3, turns_left=5)
        instructions = payload["questions"]["action"]["instructions"]
        assert QUESTION in instructions
        assert "turn 3" in instructions and "5 left" in instructions

    def test_the_criteria_are_worded_for_the_model(self) -> None:
        """They are the options the model chooses between, so an empty one is a broken question."""
        assert set(ACTION_CRITERIA) == set(ACTIONS)
        assert all(len(text) > 20 for text in ACTION_CRITERIA.values())


class TestTheBudgetIsARejection:
    def test_a_card_over_the_budget_is_refused_not_shortened(self) -> None:
        huge = "word " * 4_000
        with pytest.raises(CardTooLarge) as excinfo:
            build_request(question=QUESTION, card=huge)
        assert str(MAX_QUESTION_TOKENS) in str(excinfo.value)
        assert "formatted tokens" in str(excinfo.value)

    def test_a_card_within_the_budget_is_accepted(self) -> None:
        assert build_request(question=QUESTION, card=CARD)["state"] == CARD

    def test_the_estimate_is_named_as_an_estimate(self) -> None:
        assert estimated_tokens("x" * 300) == 101


class TestTheAnswersAreCheckedNotDefaulted:
    def test_a_well_formed_response_becomes_a_decision(self) -> None:
        decision = parse_response(_answers("summarise", noul=0.9, score=3.0))
        assert decision["action"] == "summarise"
        assert decision["relevant"] == 0.9
        assert decision["importance"] == 3.0
        assert decision["action_confidence"] == 0.4

    def test_a_json_string_is_accepted_too(self) -> None:
        assert parse_response(json.dumps(_answers()))["action"] == "expand"

    def test_an_action_outside_the_vocabulary_is_refused(self) -> None:
        """Never mapped to a default: the model answering a question nobody asked is the danger."""
        with pytest.raises(UnknownAction):
            parse_response(_answers("summarise_and_expand"))

    def test_a_missing_answer_is_refused_rather_than_invented(self) -> None:
        payload = _answers()
        del payload["answers"]["relevant"]
        with pytest.raises(MalformedDecision):
            parse_response(payload)

    def test_a_response_with_no_answers_at_all_is_refused(self) -> None:
        with pytest.raises(MalformedDecision):
            parse_response({"model": "laya"})

    def test_a_wrong_type_for_a_question_is_refused(self) -> None:
        payload = _answers()
        payload["answers"]["importance"] = {"type": "choice", "choice": "expand"}
        with pytest.raises(MalformedDecision):
            parse_response(payload)


class TestTheEngine:
    def test_one_request_per_card_in_order(self) -> None:
        client = FakeClient()
        engine = make_admission_engine(client, model="laya-typed-decisions")
        cards = [CARD, CARD + " second"]
        decisions = engine(QUESTION, cards)
        assert len(client.payloads) == 2, "one state per request, per the model's own shape"
        assert [d["card_index"] for d in decisions] == [0, 1]
        assert all(d["model"] == "laya-typed-decisions" for d in decisions)
        assert engine.engine_tag == "laya-typed-decisions"

    def test_a_failing_call_is_logged_and_reraised(self, tmp_path: Path) -> None:
        log = tmp_path / "decisions.jsonl"
        engine = make_admission_engine(
            FakeClient(error=RuntimeError("server down")), model="laya", log_path=log,
        )
        with pytest.raises(RuntimeError):
            engine(QUESTION, [CARD])
        record = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["error"] == "RuntimeError"
        assert record["card_chars"] == len(CARD)

    def test_the_log_quotes_no_card_text(self, tmp_path: Path) -> None:
        """A card is corpus-derived prose: a log that quoted one leaks a document a line at a time."""
        log = tmp_path / "decisions.jsonl"
        engine = make_admission_engine(FakeClient(), model="laya", log_path=log)
        engine(QUESTION, [CARD])
        text = log.read_text(encoding="utf-8").lower()
        for word in ("zanzibaricum", "sprocket", "calibration", "millimetres"):
            assert word not in text
        record = json.loads(text.strip().splitlines()[-1])
        assert record["card_chars"] == len(CARD)
        assert record["action"] == "expand"

    def test_an_over_budget_card_is_logged_then_refused(self, tmp_path: Path) -> None:
        log = tmp_path / "decisions.jsonl"
        engine = make_admission_engine(FakeClient(), model="laya", log_path=log)
        with pytest.raises(CardTooLarge):
            engine(QUESTION, ["word " * 4_000])
        record = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["error"] == "CardTooLarge"

    def test_no_log_path_writes_nothing(self, tmp_path: Path) -> None:
        engine = make_admission_engine(FakeClient(), model="laya", log_path=None)
        engine(QUESTION, [CARD])
        assert list(tmp_path.iterdir()) == []
