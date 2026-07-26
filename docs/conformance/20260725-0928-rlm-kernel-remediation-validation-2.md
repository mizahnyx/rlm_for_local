# Remediation Validation #2: Follow-up Commit `ccea986`

**Date:** 2026-07-25 (09:28 → validated same day)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `ccea986` ("fix: G1-G5 documentation honesty + index-backed listing (G4)") against the follow-up list in `docs/20260725-0928-rlm-kernel-remediation-validation.md` (F1–F4) and the open G-items G1–G5.
**Suite:** `pytest tests/ -q` → **172 passed, 2 skipped, 0 failed** (unchanged; the 2 skips remain the Tier-2 load tests awaiting `RLM_KERNEL_LOAD_CORPUS`).

---

## Verdict

**F1, F2, F4 done; F3 (the 100K load gate) remains open; F2 has one residual slow path; one new minor issue (dangling doc references).** Nothing here blocks daily kernel use or the start of K4 design work, but F3 is still the formal precondition the addendum set for declaring the load gate passed.

## Per-item verification

### F1 — Documentation honesty (G1, G2): ✅
- `README.md` gained a proper "rlm-kernel (Evolvable Layer)" section with the bold disclosure: **"K4 offline optimization (GEPA) is a scaffold — not yet implemented."**
- `docs/rlm-kernel-manual.md` §9 is now titled **"Offline Optimization — Scaffold, Not Yet Implemented"** with a STATUS blockquote stating the current code is a placeholder, that no live contract pages are modified, and that the sections describe the *target design*. The false held-out-gating claim is gone.

### F2 — Index-backed listing (G4): ✅ core fix, ⚠️ one residual
- New `Index.list_paths(kind, status)` (SQL, O(1)) and `KernelBridge.get_helper_definitions()` fast path with full-walk fallback — the per-`completion()` hot path no longer parses the vault. Correct fallback ordering (index → walk → empty).
- **Residual (G4b):** `src/rlm_local/prompts.py:178` — `load_system_prompt_from_vault()` still calls `vault.list(kind="helper")` (full-tree parse) **on every kernel-enabled completion**, so the per-run cost at 100K pages is reduced but not eliminated. Same for `repl_bridge.get_helper_summaries()` (line 70), which appears to have no callers — dead-ish code worth either converting or removing.
- **Fix:** give `load_system_prompt_from_vault` access to the same `list_paths` fast path (the bridge already carries `index_path`; pass it or an Index handle through), and convert or delete `get_helper_summaries`.

### F3 — 100K load gate (G3): ❌ still open
- No load-test changes in this commit; Tier-1 remains a 10K-page CI smoke; **no 100K run, no committed report** with numbers vs the D-5 targets (reindex < 2 h, search p95 < 300 ms, `git status` < 2 s), and no per-call `list()` timing assertion in the load test.
- Tier-2 (organic corpus) is wired correctly and awaits the owner setting `RLM_KERNEL_LOAD_CORPUS` — that part of the ask is on the owner, not the agent.
- **Fix:** one-off 100K Tier-1 run on the Windows dev box (include a per-completion helper-listing timing to quantify G4b), commit `docs/load-test-report.md` with the table of targets vs measured; then Tier-2 once the corpus path is provided.

### F4 — Template pages annotation (G5): ✅
- Manual now describes `contract/templates/*.md` as **introspection-only content** (P6) with behavior wiring explicitly deferred, in both the `template` kind section and §10.2; the removed `load_template`/`_TEMPLATE_PAGE_MAP` is documented as a deliberate R3-D10 decision. Honest and consistent.

## New minor issue

### N1 — Dangling doc references
`README.md:131` and `rlm-kernel-manual.md:970` point readers at `docs/20260725-0838-rlm-kernel-conformity-review-addendum.md` — that file lives in the **aisearches02** repository (the design-docs workspace), not in `rlm_for_local/docs/`. In-repo readers will 404.
**Fix:** copy the review + addendum + validation docs into `rlm_for_local/docs/` (they are the project's own conformance history), or rewrite the references to name the external location explicitly.

## Standing state after this commit

- **Done and verified:** Review D1–D13 remediated; Addendum D-1…D-4 enacted; D-5 infrastructure (both tiers) in place; D-6 vendored; G1, G2, G4 (core), G5 closed; suite green throughout.
- **Open:** F3/G3 (100K report — one slow run + commit), G4b (prompt-assembly slow path + dead-ish `get_helper_summaries`), N1 (doc references).
- **Recommended order for the next (small) commit:** N1 → G4b → F3. After F3 lands with acceptable numbers, the K4-real milestone (Addendum §3) is fully unblocked.
