# Remediation Plan — rlm_for_local Detected Problems

**Date:** 2026-09-03 21:07
**Basis:** `20260903-2107-rlm-for-local-full-analysis.md` (findings F1–F16, S1–S6). This plan is self-contained: IDs map 1:1 to that document's findings index.
**Execution target:** the owner's Linux laptop, in a follow-up session, after the current long-running workload finishes. Every item below is specified so a fresh agent session can implement it without access to the analysis conversation.

---

## 0. Environment & prerequisites (Linux laptop session)

1. **Toolchain:** Python ≥ 3.12 (repo pins 3.13 via `.python-version`), `uv`, git, a llama.cpp build with `llama-server`.
2. **Setup:** clone repo → `uv sync` → `uv run pytest tests/ -k "not slow and not load" -q`.
   Expected baseline: **271 passed** (per `docs/20260730-2221-actionable-issues-fix-validation.md`). Record the actual number before touching anything; it is the regression floor.
3. **Model server (needed only for items marked LIVE):**
   ```bash
   llama-server --model <qwen3.5-4b-instruct-q4_k_m>.gguf \
     --host 127.0.0.1 --port 9010 --ctx-size 16384 --flash-attn \
     --cache-type-k q8_0 --cache-type-v q8_0 --parallel 2
   ```
   Integration tests target `https://localhost:9010/v1` (self-signed; tests handle it).
