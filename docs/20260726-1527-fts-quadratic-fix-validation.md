# Validation: FTS-Quadratic Remediation (`4ed8db9`) — 100K Gate PASSES

**Date:** 2026-07-26 (15:27)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `4ed8db9` ("fix: F1-F5 — eliminate FTS5 rebuild quadratic, add C1 index-backed prompt listing") against the fix protocol in `docs/20260726-1412-load-gate-diagnosis-2-fts-quadratic.md` §4.
**Method:** code review + unit suite + two full 100K gate reruns (one crashed on a migration gap; one completed green).

---

## 1. Verdict

**The remediation is validated: the 100K load gate now PASSES all three acceptance metrics, with the rebuild 22× faster.** The quadratic is gone, confirmed at full scale on the real corpus.

| Metric | Target | Before (runs 1–2) | **After (run 3)** | Verdict |
|---|---|---|---|---|
| Full reindex, 100K pages | < 7,200 s | 12,680 s / 13,090 s FAIL | **582 s (9.7 min)** | **PASS (12× under budget)** |
| FTS search p95 | < 300 ms | 158 ms / 514 ms (artifact) | **116.9 ms** | **PASS** |
| `git status` | < 2,000 ms | 193 ms / 87 ms | **38 ms** | **PASS** |
| Helper listing — index | informational | 371 / 54 ms | **46 ms** | — |
| Helper listing — walk | informational | 1,634 / 705 s | 576.9 s | — |

The walk number is now purely informational: with C1 landed, no per-completion code path walks the vault (see §2). From 3.5 hours to under 10 minutes, at 5.8 ms/page — the F1 diagnosis and fix are proven at scale, not just in sample.

## 2. Fix-by-fix verification

- **F1 (fresh-build guard): ✅** `build()` indexes with `_index_page(page, for_update=False)` (`index.py:114`), skipping all per-page DELETEs — the FTS5 full-scan quadratic is structurally absent from the build path. This is where the 22× came from.
- **F2 (rowid-keyed updates): ✅** `pages.fts_rowid` column; updates `DELETE FROM fts_pages WHERE rowid = ?` using `cursor.lastrowid` captured at insert; `reindex_delta` uses rowid with a path fallback; both changed-page branches call `for_update=True` (`index.py:262,264`).
- **F3 (external-content FTS): not done — optional route.** F1+F2 is the sanctioned minimal path; F3 (storage dedup + `VALUES('rebuild')` bulk pass) remains available as a future structural upgrade, now non-urgent.
- **F4 (post-build checkpoint): ✅** `_checkpoint()` runs `PRAGMA wal_checkpoint(TRUNCATE)` after `build()` — and the run-3 search measurement (116.9 ms, consistent with the quiet-copy 117 ms baseline from diagnosis #2) confirms the run-2 artifact is gone.
- **C1 (index-backed prompt listing): ✅** `load_system_prompt_from_vault(..., bridge=)` uses `Index.list_paths` when the index exists (`prompts.py:180-196`); `root_loop.py:130` passes the bridge. The 27-minute per-completion walk is eliminated. Helper-page cap aligned at 30 lines.
- **F5 (scaling regression test): ⚠️ present but vacuous — see D-a below.**

## 3. Two defects in the remediation (both found by the validation runs, both small)

- **D-a — F5 test never exercises the delta path.** `test_delta_per_page_no_worse_than_10x_fresh` times 50 `vault.put` edits but **never calls `idx.reindex_delta(vault)`** — so its ratio assertion passes trivially and cannot catch the quadratic it was written to guard. Fix: time `reindex_delta` after the edits (per-edit or once), then assert.
- **D-b — schema migration gap.** The stale index from the earlier runs crashed the new code: `sqlite3.OperationalError: table pages has no column named fts_rowid`. `CREATE TABLE IF NOT EXISTS` doesn't migrate. Every pre-existing vault index in the wild hits this on the next build **or delta**. I cleared the derived index at the test corpus to unblock the run (safe — rebuildable from markdown), but the fix belongs in code: a `PRAGMA user_version` migration step (`ALTER TABLE pages ADD COLUMN fts_rowid INTEGER` when absent), per spec §12/ops, plus a fixture test that builds an old-schema index and migrates it.

## 4. Suite state

`pytest tests/ -q` → **169 passed, 4 failed, 2 skipped**. The 4 failures are all `tests/test_integration.py`, which requires a live llama-server at `https://localhost:9010` (currently down) and has no skip-if-unavailable logic — environmental, unrelated to this remediation. Hygiene fix for the agent: probe the endpoint in the fixture and `pytest.skip` when unreachable.

## 5. Project ledger after this validation

| Item | State |
|---|---|
| Review D1–D13, Addendum D-1…D-6, G1–G5, N1, F1/F2/F4 follow-ups | ✅ closed (N1 still trivially open) |
| **G3 — 100K load gate** | ✅ **PASSED** (582 s / 116.9 ms / 38 ms at 100K pages) |
| G4b/C1 — per-completion full-walk | ✅ closed (index-backed; 46 ms) |
| D-a — F5 test vacuous | open (trivial; see §3) |
| D-b — schema migration for `fts_rowid` | open (small; see §3) |
| test_integration skip-if-server-down | open (hygiene) |
| Tier 2 — organic corpus validation | **awaiting the user's corpus** (runbook `docs/20260725-0953-load-gate-100k-runbook.md` §4: prepare folder → converter → set `RLM_KERNEL_LOAD_CORPUS` → run `-k tier2`) |
| **K4-real (GEPA)** | **unblocked** once D-a/D-b land (they're one small commit) |

## 6. What remains before K4

One small agent commit: D-a (make F5 real), D-b (`PRAGMA user_version` migration + old-schema fixture test), integration-test skip logic, N1 (copy conformance docs into the repo or fix the references). Optionally commit this validation's numbers into `rlm_for_local/docs/load-test-report.md` (table in §1 above is ready to paste). After that, the conformance loop from the July 24–26 review cycle is fully closed and the K4 GEPA milestone (Addendum §3) starts on a green, load-gated foundation.
