# Validation: Close-out Commit + K4-real (`5cdf5ab`, `63547ab`)

**Date:** 2026-07-26 (18:09)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `5cdf5ab` (close-out) and `63547ab` (K4-real) against `docs/20260726-1636-implementation-agent-instructions-closeout-and-k4.md` Parts 1–2.
**Suite:** `pytest -q -k "not slow"` → **178 passed, 0 failed, 12 deselected (slow), 1 warning** — green including the new `test_migration.py` (138 lines) and `test_optimize.py` (12 tests).

---

## 1. Verdict

**Part 1 (close-out): fully validated — all five items correct.** The conformance loop from the July 24–26 review cycle is formally closed.

**Part 2 (K4-real): substantially implemented and green, but NOT yet conformant.** The GEPA machinery is real (package integrated, evaluator with harness-warning feedback, deterministic splits, gate-routed flow, bootstrap) — however **two wiring defects make an actual optimization run semantically inert** (D-K4-1, D-K4-2), the checkpoint/resume requirement is missing, and the milestone's acceptance run was not performed. These are fix-in-place items, not redesigns.

---

## 2. Part 1 verification (all ✅)

| Item | Evidence |
|---|---|
| **W1 — schema migration** | `_MIGRATIONS` ordered list + `PRAGMA user_version` (`index.py:98-121`); crucially, `_run_migrations()` runs **on connect** inside the `conn` property (`index.py:79`), i.e. *before* `_ensure_schema`'s version stamp — old databases migrate correctly. `test_migration.py` builds an old-schema DB and verifies ALTER + version + delta-reindex. Sound |
| **W2 — F5 test made real** | Now times `idx.reindex_delta(vault)` after the 50 edits (`test_index.py:339-342`) and asserts ratio ≤ 10×. (The instruction's one-time "fails against the old quadratic code" verification can't be re-checked retroactively; commit message claims it) |
| **W3 — integration skips** | Fixture probes the endpoint and `pytest.skip`s when down (`test_integration.py:23-45`). Confirmed by this run: server down → 0 failures |
| **W4 — conformance docs** | All 9 documents copied into `docs/conformance/` + README index; in-repo references now resolve |
| **W5 — load-test report** | `docs/load-test-report.md` present with the correct table (582 s / 116.9 ms / 38 ms at 100K) and the run-2 artifact footnote |

## 3. Part 2 verification — what conforms

- **Real GEPA integration:** `from gepa.optimize_anything import GEPAConfig, EngineConfig, optimize_anything` (`optimize.py:233-234`); `gepa>=0.1.4` added to `pyproject.toml` (the permitted dependency). Seed candidate read from the vault; train and held-out evaluators constructed separately per split.
- **Feedback channel:** `evaluate_candidate` assembles feedback text from per-task harness outcomes/warnings (`optimize.py:96-126`) — the "actionable side information" the spec requires.
- **Eval suites:** 4 suites (`needle_search`, `counting`, `fact_extraction`, `multi_hop`) with deterministic 70/30 split; suite-level test enforces ≥20 tasks total and pattern presence on every task.
- **Held-out gating logic:** promotion requires train improvement **and** `held_out.score >= baseline.score` (`optimize.py:332-333`) — the correct shape; a train-winner/held-out-loser is not promoted.
- **Gate-routed flow with lineage:** candidates go through `propose → validate → promote`; `gepa-optimized` tag + `<!-- optimized_by: gepa-run-<ts> -->` body comment; `vault.git_commit` on promotion.
- **Few-shot bootstrap:** `bootstrap_fewshots` stores transcripts through the gate (`optimize.py:373-433`); `test_bootstrap_stores_through_gate` covers it.
- **Docs honesty:** "scaffold" fully removed from README and manual §9 (grep-verified).
- **Tests:** 12 optimize tests incl. offline evaluation (monkeypatched), target-map symmetry, zero-score behavior, deterministic split.

## 4. Part 2 defects (must fix before the acceptance run means anything)

### D-K4-1 — Promotion is semantically inert (major)
`optimize.py:338-353` promotes the winning candidate to a **new** page `contract/optimized-{target}.md`. The live prompt assembly still reads the *original* target page (`contract/how-to-work.md`, `contract/templates/nudge-no-block.md`). Nothing in `rlm_local` or the kernel ever reads `contract/optimized-*` — so a successful optimization changes no behavior whatsoever. The incumbent page stays active and untouched.
**Fix:** promotion must replace the incumbent: either (a) `propose` gains an explicit `target_path`, and promotion writes the candidate body into the target page (version bump, `superseded_by` link to the archived incumbent, git commit), or (b) promote-then-demote the incumbent in the same transaction. Add a contract test proving non-inertness: after a promoted optimization, the assembled system prompt differs from the pre-optimization one.

