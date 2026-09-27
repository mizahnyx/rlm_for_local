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
        assert len(payload["questions"]["importance"]["criteria"]) == 4, (
            "the reference SDK spells the ordinal option list `criteria`"
        )

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


class TestTheSdkClient:
    """The reference package needs no server, and no `laya` install to test the wiring."""

    def test_it_loads_the_package_and_calls_system_one(self, monkeypatch) -> None:
        import sys
        import types

        from rlm_local.decisions import LayaSdkClient

        seen: dict = {}

        class FakeAgent:
            def system_one(self, state, questions):
                seen["state"] = state
                seen["questions"] = questions
                return _answers("one_line")

        fake = types.ModuleType("laya")

        def load(model, device=None):
            seen["model"] = model
            seen["device"] = device
            return FakeAgent()

        fake.load = load
        monkeypatch.setitem(sys.modules, "laya", fake)

        client = LayaSdkClient("some/model", device="cpu")
        decision = parse_response(client.decide(build_request(question=QUESTION, card=CARD)))
        assert seen["model"] == "some/model", "the checkpoint identity reaches the SDK"
        assert seen["device"] == "cpu"
        assert seen["state"] == CARD, "the card is the state, as the API shape requires"
        assert set(seen["questions"]) == {"action", "relevant", "importance"}
        assert decision["action"] == "one_line"

    def test_a_load_without_device_still_works(self, monkeypatch) -> None:
        """The package's own LAYA_DEVICE decides when no device is passed."""
        import sys
        import types

        from rlm_local.decisions import LayaSdkClient

        class FakeAgent:
            def system_one(self, state, questions):
                return _answers()

        fake = types.ModuleType("laya")
        fake.load = lambda model: FakeAgent()
        monkeypatch.setitem(sys.modules, "laya", fake)

        assert LayaSdkClient("some/model").decide(
            build_request(question=QUESTION, card=CARD)
        )["answers"]["action"]["choice"] == "expand"


