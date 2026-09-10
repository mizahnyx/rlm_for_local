# RLM for Local — Full Session Analysis: Architecture, Code, Documentation

**Date:** 2026-09-03 21:07
**Scope:** entire repository — all 33 `src/` modules read in full, all 33 docs read, test tree reviewed, git history + run artifacts inspected, fast test suite attempted.
**Method:** direct first-hand reading of the core execution loop (config, parser, repl, root_loop, subcall_manager, context_store, model_backend, prompts, templates, repl_bridge) plus four parallel deep-dive analyses (rlm_kernel, rlm_local support modules + tests, docs corpus, rlm_web + scripts + artifacts). Findings below are consolidated; every claim carries a `file:line` reference or a doc citation.
**Companion documents:** `20260903-2107-remediation-plan.md` (prioritized fixes), `20260903-2107-memanto-vs-rlm-behaviour-as-content.md` (external comparison), `20260903-2107-agent-memory-landscape-report.md` (research landscape).

---

## 1. Executive summary

`rlm_for_local` is a Recursive Language Model harness for 1–8B local models (llama.cpp-class servers) composed of three packages:

| Package | Role | Modules |
|---|---|---|
| `src/rlm_local` | Core harness: config profiles, guardrail parser, subprocess REPL, sub-call manager, root loop, prompts, CLI, chat, model-check battery | 13 |
| `src/rlm_kernel` | Evolvable layer: git-versioned markdown vault, SQLite/FTS5 index, quarantine gate, memory, GEPA optimizer | 12 |
| `src/rlm_web` | FastAPI console (SSE jobs, chat, vault search/ingest) | app + 9 templates |

The engineering *process* is exceptionally disciplined (design doc → requirements R1.1–R7.1 → conformance trail with commit hashes and reconciling numbers). The code is real and works end-to-end (33 trajectory logs under `data/jobs/`, K4 GEPA run promoted, 100K-page load gate passed after the FTS fix). The findings below concentrate in four clusters: (A) the context-store↔REPL boundary, where the headline "RAM stays flat" design quietly breaks; (B) a half-wired guardrail/repair layer; (C) GEPA promotion state hazards; (D) security hygiene. Nothing found undermines the architecture; all findings are fixable without redesign.

---

## 2. Architecture as implemented (verified)

`completion(query, context)` → `RootLoop.run()`:

1. **Config** (`config.py:58-100`): frozen profiles `tiny/laptop/workstation` (ctx 8192/16384/32768 tok; sub-prompt budget 8K/16K/24K chars; REPL cap 2K/4K/8K; turns 12/15/20; sub-calls 30/60/100; concurrency 1/2/4; cell timeout 60/60/120 s; spill threshold 500K/1M/1M). TOML + kwarg overrides merge over profile defaults via `Config.__getattr__`.
2. **Root conversation** (`root_loop.py:147-239`): byte-stable prefix (system prompt → metadata with query + context type/length only → prologue → one few-shot) for llama-server prefix caching; per-turn `Turn i/max` user headers; forced finalization as the structural backstop.
3. **Parser** (`parser.py`): fenced-block extraction → unclosed-fence rescue → smart-quote normalization (live Qwen bug: `answer['content']` with Unicode quotes) → narration nudges → `FINAL:` courtesy line.
4. **REPL** (`repl.py`): separate Python subprocess over localhost TCP, length-prefixed JSON (`[4B BE length][UTF-8 JSON]`); harness interleaves `subcall`/`search`/`propose` with `exec` while a cell runs; termination is programmatic (`answer["ready"]` inspected by the worker).
5. **SubcallManager** (`subcall_manager.py`): ThreadPoolExecutor, call/char budgets, sha256 memoization, anti-shortcut warning (>60% of context in one sub-call, R5.3), schema pass-through.
6. **Kernel bridge** (`rlm_kernel/repl_bridge.py`): vault helpers injected into the REPL namespace via `exec`; `search`/`propose` socket verbs; core-memory summary injected into metadata; vault-first system-prompt assembly (`prompts.py:157-208`).

**Kernel data flow** (verified): markdown pages (truth) → `parse_page` → `LocalVault.put` (tmp+fsync+`os.replace`, git stage) → `Index` (SQLite WAL + FTS5, derived/rebuildable, `user_version=2` migrations) → search cards ≤400 chars → `KernelBridge` → prompts/REPL. Model-authored content enters the live system only via `propose → quarantine → validate → promote → reindex_delta`. GEPA (`optimize.py`) evolves target-page bodies against 22 regex-verified eval tasks (70/30 train/held-out via md5 split), promotion requires train win **and** held-out non-regression, routed through the gate with `<!-- optimized_by: ... -->` lineage.