4. **Optional gates:** `python scripts/run_load_gate_100k.py` (only after index changes, R14), `uv run python -m rlm_local.cli check <model> --quick` (after R10/R25 model-facing changes).
5. **Windows-box leftovers (previous session):** `C:\Users\Mizahnyx\Documents\Misc\rlm_for_local\.tmp_pytest\` could not be deleted from the sandboxed session — remove manually with an elevated shell. Untracked by git; harmless otherwise.

**Ground rules (the project's own process, enforced):**
- For every bug fix, write the failing test first; before merging a guard test, temporarily remove the guard and confirm the test goes red (the documented vacuity antidote — this project has had three vacuous tests; do not add a fourth).
- Every new harness-emitted string goes into `templates.py` (R4.1).
- Run the fast suite after each item; update the affected manual section in the same commit.
- New docs follow `YYYYMMDD-HHmm-<topic>.md` in `docs/`.
- Commit style: batched fixes per wave with descriptive messages (precedent: `498abc3` "fix: P1-P4 — …").

---

## Wave 1 — Core-loop correctness (no live server required)

### R1 — Restore the disk-spill contract at the REPL boundary (F1) — HIGH
**Files:** `src/rlm_local/repl.py`, `src/rlm_local/context_store.py`, `src/rlm_local/root_loop.py`.
**Problem:** `REPLSandbox.start()` calls `str(context)` — the full context is materialized and shipped over the socket; the worker gets a plain `str`; the lazy `Context` machinery never reaches the worker; RAM is O(context) end-to-end.
**Fix spec:**
- `start()` sends either (a) inline text when the handle is `_InMemoryContext`/short str, or (b) `{kind: "file", path: <temp file path>, total: <bytes>}` when the handle is disk-backed `Context` (worker runs on the same host — the store's temp file is readable; `ContextStore.cleanup()` retains ownership, worker must not delete).
- Worker builds a local lazy reader over the path and binds it to `context`; worker-side `peek/grep/chunk/lines` operate lazily (grep streams the file; chunk reads ranges). The `hasattr(context, 'grep')` checks in the injected helpers then become real.
- Fix `Context.chunk()` to stream (read ranges) instead of `str(self)`.
**Tests:** unit — init payload size stays under the spill threshold for a 2M-char context; grep/chunk on a non-ASCII on-disk context return correct results; worker never writes to the context file.

### R2 — Byte/character offset semantics in `Context` (F2) — HIGH
**Files:** `src/rlm_local/context_store.py`.
**Problem:** `f.seek(key)` (byte offset) driven by character indices; `len()` is characters. Non-ASCII contexts corrupt slicing.
**Fix spec (choose byte semantics):** make the whole handle byte-based — `total = os.path.getsize(path)`, open binary, decode decoded slices with `errors="strict"`; `lines()` already yields decoded str. Implement the real byte-offset line index in `ingest` (replace the `pass` no-op) and use it in `lines(start)`. Update the docstring and manual §10 (`index`/`slice` are byte offsets).
**Tests:** emoji/CJK context: round-trip `ctx[i]`, slices, `lines()`, `grep()`; property test vs `str` ground truth.

### R3 — Sub-call budget & memoization order (F3) — MEDIUM
**Files:** `src/rlm_local/subcall_manager.py`.
**Fix:** check the memoization cache *before* charging the budget (cache hits must not consume budget); track a real `_cache_hits` counter and return it from `cache_hits`; delete the unused `sub_model` parameter.
**Tests:** identical prompt twice → one budget charge, `cache_hits == 1`; distinct prompts → two charges.

### R4 — REPL timeout desync (F4) — MEDIUM
**Files:** `src/rlm_local/repl.py`.
**Fix:** correlate messages — `exec` carries a monotonically increasing `cell_id`; worker echoes it in `result`; `execute()` discards any `result` whose `cell_id` ≠ the current one (drain loop with deadline); after a timeout, either (a) drain-then-continue or (b) restart the worker if a stale result is still outstanding after N seconds (pick (a) + restart-on-second-timeout). Keep the protocol backward-compatible with tests that stub the worker.
**Tests:** fake worker that sleeps past `cell_timeout` then replies — the following cell's result must not be misattributed; second timeout triggers worker restart and the loop continues.

### R5 — Wire (or remove) the unwired parser stages (F5) — MEDIUM
**Files:** `src/rlm_local/parser.py`, `root_loop.py`, `subcall_manager.py`.
**Fix:** implement design §5.6's pipeline honestly:
- Wire `parse_stderr` into `root_loop`: after a REPL result with non-empty stderr, call it and append the resulting nudge (max-consecutive-errors logic already lives in Parser).
- Wire `repair_json` into `SubcallManager._do_call` when `schema is not None` (repair-parse the response before returning; design §5.4 requires it).
- Delete the unreachable stage-3/3b regexes in `parser.parse`.
- Use or remove the dead `check_answer_in_block` call at `root_loop.py:205` (R6 consumes it).
**Tests:** existing `parse_stderr`/`repair_json` unit tests (if present) stay green; new root-loop integration test with a stub backend that emits an error-causing block then a corrected one → loop continues and recovers.

### R6 — Empty-answer handling (F6) — MEDIUM
**Files:** `src/rlm_local/root_loop.py`, `templates.py`.
**Fix:** after a REPL result: if `final_answer is not None and final_answer.strip() == ""` → append `NUDGE_EMPTY_ANSWER` as the next user message, count it against `max_consecutive_nudges`, do not finalize. Elsewhere keep `is not None` semantics (never truthiness) for answers.
**Tests:** stub model sets `ready=True` with empty content once, then submits real content → loop nudges (logged) and finishes with the real answer.

### R7 — Template discipline R4.1 (F7) — LOW
**Files:** `src/rlm_local/repl.py`, `templates.py`.
**Fix:** use `CELL_TIMEOUT_ERROR.format(timeout=...)` in the timeout path; delete `REPL_READY`/`REPL_FINAL_ANSWER` or wire them; grep for remaining inline harness strings in `root_loop.py` (e.g. the forced-finalization fallback) and freeze them.

### R8 — Implement `load_fewshots_from_vault` (F8) — MEDIUM
**Files:** `src/rlm_local/prompts.py`, tests.
**Fix:** when a vault has active `fewshot` pages, parse each body's `## Example` sections into user/assistant message pairs and append up to 1 vault few-shot after the builtin example (prompt-budget guard: skip if the added pair would exceed `sub_prompt_char_budget`/4). This unblocks K4's `bootstrap_fewshots` target, which today promotes pages that can never influence prompts.
**Tests:** fake vault with a few-shot page → `build_messages` contains the pair; empty vault → unchanged output; oversized few-shot → skipped.

