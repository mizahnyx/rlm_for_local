# Validation: Final Wiring-Fix Commit (`0b40ff6`)

**Date:** 2026-07-26 (19:40)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `0b40ff6` ("fix: D-K4-1a/D-K4-1b/G-K4-1 — gate-routed promotion, real contract test, resume removed") against `docs/20260726-1905-k4-wiring-fix-validation.md` §5 + the owner's Tier-2 amendment.
**Suite:** `pytest -q -k "not slow"` → **179 passed, 0 failed, 12 deselected (slow)**.

---

## 1. Verdict

**Four of five items land correctly — but the commit introduces one regression that must be fixed before the acceptance run: the held-out promotion gate was deleted.** Additionally, the held-out gating *test* that the Part-2 instructions required was never written, which is exactly why the deletion went undetected by the suite.

## 2. Item verification

### ✅ D-K4-1a — gate-routed promotion (structure)
The flow is now `demote` incumbent → `propose` candidate into quarantine → `validate` (must pass) → lineage → `promote(..., target_path=target_path)` → git commit (`optimize.py:333-372`). `gate.promote` gained the `target_path` override with validation-in-promote retained and the name-conflict guard correctly scoped to the default path. The direct-write bypass is gone.

### ✅ D-K4-1b — real-path contract test
`TestPromotionContract::test_run_optimization_promotes_through_gate` calls the real `run_optimization` with `gepa.optimize_anything` monkeypatched to a winner and `rlm_local.completion` faked, then asserts: status `promoted`, incumbent body changed, version bump, `optimized_by` lineage + tag, and (e) `_get_target_text` returns the new text — prompt-level non-inertness at the source of truth. This is the test that would have caught D-K4-1. Good.

### ✅ G-K4-1 — resume honestly removed
The inert `resume` flag is gone; the docstring states plainly that interrupted runs restart from zero (future work). The post-run JSONL summary log stays. The "remove rather than pretend" option — correct call.

### ✅ Housekeeping — Tier-2 status
`docs/load-test-report.md` now says Tier-2 is shelved and records the production-soak posture with 10K/50K/100K regression checkpoints. Matches the owner amendment.

### ⚠️ D-K4-1a residue — incumbent archive is decorative (minor)
`demote(vault, incumbent, superseded_by=target_path)` writes the deprecated incumbent *at the same path*, and `promote` then overwrites that same path — so the demote is a no-op in the final tree (the old body survives only in git history) and the `superseded_by` pointer is self-referential. Rollback via git works, so this is not dangerous; but if you want the spec's superseded-by chain to mean something, archive the incumbent to a distinct path (e.g. `contract/how-to-work.v{N}.md`) before promoting, or drop the decorative demote and document git as the archive.

## 3. The regression (must fix before the acceptance run)

### R-K4-1 — the held-out promotion gate was deleted in this commit
Diff shows the line `if held_out_result.score >= baseline_result.score:` was **removed**; the promotion condition is now merely `best_score > baseline_result.score and held_out_result is not None` (`optimize.py:334-337`, with the orphaned over-indentation as the tell). Consequence: **any candidate that improves on the train split is promoted regardless of held-out performance** — the overfitting guard that the spec (§8), the Part-2 instructions, and my last two validations all required. The held-out evaluation is still *computed* (and logged), it just no longer *gates*.

**Why the suite didn't catch it:** the required promotion-gating test — *"candidate that wins on train but loses on held-out is NOT promoted"* (Part-2 instructions, deliverable 7) — was never implemented; `test_optimize.py` has no such case (verified: 13 tests, none covering it). The new contract test's mocked scores satisfy either condition, so it passes with or without the gate.

**Fix (one line + one test):**
1. Restore the condition: promotion iff `best_score > baseline_result.score` **and** `held_out_result is not None` **and** `held_out_result.score >= baseline_result.score`.
2. Add the missing test: mock GEPA to return a train-winner, mock the evaluator so held-out scores below baseline, assert `status != "promoted"` and the incumbent body unchanged. This test fails on the current commit and passes after (1).

### Residual hardening note (non-blocking, file for later)
`validate()` on contract/template pages treats **missing** slots as warnings, not errors, and doesn't check whether the body's `{slots}` are a subset of the known `prompt_vars`. An evolved template that invents a `{new_slot}` would promote and then crash `format(**prompt_vars)` at runtime. In practice the evaluator self-selects such candidates out (a crashing candidate scores 0), so the hazard is mostly mitigated by the pipeline — but a strict slot-subset check for contract/template kinds is the correct belt-and-braces addition someday.

## 4. State of the milestone

- Wiring defects D-K4-1/D-K4-2: **fixed** (with R-K4-1 as the one regression introduced along the way).
- K4 acceptance run: still pending — **do not schedule it until R-K4-1 is restored**; an acceptance run without the held-out gate can promote overfit text into the live contract pages and would invalidate the point of the exercise.
- After the one-line restoration + gating test: llama-server up, `tiny` profile, `--target nudges`, `max_metric_calls` 150, overnight → `docs/k4-first-run-report.md`. That closes the K4 milestone and, with it, the entire July arc: harness → kernel → load gate → optimizer.
