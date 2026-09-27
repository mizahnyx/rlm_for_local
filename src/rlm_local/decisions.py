"""Admission decisions over context cards, for a local typed-decision model (Gate 6).

The root model in this harness never sees documents: its context holds cards, tool results and REPL
output. So the unit a context controller decides about is a **card**, and this module turns one
question plus one card into a request a typed-decision model can answer, and turns its answer back
into a decision this harness can act on — or refuse.

Three constraints are copied from the model's own documentation rather than invented here, and each
one is a guard:

* **One `state` per request, up to 16 questions about it.** A card is therefore one request, and the
  three things worth asking (what to do with it, whether it matters, how much) ride together instead
  of costing three inferences.
* **1 024 formatted tokens per question, and over-long input is *rejected*, not truncated.** So this
  module raises `CardTooLarge` rather than shortening: a silently shortened card would be a decision
  about a *different* card, which is worse than no decision.
* **A label outside our vocabulary is a refusal.** `parse_response` raises `UnknownAction`; it never
  falls back to a default. A confident answer to a question nobody asked is the failure this project
  ranks below silence.

Nothing here imports a model or a server: the client is injected, as the summariser's engine is.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

#: What the controller may decide about one card. `expand` fetches the passage or document into the
#: cell, `summarise` fetches the cached description instead, `one_line` keeps only the header, and
#: `drop` keeps nothing. Order matters: it is the order the criteria are presented in.
ACTIONS: tuple[str, ...] = ("expand", "summarise", "one_line", "drop")

#: The descriptions the model chooses between. These are the model's *options*, so their wording is
#: part of the interface, not commentary: they say what each action costs the reader.
ACTION_CRITERIA: dict[str, str] = {
    "expand": "Read the full passage: it is likely to contain the answer, and nothing shorter will do",
    "summarise": "Use a short description of it instead of the full passage",
    "one_line": "Keep only its title or first line as a hint that it exists",
    "drop": "Leave it out of the context entirely",
}

#: The model's documented per-question ceiling (formatted tokens).
MAX_QUESTION_TOKENS = 1024

#: Inferred, not measured: characters per token for technical prose on these models. The real ratio
#: measured on corpus documents was 2.16-2.94 (`docs/20260923-2000-...`), so 3.0 is the conservative
#: side of that — it makes the guard *tighter* than reality, which fails toward refusal.
CHARS_PER_TOKEN = 3.0


class CardTooLarge(ValueError):
    """The request would exceed the model's per-question budget, so it is not sent.

    Raised instead of truncating. The model itself rejects over-long input, and shortening here
    would hide that a decision was made about a card nobody chose.
    """


class UnknownAction(ValueError):
    """The model named an action outside `ACTIONS` — refused, never mapped to a default."""


class MalformedDecision(ValueError):
    """The response did not carry a typed answer for a question we asked."""


class DecisionsClient(Protocol):
    """What the engine needs from a server: one call, one parsed JSON response."""

    def decide(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def estimated_tokens(text: str) -> int:
    """Characters ÷ `CHARS_PER_TOKEN`, rounded up — an estimate, and named like one."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def build_request(
    *, question: str, card: str, turn: int | None = None, turns_left: int | None = None,
) -> dict[str, Any]:
    """One card as a decision request: what to do with it, whether it matters, how much.

    The card is the `state`; the question, the turn position and the criteria live in the per-question
    `instructions`. The budget check is deliberately on the *whole formatted* request, because that is
    what the model measures.
    """
    position = ""
    if turn is not None and turns_left is not None:
        position = f" (turn {turn}, {turns_left} left)"
    action_instructions = (
        f"Question being answered{position}: {question}\n"
        "Decide what this card should contribute to the context of the model answering it."
    )
    relevance_instructions = (
        f"Question being answered{position}: {question}\n"
        "Does the card contain anything that bears on that question?"
    )
    importance_instructions = (
        f"Question being answered{position}: {question}\n"
        "How much would including the full card help answer it?"
    )
    payload: dict[str, Any] = {
        "state": card,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": action_instructions,
                "criteria": dict(ACTION_CRITERIA),
            },
            "relevant": {"type": "noul", "instructions": relevance_instructions},
            "importance": {
                "type": "score",
                "instructions": importance_instructions,
                "criteria": [
                    "No bearing on the question",
                    "Background only",
                    "Part of the answer",
                    "The answer itself",
                ],
            },
        },
    }
    formatted = card + action_instructions + relevance_instructions + importance_instructions
    formatted += "".join(ACTION_CRITERIA.values())
    if estimated_tokens(formatted) > MAX_QUESTION_TOKENS:
        raise CardTooLarge(
            f"card of {len(card)} characters would need about "
            f"{estimated_tokens(formatted)} formatted tokens, over the model's "
            f"{MAX_QUESTION_TOKENS}"
        )
    return payload


