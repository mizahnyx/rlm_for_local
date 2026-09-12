# AGENTS.md — working conventions for this repository

Instructions for anyone (agent or human) changing `rlm_for_local`. The project's
own discipline is unusual enough that most of this file is rules that already
exist, written down in one place so they stop being re-derived per session.

**Read first:** `docs/20260912-1155-roadmap.md` (the plan and the open ledger),
`README.md` (what the system is), `docs/operator-guide.md` (how to run it).

---

## 1. Non-negotiable rules

1. **Test first.** A fix or feature lands with the test that fails without it.
2. **Prove every guard non-vacuous.** Remove the guard, watch the *named* test go
   red, restore it. This is mechanised: add an entry to
   `scripts/check_guard_nonvacuity.py` and run it. This project has shipped
   vacuous tests three times, each caught late; the mutation table is the antidote
   and a mutation whose target no longer exists is reported as a problem rather
   than skipped.
3. **Harness-emitted strings live in `templates.py`.** Prompts, nudges and
   harness errors are not inlined at the call site; `tests/test_templates.py`
   enforces it. (Console/CLI output is not governed by this.)
4. **Documents are dated and never rewritten.** New documents under `docs/` are
   named `YYYYMMDD-HHmm-<topic>.md` (creation time). Point-in-time records are
   history: a correction is a *new* document that cites the old one. Living docs
   are listed in `docs/20260912-1155-roadmap.md` §1.
5. **Do not state what you have not verified.** If a number, a behaviour or a
   mechanism is inferred rather than observed, label it inferred. Several of this
   project's best findings came from that habit, and its worst incidents came
   from skipping it.
6. **No scope creep without an owner call.** Scoring semantics, vault-format
   changes, sandbox boundaries and load-test scope are owner decisions. Ask, or
   record the item in the roadmap's ledger as `owner call`.
7. **A score means nothing without its scale.** Any reported model verdict states
   the weight profile, the sampling, and the host state it came from.

## 2. Commands

```bash
# Fast suite — select on MARKERS, never on names
uv run pytest -m "not slow and not load" -q        # ~800 tests, ~4-7 min

# Everything, including tests needing a live model server and the load corpus
uv run pytest -q

# Guard non-vacuity (the whole table, or one item)
uv run python scripts/check_guard_nonvacuity.py
uv run python scripts/check_guard_nonvacuity.py --only R26

# Documentation lint, and the linter's own falsifiability check
uv run python scripts/check_docs.py
uv run python scripts/check_docs.py --self-test

# Model suitability: sweep every model a router offers (sequential by design)
uv run python scripts/assess_router_models.py --screen
uv run python scripts/assess_router_models.py --only <model> --before-each \
    "ssh lunacode systemctl --user restart llama-router.service"

# Re-score a recorded sweep under a different weight profile — no model runs
uv run python scripts/rescore_sweep.py logs/router-model-battery.jsonl \
    --verify-with p1-heavy
```

`-k "not slow and not load"` is **wrong**: `-k` matches a substring of the node
id, so it also drops ~20 tests that merely mention "load" in their name
(`test_ingest_loads_file`, the upload-cap tests) and reports them as "deselected".
Use `-m`. (Measured on 2026-09-11: `-m` 744 passed / 12 deselected vs `-k` 724 /
32.)

## 3. Environment traps (all of these have bitten this repo)

- **Windows + sandbox temp dirs.** `tempfile.mkdtemp` creates `0o700`
  directories, which the sandbox cannot enter, which fails ~271 tests. The fix
  lives in `.venv/Lib/site-packages/sitecustomize.py` (0o700 → 0o750 on Windows)
  and is **not** in the repository — it is environment repair, documented in
  `20260910-0730-remediation-validation.md` §2, and removable.
- **Never use PowerShell text substitution on source files.** `Get-Content … |
  Set-Content -Encoding utf8` mangles non-ASCII (U+FFFD plus a BOM). Use the
  file-editing tools; the doc linter catches the damage after the fact.
- **Scratch roots** are `.tmp_*/`, gitignored. Do not leave scratch anywhere else.
- **`git push` needs an escalated sandbox** in this environment: MSYS `ssh`
  cannot create its signal pipe under the default sandbox (`WinError 5`). The
  denial is expected; retry the same command once with `sandbox_permissions`.
- **PowerShell** does not support `&&`, and multi-line commit messages with
  quotes break `-m`; write the message to a file and use `git commit -F`.
