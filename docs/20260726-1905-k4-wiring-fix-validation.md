# Validation: K4 Wiring-Fix Commit (`cc2d9d2`)

**Date:** 2026-07-26 (19:05)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `cc2d9d2` ("fix: D-K4-1/D-K4-2/G-K4-1/G-K4-3 — incumbent promotion, real seed pages, run logging, lineage docs") against `docs/20260726-1809-closeout-k4-validation.md` §6 (items 1–4) + the owner's Tier-2 amendment (18:31).
**Suite:** `pytest -q -k "not slow"` → **179 passed, 0 failed, 12 deselected (slow)**.

---

## 1. Verdict

**D-K4-2 (target map): fully fixed. D-K4-1 (promotion): the inertness is cured but the fix bypasses the gate and its test is a simulation, not a contract test. G-K4-1 (resume): parameter present, implementation absent. G-K4-3 (lineage docs): done. One housekeeping item missed (Tier-2 status in the repo load report). The K4 acceptance run remains pending — correctly, it is the next step after one more small commit.**

## 2. What conforms

- **D-K4-2 ✅** — `TARGET_MAP` now points at real artifacts: `contract/templates/prologue.md` (seeded with the **verbatim** `PROLOGUE` text) and `fewshots/example.md` (seeded fewshot transcript). The "stand-in" sharing of `metadata-header.md` is gone. The strengthened test (`test_target_map_is_symmetric_and_distinct`) asserts paths are distinct, exist in a freshly seeded vault, and are non-empty — exactly the regression guard this defect needed.
- **Promotion non-inertness (mechanics) ✅** — the winner now replaces the incumbent page **at the same path** (`optimize.py:342-360`): body = optimized text + lineage comment, version bumped, `gepa-optimized` tag, `vault.put` + git commit. Prompt assembly reads that path, so a successful optimization now changes behavior. The original defect is cured.
- **G-K4-3 ✅** — the lineage convention (`<!-- optimized_by: gepa-run-<id> -->` body comment + tag + version bump + git rollback) is documented in the `optimize.py` module docstring.
- Suite grew by the new inertness test class and stays green.

## 3. Defects in the fix

### D-K4-1a — Promotion bypasses the gate (major, must fix)
The new flow writes the candidate **directly** to the incumbent page — `propose()`, `validate()`, and `promote()` are all gone from the path. Two concrete harms:
1. **The slot-validation guard is lost.** The previous flow ran `validate()` on candidates, which checks that contract/template pages retain their `{slot}` variables. If GEPA's evolved `how-to-work` drops `{max_turns}` or `{repl_cap}`, the next `format(**prompt_vars)` raises `KeyError` **at runtime, inside every completion** — and this path installs it unreviewed. The gate existed precisely for this (P8: all growth passes through an evaluator gate; the optimizer must never edit around it).
2. **No structured archive of the incumbent.** The old body survives only in git history; the spec's option (a) required a `superseded_by` link to an archived predecessor page.
**Fix:** keep the incumbent-replacement semantics, but route through the gate: `propose` (with an added `target_path` parameter) → `validate` (must pass: slots present, length cap) → `promote` writes into `target_path` with version bump + `superseded_by` → git commit. Slot validation failure must abort the promotion and keep the incumbent.

### D-K4-1b — The inertness "contract test" is a simulation (major, must fix)
`TestPromotionInertness::test_promotion_updates_incumbent_page` manually re-implements the promotion steps (set body, bump version, `vault.put`) instead of invoking the real promotion code. It tests that `vault.put` round-trips — something already covered elsewhere. **If the real promotion logic regresses (e.g., back to creating `optimized-*` pages), this test stays green.** A contract test must exercise the real path.
**Fix:** test `run_optimization` end-to-end with `gepa.optimize_anything` monkeypatched (returning a candidate that beats baseline) and the evaluator driven by `FakeModelServer`; then assert (a) the incumbent page body changed, (b) version bumped, (c) `optimized_by` lineage present, and — the actual contract — (d) the system prompt assembled after promotion differs from the pre-run one.

### G-K4-1 — `resume` is accepted but unimplemented (partial)
`run_optimization(..., resume: bool = False)` documents "attempt to resume from last checkpoint," but nothing reads prior state: the JSONL entry is written **after** the run (a summary, not a checkpoint), and no code path consumes it. An interrupted overnight run still restarts from zero.
**Fix:** either implement resume for real (persist per-candidate state as it completes; on `resume=True`, load the newest log for the target and skip completed evaluations — or delegate to GEPA's own `log_dir` if the package supports it), or delete the parameter and document resume as future work. An accepted-but-inert flag is the worst of the three options.

### Housekeeping missed — Tier-2 status in repo load report
`docs/load-test-report.md` §"Tier 2 (Organic Corpus)" still says **"Pending."** The owner's amendment (18:31) shelved Tier-2 (insufficient corpus). Update to "Shelved (insufficient corpus); revivable via runbook §4 if a suitable corpus emerges."

## 4. Still pending (correctly)

- **K4 acceptance run** (`docs/k4-first-run-report.md`): requires the two D-K4-1 fixes above first, then llama-server up, `tiny` profile, `--target nudges` (cheapest), `max_metric_calls` 150, overnight. This remains the milestone's closing act.
- Tier-2 soak posture per the amendment (daily-driver usage; regression checkpoints at real-vault 10K/50K/100K pages) — owner-side, no code action.

## 5. Required next commit (small, final wiring)

1. D-K4-1a: gate-routed incumbent replacement with slot validation (abort-on-fail) + `superseded_by` archive.
2. D-K4-1b: real-path contract test via mocked GEPA + FakeModelServer, asserting prompt-level non-inertness.
3. G-K4-1: implement or remove `resume`.
4. Housekeeping: Tier-2 "Shelved" in `docs/load-test-report.md`.
Then schedule the acceptance run. Nothing else stands between here and a completed K4 milestone.
