# Remediation Validation: rlm-kernel after Fix Pass

**Date:** 2026-07-25 (09:28)
**Reviewer:** Kimi Code CLI (validation requested by user)
**Validated against:** `docs/20260725-0816-rlm-kernel-conformity-review.md` (Review) + `docs/20260725-0838-rlm-kernel-conformity-review-addendum.md` (Addendum)
**Implementation:** `~/Documents/Misc/rlm_for_local/` @ commit `aa343ea` ("fix: remediate conformity review — all 13 defects + 8 spec deviations"; 20 files, +1,583/−125)
**Method:** acceptance-criteria checklist per Addendum R1–R7, targeted code verification, full suite run.

---

## 1. Verdict

**The remediation substantially conforms.** All R1–R6 directives are implemented and the full test suite is green: **172 passed, 2 skipped, 0 failed** (previously 124 passed / 11 failed). Every critical crash bug (Review D1–D6) is verifiably fixed, and the four owner decisions (Addendum D-1…D-4) are enacted as specified.

**Two items remain open** — both in R7's scope: (G3) no measured 100K-page load report exists yet, and (G4) the E8 performance hazard (`vault.list()` full-tree parses per call) was only partially addressed. Two documentation gaps (G1, G2) weaken the K4 honesty requirement. None of these reintroduce crashes; all are follow-up sized.

Per-directive scorecard:

| Directive | Status | Evidence |
|---|---|---|
| **R1** — integration test | ✅ Done | `tests/test_root_loop_integration.py` (199 lines): all 4 required cases — `test_basic_completion_without_kernel`, `test_completion_with_kernel_bridge`, `test_turn_zero_probe_then_answer`, `test_forced_finalization_path` (+2 extra) |
| **R2** — crash fixes | ✅ Done | D1: `Parser` restored (`root_loop.py:102`). D2/D3: dead `_system`/`__self__` code gone. D6: `write_text(..., encoding="utf-8")` (`repl.py:321`) — the 11 prior REPL failures now pass |
| **R3** — K1 wiring | ✅ Done | D4: `build_messages(..., system_prompt=, fewshots=)` threads vault text, fallback byte-identical (`prompts.py:115-116`). D5: `_harness_search`/`_harness_propose` defined and injected as `search`/`propose` worker globals (`repl.py:115-129`). D10: dead `load_template`/`_TEMPLATE_PAGE_MAP` deleted (the "defer-and-delete" option) |
| **R4** — index correctness | ✅ Done | D7: delete-before-insert (`index.py:148`). D8: `content_hash` stored as `idx_hash` column, correctly compared (`index.py:126,232`). D9: `links` populated from `[[wikilinks]]` (`index.py:137-138`). Property test `test_rebuild_equals_delta_reindex` present |
| **R5** — gate + K3 decisions | ✅ Done | D11: typo fixed. D12: `promote()` runs `validate()` (`gate.py:474`). D-1: `_extract_via_llm` + `llm=` callable with regex fallback (`memory.py:136-241`). D-2: `dry_run=True` default, threshold 0.85 (`memory.py:470-471`) |
| **R6** — green gate + honesty | ⚠️ Mostly | Suite green ✅; `optimize.py` docstring declares scaffold and `_set_target_text` is now a no-op with the D13 comment (`optimize.py:239-241`) ✅; **but the README note is missing (G1) and the kernel manual presents the scaffold as functional (G2)** |
| **R7** — load gate | ⚠️ Partial | Tier-1 generator + slow-marked tests + Tier-2 env-var mode with clean skip and never-commit rules ✅; **but Tier-1 runs at 10K pages only, no 100K report committed (G3); E8 unaddressed beyond `.git` exclusion (G4)** |
| **D-6** — ULID vendored | ✅ Done | `src/rlm_kernel/_ulid.py` exists; `schema.py` imports it; `pyproject.toml` no longer lists `ulid` |

Suite evidence: `pytest tests/ -q` → `172 passed, 2 skipped, 5 warnings in 264.05s`. The 2 skips are the Tier-2 organic tests (`RLM_KERNEL_LOAD_CORPUS` unset) — expected behavior.

---

## 2. Remaining gaps (follow-up sized)

### G1 — README lacks the K4 scaffold note (Addendum R6)
R6 required the scaffold disclosure in both `optimize.py` (done) and the README (absent: `grep -i "scaffold\|K4\|not.*implemented" README.md` → nothing). A reader of the project front page has no way to know K4 is not implemented.
**Fix:** 3-line README subsection under the kernel/features area: "K4 offline optimization (GEPA) is a scaffold — not yet implemented; see addendum §3."

