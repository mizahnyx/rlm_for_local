# Remediation Validation — rlm_for_local, Waves 0–4

**Date:** 2026-09-10
**Plan:** `docs/20260903-2107-remediation-plan.md` (items R1–R25)
**Basis:** `docs/20260903-2107-rlm-for-local-full-analysis.md` (findings F1–F16, S1–S6)
**Base commit:** `498abc3` "fix: P1-P4 — path traversal jail, upload caps, auth fail-closed, prompt fix"
**Environment:** Windows 11, Python 3.13.6 (`.venv`), model served by the llama.cpp
router on `lunacode:9010` (Tailscale / LAN) — the plan's "Linux laptop" LIVE
target, reached remotely.

> This document records what was actually done and measured. Where the plan's
> assumption did not hold (working on Windows rather than the owner's Linux
> laptop, model server on another host), the deviation and its cause are stated
> explicitly rather than silently.

---

## 1. Headline results

| Claim | Result |
|---|---|
| Fast suite green at ≥271 tests | **643 passed, 12 deselected** (655 collected) — floor 271 exceeded, `0 failed` |
| Every guard test proven non-vacuous | **30 of 30** guard mutations went red when the guard was removed |
| No new harness strings outside `templates.py` | enforced by `tests/test_templates.py::TestTemplateDiscipline` (fails on any unreferenced constant) |
| Manuals updated in the same change | yes — see §6 |
| One end-to-end `rlm check` + needle eval recorded | yes — see §5 |

---

## 2. Environment repair (blocking issue, not a project defect)

The plan's Wave 0 assumed the suite runs clean on the owner's laptop. On **this**
Windows host under the DSH execution sandbox it could not: **271 failed /
errored** on the first attempt, every failure a `PermissionError: [WinError 5]`
from `tempfile` usage.

**Root cause (isolated, not guessed):**

```
os.mkdir(p, 0o700)  ->  creating a child inside p FAILS (WinError 5)
os.mkdir(p, 0o755)  ->  OK
os.mkdir(p, 0o777)  ->  OK
os.mkdir(p, 0o500)  ->  OK
```

Only mode `0o700` is affected — and `tempfile.mkdtemp()` (therefore
`TemporaryDirectory`, pytest's `tmp_path`, and every fixture in this repository
that uses them) hardcodes exactly `0o700`. Directories created that way are
unreadable, unwritable and **undeletable** for the current user; icacls on such a
directory reports "access denied", i.e. its DACL denies the owner.

**Fix:** a scoped environment shim installed at
`.venv/Lib/site-packages/sitecustomize.py` (not part of the repository; the venv
is gitignored). It upgrades `os.mkdir(..., 0o700)` to `0o750` **on Windows only**.
On Windows, POSIX mode bits carry no ACL semantics, so where the mode is honoured
at all this is inert; where this sandbox honours it, it removes the failure. It is
inherited by subprocesses, which matters because the REPL worker is a child
process and `repl.py` writes its worker script into a `mkdtemp` directory.

**Leftover artefacts.** The poisoned directories created before the shim are
undeletable from a sandboxed session: `.tmp_pytest/` (from the previous session)
and `.tmp_rt/` (from this diagnosis). Both are now gitignored. Remove them from
an elevated shell:

```powershell
Remove-Item -Recurse -Force .tmp_pytest, .tmp_rt
```

---

## 3. Wave-by-wave results

### Wave 0 — baseline and hygiene

| Item | Status | Evidence |
|---|---|---|
| Baseline suite | recorded | `271 passed, 12 deselected` before any change — matches the plan's regression floor exactly |
| R17 — untrack the TLS private key | done | `git rm --cached cert.pem key.pem`; `*.pem`/`*.crt`/`*.key` gitignored; operator guide gains a per-host `openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 -subj "/CN=localhost"` one-liner and a note that `git rm --cached` leaves the files on disk |

### Wave 1 — core-loop correctness

