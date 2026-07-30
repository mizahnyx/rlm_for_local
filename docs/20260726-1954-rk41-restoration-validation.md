# Validation: R-K4-1 Restoration Commit (`4e14ce2`)

**Date:** 2026-07-26 (19:54)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `4e14ce2` ("fix: R-K4-1 — restore held-out promotion gate, add gating test") against `docs/20260726-1940-final-wiring-fix-validation.md` §3.
**Suite:** `pytest -q -k "not slow"` → **180 passed, 0 failed, 12 deselected (slow)**.

---

## 1. Verdict

**The gate restoration is correct — but the gating test added to protect it is vacuous: it passes identically with or without the held-out gate.** Proven by direct simulation through the project's own loader (§3). One small test fix is required before the acceptance run, because right now the held-out gate has *code* but no *regression protection*.

## 2. What conforms

**R-K4-1 (gate restoration): ✅** The full three-part condition is back at `optimize.py:337`:
```python
if best_score > baseline_result.score and held_out_result is not None and held_out_result.score >= baseline_result.score:
```
with the orphaned over-indentation cleaned up (the whole try block re-indented properly). Promotion now requires: train improvement AND held-out present AND held-out ≥ baseline. Verified in source.

## 3. The problem: the gating test cannot see the gate

`test_train_winner_held_out_loser_not_promoted` mocks GEPA with `best_score = 0.9` and feeds answers crafted so train tasks pass and held-out tasks fail. I simulated the exact scenario through `tests.evals.load_suite` (the real loader, real split):

```
train:     [year_1648, alice, price, email, version]   ← 5 crafted answers all match
held_out:  [color_blue, temperature]                   ← garbage answers
baseline = 1.000    best = 0.9    held_out = 0.000
```

Now evaluate both promotion conditions:

- **With the gate:** `0.9 > 1.0` is False → not promoted. Test asserts `!= "promoted"` → **passes**.
- **Without the gate:** `0.9 > 1.0` is *still* False → not promoted → the test **also passes**.

The test author's answer stream makes the baseline *perfect* (1.000), so the mocked best (0.9) never clears the **train** condition `best_score > baseline` — the promotion is blocked upstream of the held-out gate no matter what. The held-out comparison could be deleted tomorrow and this test would stay green. (The suite agrees: 180/180, including this test.)

**Why this keeps happening:** this is the third vacuous test in this project's history (the F5 timing test that never called `reindex_delta`; the inertness "contract test" that simulated rather than exercised; now this). The antidote is a **vacuity check**: when writing a guard test, temporarily remove the guard and confirm the test goes red. The gating test was never run against the guard-less code.

### Required fix (test-only, small)

Make the baseline imperfect so the train condition passes and only the held-out gate can block:

```python
train_answers = [
    "the year is 1648",   # year task: PASS
    "wrong answer",       # alice task: FAIL
    "also wrong",         # price task: FAIL
    "email is support@example.com",  # email task: PASS
    "version 3.7.2",      # version task: PASS
] + ["xyzzy_nomatch"] * 20   # held-out: all FAIL
```

Verified against the real loader: this yields `baseline = 0.600`, `best = 0.9 > 0.6` (train gate **opens**), `held_out = 0.0 < 0.6` (held-out gate **blocks**) → with the gate: not promoted; **without the gate: promoted → test fails**, which is exactly the discriminating power the test needs. Then perform the vacuity check once: comment out `held_out_result.score >= baseline_result.score`, watch it go red, restore.

## 4. State of the milestone

- R-K4-1: gate restored in code ✅; regression protection: **pending the §3 test fix**.
- The two earlier residuals stand (decorative `demote` archive — minor; warnings-only slot validation — hardening note), neither blocking.
- **K4 acceptance run: clear to schedule once the test fix lands.** llama-server up, `tiny` profile, `--target nudges`, `max_metric_calls` 150, overnight → `docs/k4-first-run-report.md` with baseline vs best on held-out, metric calls, and lineage. That is the final act of the K4 milestone.
