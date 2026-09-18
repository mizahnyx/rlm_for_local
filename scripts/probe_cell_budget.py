"""Produce traces of the cell-budget behaviour, for a human to analyse (2026-09-17).

Why this exists
---------------
The two-stage cell budget could not be verified. In the configuration where the
*soft* limit is lower than the hard one, the harness reported `corpus_calls=0
last_helper=none` — it never saw the helper request the cell had made — while the
configuration where both limits are equal reported `last_helper=corpus_coverage` for
the same cell body, the same bridge and the same host.

Reasoning about that from the code produced two hypotheses and no answer. This script
reproduces the two configurations and writes trajectories detailed enough to *read*:
every request the parent receives is logged with its millisecond offset from the cell's
own start, alongside the cell's outcome, so "did the parent see the helper, and when?"
is a fact in the trace rather than an inference.

It changes nothing in the harness: `REPLSandbox._handle_request` and `.execute` are
wrapped in this process only, and the wrap only writes events.

Usage (where the corpus and the models are — lunacode):

    python scripts/probe_cell_budget.py --out-dir ~/rlm-derived

Then render what it wrote, and read it:

    rlm trace render ~/rlm-derived/probe-budget-A.jsonl --out-dir ~/rlm-derived/traces \
        --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite

Prints counts only; the details are in the trajectories.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_local.config import load_config  # noqa: E402
from rlm_local.logger import TrajectoryLogger  # noqa: E402
from rlm_local.repl import REPLSandbox  # noqa: E402
from rlm_local.root_loop import RootLoop  # noqa: E402

#: The cell both configurations run. It asks the corpus for one thing, then works
#: longer than the soft limit — the shape that must be *extended*, not killed.
SLOW_CELL = "```repl\ncorpus_coverage()\nimport time; time.sleep(12)\n```"
SUBMIT = "```repl\nanswer['content'] = 'done'\nanswer['ready'] = True\n```"


class StubBackend:
    """A backend that returns scripted responses: no model is involved in a probe."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages, *, tier="root", max_tokens=1024, temperature=0.0):
        if self.responses:
            return self.responses.pop(0)
        return "FINAL: no more scripted responses"


def instrument(logger: TrajectoryLogger) -> dict:
    """Wrap the sandbox so every request and cell outcome lands in the trajectory.

    Returns a dict of counters the caller prints — counts only, never content.
    """
    counts = {"requests": 0, "cell_start": 0.0, "cells": 0}
    original_handle = REPLSandbox._handle_request
    original_execute = REPLSandbox.execute

    def handle(self, msg_type, msg):
        elapsed_ms = (time.monotonic() - counts["cell_start"]) * 1000.0
        counts["requests"] += 1
        logger.log_guardrail(
            getattr(self, "_probe_turn", 0), "probe_request",
            f"after={elapsed_ms:.0f}ms type={msg_type} "
            f"msg_cell={msg.get('cell_id')} sandbox_cell={self._cell_seq} "
            f"corpus_calls={getattr(self, 'corpus_calls', 0)}",
        )
        return original_handle(self, msg_type, msg)

    def execute(self, code):
        counts["cell_start"] = time.monotonic()
        counts["cells"] += 1
        started = counts["cell_start"]
        result = original_execute(self, code)
        logger.log_guardrail(
            getattr(self, "_probe_turn", 0), "probe_cell",
            f"cell={counts['cells']} first_line={code.strip().splitlines()[0][:40]!r} "
            f"elapsed={(time.monotonic() - started):.2f}s "
            f"timed_out={result.timed_out} "
            f"hard={getattr(result, 'hard_timeout', 'n/a')} "
            f"corpus_calls={getattr(self, 'corpus_calls', 0)} "
            f"activity={getattr(self, '_cell_activity', 'n/a')} "
            f"stdout={len(result.stdout)} stderr={len(result.stderr)}",
        )
        return result

    REPLSandbox._handle_request = handle
    REPLSandbox.execute = execute
    return counts


def run_case(name: str, *, soft: float, hard: float, bridge, out_dir: Path) -> dict:
    path = out_dir / f"probe-budget-{name}.jsonl"
    logger = TrajectoryLogger(path)
    counts = instrument(logger)
    cfg = load_config("tiny", max_turns=2, cell_timeout=soft, cell_timeout_hard=hard)
    backend = StubBackend([SLOW_CELL, SUBMIT])
    loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None,
                    corpus_bridge=bridge)
    warnings: list[str] = []
    loop._warning_sink = warnings.append
    try:
        answer = loop.run("Probe: does the parent see the helper?", "probe context")
    finally:
        loop.shutdown()
    return {
        "name": name, "soft": soft, "hard": hard, "path": str(path),
        "requests": counts["requests"], "cells": counts["cells"],
        "corpus_calls": getattr(loop, "_probe_calls", None),
        "timeouts": loop.cell_timeouts,
        "answer_is_cell_text": answer.strip().startswith("```"),
        "warnings": len(warnings),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=Path.home() / "rlm-derived")
    ap.add_argument("--corpus-root", default="/srv/corpus")
    ap.add_argument("--corpus-index",
                    default=str(Path.home() / "rlm-derived" / "corpus.sqlite"))
    args = ap.parse_args()

    from rlm_kernel.corpus import CorpusBridge

    bridge = CorpusBridge.open_for(args.corpus_root, args.corpus_index,
                                   cache_root=Path(args.corpus_index).parent / "cache")
    try:
        # The two configurations differ in exactly one thing: whether a second,
        # higher limit exists. Everything else — cell body, bridge, host — is equal.
        cases = [
            ("A-soft-equals-hard", 6.0, 6.0),
            ("B-soft-below-hard", 6.0, 60.0),
        ]
        summary = [run_case(name, soft=soft, hard=hard, bridge=bridge,
                            out_dir=args.out_dir)
                   for name, soft, hard in cases]
    finally:
        bridge.close()

    for row in summary:
        print(json.dumps(row), flush=True)
    print(f"trajectories written to {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
