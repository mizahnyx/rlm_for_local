# Conformity Review: `rlm_kernel` Implementation vs. Spec

**Date:** 2026-07-25
**Reviewer:** Kimi Code CLI (validation requested by user)
**Spec:** `docs/20260724-1736-rlm-kernel-evolvable-spec.md`
**Implementation:** `~/Documents/Misc/rlm_for_local/` @ commit `22c0bd8` ("feat: implement rlm-kernel")
**Method:** full read of all 11 kernel modules (~3,000 LOC) + git diff of `rlm_local` edits + full test-suite run (124 passed, 11 failed) + targeted wiring checks.

---

## 1. Verdict

**The implementation does NOT yet conform to the spec.** The K0 foundations (schema/vault/index/search/seed/CLI) are substantially conformant and the gate (K2) is functionally strong — but the K1 integration into `rlm_local` is **broken in three independent ways that each crash or neuter every `completion()` call**, K4 (`optimize.py`) is a placeholder rather than the specified GEPA runner, and the test suite has a hole exactly where it matters: **no test calls `RootLoop.run()` or `rlm_local.completion()`**, so all three crash bugs shipped undetected.

Phase-level scorecard:

| Phase | Conformance | Summary |
|---|---|---|
| **K0** — vault, index, search, seed, CLI, loaders | **~80%** | Solid core; two index bugs, dead loaders, wiring mismatches |
| **K1** — REPL assembly + prompt wiring | **Broken** | 3 crash bugs; vault prompt/template loading unwired; `search`/`propose` unreachable from REPL code |
| **K2** — the gate | **~80%** | Good state machine + validators; validation not wired into promote; no tests; one typo'd dead function |
| **K3** — memory | **~50%** | Decay/forget/compact/core-memory present; note construction is regex not LLM; orientation caches missing |
| **K4** — optimize | **~20%** | Skeleton only: no GEPA, no reflection, no feedback channel, no held-out gate, bypasses the gate |
| **Testing (§12)** | **Non-conformant** | 60 kernel unit tests for K0 only; zero integration tests through `run()`; no property tests; no load test |

---

## 2. Critical defects (must fix — each breaks `completion()`)

### D1 — `Parser` is never instantiated in `RootLoop.run()` — every run crashes
`src/rlm_local/root_loop.py:51` sets `self._parser: Parser | None = None`; the kernel edit **deleted** the `self._parser = Parser(...)` block (visible in `git diff c675b5c 22c0bd8`), but `self._parser.parse(...)` is still called at line 165. `grep -n "Parser(" src/rlm_local/root_loop.py` returns nothing. **Any call reaching the main loop raises `AttributeError: 'NoneType' object has no attribute 'parse'`.**
Fix: restore the two-line Parser instantiation after the SubcallManager block.

### D2 — `build_system_prompt` called but never imported — non-kernel runs crash
`root_loop.py:123` (else-branch): `_system = build_system_prompt(prompt_vars)`. Module imports only `build_messages` (line 17). **Any run without a kernel bridge raises `NameError`** before the first turn.
Fix: add the import — or better, remove the dead `_system` assignment entirely (see D4).

### D3 — `load_system_prompt_from_vault.__self__` — kernel runs crash
`root_loop.py:121`: `_fewshots = load_system_prompt_from_vault.__self__  # not used, fallback`. Plain functions have no `__self__` → **AttributeError on every kernel-enabled run**. This line is hallucinated dead code.
Fix: delete it (few-shot loading belongs in `build_messages`, see D4).

### D4 — Vault-first prompt loading is computed then discarded
`root_loop.py:120` computes `_system` from the vault, then line 125 calls `build_messages(query, context_len, _context_type, prompt_vars)` — which builds the system prompt internally from the hardcoded constants. `_system` is never used. **K1's "prompt assembly from pages" (spec §5.2) is therefore not in effect**; the system prompt is always the package fallback.
Fix: thread an optional `system_prompt`/`fewshots` override through `build_messages`, or assemble messages in `root_loop` when a bridge is present.

### D5 — `search`/`propose` are unreachable from REPL code
Spec §5.1: "New builtins injected alongside: `search(query, k=5)` … and `define(...)`/`propose(...)`." Implementation status:
- The **harness side** handles `search`/`propose` socket messages (`repl.py:411-431`) and the **worker main loop** has matching `elif cmd ==` branches — but the model writes code inside `exec`, not socket messages. To be callable from a cell, the worker needs injected **functions** (like `_harness_llm_query`) that send the command and wait. None exist: there is no `def search`/`def propose` in the worker script.
- The one attempt is `repl.py:236`: `exec(f"{h['name']} = _harness_search", globals()) if h["name"] == "search" else None` — references `_harness_search`, which is **undefined anywhere** (only masked by the bare `except: pass`).
- Net effect: a model calling `search("…")` in a cell gets `NameError`. The socket plumbing works but has no caller. Introspection (P6) is dead.
Fix: define `_harness_search(query, k=5, kinds=None)` and `_harness_propose(kind, name, body, rationale)` at the top of `_WORKER_SCRIPT` (mirroring `_harness_llm_query`), and inject them unconditionally into worker globals — not via helper pages.

