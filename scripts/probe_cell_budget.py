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

#: The *real* limits (owner's criticism, 2026-09-17): 6 s and 60 s were picked for test
#: speed and are not our operating regime. A probe whose cases cannot reach the real
#: magnitudes cannot say anything about the real magnitudes.
SOFT = 60.0        # the profile's cell_timeout on tiny/laptop
HARD = 1200.0      # the agreed hard ceiling: 20 minutes

#: The cell bodies. Both run *past* the soft limit and well inside the hard one, so
#: what is observed is the boundary that matters: a cell slower than any normal
#: operation but nowhere near the theoretical ceiling.
SLOW_WITH_HELPER = "```repl\ncorpus_coverage()\nimport time; time.sleep(75)\n```"
SLOW_NO_HELPER = "```repl\nimport time; time.sleep(75)\n```"
SUBMIT = "```repl\nanswer['content'] = 'done'\nanswer['ready'] = True\n```"


def supports_two_limits() -> bool:
    """Whether the build under test can even express a second limit.

    The first version of this probe compared two configurations and got identical
    traces, because the second limit was not in the tree — the comparison could not
    exist and nothing on the page said so. That is recorded now, on the page.
    """
    import inspect

    params = inspect.signature(REPLSandbox.__init__).parameters
    return "cell_timeout_hard" in params


class StubBackend:
    """A backend that returns scripted responses: no model is involved in a probe."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def chat(self, messages, *, tier="root", max_tokens=1024, temperature=0.0):
        if self.responses:
            return self.responses.pop(0)
        return "FINAL: no more scripted responses"


#: Installed once for the whole probe. Installed per case, the wrappers stack and a
#: later case's requests are logged into the earlier case's trajectory — which is
#: exactly the kind of confusing evidence this script exists to prevent.
_PROBE: dict = {"logger": None, "cell_start": 0.0, "cells": 0, "installed": False}


def install() -> None:
    """Wrap the sandbox so every request and cell outcome lands in the trajectory.

    Returns nothing; the counters are in `_PROBE`, and the caller prints them —
    counts only, never content.
    """
    if _PROBE["installed"]:
        return
    _PROBE["installed"] = True
    original_handle = REPLSandbox._handle_request
    original_execute = REPLSandbox.execute

    def handle(self, msg_type, msg):
        elapsed_ms = (time.monotonic() - _PROBE["cell_start"]) * 1000.0
        logger = _PROBE["logger"]
        if logger is not None:
            logger.log_guardrail(
                getattr(self, "_probe_turn", 0), "probe_request",
                f"after={elapsed_ms:.0f}ms type={msg_type} "
                f"msg_cell={msg.get('cell_id')} sandbox_cell={self._cell_seq} "
                f"corpus_calls={getattr(self, 'corpus_calls', 0)}",
            )
        return original_handle(self, msg_type, msg)

    def execute(self, code):
        _PROBE["cell_start"] = time.monotonic()
        _PROBE["cells"] += 1
        started = _PROBE["cell_start"]
        result = original_execute(self, code)
        logger = _PROBE["logger"]
        if logger is not None:
            logger.log_guardrail(
                getattr(self, "_probe_turn", 0), "probe_cell",
                f"cell={_PROBE['cells']} first_line={code.strip().splitlines()[0][:40]!r} "
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


def run_case(name: str, *, soft: float, hard: float, cell: str, bridge,
             out_dir: Path) -> dict:
    path = out_dir / f"probe-budget-{name}.jsonl"
    # Truncate: the logger appends, so a second probe run would otherwise leave both
    # runs' events in one file and the trace would show executions that never happened
    # in that run.
    path.unlink(missing_ok=True)
    logger = TrajectoryLogger(path)
    logger.log_guardrail(
        0, "probe_env",
        f"requested soft={soft:g}s hard={hard:g}s two_limits_supported="
        f"{supports_two_limits()} cell_sleeps={cell.count('sleep') and 'yes' or 'no'} "
        f"python={sys.version.split()[0]}",
    )
    _PROBE["logger"] = logger
    _PROBE["cells"] = 0
    cfg = load_config("tiny", max_turns=2, cell_timeout=soft, cell_timeout_hard=hard)
    backend = StubBackend([cell, SUBMIT])
    loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None,
                    corpus_bridge=bridge)
    warnings: list[str] = []
    loop._warning_sink = warnings.append
    try:
        answer = loop.run("Probe: does the parent see the helper?", "probe context")
    finally:
        loop.shutdown()
    _PROBE["logger"] = None
    return {
        "name": name, "soft": soft, "hard": hard, "path": str(path),
        "cells": _PROBE["cells"],
        "sandbox_corpus_calls": getattr(loop._repl, "corpus_calls", None),
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
    install()
    try:
        # At the real magnitudes, and one thing at a time: a cell that asks for
        # something and runs past the soft limit; and the same cell that asks for
        # nothing, which is the "stuck, not slow" control. ~2.5 minutes of wall clock,
        # because a probe that cannot reach the real boundary measures nothing.
        cases = [
            ("real-slow-with-helper", SOFT, HARD, SLOW_WITH_HELPER),
            ("real-stuck-no-helper", SOFT, HARD, SLOW_NO_HELPER),
        ]
        summary = [run_case(name, soft=soft, hard=hard, cell=cell, bridge=bridge,
                            out_dir=args.out_dir)
                   for name, soft, hard, cell in cases]
    finally:
        bridge.close()

    for row in summary:
        print(json.dumps(row), flush=True)
    print(f"trajectories written to {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