- **Never run `check_guard_nonvacuity.py` while editing source.** It rewrites the
  file, runs one test, and restores the *version it read* — an edit made in that
  window is silently lost.
- **`ssh` to `lunacode`** uses key auth with no persisted host key:
  `ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL lunacode …`.
  PowerShell re-adds CR to piped scripts; pipe remote scripts through
  `tr -d '\r' | bash -s`.
- **Do not hand-pick a mix of `tests/` and `tests/rlm_kernel/` modules in one
  pytest invocation.** A combination like
  `pytest tests/rlm_kernel/test_index.py tests/test_cli.py tests/rlm_kernel/test_repl_bridge.py`
  can fail the *last* module with `fixture 'temp_vault' not found` even though
  every module passes — and every pair of those modules passes — in isolation.
  The documented invocations (`pytest tests/`, `pytest tests/rlm_kernel/`) are
  unaffected and are what the recorded suite numbers come from; select modules
  from one directory, or run the directory.

## 4. The local model server

- The harness talks to any OpenAI-compatible endpoint. On this LAN the model
  server is a llama.cpp **router** on `lunacode:9010` (HTTPS, self-signed,
  `role=router`, `models_autoload=true`), which loads models on demand.
- One export configures every model-facing command:
  `RLM_ENDPOINT`, `RLM_MODEL` (the CLI's `--endpoint` / `--model` do the same per
  invocation). `RLM_TEST_ENDPOINT` / `RLM_TEST_MODEL` gate the integration tests.
- Battery knobs: `RLM_CHECK_WEIGHTS` (`default` | `p1-heavy`) and
  `RLM_CHECK_P4_TRIALS` (1–3; unusable values fall back to 3, deliberately).
- **Host limits are real.** 15 GiB RAM, one model instance at a time in practice,
  ~6.6 tok/s prompt / ~3.0 tok/s decode. A quick battery on a 2–4B model takes
  30–60 min. Restart the router between models (`--before-each`) or the router
  keeps every model it has served resident and the box starts swapping.
- Model behaviour is **not** stable across router cache states: identical prompt,
  `temperature=0.0`, same server produced both voluntary submission and no
  submission minutes apart (`20260911-1359-p4-live-confirmation.md`).

## 5. Repository map

| Path | What it is |
|---|---|
| `src/rlm_local/` | The harness: root loop, parser, subprocess REPL, sub-call manager, context store, model backend, prompts/templates, CLI, model-check battery |
| `src/rlm_kernel/` | The evolvable layer: git-versioned vault, gate, SQLite FTS5 index, memory, GEPA optimizer, seed |
| `src/rlm_web/` | FastAPI console (Jinja2, SSE, session-cookie auth, origin-checked POSTs) |
| `docs/` | Living documents, dated records, `conformance/` history |
| `scripts/` | Guard-mutation table, doc linter, load-gate phases, router sweep, sweep re-scorer, doc self-test |
| `tests/` | Suite; `tests/load/` carries both `slow` and `load` markers |
| `logs/` | Run artifacts (gitignored: `*.jsonl`) |

## 6. The working loop for one item

1. Read the item in the roadmap ledger; if it changes behaviour, confirm it is
   not an owner call (or that the owner has called it).
2. Write the test that must fail without the change.
3. Implement the change.
4. Prove the guard non-vacuous: add a mutation entry, run it, see it red.
5. Update the living docs that describe the behaviour (manuals, operator guide,
   README) — a behaviour change with stale docs is an unfinished change.
6. If the change produced evidence worth keeping (a live run, a measurement, a
   verdict), write a dated record; if it closed a ledger item, update the
   roadmap's status.
7. Run the fast suite, the mutation table, and the doc lint (with `--self-test`).
8. Commit with the reasoning in the message (what was wrong, what changed, what
   was verified, what remains unverified), and push.

## 7. Security posture, in one paragraph

The defaults assume a single trusted user on one machine. The kernel gate is a
*quality* gate, not containment; the REPL is a process boundary, not a sandbox —
model code runs without `eval`/`exec`/`compile`/`globals`/`locals` and the worker
can be memory-bounded, but imports stay permitted and the worker's own names are
reachable through `globals()`, so a cell can still reach the filesystem (roadmap
DG10 covers closing that); TLS verification is off for the local self-signed
server (with a warning); the web console is loopback-only without
`RLM_WEB_TOKEN`, requires `RLM_WEB_SECRET` with one, and checks the origin of
every state-changing request. Run against content and models you trust. Details:
`README.md` §Security Posture and `docs/operator-guide.md` §6.
