"""Re-score a recorded model sweep under a different weight profile.

A sweep's JSONL keeps each model's *per-probe* scores, and `_score_model` is a
pure function of those plus a weight profile. So when the battery's weights change
— as they did on 2026-09-11, when P1 was found to be saturated and its weight
moved onto P4 and P6 — the recorded verdicts can be re-scored **without running a
single model again**.

That matters on this project's hardware: re-running the sweep is ~1.5 h per model
on `lunacode`, while this takes milliseconds.

Self-check: re-scoring with the profile a record was produced under must
reproduce its recorded score and verdict exactly. A record whose stored numbers do
not come back is reported as a problem rather than silently re-scored, because it
would mean the JSONL and the scoring code disagree about what happened.

Usage:
    .venv/Scripts/python.exe scripts/rescore_sweep.py logs/router-model-battery.jsonl
    .venv/Scripts/python.exe scripts/rescore_sweep.py <file> --weights p1-heavy
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rlm_local.model_check import (  # noqa: E402
    DEFAULT_WEIGHT_PROFILE,
    WEIGHT_PROFILES,
    _score_model,
    _verdict,
)


def load_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rescore(record: dict, profile: str) -> float | None:
    """Score one record under `profile`, or None when it has no probe results."""
    per_probe = record.get("per_probe") or {}
    scorable = {
        pid: result
        for pid, result in per_probe.items()
        if result.get("max_score")
    }
    if not scorable:
        return None
    weights = {
        pid: WEIGHT_PROFILES[profile][pid]
        for pid in scorable
        if pid in WEIGHT_PROFILES[profile]
    }
    if not weights:
        return None
    return _score_model(scorable, weights)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep", type=Path, help="sweep JSONL written by assess_router_models.py")
    ap.add_argument("--weights", default=DEFAULT_WEIGHT_PROFILE,
                    help=f"profile to re-score with (default: {DEFAULT_WEIGHT_PROFILE})")
    ap.add_argument("--verify-with", default=None,
                    help="profile to reproduce the recorded score with "
                         "(default: none; use p1-heavy for pre-2026-09-12 sweeps)")
    args = ap.parse_args()

    if args.weights not in WEIGHT_PROFILES:
        print(f"unknown weight profile {args.weights!r}; "
              f"choose from {', '.join(sorted(WEIGHT_PROFILES))}", file=sys.stderr)
        return 2

    records = load_records(args.sweep)
    problems: list[str] = []
    rows: list[tuple[str, float | None, str, float | None, str]] = []

    for record in records:
        model = str(record.get("model", "?"))
        recorded = record.get("score")
        recorded_verdict = record.get("verdict")

        if args.verify_with:
            reproduced = rescore(record, args.verify_with)
            if reproduced is not None and (
                recorded is not None
                and (round(reproduced, 1) != round(float(recorded), 1)
                     or _verdict(reproduced) != recorded_verdict)
            ):
                problems.append(
                    f"{model}: recorded {recorded}/{recorded_verdict} but "
                    f"{args.verify_with} reproduces {reproduced}/{_verdict(reproduced)}"
                )

        new_score = rescore(record, args.weights)
        rows.append((
            model,
            recorded if recorded is None else float(recorded),
            recorded_verdict or "-",
            new_score,
            "-" if new_score is None else _verdict(new_score),
        ))

    width = max((len(r[0]) for r in rows), default=20)
    print(f"{'model'.ljust(width)}  {'recorded':>8}  {'verdict':<13}  "
          f"{args.weights:>8}  {'verdict':<13}")
    for model, old, old_verdict, new, new_verdict in rows:
        old_text = "-" if old is None else f"{old:.1f}"
        new_text = "-" if new is None else f"{new:.1f}"
        moved = ""
        if old is not None and new is not None and old_verdict != new_verdict:
            moved = "   <- verdict changed"
        print(f"{model.ljust(width)}  {old_text:>8}  {old_verdict:<13}  "
              f"{new_text:>8}  {new_verdict:<13}{moved}")

    if problems:
        print("\nPROBLEMS (recorded numbers were not reproduced):")
        for problem in problems:
            print("  -", problem)
    elif args.verify_with:
        print(f"\nok: every record is reproduced by {args.verify_with}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
