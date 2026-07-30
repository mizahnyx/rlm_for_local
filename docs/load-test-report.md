# Load-Gate Report — rlm-kernel 100K-Page Benchmark

**Date:** 2026-07-26
**Corpus:** Synthetic Tier 1 (100,000 pages, deterministic seed 42, `tests/load/gen_corpus.py`)
**Machine:** Windows dev box (Intel i5-10400F CPU @ 2.90 GHz, 16 GB RAM)
**Commit:** `4ed8db9` ("fix: F1-F5 — eliminate FTS5 rebuild quadratic, add C1 index-backed prompt listing")

## Acceptance Targets (from spec §12)

| Metric | Target | Status |
|---|---|---|
| Full reindex (100K pages) | < 7,200 s (2 h) | **PASS** |
| FTS search p95 | < 300 ms | **PASS** |
| `git status` | < 2,000 ms | **PASS** |

## Results

| Metric | Run 1 (pre-Defender) | Run 2 (post-exclusion) | **Run 3 (post-F1–F5)** | Target |
|---|---|---|---|---|
| Pages | 100,000 | 100,000 | 100,000 | 100,000 |
| Full reindex | 12,680 s FAIL | 13,090 s FAIL | **582 s (9.7 min)** | < 7,200 s |
| Search p95 | 158 ms PASS | 514 ms FAIL* | **116.9 ms** | < 300 ms |
| `git status` | 193 ms PASS | 87 ms PASS | **38 ms** | < 2,000 ms |
| Helper listing (index) | 371 ms | 54 ms | 46 ms | informational |
| Helper listing (walk) | 1,634 s | 705 s | 577 s | informational |

*Run 2 search measured immediately post-rebuild with un-checkpointed WAL;
quiet-copy baseline was 117 ms (diagnosis #2 §3).

## Root Cause of Run 1–2 Failures

The original D7-remediated code executed `DELETE FROM fts_pages WHERE path = ?`
for every page during `Index.build()`. SQLite FTS5 virtual tables have **no
index on content columns** — only rowid and MATCH are indexed access paths.
Each DELETE performed a full scan of the growing FTS table, producing a
quadratic total cost. Measured per-page cost grew from 3.7 ms to 38.9 ms
over 20K pages, extrapolating to ~3.5 hours at 100K.

## Fix (F1–F5, C1)

- **F1:** `_index_page(for_update=False)` on fresh builds skips all per-page
  DELETEs (tables are emptied up front). This removes the quadratic from `build()`.
- **F2:** `fts_rowid` column captures `last_insert_rowid()` on FTS insert;
## Tier 2 (Organic Corpus)

**Shelved (insufficient corpus).** The organic corpus proved insufficient in
quality and quantity for a meaningful 100K-class validation. If a suitable
corpus ever emerges, the runbook
(`docs/20260725-0953-load-gate-100k-runbook.md` §4) revives this
test unchanged; the two env-gated Tier-2 suite tests stay in place and will
activate on `RLM_KERNEL_LOAD_CORPUS` being set.

The kernel moves to **production soak testing** as a daily driver. Regression
checkpoints (rebuild, search p95, git status) should be re-run when the real
vault crosses 10K / 50K / 100K pages.
```
.venv/Scripts/python.exe -m pytest tests/load/test_load.py -v -k "tier2" --timeout=7200
```