---

## 3. Verified findings — correctness

### F1. Disk-spill design defeated end-to-end (HIGH — headline claim breaks)
- `REPLSandbox.start()` calls `str(context)` (`repl.py:356` region), materializing the entire context and shipping it over the socket; the worker holds a plain `str`, so `hasattr(context, 'grep')` is always False and the lazy `Context` machinery never reaches the worker.
- RAM is O(context) in harness AND worker regardless of `context_spill_threshold`. `context_store.py` docstring "RAM stays flat regardless of context size" and design R2.1 are not honored.
- `Context.chunk()` also materializes: both modes start with `text = str(self)` (`context_store.py:231-236`).
- The line-offset indexer is a no-op: `for i, ch in enumerate(chunk): ... pass` ("simplified for now", `context_store.py:158-167`); `Context` is constructed without offsets (`context_store.py:169`), so `lines(start>0)` always falls back to sequential `readline()`.

### F2. Byte-vs-character offset corruption (HIGH — data correctness for non-ASCII)
- `Context.__getitem__` does `f.seek(key)` on a text-mode file (byte offset) with character semantics; `total_chars = len(text)` counts characters. Any multi-byte UTF-8 context corrupts indexing/slicing or raises mid-codepoint `UnicodeDecodeError` (`context_store.py:38-57`).
- No test covers non-ASCII contexts (test gap, §6).

### F3. Sub-call budget charged before memoization (MEDIUM)
- `_do_call` increments `_calls_used`/`_chars_used` *before* the cache check (`subcall_manager.py:91-104`): cache hits consume budget — defeats memoization's purpose under pressure.
- `cache_hits` property returns `len(self._cache)` — cache size, not hits (`subcall_manager.py:161-163`).
- `sub_model` constructor parameter accepted and never used (`subcall_manager.py:35`).

### F4. REPL timeout desyncs the socket protocol (MEDIUM — silent result misattribution)
- On `cell_timeout`, `execute()` returns an error while the worker keeps executing; the next cell's `execute()` will read the *previous* cell's result message (no sequence/correlation IDs, no worker restart) (`repl.py:394-398` region + `_recv_msg`).
- Design §5.3 calls for structured `TimeoutResult` with partial state; not implemented.

### F5. Half-wired guardrail pipeline (MEDIUM — docstring promises stages that don't run)
- `parse_stderr` (`parser.py:174`) and `repair_json` (`parser.py:209`) are defined but **never called anywhere in src** (grep-verified). The module docstring advertises "stderr self-correction → finalization repair → sub-call output repair"; none of those stages execute. Design §5.4 explicitly specifies repair-parsing sub-call results.
- Parser stages 3/3b (`parser.py:107-131` region) are unreachable: their regexes are strict subsets of stages 1/2 (`(?:python)?` ⊂ `(?:repl|python)?`).
- `root_loop.py:205` calls `self._parser.check_answer_in_block(block)` and discards the result (dead call).

### F6. Empty-answer handling gap (MEDIUM — wasted turns)
- Model sets `answer["ready"]=True` with `content=""` → `final_answer = ""` (via worker) → `if final_answer:` truthiness check at `root_loop.py:238` treats it as non-final → loop continues silently.
- The nudge that exists for exactly this case, `NUDGE_EMPTY_ANSWER` (`templates.py:50-53`), is never referenced in src. `check_answer_in_block` already returns the content — see F5 dead call.

### F7. Template discipline (R4.1) violated (LOW)
- `templates.py` claims "every string the harness emits is frozen here"; unused: `NUDGE_EMPTY_ANSWER`, `CELL_TIMEOUT_ERROR`, `REPL_READY`, `REPL_FINAL_ANSWER` (grep-verified). The REPL timeout path uses an inline f-string instead of `CELL_TIMEOUT_ERROR` (`repl.py` timeout branch).

### F8. `load_fewshots_from_vault` is a stub (MEDIUM — K1/K4 feature inert)
- Loads vault `fewshot` pages then returns the hardcoded example regardless (`prompts.py:211-227`): `result = list(FEWSHOT_EXAMPLE); return result`. K4's `bootstrap_fewshots` promotes few-shot pages that can never affect prompts through this path.