class TestTheGliNERClient:
    """GLiNER2.5-Decide takes the same card and the same three questions, in one call.

    The mapping is a translation between two typed-request dialects, so it is written as two
    pure functions with the client as a thin shell — which is what makes it testable without
    the 340M model, and what keeps a wrong label from being quietly repaired on the way out.
    """

    def _schema(self) -> dict:
        from rlm_local.decisions import gliner_schema_from

        return gliner_schema_from(build_request(question=QUESTION, card=CARD,
                                                turn=2, turns_left=4))

    def test_each_question_becomes_one_head(self) -> None:
        schema = self._schema()
        assert set(schema) == {"action", "relevant", "importance"}

    def test_the_choice_head_carries_its_criteria_as_described_labels(self) -> None:
        """The criteria are the model's options, so their wording rides with the labels."""
        action = self._schema()["action"]
        assert set(action["labels"]) == set(ACTIONS)
        assert action["labels"]["drop"] == ACTION_CRITERIA["drop"]
        assert QUESTION in action["prompt"], "the question being answered reaches the head"

    def test_the_relevance_head_is_a_yes_no_decision(self) -> None:
        assert self._schema()["relevant"]["labels"] == ["yes", "no"]

    def test_the_ordinal_head_follows_its_criteria_count(self) -> None:
        """No hard-coded 0..3: the scale is whatever the request's criteria are."""
        from rlm_local.decisions import gliner_schema_from

        payload = build_request(question=QUESTION, card=CARD)
        payload["questions"]["importance"]["criteria"] = ["a", "b", "c", "d", "e"]
        head = gliner_schema_from(payload)["importance"]
        assert list(head["labels"]) == ["0", "1", "2", "3", "4"]

    def test_a_question_without_criteria_is_refused(self) -> None:
        from rlm_local.decisions import gliner_schema_from

        payload = build_request(question=QUESTION, card=CARD)
        del payload["questions"]["action"]["criteria"]
        with pytest.raises(MalformedDecision):
            gliner_schema_from(payload)

    def test_the_answers_come_back_in_the_shape_the_parser_expects(self) -> None:
        from rlm_local.decisions import gliner_answers_from

        result = {
            "action": {"label": "drop", "confidence": 0.31},
            "relevant": {"label": "no", "confidence": 0.5},
            "importance": {"label": "2", "confidence": 0.4},
        }
        decision = parse_response({"answers": gliner_answers_from(result)})
        assert decision["action"] == "drop"
        assert decision["action_confidence"] == 0.31
        assert decision["relevant"] is False
        assert decision["importance"] == 2

    def test_a_bare_label_is_accepted_and_carries_no_confidence(self) -> None:
        from rlm_local.decisions import gliner_answers_from

        decision = parse_response({"answers": gliner_answers_from({
            "action": "summarise", "relevant": "yes", "importance": "3"})})
        assert (decision["action"], decision["relevant"], decision["importance"]) == (
            "summarise", True, 3)
        assert decision["action_confidence"] is None

    def test_a_label_outside_the_vocabulary_is_not_repaired(self) -> None:
        """The adapter translates; it does not sanitise. `parse_response` is the guard."""
        from rlm_local.decisions import gliner_answers_from

        with pytest.raises(UnknownAction):
            parse_response({"answers": gliner_answers_from({"action": "expandd",
                                                            "relevant": "yes",
                                                            "importance": "1"})})

    def test_the_client_sends_the_card_and_asks_for_confidence(self, monkeypatch) -> None:
        import sys
        import types

        from rlm_local.decisions import GLiNERDecideClient

        seen: dict = {}

        class FakeExtractor:
            def classify_text(self, text, tasks, **kwargs):
                seen["text"] = text
                seen["tasks"] = tasks
                seen["kwargs"] = kwargs
                return {"action": {"label": "one_line", "confidence": 0.44},
                        "relevant": {"label": "yes", "confidence": 0.8},
                        "importance": {"label": "1", "confidence": 0.3}}

        fake = types.ModuleType("gliner2")
        fake.AutoExtractor = type("AutoExtractor", (), {
            "from_pretrained": staticmethod(
                lambda name: (seen.__setitem__("model", name), FakeExtractor())[1])})
        monkeypatch.setitem(sys.modules, "gliner2", fake)

        client = GLiNERDecideClient("some/checkpoint")
        decision = parse_response(client.decide(build_request(question=QUESTION, card=CARD)))
        assert seen["model"] == "some/checkpoint"
        assert seen["text"] == CARD, "the card is the text, as the API shape requires"
        assert set(seen["tasks"]) == {"action", "relevant", "importance"}
        assert seen["kwargs"].get("include_confidence") is True
        assert decision["action"] == "one_line"

    def test_split_heads_asks_one_question_per_call(self, monkeypatch) -> None:
        """Measured 2026-09-26: three heads in one call costs the action answer.

        The same 14 cards scored 5/12 with all three heads at once and 7/12 with the
        action question alone — and the `drop` label never appears at all in the
        three-head call. So the client can be told to split, and the probe exposes it.
        """
        import sys
        import types

        from rlm_local.decisions import GLiNERDecideClient

        calls: list[dict] = []

        class FakeExtractor:
            def classify_text(self, text, tasks, **kwargs):
                calls.append(dict(tasks))
                if "action" in tasks:
                    return {"action": {"label": "drop", "confidence": 0.5}}
                if "relevant" in tasks:
                    return {"relevant": {"label": "no", "confidence": 0.6}}
                return {"importance": {"label": "2", "confidence": 0.4}}

        fake = types.ModuleType("gliner2")
        fake.AutoExtractor = type("AutoExtractor", (), {
            "from_pretrained": staticmethod(lambda name: FakeExtractor())})
        monkeypatch.setitem(sys.modules, "gliner2", fake)

        client = GLiNERDecideClient("some/checkpoint", split_heads=True)
        decision = parse_response(client.decide(build_request(question=QUESTION, card=CARD)))
        assert len(calls) == 3, "one call per head when split"
        assert all(len(call) == 1 for call in calls), "each call carries exactly one head"
        assert (decision["action"], decision["relevant"], decision["importance"]) == (
            "drop", False, 2)


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
