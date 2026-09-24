"""Measure what describing a document costs, cold and warm (RO6, 2026-09-23).

The first live measurement could not explain its own spread: two documents of similar size took
29.3 s and 802.9 s, and the client-side numbers said nothing about why. The engine now records
what the *server* reports per call (`timings`: `prompt_n`/`prompt_ms`, `predicted_n`/`predicted_ms`,
`cache_n`; and `usage.prompt_tokens_details.cached_tokens`), so this probe asks the question
directly, with a paired design:

* the **first** call for a document pays for its prompt — llama.cpp says how many tokens it
  evaluated and how long that took;
* the **second** call sends the identical prompt, so if llama.cpp's prompt cache is what makes
  some calls fast, the second call should report almost no fresh prompt tokens.

Two calls per document, back to back, same model, one model at a time. The probe prints numbers
and aggregates only; the metrics log it writes goes beside the corpus (0600) and contains no
document text, by construction (`rlm_local.summary_metrics.measure`).

Usage, on the machine that holds the corpus:

    uv run python scripts/probe_summarise_cost.py \
        --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite \
        --plan ~/rlm-derived/enrich-plan.tsv --n 3 \
        --log ~/rlm-derived/summaries-probe.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rlm_kernel.enrich import read_plan, render_plan  # noqa: E402
from rlm_kernel.mounts import LocalTreeMount  # noqa: E402
from rlm_local.config import load_config  # noqa: E402
from rlm_local.model_backend import HTTPModelBackend  # noqa: E402
from rlm_local.summarise import make_summarise_engine, summarise_timeout_seconds  # noqa: E402
from rlm_local.summary_metrics import aggregate, read_log, render  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--corpus-index", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--n", type=int, default=3, help="documents to describe twice")
    parser.add_argument("--profile", default="laptop")
    parser.add_argument("--model", default=os.environ.get("RLM_MODEL"))
    parser.add_argument("--endpoint", default=os.environ.get("RLM_ENDPOINT"))
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument(
        "--cited-only", action="store_true", default=True,
        help="restrict to documents an answer cited (the default, and the point of RO6)",
    )
    args = parser.parse_args(argv)

    overrides: dict[str, object] = {}
    if args.endpoint:
        overrides["root_endpoint"] = args.endpoint
    if args.model:
        overrides["root_model"] = args.model
        overrides["sub_model"] = args.model
    config = load_config(args.profile, **overrides)

    from rlm_kernel.mine import MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS

    max_tokens = args.max_tokens or SUMMARY_MAX_TOKENS
    timeout = args.timeout or summarise_timeout_seconds(MAX_SUMMARY_INPUT_BYTES, max_tokens)

    candidates = [candidate for candidate in read_plan(args.plan) if candidate.cited][: args.n]
    print(render_plan(read_plan(args.plan)))
    print(f"probe: {len(candidates)} document(s), two calls each, timeout {timeout:g}s")

    mount = LocalTreeMount(args.corpus_root)
    backend = HTTPModelBackend(
        root_endpoint=config.root_endpoint, root_model=config.root_model, verify=False,
        timeout=timeout,
    )
    engine = make_summarise_engine(
        backend, model=config.root_model, endpoint=config.root_endpoint,
        log_path=args.log, max_tokens=max_tokens,
    )
    print(f"model {engine.engine_tag}")
    print(f"metrics log {args.log}")

    read_failures = 0
    call_failures = 0
    try:
        for candidate in candidates:
            try:
                with mount.open_readonly(candidate.source, max_bytes=MAX_SUMMARY_INPUT_BYTES) as fh:
                    text = fh.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — classified and counted, never printed (§1.9)
                read_failures += 1
                continue
            for attempt in ("cold", "warm"):
                try:
                    _summary, meta = engine(text, max_tokens)
                except Exception as exc:  # noqa: BLE001
                    call_failures += 1
                    print(f"  {attempt}: failed — {type(exc).__name__}")
                    continue
                server = meta.get("server") or {}
                print(
                    f"  {attempt}: {meta['seconds']:8.1f}s  in {meta['input_chars']:6d} chars  "
                    f"prompt {server.get('prompt_n')} tok in {server.get('prompt_ms')} ms  "
                    f"cache_n {server.get('cache_n')}  decode {server.get('predicted_n')} tok in "
                    f"{server.get('predicted_ms')} ms  unexplained "
                    f"{meta.get('client_residual_seconds')}s"
                )
    finally:
        backend.close()

    print(f"reads skipped: {read_failures}, calls failed: {call_failures}")
    print(
        render(
            aggregate(read_log(args.log)),
            scale=f"{engine.engine_tag}, timeout {timeout:g}s, cold/warm pairs",
            sets={"value set": 143, "cited set": 13},
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
