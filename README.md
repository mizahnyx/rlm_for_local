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
# Example: start llama-server with a 4B model
llama-server \
    --model qwen3-4b-instruct-2507-q4_k_m.gguf \
    --host 127.0.0.1 --port 9010 \
    --ctx-size 16384 --flash-attn \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --parallel 2
```

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

| Profile | RAM | Model Guidance | Max Turns | Sub-call Budget |
|---|---|---|---|---|
| `tiny` | 8 GB, CPU | Phi-4-mini / 1.6B Q4 | 12 | 30 calls / 1M chars |
| `laptop` | 16–32 GB, M-series | 4B Q4_K_M | 15 | 60 calls / 4M chars |
| `workstation` | 32+ GB, 6+ GB GPU | 8B Q4_K_M | 20 | 100 calls / 12M chars |

All capacity claims in the system prompt are **config-injected** — the model is
never lied to about what it can handle.

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
uv run python -m rlm_web.app
# Open https://localhost:8778
```

## Documentation

| Document | Audience |
|---|---|
| [Operator Guide](docs/operator-guide.md) | New users — install, configure, operate |
| [RLM Local Manual](docs/rlm-local-manual.md) | Engineers — architecture, modules, API |
| [RLM Kernel Manual](docs/rlm-kernel-manual.md) | Engineers — vault, gate, memory, optimizer |
| [Extensibility Guide](docs/extensibility-guide.md) | Developers — adding tools, helpers, capabilities |
| [Conformance History](docs/conformance/README.md) | Auditors — review and remediation trail |

## Testing

```bash
# Fast suite: unit + integration stubs, no server, no load-gate benchmarks
uv run pytest tests/ -k "not slow and not load" -q

# Everything, including tests that need a running llama-server and the
# load-gate benchmarks (the latter build a 10k-page corpus — expect minutes)
uv run pytest tests/ -q
```

652 tests collected: 12 marked `slow` (real llama-server integration + load),
5 of those also marked `load` (corpus benchmarks). The integration tests read
`RLM_TEST_ENDPOINT` / `RLM_TEST_MODEL` and skip when the endpoint is
unreachable, defaulting to the configured model in `src/rlm_local/config.py`.

## Requirements

- Python ≥ 3.12
- `httpx`, `pydantic`, `pyyaml`, `gepa`, `fastapi`, `uvicorn`, `jinja2`
- An OpenAI-compatible inference server (llama.cpp, Ollama, LM Studio, MLX)
- Git (for vault versioning)

## License

MIT
