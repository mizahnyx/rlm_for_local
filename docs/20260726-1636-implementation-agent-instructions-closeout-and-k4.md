# Instruction Report for the Implementation Agent — Closing the Loop and Starting K4

**Date:** 2026-07-26 (16:36)
**From:** project owner via Kimi Code CLI (validation track)
**To:** the implementation agent working on `~/Documents/Misc/rlm_for_local/`
**Authority chain:** this report operationalizes `docs/20260726-1527-fts-quadratic-fix-validation.md` (validation #3) §3–§6 and `docs/20260725-0838-rlm-kernel-conformity-review-addendum.md` §3. Where older documents disagree, the newest one wins.

Read first, in this order: (1) the validation report #3 (what passed and the two defects you shipped), (2) diagnosis #2 (why the fix is shaped that way), (3) this report.

---

## Part 1 — Close-out commit (small, do first)

Five work items. All land in **one commit** with tests, after which the conformance loop is declared closed.

### W1 — Schema migration for `fts_rowid` (defect D-b) — highest priority

**Problem:** `4ed8db9` added `pages.fts_rowid` to `_SCHEMA_SQL`, but `_ensure_schema` uses `CREATE TABLE IF NOT EXISTS`, which never alters existing tables. Any index built by pre-`4ed8db9` code crashes the new code with `sqlite3.OperationalError: table pages has no column named fts_rowid` — on both `build()` and `reindex_delta()`. Every pre-existing vault index in the wild is affected.

**Required change (`src/rlm_kernel/index.py`):**
- Introduce `PRAGMA user_version` management: set `user_version = 2` for the current schema. On `Index` connect, if `user_version < 2`, run migration step 1→2: `ALTER TABLE pages ADD COLUMN fts_rowid INTEGER`, then set `user_version = 2`. Structure it as an ordered `_MIGRATIONS` list so later steps slot in (spec §12/ops: "schema migrations via `PRAGMA user_version` steps").
- Old rows will have `fts_rowid = NULL`. The update path already falls back to `DELETE FROM fts_pages WHERE path = ?` when `fts_rowid IS NULL` (keep that fallback) — acceptable for rows last indexed by old code; they get rowids on their next update. Document that a full `build()` is the recommended repair for large old indexes.
- **Acceptance:** a fixture test that (a) creates an index with the *old* schema (hand-written `CREATE TABLE pages (...)` without `fts_rowid`, `user_version = 1`), inserts rows, (b) opens it with the current `Index` class, (c) asserts migration ran (column exists, `user_version == 2`, old rows intact), and (d) asserts `reindex_delta` works on it without error.

### W2 — Make the F5 scaling test real (defect D-a)

**Problem:** `tests/rlm_kernel/test_index.py::test_delta_per_page_no_worse_than_10x_fresh` times 50 `vault.put` edits but never calls `idx.reindex_delta(vault)` — it cannot catch the quadratic it guards.

**Required change:** after the edit loop, measure `idx.reindex_delta(vault)` (one timed call is fine; per-edit timing optional). Compute `delta_per_page_ms` from that call (divide by number of changed pages detected, e.g., 50). Keep the `ratio <= 10.0` assertion against fresh-build per-page. Sanity: on the current code the ratio should be ~1–3.
- **Acceptance:** the test measurably exercises `reindex_delta` (assert it ran ≥ 1 time with ≥ 50 changed pages processed — e.g., via a spy or by asserting the post-delta index contents changed), and it **fails when run against `4ed8db9~1`'s quadratic code** (verify by `git stash`-style temporary revert, once, manually; note the result in the commit message).

### W3 — `test_integration.py` skip-if-server-down

**Problem:** `tests/test_integration.py` hard-requires a live llama-server at `https://localhost:9010` and hard-fails (`httpx.ConnectError`) when it's down — 4 red tests on any machine without the server.

**Required change:** in the class fixture, probe the endpoint once (`GET /health` or a 1-token chat, 2 s timeout, `verify=False`) and `pytest.skip("llama-server not available", allow_module_level=True)` on connection failure.
- **Acceptance:** full suite reports these tests as **skipped** (not failed) with the server down; they run and pass with the server up.

### W4 — N1: dangling doc references

**Problem:** `README.md:131` and `docs/rlm-kernel-manual.md:970` reference `docs/20260725-0838-rlm-kernel-conformity-review-addendum.md`, which lives in the external `aisearches02` workspace, not in this repo.

**Required change:** copy the project's conformance history into `rlm_for_local/docs/conformance/` (plain copies, filenames unchanged): the conformity review, the review addendum, validation reports #1–#3, diagnosis #1–#2, and the load-gate runbook. Add one `docs/conformance/README.md` index line per file. Keep the in-repo references pointing at `docs/conformance/…`.
- **Acceptance:** every doc reference in README/manual resolves inside the repo.

### W5 — Commit the load-test report

Create `docs/load-test-report.md` with the run-3 numbers (source: `load-gate-tier1-rerun3.txt` / validation report #3 §1):

| Metric | Target | Run 1 | Run 2 (post-exclusion) | **Run 3 (post-F1–F5)** |
|---|---|---|---|---|
| Pages | 100,000 | 100,000 | 100,000 | 100,000 |
| Full reindex | < 7,200 s | 12,680 FAIL | 13,090 FAIL | **582 PASS** |
| Search p95 | < 300 ms | 158 PASS | 514 FAIL* | **116.9 PASS** |
| `git status` | < 2,000 ms | 193 PASS | 87 PASS | **38 PASS** |
| Helper listing (index) | info | 371 ms | 54 ms | 46 ms |
| Helper listing (walk) | info | 1,634 s | 705 s | 577 s |

*run-2 search measured immediately post-rebuild with un-checkpointed WAL; quiet-copy baseline was 117 ms (diagnosis #2 §3).

Include the diagnosis one-liner (FTS5 `DELETE ... WHERE path` full-scan quadratic; fix = fresh-build guard + rowid-keyed updates) and the report date.
- **Acceptance:** file exists, numbers match run 3, and the report states Tier 2 (organic) is pending.

**Part 1 verification protocol:** full suite green (with server down: integration tests *skipped*; everything else passing) + W1 migration test + W2 test verified as described + docs committed. Commit message: `fix: close-out — schema migration (D-b), real F5 test (D-a), integration skips, conformance docs, load-test report`.

---

## Part 2 — K4-real: the GEPA offline optimizer (new milestone)

Preconditions (already met): R1–R6 green, F1–F4 closed, 100K gate passed, C1 landed. Scope is exactly Addendum §3 — nothing more.

### Deliverables, in order

1. **`tests/evals/` suite definitions.** Verifiable tasks only (needle regex-match, OOLONG-style numeric tolerance). ≥ 20 tasks with a fixed train/held-out split (e.g., 70/30, seeded), difficulty tuned so the current 4B pipeline scores in the **30–70% band** (optimizer saturation trap — see diagnosis-era docs). Format: JSON per suite `{name, query, context, expected_pattern, tolerance}` + loader.
2. **Evaluator wrapper.** `run_completion(query, context) -> (score, feedback)` where score is the task metric and **feedback text is assembled from the harness's templated warnings** (parse-rescue counts, shortcut warnings, budget notices — from `templates.py` and the trajectory logger). This is the "actionable side information" GEPA consumes.
3. **GEPA runner in `optimize.py`** replacing the scaffold: `gepa.optimize_anything` (pip package `gepa`, permitted new dependency — optionally `dspy` if you prefer its lineage tooling, but not required). Configuration: student = sub-tier model endpoint, **reflection = root-tier model endpoint** (both from `rlm_local.config` tiers); `max_metric_calls` 150–300; minibatch 3–5; `use_merge=False` initially; selection on the held-out split; checkpoint/resume between runs (candidates are text — persist run state to a JSONL log).
4. **Targets:** `--target {prologue, how-to-work, nudges, fewshots, helper-docs}` mapping to vault contract/template/fewshot pages (the mapping must be complete and symmetric — the old scaffold's get/set asymmetry bug must not return).
5. **Gate-routed promotion.** Candidates never touch live pages directly: `propose → validate → held-out eval → promote`, with `optimized_by: gepa-run-<id>` in frontmatter and instant rollback via git. **The evaluator itself and the gate code are never optimizer-editable.**
6. **Few-shot bootstrap** (same milestone): replay the train split, keep trajectories that reach verified-correct answers (TrajectoryLogger JSONL), auto-select 2–3 canonical transcripts into `fewshots/` through the gate.
7. **Docs + tests:** `optimize.py` docstring updated (scaffold note removed), manual §9 rewritten to describe the real flow, README scaffold note removed. Tests: dry-run against `FakeModelServer` (no real LLM), promotion-gating test (candidate that wins on train but loses on held-out is NOT promoted), lineage test (frontmatter `optimized_by` recorded).

### Hard constraints

- Optimize against the model you deploy (the AppWorld negative-transfer caveat: proxy-optimized artifacts can lose ground on different/stronger models).
- No weight-level training, no ADAS/DGM-style self-rewriting, no new dependencies beyond `gepa`/`dspy`.
- Budget: `tiny`-profile turn/call caps during optimization runs; report expected wall-clock before starting a 300-call run (one metric call = one full `completion()`).
- TDD per spec §12; every deliverable above lands with its test first.

### Acceptance for the milestone

One real overnight run on `how-to-work` or `nudges` with: baseline vs best score on held-out, number of metric calls used, lineage log committed, and a short `docs/k4-first-run-report.md`. If held-out shows no improvement, the run is still a success operationally (pipeline proven) — report it honestly either way.

---

## Standing constraints (unchanged, restated)

Backward compatibility without a vault/bridge must remain byte-identical. The frozen contract (spec Appendix A) changes only with an explicit `contract_version` bump. No scope creep: deferred items (orientation caches, K5 remote vault, media/mounts/UI) are later phases. Every fix lands with its test first. Confidential data never leaves the machine; eval suites and corpora stay local and unencrypted-material-free.
