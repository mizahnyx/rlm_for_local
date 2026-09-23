#!/usr/bin/env python3
"""Summarise a question set from its trajectories, by reading *events* rather than the
probe's one-line summary.

Why this exists: on 2026-09-22 I read a single aggregate line, concluded the 65 minutes of a
prose-question run were model decoding, and pushed that. The trajectory's own guardrail
events said a cell had hit the hard 1200 s limit inside one `corpus_search`, that the model
had never submitted, and that the answer came from forced finalisation
(`docs/20260922-2010-correction-the-20-minute-search.md`). The aggregate line is a summary,
and a summary is not an audit.

So this reads the events and reports, per question: turns, whether the answer was forced,
which helper verbs were served and how often, the hits served by band, the citation audit's
own event, and every budget event with its `limit=`/`budget=`/`last_helper=`. It touches no
index and issues no query, so it is safe to run while a set is still being probed.

Aggregates only: no question, answer, passage or address is printed.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

BAND_NAMES = ("none", "weak", "partial", "strong")


def _events(rows: list[dict]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for row in rows:
        name = str(row.get("event") or row.get("type") or "?")
        out.append((name, row))
    return out


def summarise(rows: list[dict]) -> dict:
    """Everything a set summary needs, from one trajectory's events. Pure."""
    events = _events(rows)
    names = collections.Counter(name for name, _ in events)
    helpers: collections.Counter[str] = collections.Counter()
    bands: collections.Counter[str] = collections.Counter()
    budget: list[dict] = []
    citations: list[dict] = []
    last_turn_warning = False
    for name, row in events:
        if name == "corpus_served":
            helpers[str(row.get("verb") or row.get("helper") or "?")] += 1
        elif name == "guardrail":
            kind = str(row.get("guardrail", "?"))
            detail = str(row.get("detail", ""))
            if kind == "corpus_search_quality":
                for band, count in re.findall(r"(\w+)=(\d+)", detail):
                    # Only the band names: the same detail carries `chars=`, and counting
                    # that as a band is how this summariser would have reported a set's
                    # evidence as fifty thousand hits. The test caught it.
                    if band in BAND_NAMES:
                        bands[band] += int(count)
            elif kind in ("cell_timeout", "cell_extended"):
                budget.append({"kind": kind, "detail": detail})
            elif kind in ("corpus_citation", "corpus_uncited", "corpus_weak_citation"):
                citations.append({"kind": kind, "detail": detail})
            elif kind == "corpus_last_turn":
                # A warning, not a verdict: it fires when the loop reaches its last turn
                # with no submission *at that moment*, and the model may still submit within
                # that turn. Measured 2026-09-22 (prose-004): this fired while the run's own
                # `end` event says `forced=False`, so naming it "no submission" is wrong.
                last_turn_warning = True
    answered = bool(citations)
    with_address = any("answers_with_address=True" in c["detail"] for c in citations)
    served = sum(bands.values())
    timeouts = sum(1 for b in budget if b["kind"] == "cell_timeout")
    extended = sum(1 for b in budget if b["kind"] == "cell_extended")
    return {
        "turns": names.get("turn_start", 0),
        "events": sum(names.values()),
        "helpers": dict(helpers.most_common()),
        "helper_calls": sum(helpers.values()),
        "bands": dict(bands.most_common()),
        "served": served,
        "citable_served": bands.get("partial", 0) + bands.get("strong", 0),
        "answers": 1 if answered else 0,
        "answers_with_address": with_address,
        "cell_timeouts": timeouts,
        "cell_extended": extended,
        "budget_events": budget,
        "last_turn_warning": last_turn_warning,
        "citation_events": citations,
    }


def render(summary: dict, *, label: str = "") -> str:
    """One line per question, aggregates only."""
    helpers = ", ".join(f"{k}={v}" for k, v in list(summary["helpers"].items())[:5])
    bands = ", ".join(f"{k}={v}" for k, v in list(summary["bands"].items())[:4])
    return (
        f"{label}: turns={summary['turns']} helpers={summary['helper_calls']} ({helpers}) "
        f"served={summary['served']} ({bands}) citable={summary['citable_served']} "
        f"answered={summary['answers']} with_address={summary['answers_with_address']} "
        f"hard_timeouts={summary['cell_timeouts']} extended={summary['cell_extended']} "
        f"last_turn_warning={summary['last_turn_warning']}"
    )


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path.home() / "rlm-derived" / "questions")
    ap.add_argument("--glob", default="prose-*.jsonl",
                    help="Which trajectories to include. Names are printed: use a pattern "
                         "whose ids carry no corpus information.")
    ap.add_argument("--details", action="store_true",
                    help="Also print each budget event's limit/budget/last_helper")
    args = ap.parse_args()

    paths = sorted(args.dir.glob(args.glob))
    if not paths:
        print(f"no trajectories matching {args.glob} in {args.dir}", file=sys.stderr)
        return 2
    totals: collections.Counter[str] = collections.Counter()
    print(f"# {len(paths)} trajectory(ies) in {args.dir} matching {args.glob}", flush=True)
    for path in paths:
        summary = summarise(load(path))
        print(render(summary, label=path.stem), flush=True)
        for key in ("served", "citable_served", "answers", "answers_with_address",
                    "cell_timeouts", "cell_extended", "helper_calls", "turns"):
            totals[key] += int(summary[key])
        if args.details:
            for event in summary["budget_events"]:
                print(f"    {event['kind']}: {event['detail']}", flush=True)
    print("# set totals: " + ", ".join(f"{k}={v}" for k, v in sorted(totals.items())),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