def parse_response(payload: dict[str, Any] | str) -> dict[str, Any]:
    """The typed answers as a decision: action, relevance, importance.

    Every field is checked rather than defaulted. A missing answer raises; an action outside the
    vocabulary raises; probabilities are carried through because whether the model was *sure* is part
    of what the probe measures. A raw JSON string is accepted too, for clients that hand back text.
    """
    if isinstance(payload, str):
        payload = _json_or_die(payload)
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise MalformedDecision("response carried no 'answers' object")
    action = answers.get("action")
    if not isinstance(action, dict) or action.get("type") != "choice":
        raise MalformedDecision("response carried no 'choice' answer for 'action'")
    label = action.get("choice")
    if label not in ACTIONS:
        raise UnknownAction(f"model chose {label!r}, which is not one of {ACTIONS}")
    relevant = answers.get("relevant")
    if not isinstance(relevant, dict) or relevant.get("type") != "noul":
        raise MalformedDecision("response carried no 'noul' answer for 'relevant'")
    importance = answers.get("importance")
    if not isinstance(importance, dict) or importance.get("type") != "score":
        raise MalformedDecision("response carried no 'score' answer for 'importance'")
    return {
        "action": label,
        "action_probabilities": action.get("probabilities"),
        "action_confidence": action.get("confidence"),
        "relevant": relevant.get("noul"),
        "importance": importance.get("score"),
        "importance_probabilities": importance.get("probabilities"),
    }


class LayaSdkClient:
    """In-process client for the reference `laya` package.

    `laya.load(model).system_one(state, questions)` returns exactly the `answers` object
    `parse_response` expects, so no server is needed — which matters, because the Mac-oriented
    wrapper's HTTP shape is a *different* one (`/v1/decisions`, and it spells the ordinal option
    list `levels`).

    `laya` is imported when the client is constructed, never at module import, so this module stays
    importable where the package is absent. With no `device`, the package's own `LAYA_DEVICE`
    environment variable decides.
    """

    def __init__(
        self, model: str = "convaiinnovations/laya-typed-decisions", device: str | None = None,
    ) -> None:
        import laya

        if device:
            try:
                self._agent = laya.load(model, device=device)
                return
            except TypeError:  # a version whose load() takes no device
                pass
        self._agent = laya.load(model)

    def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._agent.system_one(payload["state"], payload["questions"])

    def close(self) -> None:  # pragma: no cover - nothing to release in-process
        """Present so the runner can close either client the same way."""


class LayaClient:
    """A minimal client for a Laya-style `/v1/decisions` server.

    Not an OpenAI chat endpoint: the response is a typed-answer object, so `HTTPModelBackend` cannot
    be reused here, and this deliberately duplicates none of its retry or TLS policy beyond what a
    local call needs. TLS verification is off for the same self-signed localhost reason the rest of
    the harness documents.
    """

    def __init__(
        self, endpoint: str = "http://127.0.0.1:8000", *, timeout: float = 300.0,
    ) -> None:
        import httpx

        self._client = httpx.Client(timeout=httpx.Timeout(timeout))
        self._url = endpoint.rstrip("/") + "/v1/decisions"

    def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(self._url, json=payload)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()