| Item | Status | What changed |
|---|---|---|
| R1 — disk-spill contract at the REPL boundary | done | `REPLSandbox.start()` sends `{"kind":"file","path":…,"total":…}` for a disk-backed `Context`; the worker binds a lazy `_FileContext` reader over the same path; `Context.chunk()` streams instead of `str(self)` |
| R2 — byte/character offset semantics | done | the whole handle is byte-based (`total = os.path.getsize`-equivalent byte count, binary reads, strict decode); `_InMemoryContext` follows the same contract so indexing does not change meaning at the spill threshold; real byte-offset line index built in `ingest` and used by `lines(start)` |
| R3 — budget/memoization order | done | cache consulted **before** the budget charge; `cache_hits` is a real hit counter; `cache_size` added; unused `sub_model` parameter deleted |
| R4 — REPL timeout desync | done | every `exec` carries a `cell_id` echoed by the worker; stale results are discarded rather than misattributed; two consecutive timeouts restart the worker and tell the model the namespace was lost |
| R5 — wire the unwired parser stages | done | `parse_stderr` called by the root loop after a traceback (and `parse()` no longer resets the error streak, which had made `consecutive_errors` unreachable); `repair_json` applied to schema'd sub-call responses; dead stages 3/3b deleted; the dead `check_answer_in_block` call is now used as the advisory static reading |
| R6 — empty-answer handling | done | `is not None` semantics for answers; empty/whitespace submissions get `NUDGE_EMPTY_ANSWER`, bounded by `max_consecutive_nudges`, then forced finalization |
| R7 — template discipline | done | `CELL_TIMEOUT_ERROR` used in the timeout path; `REPL_READY`/`REPL_FINAL_ANSWER` deleted; forced-finalization fallbacks frozen; new `test_no_dead_templates` guard |
| R8 — `load_fewshots_from_vault` | done | parses `## Query`/`## Answer` and `## Example` sections into user/assistant pairs, appends at most one active vault few-shot within `sub_prompt_char_budget/4`, degrades to the builtin example on anything unrecognised |
| R9 — backend robustness | done | `/v1` normalization (`normalize_endpoint`), guarded extraction raising `ModelBackendError` with status + body snippet, tenacity retry (2 retries, exponential backoff, transport + 5xx only), `headers` for auth |
| R10 — make the truncation promise true | done | `stdout_cap` = `repl_output_char_cap`; stderr truncation is tail-preserving; **and** the root loop now reads every operating value from the `Config` (with overrides) rather than the frozen `Profile`, which is what makes "template text and behaviour agree by construction" actually true |

### Wave 2 — kernel state machine and wiring

R11–R14 were implemented by a delegated agent working only in `src/rlm_kernel/`,
`tests/rlm_kernel/` and `docs/rlm-kernel-manual.md`, then reviewed here.

| Item | Status | What changed |
|---|---|---|
| R11 — GEPA promotion state hazards | done | ordering is now `propose → validate → (pass?) demote → promote`, so a rejected candidate leaves the incumbent ACTIVE (new terminal status `validation_failed`); version lineage from the occupant; kind derived from the incumbent; mutate/eval/restore in `try/finally` under a module-level lock |
| R12 — honest memory layer | done | optional `access_count`/`last_access` on `Frontmatter` (excluded from `content_hash`); `search` re-ranks a 4×k BM25 pool by relevance × decay, exposes `decay`, and records hits through `vault.put`; `forget` combines both filters; dry-run and merge share one clustering function; dead code removed |
| R13 — directory conventions | done | singular `<kind>/` everywhere (`helper/`, `fewshot/`); kernel manual §4.7 documents the convention and ships the one-time migration snippet |
| R14 — index/search defects | done | per-token quoted `OR` query (multi-word recall restored, documented in the manual); delta key includes `id`; the tautological isolation check replaced by index-rows-vs-vault-walk; `MAX_BODY_LENGTH` measured in UTF-8 bytes |

### Wave 3 — security and hygiene

