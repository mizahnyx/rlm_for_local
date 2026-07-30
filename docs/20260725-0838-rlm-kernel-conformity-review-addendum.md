# Addendum to the rlm-kernel Conformity Review — Decisions and Remediation Directives

**Date:** 2026-07-25 (08:38)
**Addendum to:** `docs/20260725-0816-rlm-kernel-conformity-review.md` (the "Review")
**Audience:** the implementation agent working on `~/Documents/Misc/rlm_for_local/`
**Authority:** decisions D-1…D-4 were taken by the project owner on 2026-07-25; D-5…D-6 are engineering calls ratified by the owner.

This document answers the implementation agent's open questions (E1–E4, load test, E6) and defines the ordered remediation plan with acceptance criteria. Work from this document together with the Review; where they disagree, this addendum wins.

---

## 1. Decision register

| # | Question | Decision | Rationale |
|---|---|---|---|
| **D-1** | E1 — note construction | **LLM path + regex fallback.** Implement `llm_query`-based A-MEM note construction (summary/keywords/tags via the sub-tier) as the primary path in `memory.py`; keep the current regex extraction as the deterministic fallback when no model backend is configured or reachable. | Matches the spec (§9) and the harness's degrade-gracefully philosophy; keeps tests deterministic (regex path) while the real path is exercised behind `FakeModelServer`. |
| **D-2** | E2 — compaction | **Dry-run + confirm, threshold 0.85.** `compact()` defaults to producing a merge *report* only (grouped candidates, no writes); a `--confirm` flag (or interactive prompt in `rlm-kernel compact`) performs the merges. Default `similarity_threshold` raised 0.75 → 0.85. | Protects a personal wiki from silent wrong merges; keeps the anti-rot mechanism usable unattended only when explicitly requested. |
| **D-3** | E3 — PEEK orientation caches | **Deferred to phase K3b.** Do not implement `memory/caches/` now. The `cache` kind stays in the schema so pages can be introduced later without migration. | Least-proven component at small scale (PEEK is frontier-model-validated only); higher-value work (R1–R5, K4-real) comes first. |
| **D-4** | E4 — K4 optimization | **Mark as scaffold now; real GEPA as its own milestone after R1–R5.** `optimize.py` gets a prominent docstring/README note: "scaffold, not implemented." The real implementation (below, §3) starts only after the R1–R5 gate is green. | GEPA's evaluator needs a working `completion()`; it is currently crashing (Review D1–D3). Sequencing is forced, not just preferred. |
| **D-5** | Load test | **Agent writes the synthetic generator.** Deterministic generator (seeded RNG) producing realistic page distributions (kinds, body sizes 200–8K chars, tags, wikilinks) at 100K pages, in `tests/load/gen_corpus.py`, output outside the repo. **Gate machine: the Windows dev box** (the kernel vault lives where `rlm_local` runs). Targets from spec §12: full reindex < 2 h, FTS search p95 < 300 ms, `git status` < 2 s at 100K pages. Re-verify on the Arch HP laptop when K5 (remote vault) arrives — not now. | The kernel phase is single-box; the HP laptop belongs to the later LAN-service phase. |
| **D-6** | ULID dependency | **Vendor it.** Replace `python-ulid` with a small stdlib-only implementation (48-bit timestamp ms + 80-bit randomness, Crockford base32, ~15 lines in `schema.py` or `rlm_kernel/_ulid.py`) and drop the dependency. Restores the spec's dependency budget ("PyYAML + git binary only"). | Trivial cost, keeps the dependency surface minimal. |

### Amendment (2026-07-25, same day): D-5 gains a second tier

The owner offered an **organic data corpus** for load testing. Accepted as a *complement*, structured as two tiers:

- **Tier 1 (the gate, unchanged):** the deterministic synthetic generator (`tests/load/gen_corpus.py`) remains the acceptance gate — reproducible on any machine, safe for CI-style reruns, no privacy entanglement. D-5 targets apply to Tier 1.
- **Tier 2 (validation pass):** an *optional* organic-corpus run that confirms Tier-1 numbers hold on realistic text statistics (vocabulary distribution, document-length spread, wikilink density, near-duplicate content). Rules: the corpus lives **outside the repo**, is referenced by a local path/env var (`RLM_KERNEL_LOAD_CORPUS`), is **never committed or copied into the vault's git history** (it may contain confidential material — same rules as production data), and the test skips cleanly when the env var is unset. If the organic corpus has fewer than 100K pages, the generator may *amplify* it (sample + mutate with seeded RNG) to reach scale while preserving its statistics.
- If Tier-1 and Tier-2 numbers diverge materially (>2×), that is itself a finding — report both in the load-test report and note which distribution the divergence comes from.

---

## 2. Remediation directives (ordered)

Execute in this order. Do not start K4-real (§3) until step R6 reports green.

### R1 — Integration test that sees reality (Review §6.1) — FIRST
- Add `tests/test_root_loop_integration.py`: calls `rlm_local.completion()` (and/or `RootLoop.run()`) end-to-end against a stub `ModelBackend` returning scripted root-model responses, with the REPL running for real.
- Must cover at minimum: (a) a full completion without kernel bridge; (b) a completion with a `KernelBridge` backed by a tmp seeded vault; (c) a turn-0 probe → code → answer-dict flow; (d) a forced-finalization path.
- **Acceptance:** the test fails on the current commit (it will — Review D1–D3) and passes after R2.

### R2 — Crashers (Review D1, D2, D3, D6)
- D1: restore `self._parser = Parser(...)` in `RootLoop.run()`.
- D2: fix the missing `build_system_prompt` import — better: remove the dead `_system` assignment entirely and implement D4 properly in R3.
- D3: delete the `load_system_prompt_from_vault.__self__` line.
- D6: `write_text(..., encoding="utf-8")` for the REPL worker script; audit all `write_text` calls repo-wide for explicit encoding.
- **Acceptance:** full suite green on this Windows machine (124+11 passing, 0 failures); R1 test green.

### R3 — Wire K1 for real (Review D4, D5, D10)
- D4: thread the vault-assembled system prompt (and few-shots, when present) through `build_messages` or assemble messages in `root_loop` when a bridge exists. Fallback parity must be byte-identical without a vault (contract test).
- D5: inject `_harness_search(query, k=5, kinds=None)` and `_harness_propose(kind, name, body, rationale)` as worker globals in `_WORKER_SCRIPT` (mirroring `_harness_llm_query`), remove the dangling `_harness_search` exec hack, and add a contract test: model code calling `search("…")` and `propose(...)` in a cell round-trips through the socket to the bridge and back.
- D10: align `seed.py` page paths/bodies with `templates._TEMPLATE_PAGE_MAP` (or trim the map to reality); either call `load_template(name, vault)` at the template use sites in `root_loop`/`parser`/`repl`, or explicitly defer vault-templates and delete the dead loader. One of the two — no dead code either way. Note the seeded `metadata-header` body must equal `METADATA_TEMPLATE`'s semantics if kept.
- **Acceptance:** new contract tests green; helper added/edited in vault changes next-run behavior (spec §13 K1 criterion).

### R4 — Index correctness (Review D7, D8, D9)
- D7: `DELETE FROM fts_pages WHERE path = ?` before insert in `_index_page`.
- D8: store `content_hash` in its own column at index time; compare against it in `reindex_delta`.
- D9: populate `links(src, dst)` from `[[wikilinks]]` during `_index_page`.
- **Acceptance:** the §12 property test exists and passes — for any fixture vault with random edits: `rebuild ≡ delta-reindex` (same rows in `pages`, `fts_pages`, `links`, `tags`); no duplicate FTS rows after 10 sequential edits to one page.