### D6 — Pre-existing: REPL cannot start on Windows (cp1252)
`REPLSandbox.start()` does `worker_path.write_text(_WORKER_SCRIPT)` with no encoding; the script contains `──` box-drawing characters → `UnicodeEncodeError` under Windows cp1252. **All 11 `test_repl.py` failures are this.** Pre-dates the kernel, but the spec targets Windows and it now masks every other defect on this machine.
Fix: `write_text(..., encoding="utf-8")` (and audit other `write_text` calls — `vault.py` already specifies encoding).

---

## 3. Functional defects (fix before relying on the feature)

### D7 — FTS5 rows duplicated on every page update (`index.py`)
`_index_page` does `INSERT INTO fts_pages` with no prior `DELETE ... WHERE path = ?`. `reindex_delta` calls it for changed pages → **duplicate search hits accumulate on every edit**. (`build()` avoids it by clearing first; the incremental path doesn't.)
Fix: delete-by-path before insert in `_index_page`.

### D8 — Delta reindex compares the wrong hashes (`index.py:202`)
`page.content_hash` (computed property, always `sha256:…`) is compared against the `pages.hash` column (frontmatter hash, `None` until promotion). Result: `content_hash != (None or "")` is always true → **every delta reindex reindexes every page** (correctness OK, the point of delta reindexing lost).
Fix: store `content_hash` in a dedicated column at index time and compare against that.

### D9 — `links` table is never populated
Nothing extracts `[[wikilinks]]` from bodies into `links(src, dst)` — the spec's introspection story ("backlinks = senders", §4/P6) has no data. `gate._validate_definition_or_note` resolves links through the vault instead, so validation works, but backlinks don't exist.
Fix: populate during `_index_page` using the gate's `WIKILINK_PATTERN`.

### D10 — Seed ↔ template-map mismatch; template loader has no callers
- `templates.py:_TEMPLATE_PAGE_MAP` points at 16 paths; `seed.py` creates only 7 of them, and one under a **different name** (`metadata-header.md` seeded vs `metadata.md` mapped) with a **different body format** than `METADATA_TEMPLATE`. Even if loaded, 9/16 fall back silently.
- `load_template()` has **zero callers** — root loop, parser, and REPL all use the constants directly. Vault-first template loading (spec §5.2/§3.3) is dead code.
Fix: align seeded names/bodies with the map (or drop the map to the 7 real pages), and call `load_template(name, vault)` at the template-use sites (or explicitly defer vault-templates to a later phase and remove the dead code).

### D11 — `verify_quarantine_isolation` is broken (typo)
`gate.py:617` references `QUANTINE_PREFIX` (undefined) → `NameError` if called. Also its `search_vault(query="*")` would FTS-match the literal `"*"` phrase, so the check is doubly wrong. Untested, obviously.
Fix: correct the name; implement the isolation check as a path-prefix scan over index rows, not FTS.

### D12 — Promotion path skips validation (spec §7)
`gate.promote()` never calls `validate()`; `cli review` lists pages without validation reports; `cli promote` goes straight to promotion. The spec's gate is *propose → auto-validate → review-with-report → promote*. As built, a human can promote an invalid page with no friction and no report.
Fix: run `validate()` inside `promote()` (refuse on `errors`, surface `warnings`), and show reports in `rlm-kernel review`.

### D13 — K4 `optimize.py` bypasses the gate
`_set_target_text` rewrites contract pages **directly in the live namespace** during the run (spec §8 requires changes to flow through the gate, promotion only on held-out win, with `optimized_by` lineage in frontmatter). Additionally: `_get_target_text`/`_set_target_text` maps disagree (`fewshots`, `helper-docs` readable but not writable → silent no-ops); held-out evaluation is computed *after* promotion and does not gate it; no lineage is recorded.
Fix: route candidates through `propose → validate → held-out eval → promote`, and record lineage.

---

## 4. Spec deviations (design-level; decide accept-or-fix)

| # | Spec item | Implementation | Assessment |
|---|---|---|---|
| E1 | §9: note construction **via `llm_query`** (A-MEM-style summary/keywords/tags) | `memory.py` uses regex extraction (first sentence, capitalized words) | Crude but deterministic; acceptable as K3a placeholder if a follow-up adds the LLM path (the model client seam exists). Document the choice |
| E2 | §9: compaction "via sub-tier, human-reviewable diff" | Fully automatic `difflib` title-similarity merge | Risk of wrong merges on a personal wiki; add a review step or raise the threshold default |
| E3 | §9: PEEK-style orientation caches (`memory/caches/`, fixed token budget, Distill→Cartograph→Evict) | **Absent** (only the `cache` kind exists in the schema) | Missing K3 feature |
| E4 | §8: GEPA via `optimize_anything`/dspy, reflection LM, feedback = templated warnings, `max_metric_calls`, few-shot bootstrap | None of it: hand-rolled string mutations, no reflection, no feedback channel, no bootstrap | K4 is a scaffold, not the feature. Either implement for real or mark the phase "not started" |
| E5 | §12 TDD: property tests (index rebuild ≡ incremental; gate state machine), REPL contract tests, e2e propose→promote→use, load test (100K pages) | 60 kernel unit tests cover schema/vault/index/search only; no gate/memory/optimize/repl_bridge tests; **no test anywhere calls `run()`/`completion()`**; no load test | The missing integration test is why D1–D3 shipped. This is the top process fix |
| E6 | §2 deps: "PyYAML + git binary only" | Adds `ulid` package (and pydantic was already implied by §12) | Trivial; either vendor a 12-line ULID or accept the dep |
| E7 | §5.2: helper one-liners capped "~30 lines" | `load_system_prompt_from_vault` caps at 32 lines ✓ — but note summaries come from `frontmatter.summary`, not signature+summary as spec'd | Minor wording |
| E8 | Vault `list()`/`resolve_wikilink()` walk and parse every `.md` (including `.git/`) per call | O(n) full parses; `.git` not excluded | Works at K0 scale; will not at 100K pages (spec's load gate would catch this — another reason the load test matters) |

---

## 5. What conforms well (credit where due)

- **Page model & schema (§3):** kinds, statuses, frontmatter fields, ULID, hash pattern, helper extraction (Signature/Implementation), atomic writes, idempotent seeding — all as specified, with sensible extras (rationale as HTML comment).
- **Gate core (§7):** quarantine/pending semantics, kind-specific validators (AST import allowlist, blocked patterns, restricted-builtins sandbox exec, slot variables, wikilink resolution), promote with name-conflict guard + version bump + hash, demote lifecycle, `search_quarantine` + `include_quarantine` liveliness path. This is the strongest part of the implementation.
- **REPL protocol extension shape:** `init` helpers payload and backward compatibility (no definitions → old behavior) match §5.1's contract; harness-side proxy handlers are correctly placed in `execute()`'s message loop.
- **Seed content quality:** contract/how-to-work/helper pages mirror the harness prompt text faithfully, including `{slot}` variables — good source material once the wiring (D4/D10) is fixed.
- **Memory lifecycle:** decay arithmetic, forget with core-memory protection, superseded_by chains on merge.
- **K0 test coverage:** 60 focused unit tests for schema/vault/index/search.

---

## 6. Remediation plan (ordered; each step verifiable)

1. **R1 — Make the suite see reality:** add an integration test that calls `rlm_local.completion()`/`RootLoop.run()` against a stub backend (and a stub REPL if needed). This single test would have caught D1–D3. *(Do this first — it turns every following fix into a checked fix.)*
2. **R2 — Fix the crashers:** D1 (restore Parser init), D2 (import or remove dead call), D3 (delete `.__self__` line), D6 (`encoding="utf-8"` on worker write).
3. **R3 — Wire K1 for real:** D4 (thread vault-assembled prompt into `build_messages`), D5 (inject `search`/`propose` worker functions), D10 (align seed↔map, then either call `load_template` at use sites or defer-and-delete).
4. **R4 — Index correctness:** D7, D8, D9 (+ add the §12 property test "rebuild ≡ incremental", which catches D7/D8 regressions).
5. **R5 — Gate discipline:** D11, D12 (validate-in-promote + review reports) + gate property tests.
6. **R6 — Decide K3/K4 scope:** either implement E3/E4 properly (recommended: K4-lite = real GEPA call + feedback channel + gate-routed promotion) or explicitly mark them "scaffold only" in the README to avoid the appearance of conformance. Address E1/E2 with a documented decision.
7. **R7 — Load gate:** implement the 100K-page load test from §12 before claiming N3-class scale (will surface E8).

---

## 7. Appendix — evidence log

- Test run: `pytest tests/ -q` → **124 passed, 11 failed**; all failures `UnicodeEncodeError: 'charmap' codec` in `test_repl.py` (D6).
- `grep -n "Parser(" src/rlm_local/root_loop.py` → no instantiation (D1).
- `grep -n "_harness_search\|def search\|def propose" src/rlm_local/repl.py` → only the dangling `exec` line (D5).
- `grep -rn "load_template\|load_system_prompt_from_vault\|load_fewshots_from_vault" src/` → definitions plus the single dead call in `root_loop.py` (D4/D10).
- `git diff c675b5c 22c0bd8 -- src/rlm_local/` → Parser block deleted; `_system`/`__self__` lines added (D1–D4).
- Kernel modules read in full: `schema.py 236`, `vault.py 197`, `index.py 213`, `search.py 134`, `seed.py 458`, `gate.py 632`, `memory.py 514`, `optimize.py 280`, `repl_bridge.py 140`, `cli.py 184`.