### F9. Model backend fragility (MEDIUM)
- Comment claims "ensure /v1 suffix"; code only does `.rstrip("/")` (`model_backend.py:40-42`) — an endpoint without `/v1` produces a 404 path.
- `data["choices"][0]["message"]["content"]` unguarded (`model_backend.py:99-100`): empty content (reasoning models) or malformed payload → raw `KeyError`/`IndexError` that propagates uncaught through `completion()` (root-tier chat is not wrapped in try/except; only forced finalization is).
- No retry/backoff despite `tenacity` being a declared dependency (never imported anywhere — grep-verified). No auth-header support at all.

### F10. stdout cap contradicts the prompt (MEDIUM — "never lie to the model" principle)
- Prompt tells the model output is "truncated to {repl_cap} characters" (2K/4K/8K); actual cap is 256 KB (`repl.py:293`, `stdout_cap=256*1024`), head-truncated not tail-preserving. Design R1.3 specifies aggressive 2–8K truncation. `repl_output_char_cap` is used *only* in prompt text (grep-verified).

### F11. GEPA promotion state hazards (HIGH — can leave live prompt broken)
- Incumbent demoted **before** candidate validation (`optimize.py:408-446`): a validation/promotion failure leaves the live page `DEPRECATED` with nothing promoted and an opaque `gate_error` status.
- Version lineage reset: propose creates `version=0`, promote bumps to 1; with `target_path` the incumbent's version is discarded — after N optimizations the live page is still `version=1`.
- All targets proposed with hardcoded kind `"contract"` (`optimize.py:426`): optimizing `fewshots`/`nudges` overwrites pages of the wrong kind, breaking kind filters/dir conventions.
- Evaluator mutates the shared live vault with no lock (GEPA `max_workers>1` race) and no `try/finally` — an exception (e.g. `load_suite` failure) leaves candidate text in the production vault.

### F12. Memory subsystem largely unwired (MEDIUM — the layer is aspirational)
- `decay_score` (`memory.py:44-78`) is never called; its inputs (`access_count`, `last_access`) have no persistence anywhere (not in `Frontmatter`).
- Only `MemoryManager.add` is called from live code (`chat.py:240`); `note/search/forget/write_core/compact` are CLI/test-only.
- `forget` docstring says both filters combine; code is `if query: ... elif older_than is not None:` — query silently wins (`memory.py` forget method).
- `compact`'s `merged` set is never populated (dead checks); `_ensure_core_memory_dir` is an empty function.

### F13. Seed vs gate directory-convention conflict (LOW)
- `seed.py` writes `helpers/`, `fewshots/` (plural); `gate.promote` defaults to `<kind>/` (singular: `helper/hello.md`, asserted in tests). Two conventions coexist in one vault.

### F14. Kernel index/search defects (LOW-MEDIUM)
- `fts_search` wraps the whole query in double quotes → multi-word queries become FTS5 *phrase* queries (adjacent tokens only), losing AND semantics (`index.py` fts_search).
- `reindex_delta` keys on `content_hash`, which excludes `id`: rewriting a page with a new ULID but identical content leaves tags keyed by the old id.
- `verify_quarantine_isolation`'s second check is tautological (compares `search_quarantine(vault)` with `vault.list(prefix=QUARANTINE_PREFIX)` — the same call).
- `MAX_BODY_LENGTH` comment says bytes, code counts characters (`gate.py`).

### F15. model_check probe defects (LOW-MEDIUM — affects scoring validity)
- P2 paren-lexer: `if ch == "\\": continue` skips the backslash but not the escaped char → `\'` flips string state and can mis-track parens (`model_check.py` P2).
- P3 grants full credit (15/15) when **no stderr events occur** — a model that never executes code passes; pre-error `grep(` calls count as "recovery" (acknowledged weak in its own test).