| Item | Status | What changed |
|---|---|---|
| R18 — TLS verification posture | done | non-loopback `https` + `verify=False` emits a `UserWarning`; plain `http` exempt (nothing to verify). `optimize.py` no longer sets `litellm.ssl_verify` process-globally — it passes `ssl_verify` per reflection call |
| R19 — gate validation without execution | done | `validate(..., *, execute=False)` is static by default (AST parse, import allowlist, blocked-pattern scan, plus new static define + signature checks); `rlm-kernel review` prints its mode and only executes with `--execute`; `ValidationReport.executed` reports which mode produced the verdict. Trust model documented in kernel manual §7.3.1 |
| R20 — vault path containment | done | `LocalVault._resolve()` rejects absolute paths, `..` segments and anything resolving outside the root, across `get`/`put`/`delete`/`exists`; `Frontmatter.name` validated against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, so `promote`'s default target cannot escape by construction |
| R21 — web auth completeness | done | auth is a route **dependency** (`require_auth`) so a new route cannot forget it, verified by a route-table test; both SSE endpoints are covered; `vault.html` builds DOM nodes with `textContent` (no `innerHTML` from server data); `secrets.compare_digest`; `POST /login` → 400 when no token is configured; the test-client allowance is behind `RLM_WEB_ALLOW_TESTCLIENT=1`; session cookie gets `Secure` when SSL args are present; `check.html` deleted |
| R22 — logger and surface hygiene | done | `TrajectoryLogger` takes a real lock and defaults to `logs/trajectories/` under the working directory (gitignored) with an explicit `prune()` for retention; web `_jobs`/`_chat_sessions` are 100-entry LRUs |

### Wave 4 — polish, tests, docs

| Item | Status | What changed |
|---|---|---|
| R15 — model_check probes | done | P2 lexer skips the escaped character (`i += 2`); P3's two weaknesses documented in the probe docstring and manual §16.4 with scoring deliberately **not** tightened (owner decision, per plan) |
| R16 — dead code / stale strings | done | no-op `_subcall_loop` removed; unused `import io` gone; inline `__import__('re')` replaced by a top-level import; inert `prompt_vars` keys removed; `cli.py` docstring + stale "not yet implemented" message fixed; `chat.py` empty-context guard added; `search_quarantine` actually returns newest-first; `promote` refuses to overwrite deprecated/superseded occupants without `--force`; htmx removed (loaded but unused); the console's file upload is now ingested; `/check` dead UI deleted; chat handlers honour `RLM_VAULT_ROOT` |
| R23 — `schema` → `schema_version` | **deferred** | per plan: breaking vault-format change, needs dual-parse + migration |
| R24 — test suite debts | done | `load` marker added and applied; the three vacuous tests implemented or split; eval patterns anchored and hyphen/space/case-normalized; `tolerance` removed (nothing read it) — the four `.json` mirrors were invalid JSON and were regenerated; kernel-side coverage added for `repl_bridge`, `seed`, kernel CLI; `test_local_vault_implements_protocol` no longer creates a real `/tmp` vault + git repo |
| R25 — documentation repairs | done (1–5) | `load_template` marked removed; kernel §13.2 records the 2026-07-26 gate numbers; `load-test-report.md` truncations and stray fence repaired; conformance README status updated; needle normalization landed; item 6 (LIVE) — see §5 |

---

## 4. Test suite

```
$ .venv/Scripts/python.exe -m pytest tests/ -k "not slow" -p no:cacheprovider -q
643 passed, 12 deselected, 2 warnings in 245.70s (0:04:05)
```

`655 tests collected`; 12 are marked `slow` (real-model integration and the
load-gate corpus benchmarks) and 5 of those also carry the new `load` marker, so
the README's documented `-k "not slow and not load"` command now means what it
says.

The plan's floor was 271 passing. The surplus is the new remediation tests plus
the previously-uncovered modules now exercised (`repl_bridge`, `seed`, kernel
CLI, disk-backed `Context` paths, `ContextStore.cleanup`, vault-first prompt
loaders, `TrajectoryLogger`, `HTTPModelBackend`).

### Non-vacuity

The project's documented antidote to its three vacuous-test incidents is:
*remove the guard, watch the test go red, restore it.* Every guard added in this
wave was proved that way by a scripted mutation pass:

```
$ .venv/Scripts/python.exe scripts/check_guard_nonvacuity.py
...
checked 30 guards; 0 problem(s)
```

Each entry applies a literal source mutation (the pre-fix code, or a plausible
regression), runs the named test **alone**, and requires it to fail. Coverage by
item:

| Item | Guards proved |
|---|---|
| R1 | init payload carries a reference, not the text; worker reads the spilled file lazily |
| R2 | byte-count `len()`; real byte line index; `chunk()` streams; mid-codepoint addressing raises |
| R3 | cache hit is free even when the budget is exhausted; `cache_hits` counts hits not size |
| R4 | late result not misattributed; second consecutive timeout restarts the worker |
| R5 | `repair_json` wired for schema'd calls; `parse_stderr` called by the loop; `parse()` does not reset the error streak |
| R6 | empty submission is nudged, not finalized; nudge budget is bounded |
| R7 | no unreferenced template constant; timeout error is templated |
| R8 | vault few-shot reaches the prompt; prompt budget enforced |
| R9 / R18 | `/v1` normalization; guarded extraction; retries enabled; non-loopback TLS warning |
| R10 | the enforced cap is the advertised cap |
| R21 | every API route declares auth; missing peer address not trusted; constant-time compare; login without a token is 400 |
| R22 | bounded job store; logger lock; trajectories off the shared temp dir |

**Two mutations initially reported as vacuous were mutation-script bugs, and this
is recorded rather than hidden.** The R2 mutation replaced a line that the decode
loop could not reach (dead code), and the R3 mutation did not actually reorder
anything — both were rewritten to apply the real pre-fix behaviour, after which
both went red. The tests were never vacuous; the first two attempts at breaking
them were.

---

## 5. LIVE validation (model server: `lunacode:9010`)

The plan's LIVE items assume a llama.cpp server reachable at
`https://localhost:9010/v1`. In this environment the server is the llama.cpp
**router** on the `lunacode` host (Tailscale `100.77.158.95`, LAN
`192.168.100.224`), serving `Qwen3.5-4B-Abliterated` (the configured production
model) and switching models on demand. Endpoints were therefore overridden
per run (`--endpoint`, or `load_config(..., root_endpoint=…)`); no source default
was changed to accommodate this.

### 5.1 R10 sanity + R1/R2 at scale — needle-in-a-haystack

```
context: 600093 chars (spill threshold for tiny: 500000)
elapsed: 337.6s
answer: 'ZQ-7741'
VERDICT: PASS
```

Trace from the trajectory log of the passing run:

```
TURN 1/8
  A: ...the context is large (~600k chars), I will use `grep` to find the token directly...
  OUT: [002085] The archive access code is ZQ-7741.
```

This single run exercises R1 (a 600 093-character context spills to disk and the
worker greps the file lazily — the init payload carries a path, not the text), R2
(byte addressing over a half-megabyte file), R10 (the aggressive 2 000-char
stdout cap did not break the loop), and R4/R5/R6 (the loop reached a voluntary
`answer['ready']` submission inside 8 turns, in 338 s).

A companion local reproduction at the same scale (no model) confirms each layer
independently:

```
text chars: 600093 lines: 4168
needle present in python text: True
handle: Context len: 600093
direct ctx.grep: ['[002085] The archive access code is ZQ-7741.']
worker len/hasattr: 600093 | True
worker grep: [002085] The archive access code is ZQ-7741.
worker hits: 1
worker str search: True
```

**Recorded honestly:** the *first* attempt at this run "failed" (answer `'18'`,
1 585 s) and the cause was **the fixture, not the harness** — the generator keyed
needle insertion on a hardcoded line number the fill loop never reached, so the
model was asked to find a needle that was not in the haystack. That is exactly
the failure the run was supposed to detect, so it is recorded rather than
quietly re-run. The generator now asserts the needle is present before starting,
and `tests/test_repl.py::test_needle_in_the_middle_of_a_large_spilled_context`
pins the same shape as a fast test.

### 5.2 R14 load gate — 100K-page index