### D-K4-2 — TARGET_MAP points at wrong/placeholder pages for 2 of 5 targets (major)
- `"prologue"` → `contract/templates/metadata-header.md` — the actual prologue text lives in `rlm_local/templates.py:PROLOGUE`; no `contract/templates/prologue.md` is seeded anywhere.
- `"fewshots"` → the *same* `metadata-header.md`, explicitly labeled "stand-in."
Net effect: two targets optimize the wrong artifact, two targets share one page (so the `test_target_map_is_symmetric` test passes vacuously), and if D-K4-1 were fixed as-is, an "optimized prologue" would overwrite the *metadata header* with evolved text.
**Fix:** seed the real pages — `contract/templates/prologue.md` with the verbatim `PROLOGUE` text, `fewshots/example.md` with the current `FEWSHOT_EXAMPLE` transcript — and remap; delete the stand-in. Extend the symmetry test to assert map targets are *distinct* and that each path exists in a freshly seeded vault.

## 5. Part 2 gaps (smaller)

- **G-K4-1 — No checkpoint/resume.** Instruction Part 2 #3 required persisting run state to a JSONL log for resume between runs; grep finds no `jsonl`/`log_dir`/`resume` in `optimize.py`. An interrupted overnight run restarts from zero.
- **G-K4-2 — Acceptance run not performed.** The milestone's acceptance (one real run + `docs/k4-first-run-report.md` with baseline vs best on held-out, metric calls, lineage) is absent. Needs the llama-server up and hours of wall-clock; either schedule it or formally defer with owner sign-off.
- **G-K4-3 (cosmetic):** lineage is recorded as a body comment, while the instruction specified frontmatter `optimized_by:` — the frontmatter schema has no such field; either add the key to the schema (v1.1) or document the body-comment convention in the manual. Body comment is acceptable if documented.

## 6. Required next commit (small)

1. D-K4-1: incumbent-replacing promotion + non-inertness contract test.
2. D-K4-2: seed real prologue/fewshot pages, remap, strengthen the symmetry test (distinct + existing paths).
3. G-K4-1: JSONL run-state log + `--resume`.
4. G-K4-3: document lineage convention.
5. Then the **K4 acceptance run** (G-K4-2): llama-server up, `tiny` profile, `--target nudges` or `how-to-work`, `max_metric_calls` 150 → overnight; commit `docs/k4-first-run-report.md` with honest results either way.

**Bottom line:** Part 1 closes the conformance loop cleanly — migration, real F5, skips, docs, report all correct. Part 2 is a solid, well-tested GEPA *skeleton with real GEPA inside*, but until promotion actually reaches the incumbent page and the target map points at the real artifacts, an optimization run would be sound and fury signifying nothing. One small commit fixes the wiring; then the overnight acceptance run completes the milestone.

---

## Owner Amendment (2026-07-26, 18:31) — Tier-2 shelved; production soak adopted

**Decision (project owner):** the organic corpus proved **insufficient** in quality and quantity for a meaningful 100K-class validation. **Tier-2 organic load testing is shelved for the foreseeable future** — not deferred on schedule, but dropped from the critical path. If a suitable corpus ever emerges, the runbook (`docs/20260725-0953-load-gate-100k-runbook.md` §4) revives it unchanged; the two env-gated Tier-2 suite tests stay in place and will activate on `RLM_KERNEL_LOAD_CORPUS` being set.

**Substitute:** the kernel moves to **production soak testing** as a daily driver across the owner's many integrations. Real usage replaces the synthetic second tier. Clarified scope with the owner: this substitutes **only** for Tier-2 — the K4 overnight acceptance run (synthetic evals) is **still required** once D-K4-1/D-K4-2 land.

**Consequences for the ledger:**

- "Tier 2 — organic corpus validation" → **shelved (insufficient corpus); removed from K4/blocker paths.** The 100K load gate stands as passed on Tier-1 synthetic.
- K4 acceptance run (G-K4-2) → **unchanged, still open**; it uses `tests/evals/`, not organic data.
- Soak-test guidance for daily use: (a) watch per-`completion()` latency as the live vault grows — helper listing must stay index-backed (~ms, never seconds); (b) re-run the timing script (rebuild / search p95 / git status) as cheap regression checkpoints when the real vault crosses **10K / 50K / 100K pages**; (c) review the gate's quarantine weekly early on — daily use is the first real exercise of `propose`/promote hygiene; (d) run memory `compact(dry_run=True)` monthly and read the merge report before confirming; (e) file issues from **real failures**, not synthetic expectations — soak findings outrank suite findings.
- Risk note (accepted by owner): the 100K gate was validated on synthetic text statistics; real-vault behavior (vocabulary skew, near-duplicate backups, huge single pages) will be observed in production per (b) rather than pre-validated.
