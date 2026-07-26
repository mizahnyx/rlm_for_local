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

**K4 offline optimization (GEPA) is a scaffold — not yet implemented.**
The current `optimize.py` is a placeholder for development use only. Real
GEPA-based text evolution will be implemented after the R1–R6 gate is fully
green. See `docs/conformance/20260725-0838-rlm-kernel-conformity-review-addendum.md` §3
for the full specification.

Full documentation: [`docs/rlm-kernel-manual.md`](docs/rlm-kernel-manual.md).

## Design Principles

Built on [Zhang & Khattab's RLM research](https://alexzhang13.github.io/blog/2026/harness/):

- **Locally in-distribution observations** — every individual LM call handles a
  prompt within its training distribution, even when the overall task is OOD.
- **Context offloading** — the root model never sees raw context; it lives in the
  REPL and on disk.
- **Programmatic sub-agent calling** — sub-calls are plain Python functions
  (`llm_query`), not JSON tool-calling protocols.
- **Equivalence classes over tasks** — structurally similar tasks produce
  near-identical harness trajectories, enabling compositional generalization.
- **Depth-1 recursion** — root orchestrator + flat sub-calls; deeper recursion is
  rarely needed and doubles latency on one server.

## Documentation

The full manual — architecture, every module, every config knob, failure modes,
and API reference — is at [`docs/rlm-local-manual.md`](docs/rlm-local-manual.md).

## Testing

```bash
# Unit tests (fast, no server needed)
uv run pytest tests/ -k "not slow" -v

# Full suite including integration (requires running llama-server)
uv run pytest tests/ -v
```

72 unit tests, 3 integration tests against a live server.

## Requirements

- Python ≥ 3.12
- `httpx` (HTTP client)
- `setuptools` (build)
- An OpenAI-compatible inference server (llama.cpp, Ollama, LM Studio, MLX)

## License

MIT
