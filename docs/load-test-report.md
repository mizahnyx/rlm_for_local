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
quiet-copy baseline was 117 ms (load-gate diagnosis #2, §3).

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
  updates then run `DELETE FROM fts_pages WHERE rowid = ?` (indexed in FTS5)
  instead of `WHERE path = ?`, keeping `reindex_delta` O(log) per changed page.
  A `PRAGMA user_version` migration step (`D-b`) adds the column to pre-existing
  indexes.
- **F3:** external-content FTS (plain `content` table + `VALUES('rebuild')` bulk
  pass, storage dedup) — **not taken.** The F1+F2 minimal path is the sanctioned
  route and measured sufficient; F3 remains available as a non-urgent
  structural upgrade.
- **F4:** `_checkpoint()` runs `PRAGMA wal_checkpoint(TRUNCATE)` after `build()`,
  and search is measured after the checkpoint — the run-2 p95 artifact is gone.
- **F5:** scaling regression test on the rebuild≡delta property (see D-a in the
  validation doc for why the first version of it was vacuous).
- **C1:** `load_system_prompt_from_vault(..., bridge=)` lists active helpers from
  the SQL index when it exists, replacing the per-completion vault walk
  (1,634 s → 46 ms at 100K pages).

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

To revive Tier 2 once a suitable corpus exists, set `RLM_KERNEL_LOAD_CORPUS` and
run the two env-gated suite tests:

```bash
.venv/Scripts/python.exe -m pytest tests/load/test_load.py -v -k "tier2" --timeout=7200
```

---

## Re-run after the 2026-09 remediation (R14)

**Date:** 2026-09-10 · **Commit:** the R11–R14 kernel commit of that wave
**Why:** R14 changed index/search behaviour (`fts_search` query semantics,
`reindex_delta` keying, the quarantine-isolation check, the body-length unit), so
the gate was re-run — it exists to catch exactly this class of regression.

| Metric | Target | 2026-07-26 | **2026-09-10** |
|---|---|---|---|
| Pages | 100,000 | 100,000 | 100,000 |
| Full reindex | < 7,200 s | 582 s | **166 s** |
| Search p95 | < 300 ms | 116.9 ms | **176.5 ms** |
| `git status` | < 2,000 ms | 38 ms | **not measured** (see below) |
| Helper listing (index) | informational | 46 ms | **48.3 ms** |
| Helper listing (walk) | informational | 577 s | **101.4 s** |

Both measurable gates pass and nothing became quadratic: rebuild got faster and
search latency stayed in the same order of magnitude.

**The query-semantics change is visible on the same index**, which is the reason
to re-run rather than assume:

| Query form | Pages matched |
|---|---|
| `"system data"` (old — the whole query as an FTS5 phrase) | 10,906 |
| `"system" OR "data"` (new — per-token OR) | **94,393** |
| `"implementation process"` (phrase) | 3,990 |
| `"implementation" OR "process"` (OR) | **95,244** |

The old form silently dropped every page that contained the terms *separately*;
the new form restores recall while keeping exact-token matching (each token stays
quoted, so no FTS5 operator injection and no stemming surprises). The kernel
manual's §5.3 documents the semantics.

**Deviation — `git status` was not measured.** Phase 5 of
`scripts/run_load_gate_100k.py` could not complete on the machine used for the
re-run: `git add -A` over 100,000 freshly created files never finished (a `git`
process held `.git/index.lock` at 7 s of CPU for 13 minutes with no progress),
because every file operation is interposed by that environment's sandbox ACL
broker. `git status` is not the regression class this gate exists to catch, so
the index phases were measured standalone with
`scripts/run_load_gate_index_phases.py` (committed alongside this report) rather
than reported from a run that never reached them. The 2026-07-26 value of 38 ms
remains the last real measurement of that metric.
