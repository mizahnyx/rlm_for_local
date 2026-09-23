#!/usr/bin/env python3
"""Write the enrichment plan: which documents are worth a generation pass (RO6).

The owner's shape (2026-09-23): **the retrieval-touched set, ranked cited-first** — everything a
search has ever served, with the documents an answer actually cited at the top. Measured cost at
the host's rate: 5–10 minutes per document, so the plan prints hours, not documents.

    uv run python scripts/enrich_plan.py --only served,cited \
        --out ~/rlm-derived/enrich-plan.tsv

Prints **aggregates only** — counts, classes, hours. The plan itself names corpus paths, so it is
written beside the corpus at 0600 and never travels (`AGENTS.md` §1.9).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rlm_kernel.enrich import (  # noqa: E402
    iter_trajectories, rank_candidates, read_usage, render_plan, write_plan,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--derived-root", type=Path,
                    default=Path.home() / "rlm-derived",
                    help="Where the trajectories live (scanned recursively).")
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "rlm-derived" / "enrich-plan.tsv")
    ap.add_argument("--only", default="served,cited",
                    help="Which evidence counts: served, cited, or both (default both).")
    args = ap.parse_args()

    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    unknown = wanted - {"served", "cited"}
    if unknown:
        print(f"Error: --only takes served and/or cited, not {sorted(unknown)}",
              file=sys.stderr)
        return 2

    paths = list(iter_trajectories(args.derived_root))
    if not paths:
        print(f"no trajectories under {args.derived_root}", file=sys.stderr)
        return 2
    served, cited = read_usage(paths)
    candidates = rank_candidates(served, cited, only=wanted)
    write_plan(candidates, args.out)
    print(f"# scanned {len(paths)} trajectory(ies) under {args.derived_root}")
    print(render_plan(candidates))
    print(f"# plan (names paths, 0600): {args.out}")
    print("# next: the summarise handler over this list, bounded by a window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
