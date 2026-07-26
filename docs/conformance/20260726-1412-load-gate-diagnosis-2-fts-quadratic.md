# Diagnosis #2: The Rebuild Quadratic — Found, Proven, and Fix Protocol

**Date:** 2026-07-26 (14:12)
**Supersedes/extends:** `docs/20260725-1951-load-gate-tier1-diagnosis.md` (diagnosis #1)
**Inputs:** two gate runs (`load-gate-tier1.txt`, `20260726-0949_load-gate-tier1.txt`) + two controlled experiments on the machine (`scripts/diagnose_fts_scaling.py`, `scripts/diagnose_fts_fix.py`) + source review of the remediated `index.py`.

---

## 1. Executive summary

**Root cause found and proven:** the remediation's D7 fix (delete-before-insert in `_index_page`) executes `DELETE FROM fts_pages WHERE path = ?` (`src/rlm_kernel/index.py:148`). FTS5 virtual tables have **no index on content columns** — only rowid and MATCH are indexed access paths — so this DELETE is a **full scan of the FTS table for every indexed page**. That is the quadratic: rebuild cost per page grows linearly with index size (measured: 3.7 → 38.9 ms/page over 20K pages, still climbing), totaling ~3.5 hours at 100K — matching both gate runs (12,680 s and 13,090 s).

The Defender exclusion was a correct but orthogonal fix: it sped up every I/O-bound operation (walk 1,634→705 s, `list_paths` 371→54 ms, git 193→87 ms) and thereby **proved** the rebuild was never I/O-bound.

**The fix is small and validated by measurement** (§4): skip the per-page DELETEs on fresh builds (they are no-ops against just-emptied tables), delete by stored FTS rowid on update paths, and optionally migrate to the spec's original external-content design (measured 0.22 ms/page for the entire index phase). Expected rebuild after the fix: **~25–40 min at 100K → PASS**.

## 2. The evidence chain

### Gate runs

| Metric | Run 1 | Run 2 (post-Defender-exclusion) | Reading |
|---|---|---|---|
| rebuild 100K | 12,680 s FAIL | 13,090 s FAIL | unchanged → not open-bound |
| search p95 | 158 ms PASS | 514 ms FAIL | see §3 — artifact |
| git status | 193 ms PASS | 87 ms PASS | exclusion worked |
| helper walk | 1,634 s | 705 s | exclusion worked |
| helper `list_paths` | 371 ms | 54 ms | exclusion worked |

### Experiment 1 — insert cost scales linearly with index size

| Batch | ms/page |
|---|---|
| 0–4K | 3.7 |
| 4–8K | 12.6 |
| 8–12K | 20.9 |
| 12–16K | 29.7 |
| 16–20K | 38.9 |

A linearly-growing per-page cost = quadratic total. Integrating the curve to 100K reproduces the observed ~3.5 h.

### Experiment 2 — statement-level timing exonerates everything *except* the DELETE

A faithful reconstruction of `_index_page`'s statements — `INSERT OR REPLACE pages`, `DELETE FROM tags`, `INSERT tags`, `INSERT INTO fts_pages` — runs **flat at 0.45 ms/page total** (FTS insert alone: 0.08 ms/page). It omitted exactly one statement the real code has: `DELETE FROM fts_pages WHERE path = ?`. Experiment 1 (which used the real `_index_page`) shows the quadratic; experiment 2 (which lacked that DELETE) does not. The code review then confirmed the mechanism: FTS5 has no secondary index on `path`, so the DELETE scans the whole virtual table — bodies included — per page.

**Correction to diagnosis #1:** the "file opens dominate" conclusion held only for the *walk* paths; the rebuild's dominant term is this FTS DELETE. The "FTS5 write amplification" hypothesis is falsified (inserts measured at 0.08 ms/page). Diagnosis #1's G4b finding and E1 exclusion advice remain valid and are retained.

## 3. The search-p95 FAIL in run 2 was an artifact

On a copy of run 2's own index file (with its WAL): p95 = **117 ms** as-is, **109 ms** after a 7-second `INSERT INTO fts_pages(fts_pages) VALUES('optimize')`. The 514 ms reading was taken immediately after a 3.6-hour write marathon, with an un-checkpointed WAL and heavy cache pressure. Fix: checkpoint after builds and before measuring — `PRAGMA wal_checkpoint(TRUNCATE)`. Search at 100K is healthy.

Tombstone probe: DELETE of 1,000 FTS rows = 0.2 s → the delete-all in `build()` is *not* a meaningful cost (and is not the quadratic).

## 4. Fix protocol (implementation agent)

Ordered; F1 alone unblocks the gate. Each lands with a test.

- **F1 — fresh-build guard (hotfix, ~3 lines).** `Index.build()` empties all tables up front; per-page DELETEs inside `_index_page` are therefore no-ops during a fresh build. Pass a flag (`for_update=False` on the build path; `True` from `reindex_delta`) and skip the four per-page DELETEs (fts, tags, links, old-id) when not updating. **This removes the quadratic from `build()` entirely.**
- **F2 — rowid-keyed FTS maintenance (update path).** Add `fts_rowid INTEGER` to `pages`; capture `last_insert_rowid()` on FTS insert; on updates, `DELETE FROM fts_pages WHERE rowid = ?` (indexed in FTS5) instead of `WHERE path = ?`. Keeps `reindex_delta` O(log) per changed page.
- **F3 — external-content FTS (structural, recommended; this was the spec §4 design).** Plain `content` table (rowid PK) + `CREATE VIRTUAL TABLE fts_pages USING fts5(title, summary, body, content='content', content_rowid='rowid')`; bulk load = `executemany` + one `VALUES('rebuild')` pass. Measured: **1.3 s for 6,000 pages (~0.22 ms/page)** for the entire index phase, and it deduplicates storage (bodies stored once, not twice). Note: implement MATCH access via content-rowid joins; keep the `list_paths` fast path on the `pages` meta table as-is.
- **F4 — post-build hygiene.** `PRAGMA wal_checkpoint(TRUNCATE)` after `build()`; optional `'optimize'` (7 s at 100K).
- **F5 — regression test.** Extend the rebuild≡delta property test with a scaling assertion: fresh-build 4K pages, then delta-reindex after 200 edits; assert delta per-page time ≤ 10× fresh per-page time (catches quadratics). The load-gate script should checkpoint before measuring search (F4).
- **C1 from diagnosis #1 remains open and is now the top per-completion item:** index-backed helper listing in `prompts.py` (`load_system_prompt_from_vault`) — the 27-min walk at 100K, confirmed twice.

## 5. Rerun protocol (user)

1. Agent lands F1 (+F2, and F3 if they choose the structural route now) + C1.
2. Rerun: `.venv/Scripts/python.exe scripts/run_load_gate_100k.py 2>&1 | tee load-gate-tier1-rerun2.txt` (corpus exists; generation skipped).
3. Expected: rebuild ~25–40 min with F1 alone (parse ~15 ms/page + index ~1 ms/page), faster with F3 + CSafeLoader/threading later. PASS = < 7,200 s.
4. Then Tier 2 (organic corpus, runbook `docs/20260725-0953-load-gate-100k-runbook.md` §4).

## 6. Updated project ledger

| Item | State |
|---|---|
| R1–R6, F1/F2/F4 (docs) | ✅ closed |
| G3 — 100K gate | ⏳ rebuild FAIL ×2 — **root cause proven, fix specified (§4)**; search/git PASS |
| G4b — per-completion full-walk in `prompts.py` | ❗ top per-completion item (C1) |
| N1 — dangling doc references | open (trivial) |
| Tier 2 — organic corpus | awaiting user corpus prep |
| K4-real (GEPA) | blocked on: F1/F2(or F3) + C1 + gate PASS |