class GLiNERDecideClient:
    """In-process client for `fastino/GLiNER2.5-Decide` through the `gliner2` package.

    A different dialect of the same idea: GLiNER takes one text and a **schema of typed heads** and
    returns one label per head, with an optional confidence. It does not take Laya's
    `state`/`questions` shape, so the translation lives in two pure functions
    (`gliner_schema_from`, `gliner_answers_from`) and this class is only the call.

    Two measured facts shaped it (2026-09-26, lunacode, CPU):

    * **The confidence is the winner's score, not a distribution.** `include_confidence=True`
      returns `{'head': {'label': …, 'confidence': …}}`; there is no per-label probability surface
      on this API, so `probabilities` is `None` and only `confidence` is carried. A selected label
      is not a distribution, and this module does not invent one.
    * **Asking all three questions at once costs the action answer.** The same 14 cards scored
      **5/12** with the three heads in one call and **7/12** with the action head alone — and the
      `drop` label never appears in the three-head call. `split_heads=True` makes one call per
      question, which is the better configuration on that probe at ~1.9 s a call instead of
      ~2.8 s for all three; the default stays the single call, because a head-to-head with Laya
      has to use both models' one-call protocol.

    `gliner2` is imported when the client is constructed, never at module import, so this module
    stays importable where the package is absent.
    """

    def __init__(self, model: str = "fastino/GLiNER2.5-Decide",
                 *, split_heads: bool = False) -> None:
        from gliner2 import AutoExtractor

        self._extractor = AutoExtractor.from_pretrained(model)
        self._model = model
        self._split_heads = split_heads

    def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        schema = gliner_schema_from(payload)
        if self._split_heads:
            answers: dict[str, Any] = {}
            for name, head in schema.items():
                result = self._extractor.classify_text(
                    payload["state"], {name: head}, include_confidence=True,
                )
                answers.update(gliner_answers_from(result))
            return {"answers": answers}
        result = self._extractor.classify_text(
            payload["state"], schema, include_confidence=True,
        )
        return {"answers": gliner_answers_from(result)}

    def close(self) -> None:  # pragma: no cover - nothing to release in-process
        """Present so the runner can close either client the same way."""


def gliner_schema_from(payload: dict[str, Any]) -> dict[str, Any]:
    """The card-and-questions request as a GLiNER2.5-Decide schema: one head per question.

    The translations are deliberate rather than mechanical:

    * a **choice** question's criteria become *described labels*, because GLiNER lets a label carry
      the sentence that explains it — and those criteria are the model's options, not commentary;
    * a **noul** question becomes a `yes`/`no` head (`None` is not a label this model offers);
    * a **score** question becomes an ordinal head over the *count* of its criteria, so the scale is
      whatever the request declares rather than a constant repeated in two places.

    The question being answered goes in the head's `prompt`, which is where GLiNER expects the task
    text. A question with no criteria raises rather than being sent as an empty label set: a
    classifier with nothing to choose between cannot answer, and asking anyway would produce a
    confident label for a question nobody asked.
    """
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise MalformedDecision("request carried no 'questions' object")
    schema: dict[str, Any] = {}
    for name, spec in questions.items():
        if not isinstance(spec, dict):
            raise MalformedDecision(f"question {name!r} is not an object")
        qtype = spec.get("type")
        head: dict[str, Any] = {}
        if qtype in ("choice", "score"):
            criteria = spec.get("criteria")
            if not criteria:
                raise MalformedDecision(f"question {name!r} of type {qtype!r} has no criteria")
            if qtype == "choice":
                if not isinstance(criteria, dict):
                    raise MalformedDecision(f"choice question {name!r} has non-mapping criteria")
                head["labels"] = {str(k): str(v) for k, v in criteria.items()}
            else:
                head["labels"] = {
                    str(index): str(text) for index, text in enumerate(criteria)
                }
        elif qtype == "noul":
            head["labels"] = ["yes", "no"]
        else:
            raise MalformedDecision(f"question {name!r} has unknown type {qtype!r}")
        instructions = spec.get("instructions")
        if instructions:
            head["prompt"] = str(instructions)
        schema[name] = head
    return schema


