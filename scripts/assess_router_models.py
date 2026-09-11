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
from rlm_local.model_check import check_model  # noqa: E402


def list_models(endpoint: str) -> list[str]:
    """Model IDs the router advertises, in the order it returns them."""
    resp = httpx.get(f"{endpoint.rstrip('/')}/models", timeout=60.0, verify=False)
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", []) if m.get("id")]


def wait_for_router(endpoint: str, timeout: float = 120.0) -> bool:
    """Block until the router answers *twice in a row*.

    One successful `/v1/models` is not enough after a restart: the router's own
    listener can come back before it is ready to spawn a model instance, and a
    request in that window is refused at the socket level. Requiring two
    consecutive answers a couple of seconds apart avoids that race (observed as
    a spurious ConnectError for one model in the first screen run).
    """
    deadline = time.time() + timeout
    consecutive = 0
    while time.time() < deadline:
        try:
            list_models(endpoint)
            consecutive += 1
            if consecutive >= 2:
                return True
        except Exception:  # noqa: BLE001 - keep polling until the deadline
            consecutive = 0
        time.sleep(2.0)
    return False


def screen_model(endpoint: str, model: str, profile: str = "tiny") -> dict:
    """One-turn protocol screen: does the model emit a ```repl block at all?

    The full battery costs roughly 7 completions per model, and a model that
    cannot emit a fenced block fails every one of them the same way — so on a
    slow server the battery spends most of its time re-confirming a rejection.
    This uses the **real** system prompt (same `prompt_vars`, same prologue) and
    a single turn, which is enough to separate "operates the protocol" from
    "does not", for one request instead of dozens.
    """
    from rlm_local.config import load_config
    from rlm_local.parser import FENCE_RE
    from rlm_local.prompts import build_system_prompt
    from rlm_local.templates import METADATA_TEMPLATE, PROLOGUE

    cfg = load_config(profile)
    prompt_vars = cfg.prompt_vars()
    messages = [
        {"role": "system", "content": build_system_prompt(prompt_vars)},
        {"role": "user", "content": METADATA_TEMPLATE.format(
            query="What is the name of the largest ocean?",
            context_type="str",
            context_len=180,
            sub_budget=prompt_vars["sub_budget"],
            max_turns=prompt_vars["max_turns"],
        )},
        {"role": "user", "content": PROLOGUE},
    ]
    backend = HTTPModelBackend(
        root_endpoint=endpoint, root_model=model, verify=False, timeout=900.0,
    )
    started = time.time()
    try:
        text = backend.chat(messages, tier="root", max_tokens=400,
                            temperature=0.0)
    finally:
        backend.close()
    blocks = FENCE_RE.findall(text)
    return {
        "model": model,
        "mode": "screen",
        "emits_repl_block": bool(blocks),
        "n_blocks": len(blocks),
        "chars": len(text),
        "elapsed_s": round(time.time() - started, 1),
        "sample": text.strip().replace("\n", " ")[:180],
    }


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
    ap.add_argument("--screen", action="store_true",
                    help="one-turn protocol screen per model (cheap triage) "
                         "instead of the check battery")
    ap.add_argument("--order", default="",
                    help="comma-separated model IDs to assess first, so a long "
                         "sweep yields the most interesting results early")
    ap.add_argument("--before-each", default="",
                    help="shell command run before each model, then the router is "
                         "polled until it answers again. Use it to free memory: a "
                         "router with max_instances > 1 keeps every model it has "
                         "served resident, and on a RAM-tight host that means "
                         "swap thrashing and an order-of-magnitude slowdown. "
                         "Example: --before-each 'ssh box systemctl --user "
                         "restart llama-router.service'")
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
    print(f"battery: {'protocol screen (1 turn)' if args.screen else ('full (9 probes)' if args.full else 'quick (P1+P4+P6)')}",
          flush=True)
    print(f"models to assess: {len(models)}", flush=True)
    for m in models:
        print(f"  - {m}", flush=True)
    print(flush=True)

    results: list[dict] = []
    started = time.time()

    for i, model in enumerate(models, 1):
        print(f"[{i}/{len(models)}] {model} ...", flush=True)
        t0 = time.time()
        if args.before_each:
            subprocess.run(args.before_each, shell=True, capture_output=True,
                           timeout=180)
            if not wait_for_router(args.endpoint, timeout=180):
                print("    router did not come back after --before-each; stopping",
                      flush=True)
                return 2
        # A fresh backend per model: the R9 retry/timeout policy applies to the
        # router's model load, which can take tens of seconds on first touch.
        if args.screen:
            try:
                record = screen_model(args.endpoint, model, args.profile)
                record["elapsed_s"] = round(time.time() - t0, 1)
                print(f"    emits_repl_block={record['emits_repl_block']} "
                      f"({record['chars']} chars, {record['elapsed_s']}s)", flush=True)
            except Exception as e:  # noqa: BLE001
                record = {"model": model, "mode": "screen",
                          "error": f"{type(e).__name__}: {e}",
                          "elapsed_s": round(time.time() - t0, 1)}
                print(f"    ERROR {record['error']}", flush=True)
            results.append(record)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            continue

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
    if args.screen:
        for r in sorted(results, key=lambda x: (x.get("emits_repl_block") is True,
                                                x.get("elapsed_s") or 0),
                        reverse=True):
            mark = "PASS" if r.get("emits_repl_block") else (
                "ERR " if r.get("error") else "FAIL")
            print(f"  {r['model']:28s} {mark}  "
                  f"{r.get('elapsed_s', 0):>6.0f}s  {r.get('sample', '')[:60]}",
                  flush=True)
        print(f"\nraw results appended to {out}", flush=True)
        return 0

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
