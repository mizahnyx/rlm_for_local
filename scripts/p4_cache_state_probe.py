"""Is a model's P4 outcome a property of the model, or of the router's cache?

Roadmap item VD1 (`docs/20260912-1155-roadmap.md`). The 2026-09-11 investigation
found `Qwen3.5-2B-Instruct` submitting voluntarily three times in a row on a
**warm** router and never on a cold one, but it was not a controlled comparison:
the runs differed in more than cache state. This script is the controlled version.

Design: the same query, the same server, the same `temperature=0.0`, run several
times inside one router lifetime and then again after a restart. Within a round the
*first* run populates the KV/prefix cache and the later runs reuse it, so
first-vs-later isolates cache state the way the earlier investigation could not.
Two rounds, because a single pair of outcomes decides nothing.

Every run is classified exactly as the P4 probe classifies a trial (the same
`_run_p4_trial` the battery uses), so the outcomes here are directly comparable
with a recorded battery.

Usage:
    .venv/Scripts/python.exe scripts/p4_cache_state_probe.py \
        --model Qwen3.5-2B-Instruct \
        --restart-cmd "ssh lunacode systemctl --user restart llama-router.service" \
        --rounds 2 --runs-per-round 2 \
        --out logs/p4-cache-state.jsonl
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import httpx  # noqa: E402

from rlm_local.model_backend import HTTPModelBackend  # noqa: E402
from rlm_local.model_check import P4_QUERIES, _run_p4_trial  # noqa: E402


def wait_for_router(endpoint: str, timeout: float = 300.0) -> bool:
    """Block until the router has answered steadily several times in a row.

    One answer is not enough after a restart: the listener returns before a model
    instance can be spawned, and a request in that window fails at the socket
    level (this cost a whole model in the first battery run).
    """
    deadline = time.time() + timeout
    consecutive = 0
    while time.time() < deadline:
        try:
            httpx.get(f"{endpoint.rstrip('/')}/models", timeout=30.0, verify=False)
            consecutive += 1
            if consecutive >= 3:
                return True
        except Exception:  # noqa: BLE001 - keep polling until the deadline
            consecutive = 0
        time.sleep(3.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="https://lunacode:9010/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--profile", default="tiny")
    ap.add_argument("--query-index", type=int, default=0,
                    help="which P4_QUERIES entry to use (0 = the ocean question)")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--runs-per-round", type=int, default=2)
    ap.add_argument("--restart-cmd", default="",
                    help="command that restarts the router, run before each round "
                         "(without it there is no cold condition)")
    ap.add_argument("--out", default="logs/p4-cache-state.jsonl")
    args = ap.parse_args()

    query, context = P4_QUERIES[args.query_index]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for round_index in range(1, args.rounds + 1):
        if args.restart_cmd:
            print(f"[round {round_index}] restarting the router ...", flush=True)
            subprocess.run(args.restart_cmd, shell=True, check=False)
            if not wait_for_router(args.endpoint):
                print("    router did not come back; aborting", flush=True)
                return 2
        else:
            print(f"[round {round_index}] no restart command: round is warm",
                  flush=True)

        for run_index in range(1, args.runs_per_round + 1):
            position = "first" if run_index == 1 else "warm"
            print(f"    run {run_index}/{args.runs_per_round} ({position}) ...",
                  flush=True)
            backend = HTTPModelBackend(
                root_endpoint=args.endpoint,
                root_model=args.model,
                verify=False,
                timeout=900.0,
            )
            started = time.time()
            try:
                trial = _run_p4_trial(backend, query, context, args.profile)
                record = {
                    "model": args.model,
                    "round": round_index,
                    "run": run_index,
                    "cache_position": position,
                    "query_index": args.query_index,
                    "outcome": trial["outcome"],
                    "has_answer_ready": trial["has_answer_ready"],
                    "diagnostic": trial["diagnostic"],
                    "elapsed_s": round(time.time() - started, 1),
                }
            except Exception as e:  # noqa: BLE001 - a transport error is data here
                record = {
                    "model": args.model,
                    "round": round_index,
                    "run": run_index,
                    "cache_position": position,
                    "query_index": args.query_index,
                    "error": f"{type(e).__name__}: {e}",
                    "elapsed_s": round(time.time() - started, 1),
                }
            finally:
                backend.close()

            rows.append(record)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"        outcome={record.get('outcome', record.get('error'))} "
                  f"({record['elapsed_s']}s)", flush=True)

    print("\n=== summary ===")
    print(f"{'round':>5}  {'position':<6}  {'outcome':<10}  diagnostic")
    for record in rows:
        code = (record.get("diagnostic") or {}).get("code", "")
        print(f"{record['round']:>5}  {record['cache_position']:<6}  "
              f"{record.get('outcome', record.get('error', '?')):<10}  {code}")

    firsts = [r for r in rows if r.get("cache_position") == "first"]
    warms = [r for r in rows if r.get("cache_position") == "warm"]
    print(f"\nfirst-of-round: "
          f"{sum(1 for r in firsts if r.get('outcome') == 'voluntary')}/{len(firsts)} voluntary")
    print(f"warm-in-round:  "
          f"{sum(1 for r in warms if r.get('outcome') == 'voluntary')}/{len(warms)} voluntary")
    print("\nraw records appended to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
