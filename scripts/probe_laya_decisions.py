"""Run the Laya admission-controller probe and score it (Gate 6, phase B1).

The design is `docs/20260924-0200-gate-6-the-laya-controller-probe-design.md`; this is its phase B1 —
synthetic cards with a known expected action, which needs no corpus and no `lunacode`, and answers
whether the task is doable at all and whether the model's confidence means anything.

Design choices worth stating:

* **`expected` is a judgement, not ground truth.** Cases where the right answer is genuinely unclear
  carry `expected: null`: they are reported and **excluded from accuracy**, because scoring them
  would manufacture precision the probe does not have.
* **Card text is never printed.** The cards in the shipped set are hand-written and harmless, but the
  same runner is meant to be pointed at real cards in phase B2, where printing one would leak a
  document. So the report identifies cases by id and prints labels and numbers only.
* **Calibration is reported, not assumed.** Mean confidence on correct answers against incorrect ones
  is the number that decides whether a confidence threshold could ever gate an action.

Usage, where the model is:

    uv run python scripts/probe_laya_decisions.py --cases scripts/laya_decision_cases.json \
        --endpoint http://127.0.0.1:8000 --model laya-typed-decisions --out ~/laya-eval/b1.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rlm_local.decisions import (  # noqa: E402
    ACTIONS,
    CardTooLarge,
    LayaClient,
    LayaSdkClient,
    MalformedDecision,
    UnknownAction,
    make_admission_engine,
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Read a case file, refusing anything malformed rather than scoring around it."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"no cases in {path}")
    for case in cases:
        for key in ("id", "question", "card"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError(f"case {case.get('id')!r} has no {key!r}")
        expected = case.get("expected")
        if expected is not None and expected not in ACTIONS:
            raise ValueError(f"case {case['id']!r} expects {expected!r}, not one of {ACTIONS}")
    return cases


def score(cases: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy over the cases that have an expectation, plus a confusion matrix and calibration.

    A case with `expected: null`, or one whose decision raised, is reported as *unscored* or
    *errored* and kept out of accuracy. Silently counting either as wrong would make the probe look
    more decisive than it is.
    """
    rows: list[dict[str, Any]] = []
    for case, decision in zip(cases, decisions):
        row = {
            "id": case["id"],
            "expected": case.get("expected"),
            "action": (decision or {}).get("action"),
            "error": (decision or {}).get("error"),
            "confidence": (decision or {}).get("action_confidence"),
            "relevant": (decision or {}).get("relevant"),
            "importance": (decision or {}).get("importance"),
            "seconds": (decision or {}).get("seconds"),
        }
        row["correct"] = (
            None if row["expected"] is None or row["error"] else row["action"] == row["expected"]
        )
        rows.append(row)

    scored = [row for row in rows if row["correct"] is not None]
    correct = [row for row in scored if row["correct"]]
    wrong = [row for row in scored if not row["correct"]]
    confusion = {
        expected: {action: 0 for action in ACTIONS}
        for expected in ACTIONS
    }
    for row in scored:
        confusion[row["expected"]][row["action"]] += 1

    def _confidence(rows_: list[dict[str, Any]]) -> float | None:
        values = [float(row["confidence"]) for row in rows_ if row["confidence"] is not None]
        return round(statistics.mean(values), 4) if values else None

    return {
        "cases": len(rows),
        "scored": len(scored),
        "unscored": sum(1 for row in rows if row["expected"] is None),
        "errored": sum(1 for row in rows if row["error"]),
        "accuracy": round(len(correct) / len(scored), 4) if scored else None,
        "confidence_when_correct": _confidence(correct),
        "confidence_when_wrong": _confidence(wrong),
        "confusion": confusion,
        "seconds_median": (
            round(statistics.median([row["seconds"] for row in rows if row["seconds"]]), 3)
            if any(row["seconds"] for row in rows) else None
        ),
        "rows": rows,
    }


def render(report: dict[str, Any]) -> str:
    """The report as text — ids, labels and numbers; never a card."""
    lines = [
        f"{report['cases']} case(s): {report['scored']} scored, "
        f"{report['unscored']} without an expectation, {report['errored']} errored",
        f"accuracy: {report['accuracy']}",
        f"confidence when correct: {report['confidence_when_correct']}, "
        f"when wrong: {report['confidence_when_wrong']}",
        f"median decision time: {report['seconds_median']}s",
        "",
        f"{'id':34} {'expected':10} {'action':10} {'ok':4} {'conf':6} {'rel':5} {'imp':5}",
    ]
    for row in report["rows"]:
        lines.append(
            f"{row['id'][:34]:34} {str(row['expected']):10} {str(row['action']):10} "
            f"{str(row['correct']):4} {str(row['confidence']):6} "
            f"{str(row['relevant']):5} {str(row['importance']):5}"
        )
    lines += ["", "confusion (expected -> chosen, scored cases only):"]
    for expected, chosen in report["confusion"].items():
        chosen_text = ", ".join(f"{action}={count}" for action, count in chosen.items() if count)
        lines.append(f"  {expected:10} -> {chosen_text or '(none)'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).resolve().parent / "laya_decision_cases.json")
    parser.add_argument("--endpoint", default=os.environ.get("LAYA_ENDPOINT",
                                                             "http://127.0.0.1:8000"))
    parser.add_argument("--model", default="convaiinnovations/laya-typed-decisions")
    parser.add_argument("--sdk", action="store_true",
                        help="use the in-process `laya` package instead of an HTTP endpoint")
    parser.add_argument("--device", default=None, help="e.g. cpu, for the SDK")
    parser.add_argument("--log", type=Path, default=None,
                        help="JSONL of decisions (no card text, ever)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON report here (0600)")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.sdk:
        client = LayaSdkClient(args.model, device=args.device)
    else:
        client = LayaClient(args.endpoint)
    engine = make_admission_engine(client, model=args.model, log_path=args.log)
    decisions: list[dict[str, Any]] = []
    try:
        for case in cases:
            try:
                decision = engine(case["question"], [case["card"]])[0]
            except (CardTooLarge, UnknownAction, MalformedDecision, ValueError) as exc:
                decision = {"error": type(exc).__name__}
            except Exception as exc:  # noqa: BLE001 — a transport failure is a row, not a crash
                decision = {"error": type(exc).__name__}
            decisions.append(decision)
    finally:
        client.close()

    report = score(cases, decisions)
    print(render(report))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(args.out, 0o600)
        except OSError:  # pragma: no cover
            pass
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