### R5 — Gate discipline (Review D11, D12) + K3 decisions (D-1, D-2)
- D11: fix `QUANTINE_PREFIX` typo; implement the isolation check as a path-prefix scan over index rows.
- D12: `promote()` runs `validate()` first (refuses on errors, surfaces warnings); `rlm-kernel review` shows validation reports.
- D-1: LLM note construction via a pluggable model-callable (`MemoryManager(add|note)(..., llm=callable|None)`); regex path when `llm is None`. Keywords/tags from the LLM output; regex extraction only as fallback. Tests: deterministic on the regex path + `FakeModelServer` for the LLM path.
- D-2: `compact(dry_run=True)` default with merge report; `similarity_threshold` default 0.85; CLI `rlm-kernel compact [--confirm]`.
- **Acceptance:** gate property test (all proposal paths end promoted/rejected/quarantined; nothing callable-while-pending outside sandbox); memory tests for both construction paths and dry-run compaction; no behavior change to core-memory protections.

### R6 — Green gate + README honesty (D-4)
- Full suite green (unit, contract, integration, property).
- `optimize.py` docstring + README section: "K4 optimization is a scaffold — not implemented. See addendum §3." No mutation writes to live contract pages in the scaffold path (remove or quarantine that behavior now — Review D13 applies even to the scaffold).
- **Acceptance:** CI-style run of the whole suite passes; README updated.

### R7 — Load gate (D-5, Review E8)
- Implement `tests/load/gen_corpus.py` (deterministic synthetic generator) and `tests/load/test_load.py` (marked `slow`, excluded from the default run). Include the Tier-2 organic-corpus mode per the §1 amendment (`RLM_KERNEL_LOAD_CORPUS`, skip-if-unset, never committed).
- Fix what the load test exposes in E8 before reporting numbers (expected: `vault.list()`/`resolve_wikilink()` need index-backed queries instead of full-tree parses; `.git/` must be excluded from walks).
- **Acceptance:** report committed to `docs/` with measured numbers vs the D-5 targets on the Windows dev box; no regressions in the fast suite.

---

## 3. K4-real — the follow-on milestone (only after R1–R6 green)

Scope (from Review E4 + spec §8, unchanged): `rlm-kernel optimize --target …` wraps `rlm_local.completion` as evaluator via `gepa.optimize_anything` (or a thin `dspy.Module`); student = sub-tier, reflection = root-tier model; metric = verifiable tasks (needle regex, numeric tolerance) living in `tests/evals/` with train/held-out splits and 30–70% success band; **feedback channel** = the harness's templated warnings returned alongside the score; budget `max_metric_calls` 150–300 with `tiny`-profile caps during runs; candidates flow **through the gate** (`propose → validate → held-out eval → promote`) with `optimized_by: gepa-run-<id>` lineage in frontmatter; rollback via git. Few-shot bootstrap (replay trainset, keep verified-correct trajectories, select 2–3 canonical transcripts) ships in the same milestone. Out of scope: ADAS/DGM-style self-rewriting, weight-level training.

---

## 4. Standing constraints for the implementation agent

- **No scope creep.** Deferred items (D-3 caches, K5 remote vault, media/mounts/UI from the north-star spec) enter only as new phases; the frozen contract (spec Appendix A) changes only with an explicit `contract_version` bump and a note in this repo's docs.
- **No new dependencies** beyond the current set minus `python-ulid` (D-6). `gepa` (+ optionally `dspy`) is permitted only when K4-real starts.
- **Backward compatibility:** without a vault/bridge, `rlm_local` behavior must remain byte-identical (fallback parity contract test guards this).
- **Every fix lands with its test first** (TDD per spec §12). A fix without a failing-then-passing test is not done.
- The Review's defect table (D1–D13) and deviation table (E1–E8) remain the source of truth for what to fix; this addendum resolves the open scope questions those tables raised.