def gliner_answers_from(result: dict[str, Any] | str) -> dict[str, Any]:
    """GLiNER's `{head: label}` (or `{head: {label, confidence}}`) as the answers object.

    Every question in the request must come back; a missing head raises, so the parser's own
    guarantees still hold one layer up. Labels are passed through untouched — a label outside our
    vocabulary is `parse_response`'s refusal to make, not this function's to repair — except that
    `noul` and `score` answers are converted to the boolean and integer their types promise.
    """
    if isinstance(result, str):
        result = _json_or_die(result)
    if not isinstance(result, dict):
        raise MalformedDecision("GLiNER returned no result object")
    answers: dict[str, Any] = {}
    for name, raw in result.items():
        label: Any = None
        confidence: float | None = None
        if isinstance(raw, dict):
            label = raw.get("label")
            confidence = raw.get("confidence")
        elif isinstance(raw, (tuple, list)) and len(raw) == 2 and not isinstance(raw[0], (list, tuple)):
            label, confidence = raw[0], raw[1]
        elif isinstance(raw, (str, int, float)):
            label = raw
        elif isinstance(raw, list):
            label = raw[0] if raw else None
        else:
            raise MalformedDecision(f"head {name!r} answered in an unknown shape")
        if isinstance(label, str) and label in ("yes", "no"):
            answers[name] = {"type": "noul", "noul": label == "yes", "confidence": confidence}
        elif isinstance(label, str) and label.isdigit():
            answers[name] = {"type": "score", "score": int(label),
                             "probabilities": None, "confidence": confidence}
        else:
            answers[name] = {"type": "choice", "choice": label, "probabilities": None,
                             "confidence": confidence}
    return answers


def make_admission_engine(
    client: DecisionsClient,
    *,
    model: str,
    log_path: Any = None,
    tag: str | None = None,
) -> Callable[..., list[dict[str, Any]]]:
    """Build the callable: `(question, cards, turn=None, turns_left=None) -> [decision, ...]`.

    One request per card, in order, because the model takes one `state` per request. Every call —
    including one that raised — appends a metrics line when `log_path` is given, and the line carries
    **no card text**: counts, labels, probabilities and seconds only. A card is corpus-derived prose,
    so a log that quoted one would leak a document one decision at a time.
    """
    resolved_tag = tag or model

    def engine(
        question: str, cards: list[str], *, turn: int | None = None, turns_left: int | None = None,
    ) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        for index, card in enumerate(cards):
            started = time.monotonic()
            started_at = time.time()
            try:
                payload = build_request(
                    question=question, card=card, turn=turn, turns_left=turns_left,
                )
                decision = parse_response(client.decide(payload))
            except Exception as exc:
                if log_path is not None:
                    _append(
                        log_path,
                        {
                            "kind": "end",
                            "model": resolved_tag,
                            "card_index": index,
                            "card_chars": len(card),
                            "seconds": round(time.monotonic() - started, 3),
                            "started_at": started_at,
                            "error": type(exc).__name__,
                        },
                    )
                raise
            decision["card_index"] = index
            decision["card_chars"] = len(card)
            decision["seconds"] = round(time.monotonic() - started, 3)
            decision["started_at"] = started_at
            decision["model"] = resolved_tag
            decisions.append(decision)
            if log_path is not None:
                _append(log_path, dict(decision, kind="end"))
        return decisions

    engine.engine_tag = resolved_tag  # type: ignore[attr-defined]
    return engine


def _append(path: Any, record: dict[str, Any]) -> None:
    from pathlib import Path

    from rlm_local.summarise import append_record

    append_record(Path(path), record)


def _json_or_die(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as e:
        raise MalformedDecision(f"response was not JSON ({e})") from e
    if not isinstance(payload, dict):
        raise MalformedDecision("response was not a JSON object")
    return payload
