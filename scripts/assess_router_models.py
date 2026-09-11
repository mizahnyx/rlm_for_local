"""Suitability sweep over every model the llama.cpp router offers.

Runs the `rlm check` battery (P1 + P4 + P6 unless `--full`) against each model
listed by `GET /v1/models` on the configured router, and appends one JSON line
per model so partial results survive an interruption.

The sweep is deliberately **sequential**: the router loads models on demand and
the host is memory-constrained, so running checks in parallel would thrash model
swaps and risk an OOM on someone else's laptop.

Usage:
    .venv/Scripts/python.exe scripts/assess_router_models.py \
        --endpoint https://lunacode:9010/v1 --out logs/router-model-assessment.jsonl

    # a subset, or the full 9-probe battery
    ... --only Qwen3.5-2B-Instruct --full
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import httpx  # noqa: E402

from rlm_local.model_backend import HTTPModelBackend  # noqa: E402
from rlm_local.model_check import check_model  # noqa: E402


def list_models(endpoint: str) -> list[str]:
    """Model IDs the router advertises, in the order it returns them."""
    resp = httpx.get(f"{endpoint.rstrip('/')}/models", timeout=60.0, verify=False)
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", []) if m.get("id")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="https://lunacode:9010/v1")
    ap.add_argument("--profile", default="tiny")
    ap.add_argument("--out", default="logs/router-model-assessment.jsonl")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to these model IDs (default: all advertised)")
    ap.add_argument("--skip", nargs="*", default=[],
                    help="model IDs to skip, e.g. an already-assessed one")
    ap.add_argument("--full", action="store_true",
                    help="run all nine probes instead of the quick three")
    ap.add_argument("--order", default="",
                    help="comma-separated model IDs to assess first, so a long "
                         "sweep yields the most interesting results early")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        models = list_models(args.endpoint)
    except Exception as e:  # noqa: BLE001 - report and stop
        print(f"could not list models at {args.endpoint}: {type(e).__name__}: {e}",
              flush=True)
        return 2

    if args.only:
        models = [m for m in models if m in set(args.only)]
    models = [m for m in models if m not in set(args.skip)]
    if args.order:
        priority = [m.strip() for m in args.order.split(",") if m.strip()]
        models.sort(key=lambda m: priority.index(m) if m in priority else len(priority))

    print(f"router: {args.endpoint}", flush=True)
    print(f"battery: {'full (9 probes)' if args.full else 'quick (P1+P4+P6)'}", flush=True)
    print(f"models to assess: {len(models)}", flush=True)
    for m in models:
        print(f"  - {m}", flush=True)
    print(flush=True)

    results: list[dict] = []
    started = time.time()

    for i, model in enumerate(models, 1):
        print(f"[{i}/{len(models)}] {model} ...", flush=True)
        t0 = time.time()
        # A fresh backend per model: the R9 retry/timeout policy applies to the
        # router's model load, which can take tens of seconds on first touch.
        backend = HTTPModelBackend(
            root_endpoint=args.endpoint,
            root_model=model,
            verify=False,
            timeout=900.0,
        )
        record: dict = {"model": model, "profile": args.profile,
                        "quick": not args.full}
        try:
            result = check_model(backend, model,
                                 quick=not args.full, profile=args.profile)
            record.update({
                "score": result.get("score"),
                "verdict": result.get("verdict"),
                "probes_passed": result.get("probes_passed"),
                "probes_total": result.get("probes_total"),
                "per_probe": {
                    pid: {"score": pr.get("score"),
                          "max_score": pr.get("max_score"),
                          "passed": pr.get("passed")}
                    for pid, pr in (result.get("per_probe") or {}).items()
                },
                "evidence": (result.get("evidence_lines") or [])[:12],
            })
            print(f"    score={record['score']}/100 verdict={record['verdict']} "
                  f"probes={record['probes_passed']}/{record['probes_total']}", flush=True)
        except Exception as e:  # noqa: BLE001 - one bad model must not stop the sweep
            record.update({"error": f"{type(e).__name__}: {e}"})
            print(f"    ERROR {record['error']}", flush=True)
        finally:
            backend.close()

        record["elapsed_s"] = round(time.time() - t0, 1)
        results.append(record)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(flush=True)
    print(f"=== SUMMARY ({time.time() - started:.0f}s total) ===", flush=True)
    ranked = sorted(
        results,
        key=lambda r: (r.get("score") is not None, r.get("score") or 0),
        reverse=True,
    )
    for r in ranked:
        score = r.get("score")
        print(f"  {r['model']:28s} "
              f"{('n/a' if score is None else f'{score:>5}/100'):>9}  "
              f"{(r.get('verdict') or r.get('error') or '?')[:28]:28s} "
              f"{r.get('elapsed_s', 0):>7.0f}s", flush=True)
    print(f"\nraw results appended to {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