### F16. Misc verified defects (LOW)
- `root_loop.py`: `__import__('re')` inline instead of top-level import.
- `context_store.py`: unused `import io` in `ingest`.
- `_subcall_loop` is a no-op with a misleading comment (`repl.py`).
- `cli.py` module docstring omits `tag`; `_cmd_chat`'s `except ImportError` prints "Chat mode not yet implemented" (stale — chat.py exists; fires only when kernel deps are missing).
- `Config.prompt_vars()` returns `root_ctx_size`/`sub_ctx_size` that no template consumes (inert keys).
- `chat.py` passes empty session context to `completion()` with no guard.
- `TrajectoryLogger` docstring says "Thread-safe" but has no lock; default log path is `gettempdir()/rlm_trajectory_{µs}.jsonl` (predictable, shared temp dir, never cleaned).
- `search_quarantine` docstring says "newest first"; `vault.list` returns lexicographic order (ULIDs sort oldest-first).
- `promote` name-conflict guard only protects against ACTIVE occupants — deprecated/superseded pages at the target path are silently overwritten (`gate.py:497` region).
- Web: `check.html` posts to a nonexistent `POST /check` (405); console's file-upload input silently dropped (`POST /jobs` takes no files); `chat.html` advertises `/ingest` not implemented in web handler; htmx loaded, zero `hx-` attributes; chat/vault handlers hardcode `~/.local/share/rlm-kernel/vault` while `/vault*` honors `RLM_VAULT_ROOT`.

---

## 4. Design-vs-implementation gaps (documented but not built)

| Design requirement | Status |
|---|---|
| §5.3 restricted builtins in REPL (`input/eval/exec/compile/globals/locals` blocked), `open` jailed to task dir | **NOT implemented** — worker does bare `exec(code, globals())`, full builtins, unrestricted `open`. Only the kernel gate has `safe_builtins` (`gate.py:311`). |
| §5.3 scaffold names restored after every cell | **NOT implemented** — model code can brick `answer`/`context`/`llm_query` for the rest of the run. |
| §5.3 memory rlimits on worker | **NOT implemented** — killable but not memory-bounded. |
| §5.4 schema fallback: "append prose instruction and repair-parse the result" | Repair-parse (`repair_json`) exists, unwired (F5). |
| R1.3 / §5.5 output truncation 2–8K tail-preserving | Actual 256 KB head-truncate; prompt says {repl_cap} (F10). |
| §5.7 SQLite context store ("swappable in later") | Never started; file-backed only. |
| Vault templates as live behavior | Introspection-only (documented honestly per R3-D10); only system prompt, how-to-work, helpers are live. |
| Memory decay/compaction (K3) | Half-built, unwired (F12). |
| Bindings pattern (`call_api` → `KernelBridge.handle_api_call`) | Documented in extensibility guide §8.5; not in code or API reference. |

---

## 5. Security findings

### S1. Private TLS key committed to git (HIGH hygiene)
`git ls-files` tracks `cert.pem` **and** `key.pem` at repo root. Even self-signed/localhost, a private key in the repo is wrong; `test_web.py:130` shows they're passed via `--ssl-keyfile/--ssl-certfile`.

### S2. TLS verification disabled everywhere (MEDIUM, documented tradeoff)
`verify=False` is the `HTTPModelBackend` default and hardcoded at every call site (`__init__.py:66`, `chat.py:59`, `cli.py:394`); `litellm.ssl_verify = False` set module-globally (`optimize.py:350`). No warning when the endpoint is non-local.

### S3. `rlm-kernel review` executes model-authored helper code in the host process (HIGH trust-model gap)
`_cmd_review` (kernel `cli.py:127-132`) calls `validate()` on every quarantined page; helper validation does `exec(code, {"__builtins__": safe_builtins, ...})` in-process (`gate.py:_sandbox_test`). Restricted-builtin exec on CPython is escapable via `().__class__.__bases__[0].__subclasses__()` chains; the substring blocklist (`"os.system"`, `"subprocess"`, …) is trivially bypassed. The gate is a quality gate, not containment — but `review` turning a read-looking command into code execution needs an explicit trust note and an opt-in.

### S4. Vault path traversal via model-controlled `name` (HIGH)
`LocalVault.get/put/delete` compute `self.root / path` with no normalization or containment check; frontmatter `name` has no charset validation (1–128 chars only); `promote`'s default target is `f"{kind}/{name}.md"` with model-supplied `name`. A proposed page named `../../foo` promoted via CLI writes outside the vault root. (The `handle_propose` write itself is safe — ULID filename; the unsanitized name travels in frontmatter until promotion.)