```
corpus: .tmp_load_corpus
pages found by walk: 100000
[2/4] full reindex…
  100000 pages in 166s  (target < 7200s)
[3/4] search latency…
  'function': 135.7ms (5 hits)        'system': 205.3ms (5 hits)
  'data': 136.3ms (5 hits)            'process': 137.9ms (5 hits)
  'implementation': 141.4ms (5 hits)  'configuration': 122.3ms (5 hits)
  'result': 136.9ms (5 hits)          'value': 135.8ms (5 hits)
  'error': 0.4ms (0 hits)             'test': 0.3ms (0 hits)
  p95 = 176.5ms  (target < 300ms)
[4/4] helper listing…
  index list_paths: 48.3ms (9954 paths)
  vault.list walk:  101.4s (9954 parsed)

=== RESULTS ===
pages=100000 rebuild=166s search_p95=176.5ms helper_idx=48.3ms helper_walk=101.4s
gate: rebuild PASS | search PASS | git NOT MEASURED
```

The gate passes on the two metrics that catch the F14 class of regression
(rebuild time and search latency), and the index-vs-walk contrast is intact:
48 ms through the index versus 101 s to parse the same pages off disk — the
reason the index exists. Recorded 2026-07-26 values on the reference machine
were 582 s / 116.9 ms; rebuild is faster here and latency is the same order,
i.e. nothing became quadratic.

The two zero-hit queries are **not** a regression: the synthetic corpus simply
does not contain those exact FTS tokens (a direct count on the built index gives
`'error'` 0, `'test'` 0, and 84 238 / 85 064 for `'function'` / `'system'`).

The R14 semantics change is visible directly on the same index:

| Query form | Pages matched |
|---|---|
| `"system data"` (old: whole-query phrase) | 10 906 |
| `"system" OR "data"` (new: per-token OR) | **94 393** |
| `"implementation process"` (phrase) | 3 990 |
| `"implementation" OR "process"` (OR) | **95 244** |

**Deviation:** phase 5 of `scripts/run_load_gate_100k.py` (the `git status`
measurement, target < 2000 ms) **could not be measured on this host**. Its
`git add -A` over 100 000 freshly created files never completes: a `git` process
sat at 7 s of CPU for 13 minutes holding `.git/index.lock` with no progress,
because every file operation is interposed by the DSH sandbox's ACL broker (the
same mechanism behind the `0o700` defect in §2). `git status` is not the
regression class this gate exists to catch, so the index phases were re-run
standalone — `scripts/run_load_gate_index_phases.py`, committed so the measurement
is reproducible — and the git number is left as *not measured* rather than
guessed at. The 2026-07-26 value (38 ms) still stands as the last real
measurement.

### 5.3 R25.6 — voluntary `answer['ready']` submission (P4)

```
Model: Qwen3.5-4B-Abliterated
Score: 90.0/100
Verdict: SUITABLE
Probes: 3/3 passed, 0 failed
Time: 1848s

  [P1] Trial 1: PASS — valid ```repl block observed
  [P1] Trial 2: PASS — valid ```repl block observed
  [P1] Trial 3: PASS — valid ```repl block observed
  [P4] answer['ready'] = True found in model output.
  [P4] Final answer submitted: The Pacific Ocean
  [P6] Needle 1: PASS — 'ALPHA-42' found
  [P6] Needle 2: PASS — '1748' found
  [P6] Needle 3: FAIL — expected 'O-Negative', got 'The context does not contain
       a vault access code; it is a medical record for Joh'
```

**P4 = 15/15, voluntarily submitted, not forced** — against the recorded baseline
of **0/15** and the plan's target of ">0/15". The change is the second worked
example added to `FEWSHOT_EXAMPLE` (R25.6): a short probe followed by an
*immediate* submission, with the assistant turn stating explicitly that
submitting means executing code that sets `answer`, and that narrating the answer
in prose leaves the task unfinished. The K4 caveat on record ("promoted text
omitted the `answer['ready']=True` mechanism → P4 0/15, forced finalization
carries production") is thereby addressed at the prompt level.

The TLS warning in §5's stderr is R18 working as designed: the run pointed at a
non-loopback `https` endpoint with verification off.

P6's third needle remains a model-behaviour miss (the model answered a different
question); per the plan, P3/P6 scoring semantics were **not** tightened.

### 5.4 R9 live path — integration tests

```
$ RLM_TEST_ENDPOINT=https://lunacode:9010/v1 \
  RLM_TEST_MODEL=Qwen3.5-4B-Abliterated \
  .venv/Scripts/python.exe -m pytest tests/test_integration.py -m slow -q