### G2 — Kernel manual §9 presents the scaffold as the real optimizer
`docs/rlm-kernel-manual.md` §9 ("Offline Optimization") describes the current mutation loop as "GEPA-style text evolution" and claims (step 5) "If improvement on the held-out split, promotes the winner" — which the scaffold does **not** do (it restores/promotes on train score; held-out is reported after the fact). This recreates the conformance-theater problem the Review flagged, in documentation form.
**Fix:** add a prominent "Scaffold — not yet implemented" banner to manual §9 and correct the held-out description (or mark §9 as "target design, see addendum §3").

### G3 — No 100K load-gate report (Addendum R7/D-5)
Tier-1 runs at 10,000 pages as a CI smoke test (good design — `generate_corpus(output, num_pages, seed)` takes any N), but the D-5 acceptance targets (reindex < 2 h, search p95 < 300 ms, `git status` < 2 s **at 100K pages**) have no measured numbers and no committed report. The 100K gate remains undemonstrated.
**Fix:** run Tier 1 at 100K on the Windows dev box (one-off, slow), run Tier 2 when the owner points `RLM_KERNEL_LOAD_CORPUS` at the organic corpus, commit the report (numbers vs targets) to the repo's `docs/`.

### G4 — E8 only partially fixed: `vault.list()` / `resolve_wikilink()` still full-tree-parse every call
`.git` exclusion was added (`vault.py:103,128`), but both methods still `rglob` **and parse every markdown file on every call**. The hazard is not the indexer (one pass per rebuild) but `KernelBridge.get_helper_definitions()`, which calls `vault.list(kind="helper")` **once per `completion()`** — at 100K pages, every completion would parse 100K files. The Addendum (R7: "expected: index-backed queries") anticipated exactly this; the 100K report (G3) would quantify it.
**Fix:** route kind-filtered listing through the SQLite index (it already has `kind`, `path`, `status` columns): bridge asks the index for active helper paths, then `vault.get(path)` only those. Keep full-walk `list()` as the rebuild path. Add a `list()`-per-call timing assertion to the 100K load test.

### G5 (minor) — Seeded `contract/templates/*.md` pages are orphaned content
R3-D10 legitimately chose "defer vault-templates and delete the dead loader." But `seed.py` still seeds 7 template pages that no behavior path reads. As *searchable introspection content* they're philosophically fine (P6 — the model can read its conventions), but editing them changes nothing, which contradicts P4 for templates.
**Fix (cheap):** either document in the manual that template pages are currently introspection-only ("behavior wiring deferred"), or stop seeding them until vault-templates are wired.

---

## 3. Observations (no action needed)

- `optimize.py` still *calls* the now-no-op `_set_target_text` (lines 180/191/194) — harmless; can be cleaned when K4-real lands.
- `test_index.py` grew +146 lines with real delta-reindex coverage (sequential edits, delete propagation) — matches R4 acceptance.
- `test_gate.py` (274 lines) covers the gate state machine incl. validation-in-promote; `test_memory.py` (173 lines) covers both note-construction paths and dry-run compaction — matches R5/D-1/D-2 acceptance.
- `RootLoop` keeps core-memory injection and kernel-bridge passing; non-bridge fallback parity preserved.
- The R1 integration test uses scripted stub backends with a real REPL — exactly the "see reality" test the Review demanded; it is what guarantees D1–D3 cannot silently regress.

---

## 4. Recommended follow-up order

1. **F1** (docs, 15 min): G1 + G2 — scaffold honesty in README and manual §9.
2. **F2** (code, small): G4 — index-backed kind listing for the bridge path (+ load-test assertion).
3. **F3** (ops, one slow run + report): G3 — 100K Tier-1 run, then Tier-2 when the owner provides `RLM_KERNEL_LOAD_CORPUS`; commit the report.
4. **F4** (docs, 10 min): G5 — annotate template pages as introspection-only.
5. Then the Addendum §3 **K4-real milestone** may start (its precondition "R1–R6 green" is met once F1 lands).

**Bottom line:** the remediation pass is genuine and thorough — the suite is green with the right tests in the right places, and all four owner decisions are enacted. What remains is documentation honesty (F1), one performance seam (F2), and the not-yet-run 100K gate (F3). None of it blocks daily use of the kernel; F1–F4 should be a short follow-up commit, after which K4-real is cleared to start.