### S5. Web auth gaps (MEDIUM-HIGH)
- Both SSE endpoints skip `_check_auth` entirely: `/jobs/{job_id}/events` (`app.py:171` region) and `/chat/events/{stream_id}` (`app.py:390` region) — job answers and chat responses readable unauthenticated.
- `vault.html` builds `result.innerHTML` from server-echoed uploaded filenames without escaping (DOM-XSS vector).
- No CSRF tokens; session cookie lacks `Secure` (SessionMiddleware added with only `secret_key`); token compared with `==` (not `secrets.compare_digest`); `/login` accepts any token when `RLM_WEB_TOKEN` is unset; dev-fallback session secret baked in (`app.py:29`).
- `_check_auth` allows `request.client is None` (TestClient convenience) — fail-open edge if fronted by an ASGI setup without client info.
- Positives (verified): `/docs` traversal jail via `is_relative_to` (`app.py:349`), upload caps 20 files/8 MB with 413s, Jinja2 autoescaping throughout, vendored htmx.

### S6. Unprotected surfaces (LOW-MEDIUM, single-user context)
- REPL socket verbs unauthenticated (localhost by design).
- Trajectory logs (full prompts/responses) in shared temp dir, predictable name, never cleaned.
- Jobs/chat sessions in memory unbounded; trajectory path CWD-relative (`app.py` `_run_job`).

---

## 6. Test suite assessment

- **Baseline:** repo's own third-party validation (`docs/20260730-2221-actionable-issues-fix-validation.md`) reports **271 passed** with `-k "not slow"`.
- **Environment caveat (this session):** the fast suite could not be executed cleanly on this Windows machine under the execution sandbox — every test using `tempfile.TemporaryDirectory`/pytest `tmp_path` fails with `PermissionError: [WinError 5]` on child `mkdir`. Reproduced *outside* pytest with a 5-line script: dirs created with restrictive modes (`0o700` via mkdtemp) deny child creation under this environment's ACL broker. **Environmental, not a project defect.** Leftover probe dir `.tmp_pytest/` on this Windows box cannot be deleted from the sandboxed session — remove manually (`Remove-Item -Recurse -Force .tmp_pytest` from an elevated shell). On the Linux laptop the suite should run clean.
- **Strengths (verified):** no unit test touches the network (stub backends everywhere); differential good/bad-stub testing of all 9 model-check probes; migration tests with `PRAGMA user_version`; state-equivalence and scaling-ratio tests in the kernel suite; the "non-vacuous guard" discipline post-incident (remove the guard, watch the test go red).
- **Weak spots:**
  - Vacuous tests remain: `test_cli.py::test_vault_init` is literally `pass`; `test_model_check.py::TestBackendReuse` never arms its close-tracker and asserts `score >= 0` (tautology); `test_cli.py::TestSearch` accepts `rc in (0,1)`.
  - P3's bad-model test codifies the probe weakness (never-executed-code → 15/15).
  - Zero coverage: `repl_bridge.py`, `seed.py`, kernel `cli.py`, `_ulid.py`; disk-backed `Context.lines()/grep()/chunk()`, `ContextStore.cleanup()`; non-ASCII contexts (would catch F2); vault-first prompt loaders (would catch F8).
  - Eval suites: substring patterns false-positive (`"35"` matches `"135"`; documented "O-Negative" vs "O Negative" evaluator bug cost 5 points); contexts KB-scale (not the headline regime); `tolerance` unused in the loader.
  - Load tests: Tier-2 asserts nothing (prints only); the 100K-page spec gate is enforced nowhere in code (external runbook exercise). README's `-k "not slow and not load"` references a `load` marker that doesn't exist (load tests are marked `slow`).
- `test_local_vault_implements_protocol` instantiates `LocalVault(Path("/tmp/test"))` with `init_git=True` — creates a real dir + git repo as a test side effect.

---

## 7. Documentation corpus assessment

- **Strengths:** traceability (exact commit hashes, reconciling numbers across 18 conformance docs); honesty norm (scaffold disclosures, five failed K4 attempts logged, vacuous-test incident documented three times with the antidote); timestamped filenames with explicit authority rules.
- **Stale/contradictory:**
  1. `rlm-local-manual.md` §11.2.1/§17.10 documents `load_template(name, vault=)` — deleted the same day the manual was finished (R3-D10); kernel manual correctly says introspection-only.
  2. `rlm-kernel-manual.md` §13.2 lists load tests as "(future)" — the 100K gate passed earlier the same day.
  3. `load-test-report.md` corrupted mid-sentence in two places (F2 bullet truncated; stray trailing fence).
  4. `docs/conformance/README.md` status line ("K4-real is the next milestone") — K4 completed 2026-07-30.
  5. Test-count drift: manuals cite 72/26/16/10/8; suite grew 124→271 over the documented ten days.
  6. Model-era drift: local manual's integration tests target `LFM2.5-VL-1.6B` (scored FAIL by the project's own model-check); production model is `Qwen3.5-4B-Abliterated`.
