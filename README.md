# RLM Local

**A Recursive Language Model harness for small, locally-hosted open-weight models.**

Turns a single 1–8B parameter model running on CPU-only hardware into a
recursive reasoning system that answers questions over contexts 10×–100×
larger than its native context window.

```python
import rlm_local

answer = rlm_local.completion(
    "What year was the Treaty of Westphalia signed?",
    open("european_history.txt").read(),
    profile="laptop",
)
print(answer)  # → "The Treaty of Westphalia was signed in 1648."
```

## How It Works

The root model **never sees your data**. Instead:

1. Your context lives in a sandboxed Python REPL as the variable `context`.
2. The model writes Python code in ` ```repl ` blocks — probing, searching,
   chunking, delegating reading to itself via `llm_query()`.
3. Results accumulate in REPL variables. The model pulls in only what it needs.
4. When ready, it sets `answer["ready"] = True` and the harness returns the result.

This is **context offloading**: the model's conversation stays short and uniform
whether your context is 1,000 or 10,000,000 characters.

## Quick Start

### Prerequisites

- Python ≥ 3.12
- A running [llama.cpp](https://github.com/ggerganov/llama.cpp) server (or
  Ollama, LM Studio, `mlx_lm.server` — anything with `/v1/chat/completions`)

```bash
# Example: start llama-server with the configured production model
llama-server \
    --model Qwen3.5-4B-Abliterated-Q4_K_M.gguf \
    --host 127.0.0.1 --port 9010 \
    --ctx-size 16384 --flash-attn \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --parallel 2
```

The server does not have to be on this machine. Point the harness at another
host (another LAN box, a Tailscale peer, a llama.cpp router) with
`--endpoint` on `rlm check`, or `root_endpoint=` on `completion()`/`load_config`:

```bash
uv run python -m rlm_local.cli check Qwen3.5-4B-Abliterated \
    --endpoint https://lunacode:9010/v1 --quick
```

`--quick` runs P1+P4+P6. Add `--weights p1-heavy` only to reproduce scores
recorded before the 2026-09-11 reweight; see the operator guide §3.

### Install

```bash
git clone git@github.com:mizahnyx/rlm_for_local.git
cd rlm_for_local
uv sync
```

### Use

```python
import rlm_local

# Needle in a haystack — find a fact in a long document
answer = rlm_local.completion(
    "What year was the Treaty of Westphalia signed?",
    long_history_text,
    profile="tiny",   # 8 GB RAM, CPU-only
    max_turns=8,      # override any config value
)
```

## Hardware Profiles

Three shipping profiles — pick the one that fits your machine:

| Profile | RAM | Model Guidance | Max Turns | Sub-call Budget | REPL Output Cap |
|---|---|---|---|---|---|
| `tiny` | 8 GB, CPU | 1.6–4B Q4 | 12 | 30 calls / 1M chars | 2 000 chars |
| `laptop` | 16–32 GB, M-series | 4B Q4_K_M | 15 | 60 calls / 4M chars | 4 000 chars |
| `workstation` | 32+ GB, 6+ GB GPU | 8B Q4_K_M | 20 | 100 calls / 12M chars | 8 000 chars |

All three profiles ship configured for `Qwen3.5-4B-Abliterated` (both tiers); the
"Model Guidance" column is what each tier is sized for. Override any value at
call time: `load_config("tiny", root_model="…", max_turns=8)`.

Two properties of the profile that matter in practice:

- **The REPL cap is real.** `repl_output_char_cap` is both what the system prompt
  advertises *and* the cap the REPL enforces, and stderr truncation keeps the head
  **and** the tail (a traceback's last line is the useful one).
- **The model is never lied to.** Every capacity claim in the system prompt is
  config-injected, and `tests/test_prompts.py::TestPromptVarsDiscipline` asserts
  that the injected variables and the prompt's placeholders agree exactly.

## Architecture

```
User ──► completion(query, context)
              │
              ▼
     ┌────────────────────────────────────────────┐
     │  RootLoop (orchestrator)                   │
     │  ┌──────────┐  ┌──────┐  ┌─────────────┐  │
     │  │ Parser   │  │ REPL │  │ SubcallMgr  │  │
     │  │ guardrail│  │subpr.│  │ budgets,    │  │
     │  │ rescue   │  │socket│  │ memoization │  │
     │  └──────────┘  └──┬───┘  └──────┬──────┘  │
     └───────────────────┼─────────────┼─────────┘
                         │             │
                    ┌────▼────┐   ┌────▼────────┐
                    │ Context │   │ ModelBackend │
                    │ Store   │   │ HTTP /v1/    │
                    │ (disk)  │   │ chat/compile │
                    └─────────┘   └──────┬───────┘
                                         │
                                    ┌────▼────┐
                                    │ llama-  │
                                    │ server  │
                                    │ :9010   │
                                    └─────────┘
