"""R14 load-gate measurement, index phases only (steps 2-4 of the runbook).

`scripts/run_load_gate_100k.py` stalls in its phase 5: `git add -A` over 100K
newly-created files never finishes on this host (a `git` process sat at 7 s CPU
for 13 minutes holding `index.lock`, with no CPU progress) — the sandbox's
per-file ACL interposition makes that write path pathological. Phase 5 measures
`git status` (< 2000 ms, historically 38 ms), which is not the regression class
the gate exists to catch.

This script runs the phases that are: full rebuild time and search latency on a
100K-page vault, plus the index-vs-vault-walk helper listing. It streams output
line by line so progress is observable.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] if "__file__" in dir() else Path.cwd()
sys.path.insert(0, str(REPO / "src"))

from rlm_kernel.index import rebuild_index  # noqa: E402
from rlm_kernel.vault import LocalVault  # noqa: E402

CORPUS = Path(os.environ.get(
    "LOAD_CORPUS_OUT", str(REPO / ".tmp_load_corpus")
))
QUERIES = ["error", "function", "system", "data", "process",
           "implementation", "configuration", "test", "result", "value"]


def log(msg: str) -> None:
    print(msg, flush=True)


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = k - f
    return s[f] + (s[f + 1] - s[f]) * c if f + 1 < len(s) else s[f]


def main() -> int:
    pages = sum(1 for _ in CORPUS.rglob("*.md")) if CORPUS.exists() else 0
    log(f"corpus: {CORPUS}")
    log(f"pages found by walk: {pages}")
    if pages == 0:
        log("corpus missing — run scripts/run_load_gate_100k.py phase 1 first")
        return 2

    idx_path = CORPUS / ".index" / "meta.sqlite"
    vault = LocalVault(CORPUS, init_git=False)

    log("[2/4] full reindex…")
    t = time.perf_counter()
    idx = rebuild_index(vault, idx_path)
    rebuild_s = time.perf_counter() - t
    log(f"  {idx.page_count()} pages in {rebuild_s:.0f}s  (target < 7200s)")

    log("[3/4] search latency…")
    lat: list[float] = []
    for q in QUERIES:
        t = time.perf_counter()
        hits = idx.fts_search(q, limit=5)
        ms = (time.perf_counter() - t) * 1000
        lat.append(ms)
        log(f"  {q!r}: {ms:.1f}ms ({len(hits)} hits)")
    p95 = pct(lat, 95)
    log(f"  p95 = {p95:.1f}ms  (target < 300ms)")

    log("[4/4] helper listing…")
    t = time.perf_counter()
    paths = idx.list_paths(kind="helper", status="active")
    idx_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    helper_pages = vault.list(kind="helper")
    walk_s = time.perf_counter() - t
    log(f"  index list_paths: {idx_ms:.1f}ms ({len(paths)} paths)")
    log(f"  vault.list walk:  {walk_s:.1f}s ({len(helper_pages)} parsed)")
    idx.close()

    log("")
    log("=== RESULTS ===")
    log(f"pages={pages} rebuild={rebuild_s:.0f}s search_p95={p95:.1f}ms "
        f"helper_idx={idx_ms:.1f}ms helper_walk={walk_s:.1f}s")
    log(f"gate: rebuild {'PASS' if rebuild_s < 7200 else 'FAIL'} | "
        f"search {'PASS' if p95 < 300 else 'FAIL'} | "
        f"git NOT MEASURED (phase 5 stalls on this host — see docstring)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