- **Open items on record (from `20260730-2141`/`-2221`):** P4 follow-ups — (a) few-shot transcript demonstrating voluntary `answer["ready"]` submission; (b) normalize hyphens/whitespace/case in needle patterns. Documented tradeoffs: memory-only web jobs, no K4 resume (checkpoints mitigate), Tier-2 organic load shelved in favor of production soak.
- **Known residuals:** slot validation treats missing slots as warnings and doesn't check slot-subset vs `prompt_vars` (an evolved template inventing `{new_slot}` could promote and crash at runtime — mitigated only by zero-score crashes); `get_helper_summaries()` appears dead-ish (validation #2, never explicitly closed).

---

## 8. Process observations

- 31 commits, 2026-07-24 → 07-30 (7-day sprint; 17 commits on the final day). HEAD `498abc3` "fix: P1-P4 — path traversal jail, upload caps, auth fail-closed, prompt fix". One pre-existing untracked file: `docs/20260730-2221-actionable-issues-fix-validation.md`.
- Conformance loop closed for the 07-24→26 cycle: 13 defects (D1–D13), 8 deviations (E1–E8), 5 gaps (G1–G5), 2 defects (D-a, D-b) — all resolved; 100K gate PASS (582 s rebuild / 116.9 ms p95 / 38 ms git status post-FTS-fix).
- K4: five failed acceptance attempts (undeclared deps → 24 h circular run; cp1252 crashes ×2; 6-way self-contention; power cut at 32/40) then checkpoint rescue and promotion: train 1.0 (5/5), held-out 1.0 (2/2) vs baseline 0.8/~0.45. Known caveat: promoted text omitted the `answer["ready"]=True` mechanism → model P4 score 0/15 (forced finalization carries production) — verdict "model behavior, not prompt text".
- Model-check calibration history: LFM2.5-VL-1.6B FAIL (few-shot imitation); LFM2.5-8B-A1B-Uncensored FAIL (JSON-prose instead of answer dict); Qwen3.5-4B-Abliterated PASS/production (70/100 MARGINAL quick).

---

## 9. Findings index → remediation plan

| ID | Finding | Severity | Plan item |
|---|---|---|---|
| F1 | Context materialization defeats spill | HIGH | R1 |
| F2 | Byte/char offset corruption | HIGH | R2 |
| F3 | Budget-before-memoization; cache_hits; sub_model | MEDIUM | R3 |
| F4 | REPL timeout desync | MEDIUM | R4 |
| F5 | Unwired parser stages (parse_stderr, repair_json, dead stages) | MEDIUM | R5 |
| F6 | Empty-answer gap (NUDGE unwired, truthiness) | MEDIUM | R6 |
| F7 | R4.1 template violations | LOW | R7 |
| F8 | load_fewshots_from_vault stub | MEDIUM | R8 |
| F9 | Backend fragility (/v1, unguarded extraction, no retry/auth) | MEDIUM | R9 |
| F10 | stdout cap vs repl_cap contradiction | MEDIUM | R10 |
| F11 | GEPA promotion state hazards | HIGH | R11 |
| F12 | Memory subsystem unwired | MEDIUM | R12 |
| F13 | helpers/ vs helper/ convention split | LOW | R13 |
| F14 | Index/search defects (phrase quoting, id-delta, tautology) | LOW-MED | R14 |
| F15 | model_check P2 lexer, P3 weakness | LOW-MED | R15 |
| F16 | Misc dead code / stale strings / web UI drift | LOW | R16 |
| S1 | key.pem/cert.pem tracked | HIGH | R17 |
| S2 | verify=False everywhere | MEDIUM | R18 |
| S3 | review executes helper code; escapable sandbox | HIGH | R19 |
| S4 | Vault path traversal via name | HIGH | R20 |
| S5 | Web SSE auth, XSS, CSRF, cookie flags | MED-HIGH | R21 |
| S6 | Logger thread-safety claim, temp-dir trajectories | LOW | R22 |
| — | Schema rename `schema`→`schema_version` (pydantic shadow) | LOW (breaking) | R23 (deferred) |
| — | pytest `load` marker; vacuous tests; coverage gaps | MEDIUM | R24 |
| — | Doc staleness (manual load_template, kernel §13.2, corrupted report, conformance README, needle patterns, P4 few-shot) | MEDIUM | R25 |