```

## rlm-kernel (Evolvable Layer)

The companion `rlm-kernel` package makes the harness **evolvable** — its
prompts, helpers, few-shots, and memory become human-readable pages in a
git-versioned wiki that the system can grow itself. Features include:

- **Vault** — page CRUD with atomic writes and git versioning
- **Index + Search** — SQLite FTS5 with BM25 ranking over all pages
- **REPL Bridge** — vault-defined helpers injected into the REPL namespace;
  `search()` and `propose()` available from REPL code
- **Gate** — quarantine → validate → promote lifecycle for model-authored content
- **Memory** — cross-session notes with decay arithmetic, compaction, and
  pinned core-memory

**K4 offline optimization** uses GEPA (`gepa.optimize_anything`) to evolve
the harness's prompts, templates, and few-shots against verifiable eval
suites. Candidates are routed through the gate with held-out validation
before promotion.

Full documentation: [`docs/rlm-kernel-manual.md`](docs/rlm-kernel-manual.md).

## Frontends

**CLI** — single completions, interactive chat, vault management, model checks:
```bash
uv run python -m rlm_local.cli ask "What color?" --context-file doc.md
uv run python -m rlm_local.cli chat --profile laptop
uv run python -m rlm_local.cli ingest notes/*.md
uv run python -m rlm_local.cli check Qwen3.5-4B-Abliterated
```

**Web UI** — HTTPS/Tailscale-ready console with live SSE progress:
```bash
export RLM_WEB_TOKEN="your-token"
export RLM_WEB_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
uv run python -m rlm_web.app
# Open https://localhost:8778
```

Point the CLI at a model server on another host — a LAN box, a Tailscale peer,
or a llama.cpp router that loads models on demand:

```bash
export RLM_ENDPOINT="https://lunacode:9010/v1"
export RLM_MODEL="Qwen3.5-4B-Abliterated"
uv run python -m rlm_local.cli ask "What color?" --context-file doc.md
```

`--endpoint` / `--model` do the same per invocation. `--model` sets **both** the
root and sub-call tiers, so sub-calls cannot silently run on a different model
than the one you named. To sweep every model a router offers, see
`scripts/assess_router_models.py`.

## Documentation

| Document | Audience |
|---|---|
| [Operator Guide](docs/operator-guide.md) | New users — install, configure, operate |
| [RLM Local Manual](docs/rlm-local-manual.md) | Engineers — architecture, modules, API |
| [RLM Kernel Manual](docs/rlm-kernel-manual.md) | Engineers — vault, gate, memory, optimizer |
| [Extensibility Guide](docs/extensibility-guide.md) | Developers — adding tools, helpers, capabilities |
| [Conformance History](docs/conformance/README.md) | Auditors — review and remediation trail |
| [Load Test Report](docs/load-test-report.md) | Operators — the 100K-page index gate |

## Security Posture

The defaults are tuned for a single-user machine; four of them are worth
knowing before you expose anything:

- **The gate is a quality gate, not containment.** Model-authored helper code is
  parsed and pattern-scanned by default; running it in the restricted-builtin
  sandbox is opt-in (`rlm-kernel review --execute`, `validate(..., execute=True)`)
  and is escapable on CPython. See the kernel manual §7.3.1.
- **The REPL is a process boundary, not a sandbox.** Model code runs with the
  dynamic-execution family removed (`eval`/`exec`/`compile`/`globals`/`locals`,
  overridable with `RLM_REPL_ALLOW_DYNAMIC=1`) and the worker's memory can be
  bounded with `RLM_REPL_MEMORY_MB` where the OS allows it; scaffold names the
  model breaks are restored after each cell. That is hygiene, not containment:
  imports stay permitted, so a cell can still read what the harness user can read.
  Design §5.3's `open` jail is retired as unenforceable rather than claimed.
  Run against content and models you trust.
- **TLS verification is off for local self-signed servers.** A non-loopback
  `https` endpoint with verification off raises a `UserWarning` rather than
  failing silently. Certificates and keys are never committed (`*.pem`,
  `*.crt`, `*.key` are gitignored).
- **The web console is loopback-only without `RLM_WEB_TOKEN`, and requires
  `RLM_WEB_SECRET` when a token is set.** With a token, every route — including
  both SSE streams — requires an authenticated session; auth is a route
  dependency so a new route cannot forget it. Without a session secret the
  server refuses to start, because a guessable signing key would let anyone
  forge an authenticated session and bypass the token.
- **State-changing requests are checked for cross-origin (CSRF) by default.**
  A session cookie rides along on any request a browser makes, so
  authentication alone does not authorise a `POST`. The default policy rejects a
  request that claims another site's origin and allows one that claims none
  (`curl`, a script), which is why automation keeps working;
  `RLM_WEB_ORIGIN_CHECK=strict` requires an origin from everyone. See the
  operator guide §6.

Details and the reasoning: [Operator Guide](docs/operator-guide.md).

## Testing

```bash
# Fast suite: unit + integration stubs, no server, no load-gate benchmarks
uv run pytest -m "not slow and not load" -q

# Everything, including tests that need a running llama-server and the
# load-gate benchmarks (the latter build a 10k-page corpus — expect minutes)
uv run pytest -q
```

800 tests collected: 12 marked `slow` (real llama-server integration + load),
5 of those also marked `load` (corpus benchmarks). Select on the **markers**
(`-m`), not on the names (`-k`): `-k` is a substring match, so `-k "not load"`
also drops 20 tests that merely mention "load" in their name (`test_ingest_loads_file`,
the upload-cap tests) without running them. The integration tests read
`RLM_TEST_ENDPOINT` / `RLM_TEST_MODEL` and skip when the endpoint is
unreachable, defaulting to the configured model in `src/rlm_local/config.py`.

## Requirements

- Python ≥ 3.12
- `httpx`, `pydantic`, `pyyaml`, `tenacity` (retry/backoff)
- `gepa`, `litellm` (offline optimization, K4)
- `fastapi`, `uvicorn`, `jinja2`, `python-multipart` (web console)
- An OpenAI-compatible inference server (llama.cpp, Ollama, LM Studio, MLX)
- Git (for vault versioning)

## License

MIT