### R9 — Backend robustness (F9) — MEDIUM
**Files:** `src/rlm_local/model_backend.py`, `pyproject.toml`.
**Fix:** implement the promised endpoint normalization (append `/v1` when missing — the comment says it, the code doesn't); guard response extraction (raise a descriptive error including the HTTP status and a body snippet on missing `choices`/`content`); add retry with `tenacity` (already a declared, unused dependency): 2 retries, exponential backoff, transport errors and 5xx only; add optional `headers` parameter (auth support); keep `verify=False` default for localhost but emit `warnings.warn` when the endpoint host is non-loopback and verification is off (see R18).
**Tests:** `/v1` normalization table; malformed payload raises `ModelBackendError` (new) instead of `KeyError`; retry on 503 via a stub transport.

### R10 — Make the truncation promise true (F10) — MEDIUM
**Files:** `src/rlm_local/repl.py`, `root_loop.py`, `config.py`, manuals.
**Fix:** set the worker `stdout_cap` from `profile.repl_output_char_cap` (exactly what the prompt promises — "the model is never lied to"), implement tail-preserving truncation for stderr per design §5.5 (keep head N + tail N for errors), and pass the actual cap through `prompt_vars` so template text and behavior agree by construction (assert equality in a test).
**Tests:** a cell printing > cap chars returns exactly the cap with the documented marker; prompt text contains the same number.
**LIVE check:** after R10, re-run one needle eval to confirm the small-model loop still completes within turns (smaller windows change model behavior).

---

## Wave 2 — Kernel state machine & wiring (no live server required)

### R11 — GEPA promotion state hazards (F11) — HIGH
**Files:** `src/rlm_kernel/optimize.py`, `gate.py`.
**Fix (in order):**
1. Reorder: `propose → validate → (pass?) demote incumbent → promote(target_path)` — a validation failure must leave the incumbent ACTIVE.
2. Version lineage: when `target_path` overrides and an incumbent exists, new page `version = incumbent.version + 1`.
3. Kind: derive the proposed page's kind from the incumbent page's kind (read it), not hardcoded `"contract"`.
4. Evaluator: wrap vault mutation + evaluation in `try/finally` restoring the original body; guard parallel evaluation with a module-level `threading.Lock` around the mutate/eval/restore critical section (or force `max_workers=1` in `GEPAConfig` and document why).
**Tests:** forced validation failure → incumbent unchanged; two sequential promotions → `version` increments; fewshots-target promotion → page kind `fewshot`; evaluator exception → original body restored.

### R12 — Make the memory layer honest (F12) — MEDIUM
**Files:** `src/rlm_kernel/memory.py`, `schema.py`.
**Fix (minimal viable):** add optional `access_count: int = 0` and `last_access: str | None = None` to `Frontmatter` (backward compatible); `MemoryManager.search` updates them on hits (through `vault.put`, same atomic path); `decay_score` then has real inputs — call it in `search` to order results. Fix `forget` to combine `query` AND `older_than` filters (or fix the docstring — pick combine, matching the docstring). Remove the dead `merged` set in `compact` (or make dry-run and merge use the same clustering function so they cannot disagree). Delete the empty `_ensure_core_memory_dir`.
**Tests:** decay ordering with accessed vs stale notes; both-filters forget; compact dry-run count equals merge count.

### R13 — Unify directory conventions (F13) — LOW
**Files:** `src/rlm_kernel/seed.py`, `gate.py` tests.
**Fix:** standardize on `gate.promote`'s singular `<kind>/` convention; update `seed.py` (`helpers/` → `helper/`, `fewshots/` → `fewshot/`), update seed page wikilinks, and note in the kernel manual that pre-existing vaults need a one-time page move (provide a 10-line snippet in the manual, not code).
**Tests:** seed → `Index.list_paths(kind="helper")` finds the seeded helpers; seed idempotency still holds.

### R14 — Index/search defects (F14) — LOW-MEDIUM
**Files:** `src/rlm_kernel/index.py`, `search.py`, `gate.py`.
**Fix:** `fts_search`: build `"tok1" OR "tok2"` from the query (quote each token; preserves exact-match intent while restoring multi-word recall) — document the semantics change in the kernel manual §5; `reindex_delta`: include `id` in the delta key (or delete-by-path + re-add when `id` changed) so tags/links don't orphan; `verify_quarantine_isolation`: replace the tautological second check with index-rows-vs-vault-walk comparison; `MAX_BODY_LENGTH`: measure UTF-8 bytes to match the comment.
**Tests:** multi-word search finds pages where terms are non-adjacent; ULID-change reindex leaves no orphan tags; `verify_quarantine_isolation` detects an injected bad row (make the guard genuinely falsifiable).
**LIVE check:** re-run `scripts/run_load_gate_100k.py` after this item (it exists to catch exactly this class of regression).

---

## Wave 3 — Security & hygiene

### R17 — Untrack the TLS private key (S1) — HIGH
`git rm --cached cert.pem key.pem`; add `*.pem` to `.gitignore`; add a one-liner to the operator guide for generating a per-host self-signed pair (`openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 -subj "/CN=localhost"`). `test_web.py:130` monkeypatches `uvicorn.run` and needs no real files.

### R18 — TLS verification posture (S2) — MEDIUM
`HTTPModelBackend`: emit `warnings.warn` when endpoint host ∉ {localhost, 127.0.0.1, ::1} and `verify=False`; document in operator guide. `optimize.py`: scope `litellm.ssl_verify=False` as narrowly as litellm allows, else document the process-global tradeoff at the call site.

### R19 — Gate validation without execution by default (S3) — HIGH
**Files:** `src/rlm_kernel/gate.py`, `cli.py`, kernel manual.
**Fix:** `validate(page, vault, *, execute=False)` — default performs AST parse + import allowlist + blocked-pattern scan + signature static check; the sandbox `exec` runs only with `execute=True`. CLI: `rlm-kernel review [--execute]` (default off, prints "static validation only"). Document the trust model explicitly in the kernel manual §7: the gate is a quality gate; exec-based validation is an opt-in convenience for trusted authors, never containment.
**Tests:** a helper containing a `__subclasses__` escape chain passes static validation and is flagged; `review` without `--execute` never calls `exec` (monkeypatch sentinel).

### R20 — Vault path containment (S4) — HIGH
**Files:** `src/rlm_kernel/vault.py`, `schema.py`, `gate.py`.
**Fix:** `LocalVault._resolve(path)`: reject absolute paths and any `..` segment; enforce `(root / path).resolve().is_relative_to(root.resolve())` in get/put/delete/git paths. Add a `name` validator to `Frontmatter`: `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (rejects separators, leading dots, whitespace). `promote`'s default target then cannot escape by construction.
**Tests:** parametrized traversal attempts (`../x`, `/etc/x`, `a/../../x`, name `../../foo`) raise `ValueError` at parse/put/promote; legitimate dotted names (`my.helper-v2`) still work.
**Note:** the web `/docs` jail (`app.py:349`) is the in-repo reference pattern.

### R21 — Web auth completeness (S5) — MED-HIGH
**Files:** `src/rlm_web/app.py`, `templates/vault.html`.
**Fix:** call `_check_auth` in `/jobs/{id}/events` and `/chat/events/{stream_id}`; refactor `_check_auth` into a FastAPI dependency for coverage-by-construction; `vault.html`: replace `innerHTML` with `textContent` (or escape); `secrets.compare_digest` for token comparison; reject `POST /login` with 400 when `RLM_WEB_TOKEN` is unset (loopback-only mode needs no login); drop the `request.client is None` allowance behind an explicit `RLM_WEB_ALLOW_TESTCLIENT=1` env; set `https_only=True` on SessionMiddleware when SSL args are present; delete `check.html` + route refs (dead UI) or implement `POST /check`.
**Tests:** unauthenticated SSE → 401; traversal/escaping tests for ingest results; login without configured token → 400.

### R22 — Logger & surface hygiene (S6) — LOW
`TrajectoryLogger`: add a `threading.Lock` (or drop the "Thread-safe" claim); default log dir under the workspace (`logs/trajectories/`, gitignored) instead of the shared temp dir; document retention. Web: cap `_jobs`/`_chat_sessions` (simple LRU, 100 entries).

---

## Wave 4 — Polish, tests, docs

### R15 — model_check probes (F15) — LOW-MEDIUM
Fix P2's lexer escape handling (`if ch == "\\": i += 1; continue` — skip the *next* char). P3: document the weakness in the probe docstring + manual §16 (tightening changes score semantics — owner decision, defer).

### R16 — Dead code / stale strings sweep (F16) — LOW
Remove: no-op `_subcall_loop`, unused `import io`, inline `__import__('re')`, unused `sub_model` (R3), inert `prompt_vars` keys or wire them; fix `cli.py` docstring + stale "not yet implemented" message; `chat.py` empty-context guard; `search_quarantine` docstring; `promote` guard for deprecated/superseded occupants (refuse overwrite unless `--force`); web UI drift (htmx removal or wiring, `/ingest` handler or removal, `RLM_VAULT_ROOT` consistency in chat handlers).

### R23 — (DEFERRED) Frontmatter `schema` rename — LOW/breaking
`schema` shadows a pydantic v2 attribute (UserWarning on every test run). Renaming to `schema_version` requires dual-parse of existing vault pages + a migration note; defer to a planned vault-format change, not a drive-by fix.

### R24 — Test suite debts — MEDIUM
Add the `load` marker to `pyproject.toml` and mark `tests/load/*` with it (README's `-k "not slow and not load"` then works); delete or implement the three vacuous tests (`test_vault_init` `pass`, backend-reuse tautology, `rc in (0,1)` search); add the missing coverage: `repl_bridge` handlers, `seed` idempotency, kernel `cli`, disk-backed `Context` paths (with non-ASCII — pairs with R1/R2), `ContextStore.cleanup`, vault-first prompt loaders (pairs with R8); fix eval substring patterns (anchor them; `\b`-style word boundaries — pairs with R25b).

### R25 — Documentation repairs — MEDIUM
1. `rlm-local-manual.md`: remove/mark-historical `load_template` (§11.2.1, §17.10, §14.3 row).
2. `rlm-kernel-manual.md` §13.2: "(future)" load tests → record the 2026-07-26 gate results.
3. `load-test-report.md`: repair the two mid-sentence truncations and the stray trailing fence.
4. `docs/conformance/README.md`: replace the stale "K4-real is the next milestone" status line.
5. Needle-pattern normalization (P4 follow-up b, on record): case/whitespace/hyphen-insensitive matching in model_check P6 + eval patterns.
6. P4 follow-up a (LIVE): add a few-shot transcript demonstrating voluntary `answer["ready"]` submission; validate by re-running `rlm check <model> --quick` on the laptop and comparing P4 score (target: >0/15). Pre-register expectations per project convention.

---

## Suggested execution order & live-server dependency map

| Wave | Items | Offline | LIVE |
|---|---|---|---|
| 0 | baseline suite + R17 | ✓ | — |
| 1 | R2, R3, R6, R7, R9, R5, R4, R8, R1, R10 | ✓ | R10 sanity re-run |
| 2 | R13, R12, R11, R14 | ✓ | R14 load gate |
| 3 | R20, R19, R21, R18, R22 | ✓ | — |
| 4 | R16, R24, R15, R25(1–5), R25(6) | ✓ | R25(6), `rlm check` |

Rationale: R2/R3/R6 are small and unlock confidence; R1 is the largest structural change and benefits from R2 landing first (byte semantics shared by the worker-side reader); R11 before R14 (both touch kernel state); security wave before docs wave so manuals document the final trust model once.

**Acceptance for the whole plan:** fast suite green at ≥271 tests (plus new ones), every guard test proven non-vacuous, no new harness strings outside `templates.py`, manuals updated in-commit, and one end-to-end `rlm check` + needle eval run recorded as a dated validation doc (`YYYYMMDD-HHmm-remediation-validation.md`).
