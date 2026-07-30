# Implementation Instructions: CLI Frontend, Web Frontend (HTTPS/Tailscale), Model-Suitability Tool, and Operator Documentation

**Date:** 2026-07-30 (10:02)
**For:** the implementation agent working on `~/Documents/Misc/rlm_for_local/`
**From:** project owner, via Kimi Code CLI
**Context you should read first (all in `docs/`):** `20260721-1034-rlm-harness-small-models-design.md` (harness design), `20260724-1736-rlm-kernel-evolvable-spec.md` (kernel spec), `20260726-2115-k4-acceptance-run-guide.md` (how runs are operated), `k4-first-run-report.md` (current system state), and the two manuals (`rlm-local-manual.md`, `rlm-kernel-manual.md`).

**Owner-confirmed scope decisions (2026-07-30):** web frontend v1 = console + read-only vault search (no wiki editing); CLI = subcommands **plus** an interactive `chat` mode. Both frontends must ingest Markdown documents directly.

---

## 0. Standing constraints (all deliverables)

- **TDD per repo convention:** tests first; every feature lands with its tests. No real LLM, network, or clock in tests (stub backends / `FakeModelServer` patterns already in the suite).
- **Backward compatibility:** `rlm_local.completion()` behavior and the existing `rlm-kernel` CLI must remain unchanged in behavior. New work is additive.
- **Dependencies:** prefer stdlib. CLI: no new deps. Web: `fastapi`, `uvicorn`, `jinja2` (+ `python-multipart` for uploads) are approved; htmx from a local static file, **no CDN, no JS build step**. Model-check: no new deps.
- **Windows-first:** everything must work on this Windows box (Git Bash, cp1252 pitfalls — write files with explicit `encoding="utf-8"` everywhere; no `tee` reliance for observability).
- **Security:** the web frontend is internet-adjacent (Tailscale). Token auth on every route, no debug mode in production, upload size caps, server-escaped rendering, no shell-out with user input.

---

## D1 — CLI frontend (`rlm` command group)

**New module:** `src/rlm_local/cli.py` (entry point `rlm`). Keep `rlm_kernel/cli.py` (`rlm-kernel`) as-is; the `rlm` CLI delegates vault operations to it or to `rlm_kernel` APIs — do not duplicate vault logic.

### Commands

- `rlm ask "QUERY" [--context-file FILE.md ...] [--context-dir DIR] [--stdin] [--vault] [--profile tiny|laptop|workstation] [--max-turns N] [--log-path FILE]`
  Runs `rlm_local.completion()`. Context assembly (in order): one or more `--context-file` Markdown documents (concatenated with `\n\n---\n\n` separators and a `# <filename>` heading per file), `--context-dir` (all `*.md` sorted), `--stdin`, and/or `--vault` (use an existing vault page path as context, e.g. `--vault sources/foo.md`). Prints the answer to stdout; `--log-path` writes the trajectory JSONL. Exit codes: 0 answer produced, 2 no answer/error.
- `rlm chat [--profile ...] [--vault-path PATH]`
  Interactive loop (stdlib `input()`, no prompt-toolkit dep). Slash commands:
  - `/ask <query>` — completion using the session's accumulated context (empty by default)
  - `/ingest <path...>` — load `.md` files into the session context (and print their char counts)
  - `/context` — show what's loaded (file list, total chars)
  - `/clear` — drop session context
  - `/search <query>` — hybrid search over the vault (compact cards)
  - `/get <path>` — print a vault page
  - `/note <text>` — `memory.add` style: create a note page in the vault (via `rlm_kernel.memory.MemoryManager.add`, regex path — no model call)
  - `/check <model-id>` — run the suitability battery (D3) on the served model
  - `/quit`
  Non-slash input is treated as `/ask <input>`. Print a one-line banner with the active profile/model at start. Handle Ctrl+C/Ctrl+D cleanly (leave the REPL sandbox shut down).
- `rlm ingest <path...> [--kind note|source] [--tags a,b]`
  Ingest Markdown documents **into the vault as pages** (permanent): one page per document (slugified name, title from first heading, `kind` as given, tags), then reindex (`rlm_kernel.index.reindex_delta`). Print per-file page paths. Idempotent by content hash: re-ingesting the same file reports `skipped (duplicate)` instead of duplicating.
- `rlm search "QUERY" [--kind K] [-k N]` — vault hybrid search, compact cards.
- `rlm get <path>` — print a vault page (frontmatter + body).
- `rlm check <model-id> [--endpoint URL] [--quick]` — the suitability battery (D3).
- `rlm vault init|index|review|promote|demote` — thin pass-through to `rlm-kernel` CLI.
- `rlm optimize ...` — existing optimizer entry (unchanged).

### Tests (D1)

argparse flows for every subcommand against a stub `ModelBackend` + tmp vault; context assembly order and separators; `ingest` idempotency (same file twice → one page); `chat` loop scripted via `input` monkeypatching (each slash command); exit codes; UTF-8 handling end-to-end on Windows.