3 passed, 1 warning in 8.00s
```

The three slow integration tests (root-tier chat, code-block emission, sub-tier
routing) run against the live server through the R9-normalized endpoint. The
single warning is the intended R18 notice about a non-loopback `https` endpoint
with verification off; the tests still skip cleanly when the endpoint is
unreachable, and they now read `RLM_TEST_ENDPOINT` / `RLM_TEST_MODEL` instead of
pinning the stale, FAIL-rated `LFM2.5-VL-1.6B` model.

---

## 6. Documentation updated in-commit

| Document | Change |
|---|---|
| `README.md` | remote-endpoint guidance (`--endpoint`, `load_config(root_endpoint=…)`); profile table gains the enforced REPL cap and the configured production model; new **Security Posture** section; requirements corrected (`tenacity`, `litellm`, `python-multipart`); testing section rewritten with real counts and marker semantics |
| `docs/extensibility-guide.md` | brought back in line with the code: singular `helper/` paths throughout; the gate's opt-in execution and trust model (**new §5.2.1**); `promote` occupancy/`force` rules; vault path containment and the `name` validator; vault few-shots that actually affect the prompt (**new §2.5**); byte-addressed lazy `context`; the real output caps and `cell_id` correlation; the `call_api`/`handle_api_call` "bindings pattern" explicitly marked **not implemented** with the four RPC verbs that do exist; §8's false "the sandbox blocks `import os` / no network / no secrets" claims replaced with the truth (process boundary, not a sandbox); "deprecated helpers stay callable" and "deprecated pages are excluded from search" both corrected; `search()` documented as returning a string |
| `docs/rlm-local-manual.md` | §4.3 prompt-variable discipline; §5.1/§5.2 backend robustness + TLS posture; §6.1 **the design's restricted builtins were never implemented** — stated plainly; §6.2 cell correlation and the file-reference init; §6.3 lazy `context`; §6.7 the real caps incl. tail-preserving stderr; §6.8 `restart_worker`; §7.5 cache-before-budget; §7.7 `cache_hits`/`cache_size`; §8.4/§8.5 stderr nudge, empty-submission nudge, `is not None` semantics; §8.6 terminal placeholders; §9.2 dead stages; §9.4–§9.7 wiring; §10.2 byte-offset contract; §10.3 spill reaching the worker; §11.2 template table; §11.3.1/§11.4 vault few-shots live; §11.2.1 `load_template` removed (historical); §12 default log location + retention; §15.4/§15.5 the real error-nudge and timeout behaviour; §16.1 test tree; §16.2/§16.2.1 running tests + doc lint; §16.3 integration endpoint env vars; §17 API reference |
| `docs/rlm-kernel-manual.md` | §3.2 frontmatter fields (incl. the `name` charset rule); §4.3 `LocalVault` containment; §4.3.1 path containment; §4.7 directory convention + migration; §5.3 query semantics; §7.3 static-by-default validation; **new §7.3.1 trust model**; §7.4 the real promotion steps (staging, not committing); §7.6 demotion does **not** hide a page from search; §10.1/§10.2 few-shots are live, templates are not; §11 CLI flags; §13.2 recorded gate numbers; §15 API reference corrected to the real signatures |
| `docs/operator-guide.md` | TLS material never committed + per-host generation; **new "TLS Verification Posture"** incl. the web console's trust model; remote-model-server guidance; vault layout (singular kinds) + migration pointer; route table corrected (`/check` removed, upload/chat routes added); `rlm check` no longer claims to persist a report it does not write; `rlm vault review --execute` / `promote --force`; security notes section rewritten (fail-closed auth, escaped rendering, containment, trajectory-log retention) |
| `docs/load-test-report.md` | F2 bullet completed, missing F3/F4/F5/C1 bullets restored, stray trailing fence repaired, Tier-2 duplication trimmed, and a **2026-09-10 re-run section** with the post-remediation numbers and the FTS semantics differential |
| `docs/conformance/README.md` | "K4-real is the next milestone" replaced with the 2026-07-30 completion; timeline extended through 2026-09-10 so the remediation cycle is traceable; status section states the residuals rather than implying there are none |

---

## 7. Deviations, limitations and residual risk

1. **Environment, not project.** The Windows temp-directory defect (§2) and the
   undeletable leftovers are properties of this machine under the DSH sandbox.
   The `sitecustomize.py` shim is in the venv, not the repository, and is
   documented as removable.
2. **The gate is not containment.** R19 makes execution opt-in, but the sandbox
   remains escapable and never calls the helper it defines. §7.3.1 of the kernel
   manual states both limitations, and
   `tests/rlm_kernel/test_gate_execution_policy.py` pins them as *documented*
   behaviour rather than pretending otherwise.
3. **`promote` now validates statically.** A consequence of R19's default: a
   helper page promoted through the CLI is parsed, not executed. This is the
   safer default; an operator who wants the old behaviour must call
   `validate(..., execute=True)` explicitly.
4. **R23 deferred** (frontmatter `schema` field shadowing a pydantic attribute),
   as the plan instructed.
5. **`answer`/`context` are not restored after each cell** (design §5.3). This
   was outside the plan's item list, so it was deliberately *not* implemented
   here — adding it would be an unplanned behaviour change. Noted as a residual.
6. **Worker-internal error strings** (`"Error: invalid regex…"`, `"Error: subcall
   manager not available"`) live inside the sandboxed worker program rather than
   `templates.py`. They predate this wave and were not changed; R4.1's guard test
   covers the harness message layer.