**Acceptance D1:** every command above works against a stub backend in tests and, manually verified once, against the live server: `rlm ask` on a Markdown file returns an answer; `rlm ingest` creates searchable pages; `rlm chat` completes one full `/ingest` → `/ask` cycle.

---

## D2 — Web frontend (console + vault search, HTTPS, Tailscale-reachable)

**New package:** `src/rlm_web/` (FastAPI app, `uvicorn` entry: `rlm-web` script or `python -m rlm_web`). Server-rendered Jinja2 + htmx (static vendored file), no build step, all assets local.

### Pages/routes

1. **Console** (`GET /`): query form — textarea for the query, **Markdown ingestion** via (a) multi-file upload `.md` (cap 20 files / 8 MB total), (b) paste-in textarea, (c) vault-page picker (search-select an existing page as context). Submit → creates a job, redirects to the job page.
2. **Job view** (`GET /jobs/{id}`): live progress via **SSE** (`/jobs/{id}/events`) driven by the trajectory log: each turn's parse/repl/nudge/final events as they land; final answer rendered when done; cancel button (`POST /jobs/{id}/cancel` → sets a cancel flag the loop checks between turns; if true, `RootLoop` should be stoppable via its shutdown path — if clean cancellation isn't feasible, document the limitation instead of hacking it).
3. **Answer** rendered in the job view (Markdown → HTML, escaped).
4. **Vault search** (`GET /vault?q=`): read-only hybrid search cards; `GET /vault/page/{path}` renders a page read-only.
5. **Model check** (`GET /check` form + `POST /check` to run): runs the D3 battery against the configured model, renders the score report (also persisted — see D3).
6. **Docs** (`GET /docs/{name}`): serves selected Markdown docs from `docs/` rendered to HTML (operator guide, manuals).

### Job runner

One background thread (default; configurable to 2). Job record = JSON on disk under `data/jobs/<id>/` (state, query, context files, trajectory.jsonl, result). Survives page refresh and server restart (resumable listing, not resumable execution). Run completions via the existing `rlm_local.completion(..., log_path=...)` so `TrajectoryLogger` gives the event stream for SSE for free.

### HTTPS (two paths, implement both, choose at runtime)

1. **Preferred — `tailscale cert`:** if the tailnet has MagicDNS + HTTPS enabled, `tailscale cert <machine>.<tailnet>.ts.net` yields a real Let's Encrypt cert (no phone warnings). Detect by running `tailscale cert` once; if it succeeds, use it and document the one-liner.
2. **Fallback — self-signed:** generate with `openssl` (present in Git for Windows) — SAN entries for the Tailscale hostname **and** the `100.x.y.z` Tailscale IP; ship a small script `scripts/make_selfsigned_cert.sh`; document installing the cert on Android/iOS (or the accept-risk path). Uvicorn: `--ssl-keyfile`/`--ssl-certfile`.

**Binding/auth:** bind `0.0.0.0` (Tailscale interface is the only reachable one in practice; document optional `--host 100.x.y.z`). Auth: a bearer token configured via env var `RLM_WEB_TOKEN`; login form sets an HttpOnly signed cookie (itsdangerous via Starlette sessions); every route requires it; logout route. No token → 401 everywhere except `/login`.

### Tests (D2)

FastAPI `TestClient`: login flow + 401s; console submit creates job and job page streams SSE events from a stub-backed completion (scripted `TrajectoryLogger`); upload cap enforced; vault search renders; context from vault-page picker assembles correctly; XSS probe in a page body renders escaped; Windows UTF-8 safe.

**Acceptance D2:** from a phone on Tailscale: open `https://<host>:<port>`, log in, submit a query with an uploaded `.md`, watch turns stream live, read the final answer; search the vault. On desktop: `pytest` suite green including the SSE flow.

---

## D3 — Model suitability tool (`rlm check`)

**Purpose:** given a model served on an endpoint, decide with a **confidence score** whether it's suitable as root tier, sub tier, or unsuitable for this harness. This operationalizes the design spec's §10.1 conformance suite, calibrated by our real history:

- `LFM2.5-VL-1.6B` → **FAIL** (few-shot imitation, malformed helper calls, no recovery)
- `LFM2.5-8B-A1B-Uncensored` → **FAIL** (JSON-prose `{"content":..., "ready":true}` instead of executing the answer dict; slow)
- `Qwen3.5-4B-Abliterated` → **PASS** (80% baseline, correct loop, smart quotes frequent but normalized)

### Probe battery (P1–P9)

Each probe runs N small scripted completions against the target endpoint and records structured outcomes (implement in `src/rlm_local/model_check.py`):

| Probe | What it measures | Score weight |
|---|---|---|
| P1 Protocol emission | Emits a valid ` ```repl ` block on turn 1 (3 trials) | 20 |
| P2 Helper-call correctness | Calls `peek/grep/chunk` with valid arguments (no `multiple values for` errors) | 15 |
| P3 stderr recovery | After a deliberately broken block (bad regex), fixes and continues | 15 |
| P4 answer-dict submission | Sets `answer["content"]`+`ready` within budget (vs forced finalization) | 15 |
| P5 Format discipline | No bare JSON/prose answers outside code (the 8B failure mode) | 10 |
| P6 Needle accuracy | 3 needle tasks answered correctly (verifiable) | 15 |
| P7 Smart-quote rate | Share of blocks needing normalization (informational — degrades score mildly, not a fail) | 5 (deduction only) |
| P8 Sub-call usage | Uses `llm_query` appropriately when needed (not 0, not runaway) | 5 |
| P9 Speed | tok/s + per-completion wall time vs thresholds (informational band) | report only |

**Score:** 0–100 weighted. **Verdicts:** `≥75 SUITABLE (root tier)`, `50–74 MARGINAL (sub-tier / assisted)`, `<50 NOT SUITABLE`. Report includes per-probe evidence (the actual model outputs, trimmed), so failures are explainable like the three calibration cases above.

**Output:** console summary + persisted report `docs/model-checks/<model-id>-<timestamp>.md` (frontmatter: model, endpoint, score, verdict, date; body: per-probe evidence). `rlm check <id> --quick` runs P1+P4+P6 only.

### Tests (D3)

Scripted stub backends: a "good model" (valid blocks, correct args, answers needles) scores ≥75; a "bad model" (smart quotes everywhere, JSON-prose answers, no recovery) scores <50 with the expected per-probe failures recorded; score math unit tests; report file written with frontmatter.

**Acceptance D3:** `rlm check Qwen3.5-4B-Abliterated --quick` on the live server returns SUITABLE with evidence; the two known-fail historical models would score <50 (validate with scripted fixtures replicating their recorded failure modes).

---

## D4 — Documentation (comprehensive quality)

**New:** `docs/operator-guide.md` — the single entry point. Chapters:

1. **What this system is** (RLM in 5 paragraphs: root model, REPL, sub-calls, vault, gate — with the architecture diagram from the design doc).
2. **Quickstart** (server up → vault init → first `rlm ask` → first `rlm chat` → first web login).
3. **CLI reference** (every D1 command with examples).
4. **Web UI reference** (pages, job lifecycle, HTTPS setup **both paths**, phone setup on Tailscale).
5. **Model management** — the three required procedures, step-by-step:
   - **Initialize a vault for a new model:** `rlm vault init`, run `rlm check <model>`, update `config.py` model ids (or profile), baseline sanity eval, first query.
   - **Repurpose an existing vault for a new model:** vault content is model-agnostic (nothing to migrate); required steps = suitability check on the new model, config update, **evaluate the promoted/evolved prompt pages against the new model** (`evaluate_candidate` on `how-to-work`) and — per the AppWorld negative-transfer lesson — if degraded, restore the seed (`git` in the vault) and re-optimize rather than trusting cross-model transfer.
   - **Grade a model's suitability:** run the full battery, read the verdict bands, how to place a model at root vs sub tier, re-check cadence (on model upgrade, on prompt change).
6. **Security notes** (token handling, Tailscope scope, self-signed caveats, no debug in prod).
7. **Troubleshooting** (from our real history: cp1252 errors, smart quotes, model swaps/timeouts, slow runs, power-loss recovery via checkpoints).
8. **FAQ** (10–15 real Qs: where does the vault live, how do I back it up, why did my model fail the check, can I use model X…).

**Update:** `README.md` (frontends section + quickstart links), `rlm-local-manual.md` and `rlm-kernel-manual.md` (link to the operator guide). Style: task-oriented, every procedure numbered and copy-pasteable, no filler.

**Acceptance D4:** a new user can go from clone to first answer using only the operator guide (verify by following it yourself on a fresh vault); all links resolve; the three procedures work as written.

---

## Build order (milestones, each independently shippable)

| MS | Deliverable | Gate |
|---|---|---|
| M1 | D1 CLI (subcommands) + tests | acceptance D1 commands green |
| M2 | D3 model-check + tests + live check of Qwen3.5-4B | acceptance D3 |
| M3 | D1 chat mode + ingestion-to-vault + tests | acceptance D1 chat cycle |
| M4 | D2 web (no HTTPS) + tests | acceptance D2 desktop flow |
| M5 | D2 HTTPS both paths + phone verification + D4 docs | acceptance D2 phone + D4 walkthrough |

Report per milestone with suite status. **Do not** expand scope: no wiki editing in web v1, no MCP, no new model training.

## Cross-cutting: Markdown ingestion semantics (both frontends)

Two distinct flows, both required:
- **As completion context (ephemeral):** files/paste → concatenated into the query's context for this run only (CLI `--context-file`, web upload/paste). Never touches the vault.
- **Into the vault (permanent):** `rlm ingest` / web "Save to vault" action → page per document (slug, title from first heading, tags), reindexed, searchable. Idempotent by content hash.

Both flows must handle UTF-8, CRLF, files without frontmatter (treat as plain body; synthesize minimal frontmatter when creating vault pages), and files up to 1 MB each.