7. **P3's two scoring weaknesses** are documented, not fixed, per the plan
   ("tightening changes score semantics — owner decision, defer").
8. **The REPL is not a sandbox.** Design §5.3's restricted builtins and `open`
   jail were never implemented; the worker runs `exec(code, globals())` with
   normal builtins and inherits the harness environment. The local manual §6.1
   now says so plainly instead of describing the unimplemented design. This is a
   pre-existing gap (it predates this wave), documented rather than silently
   carried.
9. **Deprecated and superseded pages still match search** — `Index.fts_search`
   has no status predicate. The kernel manual §7.6 previously claimed otherwise
   and now states the real behaviour.
8. **The 100K load-gate corpus** written during §5.2 lived in `.tmp_load_corpus/`
   inside the workspace (536 MB, 100 004 files) and was deleted afterwards
   (64 s); `.tmp_*` roots are gitignored. The load-gate phase 5 `git` measurement
   could not run on this host, for the reason recorded in §5.2.

### New tooling committed with this wave

| Script | Why |
|---|---|
| `scripts/check_guard_nonvacuity.py` | makes the "every guard test goes red when its guard is removed" claim reproducible; the mutation table is deliberately literal and point-in-time, and a mutation whose target no longer exists is reported as a problem rather than skipped |
| `scripts/run_load_gate_index_phases.py` | re-runs the load-gate phases that are measurable on a host where the `git` phase stalls (rebuild time, search latency, index-vs-walk) and streams progress instead of buffering it |
| `scripts/check_docs.py` | structural lint over the living docs: unbalanced fences, `§` references that resolve to nothing and name no document, dangling relative links, BOM/U+FFFD damage. Verified falsifiable against a planted instance of each of the five defect classes |

---

## 8. What a reviewer should check first

1. `tests/test_templates.py` — the dead-template guard is the cheapest way to
   see whether R4.1/R7 discipline held.
2. `src/rlm_local/repl.py` — R1/R4/R7/R10 all meet in `start()`, `execute()` and
   `_build_result()`; the worker script's `_FileContext` is the R1 payload.
3. `src/rlm_kernel/gate.py::validate` + `promote` — R19's opt-in execution and
   R16's occupancy guard.
4. `src/rlm_web/app.py::_authorize` + the route-table test — R21 by construction.
5. `tests/test_context_store.py::TestByteOffsetSemantics` — the R2 contract, with
   a property test against `str` ground truth.
6. `scripts/check_guard_nonvacuity.py` + `logs/`-free re-run — the cheapest way
   to re-check the strongest claim in §1.
