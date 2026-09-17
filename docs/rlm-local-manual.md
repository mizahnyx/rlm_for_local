# RLM Local: A Recursive Language Model Harness for Small Models

**Comprehensive System Documentation**  
**Version 0.1.0 | July 2026**  
**Updated for rlm-kernel 0.1.0 integration**

---

## Foreword

Modern frontier language models solve complex reasoning tasks by scaling parameters and
compute. But what if you are running on a laptop with no GPU? What if your model is a
1–8 billion parameter quantization that can barely follow a three-step instruction?

*RLM Local* is the answer. It is a fully self-contained harness that wraps any
OpenAI-compatible local inference server—llama.cpp, Ollama, LM Studio, MLX—and
transforms a single small model into a **recursive reasoning system** capable of
answering questions over contexts 10×, 50×, or 100× larger than the model's own
context window.

When paired with the companion `rlm-kernel` package, the harness gains
**evolvability**: its prompts, helpers, few-shots, and memory become
human-readable pages in a versioned wiki that the system can grow itself.

This book documents every module, every configuration knob, every design decision,
and every failure mode. It is written for the engineer who needs to understand *why*
a thing works, not just *that* it works.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start](#2-quick-start)
3. [The `completion()` API](#3-the-completion-api)
4. [Configuration System](#4-configuration-system)
5. [Model Backend](#5-model-backend)
6. [The REPL Sandbox](#6-the-repl-sandbox)
7. [Subcall Manager](#7-subcall-manager)
8. [The Root Loop](#8-the-root-loop)
9. [Parser and Guardrails](#9-parser-and-guardrails)
10. [Context Store](#10-context-store)
11. [Prompts and Templates](#11-prompts-and-templates)
12. [Trajectory Logger](#12-trajectory-logger)
13. [Hardware Profiles](#13-hardware-profiles)
14. [Kernel Integration](#14-kernel-integration)
15. [Failure Modes and Mitigations](#15-failure-modes-and-mitigations)
16. [Testing and Conformance](#16-testing-and-conformance)
17. [API Reference](#17-api-reference)

---

## 1. Architecture Overview

The RLM harness wraps a single local model and presents it as a
**drop-in `completion()` function** — one call, one answer. Everything else is internal.

### 1.1 The Big Picture

```
User ──► completion(query, context, kernel_bridge=...)
              │
              ▼
     ┌──────────────────────────────────────────────────┐
     │                   RootLoop                       │
     │                                                  │
     │  messages = [system + few-shot + metadata]       │
     │            + [core-memory summary]  ← kernel     │
     │                                                  │
     │  for turn in 1..max_turns:                       │
     │     text  = ModelBackend.chat(messages)   ◄──── HTTP ──── llama-server
     │     block = Parser.extract(text)                 │            :9010
     │     result = REPL.execute(block)          ◄── socket ── subprocess
     │     if result.final_answer: return it            │
     │     messages += [assistant, REPL output]         │
     │                                                  │
     │  return ForcedFinalizer(messages)                │
     └──────────────────────────────────────────────────┘
              │                        │
              ▼                        ▼
     TrajectoryLogger (JSONL)    KernelBridge (vault)
```

### 1.2 The Core Idea

The root model **never sees the user's context data**. Instead:

1. The context lives in a Python REPL as the variable `context`.
2. The root model writes Python code in fenced ` ```repl ` blocks.
3. That code can inspect the context (`peek()`, `grep()`), split it (`chunk()`),
   delegate reading to sub-calls (`llm_query()`), and aggregate results.
4. When the model is ready, it sets `answer["ready"] = True` from inside the REPL.
5. The harness extracts `answer["content"]` and returns it.

This is **context offloading**: the root model's conversation history stays short
and uniform regardless of context size. Two different 10-million-character tasks
look structurally identical to the root model — a property called
**equivalence class induction** that is the theoretical foundation of RLMs.

### 1.3 Module Dependency Graph

```
__init__.py  (public API)
    └── root_loop.py  (orchestrator)
         ├── config.py        (profiles, overrides)
         ├── model_backend.py (HTTP chat completions)
         ├── prompts.py       (system prompt, few-shots)
         │   └── templates.py (frozen harness messages)
         ├── repl.py          (subprocess sandbox)
         │   └── context_store.py (disk-backed context)
         ├── subcall_manager.py   (budgets, concurrency)
         ├── parser.py            (rescue parsing, nudges)
         └── logger.py            (JSONL trajectory)

rlm_kernel (optional integration layer)
    ├── schema.py         → Page/Frontmatter models
    ├── vault.py          → Page CRUD, git versioning
    ├── index.py          → SQLite + FTS5 search index
    ├── search.py         → BM25 search with card formatting
    ├── repl_bridge.py    → Helper injection, search/propose proxy
    ├── seed.py           → First-run vault initialization
    ├── gate.py           → Quarantine → validate → promote lifecycle
    ├── memory.py         → Notes, decay, compaction, core memory
    ├── optimize.py       → GEPA text evolution for prompts
    └── cli.py            → Command-line interface
```

---

## 2. Quick Start

### 2.1 Installation

```bash
git clone <repo> rlm-local
cd rlm-local
uv sync
```

Dependencies: Python ≥ 3.12, `httpx`, `setuptools`, `pydantic`, `pyyaml`,
`python-ulid`. The harness is pure Python with no native extensions.

### 2.2 Prerequisites

A running OpenAI-compatible inference server. The reference target is
**llama.cpp's `llama-server`**, but Ollama, LM Studio, and `mlx_lm.server` all work.

```bash
# Example: llama-server with a 4B model
llama-server \
    --model qwen3-4b-instruct-2507-q4_k_m.gguf \
    --host 127.0.0.1 --port 9010 \
    --ctx-size 16384 \
    --flash-attn \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --parallel 2
```

### 2.3 First Call

```python
import rlm_local

answer = rlm_local.completion(
    "What year was the Treaty of Westphalia signed?",
    "The Thirty Years' War (1618-1648) was one of the most destructive "
    "conflicts in European history. The Peace of Westphalia, signed in "
    "1648, established the principle of cuius regio, eius religio...",
    profile="laptop",
)

print(answer)
# → "The Treaty of Westphalia was signed in 1648."
```

That is the entire public API. One function, two required arguments, and a handful
of optional knobs.

### 2.4 First Call with Kernel Integration

```python
import rlm_local
from rlm_kernel.repl_bridge import KernelBridge
from rlm_kernel.vault import LocalVault
from pathlib import Path

# Open a seeded vault
vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")
bridge = KernelBridge(vault, vault.root / ".index" / "meta.sqlite")

answer = rlm_local.completion(
    "What color is mentioned?",
    "The sky was bright blue with scattered white clouds.",
    profile="laptop",
    kernel_bridge=bridge,
)
```

With a kernel bridge, the REPL gains vault-defined helpers and `search()`/`propose()`
capabilities. The system prompt is loaded from contract pages in the vault.

### 2.5 What Happens Internally

When you call `completion()`:

1. **Profile loaded.** The `"laptop"` profile sets context windows, budgets, turn
   limits, and timeouts.
2. **Context ingested.** The raw text is wrapped in a `Context` handle and made
   available in the REPL as `context`.
3. **Messages built.** The system prompt, task metadata (query + context *size*,
   never the content itself), decomposition prologue, and a few-shot example are
   assembled into a byte-stable message list.
4. **Kernel integration (if bridge provided).** Helper definitions are collected
   from the vault. Core-memory summary is injected into metadata. The system
   prompt is loaded vault-first with package-bundled fallback.
5. **REPL started.** A subprocess Python interpreter is launched, connected over
   a local TCP socket. Vault helpers are injected into the worker namespace.
6. **Turn loop begins.** The root model receives the messages, emits code in
   ` ```repl ` blocks, the harness executes that code in the REPL, and the output
   is appended to the conversation.
7. **Termination.** The model sets `answer["ready"] = True`, writes a `FINAL:` line,
   or exhausts its turn budget (triggering forced finalization).
8. **Cleanup.** REPL subprocess killed, context files removed, HTTP client closed.
9. **Answer returned.** A plain string.

---

## 3. The `completion()` API

### 3.1 Signature

```python
def completion(
    query: str,
    context: str | list[str],
    *,
    profile: str = "laptop",
    backend: ModelBackend | None = None,
    config: Config | None = None,
    logger: TrajectoryLogger | None = None,
    log_path: str | None = None,
    kernel_bridge: Any = None,
    **overrides: Any,
) -> str:
```

### 3.2 Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | `str` | Yes | The question or task. Placed in the root model's first user message. |
| `context` | `str \| list[str]` | Yes | The data to answer from. **Never seen by the root model directly.** Available in the REPL as `context`. Lists are joined with newlines. |
| `profile` | `str` | No | Hardware profile: `"tiny"`, `"laptop"`, or `"workstation"`. Default: `"laptop"`. |
| `backend` | `ModelBackend` | No | Pre-configured backend. Created automatically from config if omitted. |
| `config` | `Config` | No | Pre-built `Config` object. Created from `profile` if omitted. |
| `logger` | `TrajectoryLogger` | No | Pre-configured logger. Created if `log_path` is given. |
| `log_path` | `str` | No | Filesystem path for the JSONL trajectory log. |
| `kernel_bridge` | `KernelBridge` | No | Optional `rlm_kernel.repl_bridge.KernelBridge` for vault-aware operation. When provided: vault helpers are injected into the REPL, system prompts are loaded from contract pages, core-memory is included in metadata, and `search()`/`propose()` become available in the REPL. |
| `**overrides` | `Any` | No | Individual config value overrides (e.g., `max_turns=10`, `cell_timeout=90.0`). |

### 3.3 Return Value

A plain `str` — the final answer. Never `None`; if all else fails, forced
finalization produces a best-effort response (possibly empty, but never `None`).

### 3.4 Examples

```python
# Minimal: profile defaults, no logging
answer = rlm_local.completion("Summarize this.", long_text)

# Explicit profile, turn limit override
answer = rlm_local.completion(
    "Find all dates.", corpus, profile="tiny", max_turns=6
)

# With trajectory logging
answer = rlm_local.completion(
    "Extract email addresses.", contact_list,
    profile="workstation", log_path="/tmp/traj.jsonl"
)

# Custom backend (e.g., different server for sub-calls)
from rlm_local import HTTPModelBackend
be = HTTPModelBackend(
    root_endpoint="https://localhost:9010/v1",
    sub_endpoint="https://localhost:9011/v1",
    root_model="qwen3-8b", sub_model="qwen3-4b",
)
answer = rlm_local.completion(query, ctx, backend=be)

# With kernel integration — vault-aware execution
from rlm_kernel.repl_bridge import KernelBridge
from rlm_kernel.vault import LocalVault
vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")
bridge = KernelBridge(vault, vault.root / ".index" / "meta.sqlite")
answer = rlm_local.completion(query, ctx, kernel_bridge=bridge)
```

---

## 4. Configuration System

### 4.1 The `Config` Object

`Config` is the central configuration object. It layers three sources:

1. **Profile defaults** — frozen dataclass values from `PROFILES`.
2. **TOML file overrides** — optional `config.toml`.
3. **Keyword overrides** — passed to `load_config()` or `completion()`.

Resolution order: profile defaults → TOML file → keyword overrides.

```python
from rlm_local.config import Config, load_config

# Profile only
config = load_config("tiny")

# Profile + overrides
config = load_config("laptop", max_turns=10, cell_timeout=90.0)

# Profile + TOML file
config = load_config("workstation", config_path="./my_config.toml")
```

### 4.2 Configuration Keys

Every key in a `Profile` is accessible as an attribute on `Config` and overridable.
The complete reference:

| Key | Type | `tiny` | `laptop` | `workstation` | Description |
|---|---|---|---|---|---|
| `name` | `str` | `"tiny"` | `"laptop"` | `"workstation"` | Profile identifier |
| `root_model` | `str` | model ID | model ID | model ID | Model name sent to server for root tier |
| `sub_model` | `str` | model ID | model ID | model ID | Model name for sub-call tier |
| `root_endpoint` | `str` | URL | URL | URL | Base URL for root model (`/v1` appended) |
| `sub_endpoint` | `str` | `""` | `""` | `""` | Base URL for sub model (empty = same as root) |
| `root_ctx_size` | `int` | 8192 | 16384 | 32768 | Root model context window (tokens) |
| `sub_ctx_size` | `int` | 8192 | 16384 | 32768 | Sub model context window (tokens) |
| `sub_prompt_char_budget` | `int` | 8000 | 16000 | 24000 | Recommended max chars per sub-call prompt |
| `repl_output_char_cap` | `int` | 2000 | 4000 | 8000 | Max chars of REPL output shown to root |
| `max_turns` | `int` | 12 | 15 | 20 | Maximum root model turns per completion |
| `max_concurrent_subcalls` | `int` | 1 | 2 | 4 | Concurrent sub-call limit |
| `max_subcalls` | `int` | 30 | 60 | 100 | Hard limit on total sub-calls |
| `max_subcall_chars` | `int` | 1M | 4M | 12M | Hard limit on total sub-call prompt characters |
| `max_depth` | `int` | 1 | 1 | 1 | Recursion depth (1 = root + flat sub-calls) |
| `cell_timeout` | `float` | 60.0 | 60.0 | 120.0 | Per-REPL-cell wall-clock timeout (seconds) |
| `context_spill_threshold` | `int` | 500K | 1M | 1M | Context size above which disk spill activates |
| `max_consecutive_errors` | `int` | 3 | 3 | 3 | stderr errors before forced finalization |
| `max_consecutive_nudges` | `int` | 2 | 2 | 2 | Parse nudges before counting as error |
| `shortcut_warn_fraction` | `float` | 0.60 | 0.60 | 0.60 | Sub-call/context ratio triggering decomposition warning |

### 4.3 Prompt Variables

`Config.prompt_vars()` returns a dict of values injected into the system prompt at
`str.format()` time. This ensures capacity claims are always truthful:

```python
pv = config.prompt_vars()
# {
#     "repl_cap": 4000,          # repl_output_char_cap
#     "sub_budget": 16000,       # sub_prompt_char_budget
#     "max_turns": 15,           # max_turns
#     "example_chunking_idiom": "...",
# }
```

The dict is **exactly** the set of placeholders the system prompt consumes.
`tests/test_prompts.py::TestPromptVarsDiscipline` asserts set equality in both
directions: an inert key is a capacity claim the model never sees, and a
placeholder with no key is a `KeyError` at run time. `root_ctx_size` /
`sub_ctx_size` were inert and were removed (R16).

Note that the root loop reads *operating* values (caps, turn limits, budgets,
thresholds) through the `Config`, not the frozen `Profile`, so a keyword override
changes both what the model is told and what is enforced (R10).

### 4.4 The `Profile` Dataclass

`Profile` is a frozen (`@dataclass(frozen=True)`) dataclass. Instances are
immutable after creation — profiles are constants, not mutable state.

```python
from rlm_local.config import PROFILES

tiny = PROFILES["tiny"]
print(tiny.max_turns)  # 12

# This raises an error:
tiny.max_turns = 20  # FrozenInstanceError
```

---

## 5. Model Backend

### 5.1 Protocol and Implementation

The model backend is defined by the `ModelBackend` protocol:

```python
class ModelBackend(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...
```

The sole implementation is `HTTPModelBackend`, an OpenAI-compatible HTTP client
that speaks `/v1/chat/completions`.

**Robustness contract (R9).**

- **Endpoint normalization.** Endpoints are normalized to a `/v1` base:
  `http://host:9010`, `http://host:9010/` and `http://host:9010/v1/` all become
  `http://host:9010/v1`. The comment always claimed this; the code only stripped
  trailing slashes, so an endpoint without `/v1` produced a 404 path.
- **Guarded extraction.** A response without `choices` / `message` / `content`
  raises `ModelBackendError` carrying the HTTP status and a body snippet, instead
  of leaking a bare `KeyError` or `IndexError` out of `completion()`. A null
  `content` with a `reasoning_content` sibling uses the latter (reasoning
  models); a `content` that is an empty string is returned as-is.
- **Retries.** Transport errors and 5xx responses are retried twice with
  exponential backoff (the `tenacity` dependency was declared but never
  imported). 4xx is not retried — retrying a client error cannot help.
- **Auth headers.** `headers=` allows a bearer token for a proxied endpoint.

### 5.2 Constructor

```python
HTTPModelBackend(
    root_endpoint: str = "https://localhost:9010/v1",
    sub_endpoint: str = "",
    root_model: str = "",
    sub_model: str = "",
    verify: bool = False,
    timeout: float = 300.0,
    headers: dict[str, str] | None = None,
)
```

| Parameter | Default | Description |
|---|---|---|
| `root_endpoint` | `"https://localhost:9010/v1"` | Base URL for root-tier chat completions (normalized to a `/v1` base) |
| `sub_endpoint` | `""` (same as root) | Base URL for sub-tier chat completions |
| `root_model` | `""` (from config) | Model name for root tier |
| `sub_model` | `""` (same as root) | Model name for sub tier |
| `verify` | `False` | TLS certificate verification (self-signed certs on localhost) |
| `timeout` | `300.0` | HTTP request timeout in seconds |
| `headers` | `None` | Extra headers on every request (auth) |

**TLS posture (R18).** `verify=False` stays the default because a local
self-signed server is the reference deployment. When the endpoint is `https` on a
non-loopback host *and* verification is off, the constructor emits a
`UserWarning` naming the endpoint — the traffic can be intercepted, and that
should not be silent. Plain `http` has no certificate to verify and is exempt.
`tests/test_model_backend.py::TestTLSVerificationWarning` pins both sides.

### 5.3 Two-Tier Routing

The `tier` parameter on `chat()` selects the endpoint and model:

- `"root"` → `root_endpoint` + `root_model`
- `"sub"` → `sub_endpoint` + `sub_model`

When `sub_endpoint` is empty and `sub_model` is empty, both tiers use the same
server and model. This is the common case: one `llama-server` process, one model,
handling both orchestration and bulk sub-calls.

### 5.4 Prefix-Cache Discipline

The system prompt is byte-identical across all calls. Conversation history is
append-only. The harness contains **no timestamps, no per-turn randomness, no
changing headers**. This ensures llama.cpp's `--cache-ram` prompt caching hits
on every turn after the first, reducing prompt evaluation latency by 5–10×.

When kernel integration is active, the vault is consulted once at the start of
`RootLoop.run()` — the resulting prompt text is cached for all subsequent turns.

> **Warning:** A single changing character anywhere in the prefix silently
> invalidates the entire cache. Do not add timestamps or dynamic text to any
> message that appears before the latest turn.

### 5.5 Response Schema (Constrained Decoding)

When `response_schema` is passed, the backend sets `response_format` to
`{"type": "json_schema", ...}` per the OpenAI structured output spec.
llama.cpp servers with GBNF grammar support will enforce the schema.

The harness uses constrained decoding **only for sub-calls** and only when
explicitly requested via `llm_query(prompt, schema=...)`. The root model's
free-form ` ```repl ` output is never grammar-constrained — rescue parsing
(Chapter 9) is cheaper and safer there.

---

## 6. The REPL Sandbox

### 6.1 Design Rationale

The REPL is a **subprocess-isolated** Python interpreter, not an in-process
`exec()`. Two motivations:

1. **Killability.** A small model emitting `while True:` must not hang the harness.
   The worker can be killed, restarted, and its late results discarded (§6.2, R4).
2. **Memory isolation.** The worker's memory is bounded by the OS, not by the
   harness process.

**It is a process boundary, not a security sandbox.** Design §5.3 asked for four
things; three are now implemented and one is retired, and the distinction matters
more than the summary (roadmap item 6, 2026-09-12):

| §5.3 requirement | State |
|---|---|
| Restricted builtins for model code | **Implemented.** `input`, `eval`, `exec`, `compile`, `globals` and `locals` are removed for the model's cell (plus `breakpoint`, which in a worker with no stdin would sit there until the cell timeout). `RLM_REPL_ALLOW_DYNAMIC=1` restores them. |
| Memory bound on the worker | **Implemented where the OS allows** — `RLM_REPL_MEMORY_MB` sets `RLIMIT_AS` at worker start. POSIX only; on Windows there is no `resource` module and the cell timeout is the only bound. |
| Scaffold names restored after every cell | **Implemented.** `answer` rebound to a non-dict, `context` deleted, a helper overwritten by a non-callable: the working binding is put back and the repaired names are recorded in the trajectory. "Restored" means *usable*, not *reset* — `answer['content']` survives between cells, or the submission protocol could not work at all. |
| `open` jailed to the task directory | **Retired, deliberately.** It cannot be enforced: imports stay permitted (`import os`), the worker's own `_FileContext` must read the spill file outside any task directory, and `os.open`/`pathlib` bypass a Python-level `open` wrapper. Keeping the requirement would have bought a claim, not a boundary. |

**What remains true, stated plainly:** the worker imports the standard library
itself, opens the loopback socket it needs, and is launched with
`env={**os.environ, ...}`, so it inherits the harness's environment. Its own
module names (`os`, `socket`, `sys`, and the protocol functions `_send`/`_recv`)
are reachable from a cell through `globals()`, and `import os` is allowed — so a
model that *means* to reach the filesystem or a shell still can. Executing model
code in a dedicated namespace, so the harness's internals are not sitting in the
model's globals, is recorded as roadmap DG10 rather than implied to be done.

What that means in practice: run the harness against content and models you
trust. The kernel's gate (§7.3.1 of the kernel manual) is likewise a *quality*
gate rather than containment. If you need real isolation, run the whole harness
in a container or a dedicated user account.

### 6.2 Communication Protocol

The harness and worker communicate over a **bidirectional TCP socket** on
`localhost` using a length-prefixed JSON protocol:

```
[4-byte big-endian length][UTF-8 JSON payload]
```

Message types sent by the harness:
- `{"cmd": "init", "context": <spec>, "helpers": [...]}` — initialize the worker with context and optional helper definitions. `<spec>` is either the raw text (small/in-memory context) or a **file reference** `{"kind": "file", "path": "...", "total": <bytes>}` when the context spilled to disk (R1). The reference is small and constant-size regardless of context size; the worker opens a lazy reader over the same path.
- `{"cmd": "exec", "code": "<python source>", "cell_id": <int>}` — execute a cell
- `{"cmd": "shutdown"}` — terminate the worker

Message types sent by the worker:
- `{"type": "result", "cell_id": <int>, "stdout": "...", "stderr": "...", "final_answer": null}` — cell result
- `{"type": "subcall", "prompt": "...", "schema": null, "cell_id": <int>}` — request a sub-LLM call
- `{"type": "subcall_batched", "prompts": [...], "schema": null, "cell_id": <int>}` — request batched sub-calls
- `{"cmd": "search", "query": "...", "k": 5, "kinds": null, "cell_id": <int>}` — request vault search (kernel)
- `{"cmd": "propose", "kind": "...", "name": "...", "body": "...", "rationale": "...", "cell_id": <int>}` — propose new page (kernel)

The subcall messages are **interleaved** with execution: the worker sends a
subcall request, the harness proxies it to the `SubcallManager`, sends the
response back, and the worker continues executing the cell.

**Cell correlation (R4).** Every `exec` carries a monotonically increasing
`cell_id`, and every worker message echoes it. If a cell exceeds its timeout the
harness returns a timeout error while the worker keeps running; the next
`execute()` discards any `result` whose `cell_id` is not the current one instead
of handing it to the model as this cell's output. Two consecutive timeouts
restart the worker (the model is told the REPL namespace was lost).

### 6.3 REPL Namespace

When a cell executes, the following names are available in the worker's global
scope:

| Name | Type | Description |
|---|---|---|
| `context` | `str` or lazy file handle | For an in-memory context, a plain `str`. For a disk-backed context, a byte-addressed lazy reader over the spilled file — `len()`, indexing, `lines()`, `grep()` and `chunk()` all read from disk (R1). |
| `answer` | `dict` | `{"content": "", "ready": False}` — set to signal completion |
| `llm_query(prompt, *, schema=None)` | function | One-shot sub-LLM call; no history, no system prompt |
| `llm_query_batched(prompts, *, schema=None)` | function | Batched sub-calls with bounded parallelism |
| `peek(n=2000)` | function | Print and return the first `n` characters of context |
| `grep(pattern, max_hits=50)` | function | Regex search over context lines; prints and returns hits |
| `chunk(size=None, by=None)` | function | Split context into chunks; `by="paragraph"` for paragraph splitting |
| `map_query(items, template, batch=True)` | function | Apply a template to items and call `llm_query_batched` |
| `show_vars()` | function | Print all user-defined variables in the REPL |
| `search(query, k=5)` | function | **Kernel only.** Search the vault via BM25; returns formatted results |
| `propose(kind, name, body, rationale)` | function | **Kernel only.** Propose a new page to quarantine for gate review |
| `corpus_find(query, limit=20, kind=None, under="")` | function | **Corpus only.** Matching corpus paths from the path index. A query with a `/` searches whole paths; without one it searches file names. |
| `corpus_list(rel="", limit=50)` | function | **Corpus only.** One directory level (never the subtree) |
| `corpus_stat(rel)` | function | **Corpus only.** Kind, size and mtime of one path |
| `corpus_read(rel, max_bytes=20000)` | function | **Corpus only.** Read one file, bounded; truncation is reported |
| `corpus_count(kind=None, under="")` | function | **Corpus only.** Counts and byte totals without listing anything |
| `corpus_search(query, k=8, include_vendored=False)` | function | **Corpus only.** Search the **words** inside the corpus's files and extracted documents. Each hit is an address (`path#L<start>-<end>`) that can be re-read, derived text is labelled with its engine, and the result states its indexing coverage. `corpus_find` additionally searches inside listed archives and reports `container!member`. |

The `corpus_*` helpers exist only when the run was given a corpus
(`--corpus-root`, or the `corpus_bridge` argument to `completion()`), and they are
answered in the parent process through the read-only mount — the worker never
opens a corpus file itself. Without a corpus they return a message saying so.
Reads are bounded and paths are contained inside the corpus root; a path that
would escape it is refused, not resolved. See `docs/operator-guide.md` §3
(`rlm corpus`) and `AGENTS.md` §1.8 for the read-only guarantee this implements.

### 6.4 Vault Helper Injection (Kernel Integration)

When a `KernelBridge` is provided, the harness collects active helper definitions
from vault helper pages and injects them into the worker's namespace alongside
the hardcoded helpers. This follows the Forth dictionary property:
user-authored helpers are structurally indistinguishable from builtins.

The injection path:
1. `RootLoop.run()` calls `kernel_bridge.get_helper_definitions()`.
2. The result — `[{"name": "grep", "code": "def grep(...): ..."}, ...]` — is
   passed to `REPLSandbox.start(definitions=defs)`.
3. The `init` socket command includes a `helpers` array.
4. The worker `exec()`s each helper's code into its globals.
5. If no helpers are provided, the worker falls back to its hardcoded set.

This means adding a capability to the system is authoring a helper page in the
vault — no code changes to `rlm_local` required.

### 6.5 The `answer` Dict Mechanism

Termination is signaled from **inside** the REPL, not by regex-parsing the model's
prose output. The model sets:

```python
answer["content"] = "The Treaty of Westphalia was signed in 1648."
answer["ready"] = True
```

The harness detects `answer["ready"] == True` in the post-execution state and
extracts `answer["content"]` as the final answer. This mechanism is strictly
more robust than prose-based termination (e.g., "FINAL: ...") because it is
programmatic, not linguistic.

### 6.6 State Persistence

Variables defined in one cell are available in the next. The worker uses
`exec(code, globals())` so that assignments survive across cells:

```python
# Cell 1
x = 42

# Cell 2
print(x)  # → 42
```

### 6.7 Cell Limits

| Limit | Default | Behavior on Violation |
|---|---|---|
| Wall-clock timeout | 60 s (`tiny`/`laptop`), 120 s (`workstation`) | Templated `Error: cell exceeded the {timeout}s time limit.` returned as stderr; the worker keeps running, and its late result is discarded by cell id (R4). Two consecutive timeouts restart the worker. |
| stdout capture | `repl_output_char_cap` — 2 000 / 4 000 / 8 000 chars by profile (R10) | Head-truncated with `[... output truncated to N characters ...]` appended |
| stderr capture | same cap (R10) | **Tail-preserving**: the head *and* the tail are kept with the elided middle replaced by `[... N characters of stderr elided ...]`, because a traceback's last line is the useful one |

The stdout cap is exactly the number the system prompt advertises as
`{repl_cap}`: both come from the same `Config` attribute, so the prompt cannot
promise a different limit than the one enforced.

### 6.8 REPLSandbox Class

```python
class REPLSandbox:
    def __init__(self, cell_timeout: float = 60.0,
                 stdout_cap: int = 256 * 1024,
                 restart_after_consecutive_timeouts: int = 2): ...
    def start(self, context: Any, subcall_manager: Any,
              definitions: list[dict[str, str]] | None = None) -> None: ...
    def execute(self, code: str) -> REPLResult: ...
    def restart_worker(self) -> None: ...
    def shutdown(self) -> None: ...
```

`start()` launches the subprocess, accepts the TCP connection, and sends the
initial context (inline text, or a file reference for a spilled context) along
with optional helper definitions.

`execute()` sends code, handles interleaved subcall requests, and returns a
`REPLResult`. With kernel integration, it also proxies `search` and `propose`
commands to the `KernelBridge`.

`restart_worker()` kills and relaunches the worker, replaying the init payload.
The REPL namespace is lost; the loop tells the model so.

```python
@dataclass
class REPLResult:
    stdout: str = ""
    stderr: str = ""
    final_answer: str | None = None
    warnings: list[str] = field(default_factory=list)
```

`shutdown()` sends the shutdown command, waits up to 5 seconds for graceful exit,
then hard-kills. It also removes the temporary worker script directory.

---

## 7. Subcall Manager

### 7.1 Role

The `SubcallManager` is the economics layer for the hot path. Every `llm_query()`
and `llm_query_batched()` call from the REPL passes through it. It enforces
budgets, manages concurrency, memoizes identical calls, and attaches sizing
warnings.

### 7.2 Constructor

```python
SubcallManager(
    backend: ModelBackend,
    *,
    max_concurrent: int = 2,
    max_calls: int = 60,
    max_chars: int = 4_000_000,
    prompt_char_budget: int = 16000,
    context_total_chars: int = 0,
    shortcut_warn_fraction: float = 0.60,
)
```

### 7.3 Budgets

Three budgets are enforced per completion:

| Budget | Key | Default | Exhaustion Behavior |
|---|---|---|---|
| Call count | `max_calls` | 60 | `llm_query()` returns a templated exhaustion string |
| Character count | `max_chars` | 4,000,000 | `llm_query()` returns a templated exhaustion string |
| Per-prompt size | `prompt_char_budget` | 16,000 | **Warning only** (not a hard error); result includes warning text |

Call-count and character-count exhaustion are **hard stops**: further sub-calls
return error strings that the root model can recognize and respond to (ideally
by finalizing with what it has). Per-prompt size is a **soft warning**: the call
succeeds but the result includes a note that quality may degrade.

### 7.4 Concurrency

Sub-calls use a `ThreadPoolExecutor` with `max_concurrent` workers. The default
of 2 matches a typical `llama-server --parallel 2` configuration.

`llm_query_batched()` fans out all prompts to the thread pool simultaneously,
respecting the concurrency cap. This is why the system prompt urges the model to
prefer `llm_query_batched` over a loop of `llm_query` calls: one turn vs. N turns.

### 7.5 Memoization

Identical sub-call prompts within a single completion are memoized via SHA-256
hash. If the model retries the exact same sub-call (a common pattern under retry
nudges), the cached response is returned without hitting the server.

**The cache is consulted before the budget is charged (R3).** Memoization exists
to relieve budget pressure, so a cache hit costs neither a call nor a character
budget unit — a repeated prompt still works once the budget is exhausted:

```python
# First call: goes to server, charges 1 call
result1 = mgr.llm_query("Summarize: The quick brown fox...")

# Second call with identical prompt: cache hit, free
result2 = mgr.llm_query("Summarize: The quick brown fox...")
assert result1 == result2
assert mgr.calls_used == 1
assert mgr.cache_hits == 1
```

`cache_hits` counts hits; `cache_size` counts distinct memoized prompts.

### 7.6 Anti-Shortcut Guard

If a single sub-call prompt exceeds `shortcut_warn_fraction` (default 60%) of the
total context size, the result includes a templated warning:

```
[WARNING] This sub-call received 75% of the full context. Consider decomposing
into smaller chunks for better results.
```

This implements design requirement R5.3: a single sub-call that offloads the
entire problem is a non-generalizing shortcut.

### 7.7 Public Properties

| Property | Type | Description |
|---|---|---|
| `calls_used` | `int` | Total sub-calls charged against the call budget |
| `chars_used` | `int` | Total prompt characters charged against the char budget |
| `calls_remaining` | `int` | `max_calls - calls_used` (floor 0) |
| `cache_hits` | `int` | Number of sub-calls **served from the cache** (R3 — this used to return the cache size) |
| `cache_size` | `int` | Number of distinct prompts memoized |

---

## 8. The Root Loop

### 8.1 Role

`RootLoop` is the orchestrator. It owns the turn-by-turn conversation with the
root model, dispatches code to the REPL, interprets results, and decides when
to terminate.

### 8.2 Constructor

```python
class RootLoop:
    def __init__(
        self,
        config: Config,
        backend: ModelBackend,
        logger: TrajectoryLogger | None = None,
        kernel_bridge: Any = None,
    ) -> None:
```

The `kernel_bridge` parameter is optional. When provided (a `KernelBridge` from
`rlm_kernel.repl_bridge`), the root loop gains vault awareness: helper
definitions, core-memory injection, vault-first prompt loading, and
`search`/`propose` proxy.

### 8.3 Message Layout

The message list is built once at startup and grown append-only. The byte-stable
prefix is critical for prompt caching:

```
┌──────────────────────────────────────────────────────────┐
│ system     │ SYSTEM_PROMPT (hardcoded or vault-loaded)    │  ← byte-stable
├────────────┼─────────────────────────────────────────────┤
│ user       │ METADATA (query + context size               │  ← byte-stable
│            │  + optional core-memory summary)             │
├────────────┼─────────────────────────────────────────────┤
│ user       │ PROLOGUE (decomposition nudge)               │  ← byte-stable
├────────────┼─────────────────────────────────────────────┤
│ (few-shot) │ user/assistant pairs                         │  ← byte-stable
├────────────┼─────────────────────────────────────────────┤
│ user       │ Turn 1/15. (safeguard)                       │  ← first novelty
├────────────┼─────────────────────────────────────────────┤
│ assistant  │ model response                               │
├────────────┼─────────────────────────────────────────────┤
│ user       │ REPL output: ...                             │
├────────────┼─────────────────────────────────────────────┤
│ user       │ Turn 2/15.                                   │
├────────────┼─────────────────────────────────────────────┤
│ ...        │ ...                                          │
└────────────┴─────────────────────────────────────────────┘
```

**Kernel integration changes:**
- The system prompt may be loaded from `contract/repl-contract.md` and
  `contract/how-to-work.md` (vault-first, fallback to hardcoded).
- The metadata message may include a "Core memory: ..." line if a
  core-memory page exists in the vault.
- Helper one-liners from active vault helper pages are included in
  the system prompt under `Available helpers:`.

### 8.4 Turn Flow

Each turn follows this exact sequence:

1. **Header appended.** `"Turn {i}/{max_turns}."` — plus the turn-0 safeguard on
   the first turn: `"You have not inspected the context yet. Probe it first with
   peek() or grep(); do NOT finalize."`

2. **Root model queried.** `backend.chat(messages, tier="root", temperature=0.0)`.
   Turn 0 gets `max_tokens=3000` (room for the decomposition plan); subsequent
   turns get `max_tokens=1500`.

3. **Response parsed.** `Parser.parse(text)` extracts code blocks, `FINAL:` lines,
   or triggers retry nudges.

4. **Nudge handled.** If the parser produced a nudge, it is appended as a
   `user` message and the turn restarts (no REPL execution).

5. **Code executed.** Each extracted block runs in the REPL via
   `repl.execute(block)`. With kernel integration, `search` and `propose`
   proxy commands are handled inline.

6. **stderr checked (R5).** If the cell produced a traceback, the parser counts
   it as a consecutive error and — while the error budget holds — its
   `NUDGE_STDERR_ERROR` nudge is appended after the REPL output. A cell that ran
   clean resets the error streak.

7. **Termination checked.** If `result.final_answer is not None`, the loop breaks.
   Note `is not None`, never truthiness: an empty string is a *submission*.

8. **Unsearched corpus submission nudged (RO4).** When the run has a corpus
   bridge and the sandbox has served **no** `corpus_*` helper request,
   `NUDGE_CORPUS_UNSEARCHED` is appended and the submission is refused — the
   turn restarts. This nudge is *evidence*, not a guess: the parent process
   serves every helper request, so `REPLSandbox.corpus_calls`
   (`src/rlm_local/repl.py`) is a fact the harness owns, counted for every
   corpus request it answers, successful or not. `context` in a corpus run is a
   placeholder, so a submission with zero helper calls is a claim about a
   corpus the model never opened. The third live run did exactly that — one
   `print(len(context))`, then "not mentioned in the corpus" — and it is the
   reason this step exists. A run without a corpus is unaffected, and one
   helper call of any kind (even a failed search) satisfies it.

9. **Empty submission nudged (R6).** If the submission is empty or whitespace
   only, `NUDGE_EMPTY_ANSWER` is appended and the turn restarts, counted against
   `max_consecutive_nudges`. After that budget is exhausted the loop breaks into
   forced finalization. Before R6 an empty submission silently continued the
   loop (the truthiness check read `""` as "not final"), wasting turns with no
   explanation.

10. **Output formatted.** The REPL stdout/stderr is converted to a templated
    `"REPL output:"` message and appended.

11. **Error budget checked.** If `consecutive_errors > max_consecutive_errors`,
    the loop breaks into forced finalization.

12. **Uncited answer refused (RO4).** In a corpus run, an answer that cites no
    address and names no coverage is refused — `NUDGE_CORPUS_UNCITED` is appended
    and the turn restarts, on its own budget (`corpus_uncited_nudges`, bounded by
    `max_consecutive_nudges`). `_refusal_reason` is the rule; the second arm is
    the escape hatch and the reason it cannot trap a run: *"the corpus does not
    contain this, here is the coverage"* is a truthful answer and is accepted, and
    on a partly-indexed corpus it is the common one. The rule is applied on both
    submission channels — the answer dict and a courtesy `FINAL:` line — and the
    `FINAL:` channel gets the unsearched rule as well, because a final line is
    still an answer about a corpus the run may never have opened.

    **A citation must be an address the harness served.** `REPLSandbox` records
    every address a helper handed over (`corpus_addresses_served`): the hits a
    search returned, and the address a *successful* read resolved — a failed call
    serves nothing, or asking for an address would be enough to legitimise it.
    `_unserved_citations` compares what the answer cites against that set, and any
    address that is not a member refuses the answer *even when it also names
    coverage*: the escape arm is for absence, not for a receipt that points at
    nothing. The reason is measured — a four-turn run read nothing, printed
    nothing, and still ended with a `Citations:` line naming an address nobody had
    served it (`docs/20260916-1445-corpus-turn-budget-buys-speed-not-provenance.md`).
    Each refusal is logged as `corpus_uncited` with `unserved=N`, so fabrication
    rates are countable. Every **served search** also writes a
    `corpus_search_quality` event (`served weak=3 partial=1 chars=1234`): the labels
    live inside tool results, so a run that prints none of them would otherwise
    leave no record of what it was shown, and "did the model see a weak match?"
    would be an inference from the code rather than a fact in the trajectory.
    The run's question is set on the bridge by `RootLoop.run` — not only by the CLI
    — so the label is measured against what was asked at every entry point.

    **A citation must also rest on a passage that answers the question.** The served
    check says the harness handed the address over; it does not say the passage is an
    answer. So the band each hit was labelled with is remembered beside it
    (`REPLSandbox.corpus_address_bands`), read from the hit's **own header line
    only** — a passage that happens to quote the word `(weak)` must not be able to
    label itself — and the strongest band for an address wins, so a later search on
    a question the same passage does not answer cannot demote it. An answer whose
    cited addresses were *all* served `weak`/`none` is refused with
    `NUDGE_CORPUS_WEAK_EVIDENCE` unless it also names coverage
    (`_cites_only_unanswering_evidence`). One `strong` or `partial` citation is
    enough, and an address carrying no band at all — one handed over by
    `corpus_read`, or any hit from a question with no content words — is `unknown`
    and refuses nothing (AGENTS.md §1.8: a check that cannot see the truth says
    `unknown`). The refusal is its own event, `corpus_weak_citation`, carrying
    `band=` and `addresses=`: "the model cited nothing" and "the model cited a
    non-answer" are different failures, and an operator counting one should not
    count the other. It is enforced rather than merely stated because the statement
    was measured and ignored — a run was served `weak` eight times and cited the
    hits anyway (`docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md`).

13. **Last-turn nudge (RO4).** A corpus run that reaches its **final turn** having
    called at least one helper but never submitted is told so before that turn's
    model call: `NUDGE_CORPUS_LAST_TURN` names both arms — cite what you read, or
    say the corpus does not contain it and quote the coverage line — and states
    that "I did not find it" is a complete answer. It costs no turn of its own
    (it is appended *before* the call), and it fires on that condition only: a run
    that has not looked at all is the unsearched guard's case, and two conflicting
    nudges would be worse than one. The reason it exists is measured: three live
    runs on a question the corpus cannot answer spent 5, 8 and 8 of their turns
    exploring and were answered by forced finalization. The turn header already
    carried `Turn N/M`, so what was missing was permission to stop, not
    information about the budget.

`_record_citations` runs at each of the three places an answer becomes **final**(the answer-dict submission, a courtesy `FINAL:` line, and the forced-finalization
answer). In a corpus run it counts the answer (`RootLoop.corpus_answers`,
`RootLoop.corpus_answers_uncited`) and writes one `corpus_citation` guardrail
event carrying `answers_with_address=True|False`; refusals are recorded separately
as `corpus_uncited` (nothing cited, no coverage named, or an address no helper
served) and `corpus_weak_citation` (every cited address was served as a
`weak`/`none` hit). The prompt requires a `Citations:` line
(`CORPUS_SECTION_LINES`, "Cite your evidence"), and the order matters: it was
required and *measured* first, and the refusal was added only because three
consecutive live runs of the 4B laptop model cited nothing at all
(`docs/20260915-0655-corpus-citation-compliance-measured.md`). The check is for an
address anywhere in the answer, not for the line's format — an answer that quotes
its addresses is grounded even if it lays them out differently, while a
`Citations:` heading with nothing checkable after it is not.

### 8.5 Termination Paths

The loop terminates by one of five mechanisms, in priority order:

1. **answer-dict signal (highest priority).** `answer["ready"] == True` detected
   in post-execution REPL state, with non-empty content. Returns
   `answer["content"]`.

2. **Courtesy `FINAL:` line.** The parser detects a line matching
   `^FINAL:\s*(.+)$`. Returns the captured text.

3. **Turn exhaustion.** All `max_turns` consumed without termination. Triggers
   forced finalization (see below).

4. **Error budget exhaustion.** `max_consecutive_errors` exceeded. Triggers
   forced finalization.

5. **Nudge exhaustion.** Repeated empty submissions, or repeated submissions
   from a corpus run that has not called a single `corpus_*` helper, past
   `max_consecutive_nudges`. Triggers forced finalization. Each nudged
   submission restarts the turn, so `NUDGE_EMPTY_ANSWER` and
   `NUDGE_CORPUS_UNSEARCHED` each carry their own counter and the same bound
   (`max_consecutive_nudges`); a model that ignores both still terminates.

### 8.6 Forced Finalization

When the turn loop ends without a final answer, a single forced-finalization
call is made:

```
"Based on everything you have learned so far, provide your best final
answer now. Summarize your findings in plain text."
```

A **corpus** run gets `FORCED_FINALIZATION_CORPUS_PROMPT` instead, which asks for
the same thing plus the evidence: end with a `Citations:` line, or say the corpus
does not contain the answer and quote `corpus_coverage()`'s line. The reason is
measured rather than stylistic — two live runs on a question the corpus cannot
answer spent their whole turn budget exploring, never submitted anything, and
were answered from this prompt, both times uncited; the citation guard sees only
submissions, so the terminal answer is the one place the requirement has to be
restated (`docs/20260916-0902-corpus-terminal-answer-provenance.md`). It is asked
for, never refused: refusing at the terminal point would turn a weak answer into
no answer at all.

The model's response to this prompt becomes the final answer. If the response
contains a `FINAL:` line, that is extracted; otherwise the raw text is returned.

Forced finalization **never returns empty**. If the model produces nothing,
the templated constants `NO_ANSWER_PRODUCED` (`"(No answer produced)"`, returned
when the loop ends with no answer at all or with an empty one) and
`FINALIZATION_FAILED` (`"(No answer produced — forced finalization failed)"`,
returned when the finalization call itself raised) are used. Both live in
`templates.py`; the fallback strings used to be inline in `root_loop.py` (R7).

### 8.7 Lifecycle

```python
loop = RootLoop(config, backend, logger, kernel_bridge=bridge)
try:
    answer = loop.run(query, context)
finally:
    loop.shutdown()  # kills REPL, cleans context files, shuts down subcall pool
```

`RootLoop` is not reusable. Create a new instance for each `completion()` call.

---

## 9. Parser and Guardrails

### 9.1 Role

The `Parser` is the guardrail layer. It extracts executable code from model
responses, detects termination signals, and generates templated retry nudges
when the model drifts off-format. Every rule is mechanical and cheap — the harness
never asks the model to re-do work it can fix itself.

### 9.2 Extraction Pipeline

The `parse()` method applies these rules in order, returning on the first match:

| Step | Rule | Example Input |
|---|---|---|
| 1 | Extract ` ```repl ` fenced blocks (DOTALL regex; also accepts ` ```python ` and bare fences) | ```` ```repl\nprint("hi")\n``` ```` |
| 2 | Rescue: unclosed final fence (also the unclosed narration pattern) | ```` ```repl\nprint("hi") ```` |
| 3 | Courtesy `FINAL:` line | `FINAL: The answer is 42.` |
| 4 | Narration nudge (code keywords, no fences) | `I would run llm_query to find...` |
| 5 | Generic no-block nudge | `The answer is 42.` |

**R5 note.** The original pipeline had four fence stages. Stages 3 and 3b
("` ```python ` or bare ```" and its unclosed variant) were strict subsets of
stages 1 and 2 — their regexes accepted `(?:python)?` where stages 1/2 accept
`(?:repl|python)?` — so no input could ever reach them. They were deleted; the
behaviour they were meant to provide is covered by stages 1/2, and
`tests/test_parser.py::TestUnreachableStagesRemoved` pins both the behaviour and
the absence of the dead regexes.

### 9.3 The `ParseResult` Dataclass

```python
@dataclass
class ParseResult:
    blocks: list[str]           # Code blocks to execute (in order)
    final_answer: str | None    # Non-None if termination detected
    warnings: list[str]          # Templated harness warnings
    nudge: str | None           # Retry nudge to append, or None
    raw_text: str               # Original model response (for logging)
```

### 9.4 Retry Nudges

When the model emits no parseable code and no `FINAL:` line, a **templated nudge**
is appended as a `user` message:

```
No ```repl block found. Emit exactly one ```repl block with your next step.
```

If the model narrates code without fencing it:

```
You described code without emitting it. Emit exactly one ```repl block
containing the code you intend to run.
```

**Nudge budget:** `max_consecutive_nudges` (default 2). After exhausting the
budget, the parser emits no nudge — the turn counts as an error and the error
budget is checked separately.

A third nudge exists for a distinct mistake: the model sets
`answer["ready"] = True` while `answer["content"]` is empty (or whitespace).
The root loop appends `NUDGE_EMPTY_ANSWER` rather than treating the submission
as final, and counts it against the same `max_consecutive_nudges` budget before
falling through to forced finalization (R6).

### 9.5 Error Tracking

`Parser` tracks `consecutive_errors` (incremented by `parse_stderr()` after a
cell produces a traceback) and `consecutive_nudges` (incremented on unparseable
output). The `RootLoop` checks the error counter against
`max_consecutive_errors` to trigger forced finalization.

`parse_stderr(text, stderr_text)` is **wired into the root loop** (R5): after any
REPL result with non-empty stderr the parser counts the failing cell and, while
the error budget holds, returns a templated nudge naming the exception class
(`NUDGE_STDERR_ERROR`). A cell that runs clean calls `reset_errors()`.

Two deliberate details:

- `parse()` does **not** reset the error streak. It runs *before* execution, so
  a successful parse is not evidence of a successful execution; only a clean
  REPL result is. Resetting there made `consecutive_errors` unreachable and the
  error budget inert.
- `parse_stderr()` is side-effect free apart from the error counter — it must
  not advance the nudge counter or re-run the extraction pipeline.

### 9.6 Answer-in-Block Detection

`check_answer_in_block(block)` scans a code block for the answer-dict pattern:

```python
content, ready = parser.check_answer_in_block(code)
# content: str | None — the value assigned to answer["content"]
# ready: bool — whether answer["ready"] = True appears
```

This is advisory: it says what the block *declares*, before execution. The
definitive check is the post-execution REPL state (an expression like
`answer["content"] = result` only has a value at runtime). The root loop logs the
static reading alongside the runtime one when it has to nudge an empty
submission.

### 9.7 JSON Repair

`repair_json(text, schema=None)` repairs malformed JSON from sub-call responses
(design §5.6 item 6). It strips markdown fences, finds the first `{`, balances braces,
and returns the extracted object. If no JSON is found, the text is returned
as-is.

It is **wired into `SubcallManager._do_call`** (R5): whenever a sub-call was
issued with a `schema`, the response is repair-parsed before it is returned and
cached, because servers that only honour `response_format` loosely hand back
fenced or prose-wrapped JSON.

```python
from rlm_local.parser import repair_json

repair_json('```json\n{"key": "value"}\n```')
# → '{"key": "value"}'

repair_json('Here: {"name": "test", "val": 42} extra text')
# → '{"name": "test", "val": 42}'
```

---

## 10. Context Store

### 10.1 Role

`ContextStore` manages the user's context data. Small contexts stay in memory;
large contexts spill to disk. The REPL always interacts with a uniform
`Context` handle regardless of the backing store.

### 10.2 The `Context` Handle

```python
class Context:
    def __len__(self) -> int: ...
    def __getitem__(self, key: int | slice) -> str: ...
    def __iter__(self) -> Iterator[str]: ...
    def __str__(self) -> str: ...
    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]: ...
    def grep(self, pattern: str, max_hits: int = 50) -> list[str]: ...
    def chunk(self, size: int | None = None, by: str | None = None) -> list[str]: ...
```

`Context` supports `len()`, indexing, slicing, iteration, and the `grep()` and
`chunk()` methods. For disk-backed contexts, all operations stream from the file
rather than loading the entire text into memory.

**Offset semantics (R2): every context handle is byte-addressed.**

| Expression | Meaning |
|---|---|
| `len(ctx)` | UTF-8 **byte** count (not a character count) |
| `ctx[i]` | the character *starting* at byte offset `i` |
| `ctx[a:b]` | the text decoded from byte offsets `a`..`b` |
| `ctx.lines()`, `ctx.grep()`, `ctx.chunk()` | decoded `str`, as before |

Addressing a byte that is not a character boundary raises `UnicodeDecodeError`.
That is the honest answer for a byte-addressed view: the alternative — silently
returning the wrong characters — is worse. `_InMemoryContext` follows the same
contract, so indexing does not quietly change meaning when a context crosses the
spill threshold.

Before R2 the disk handle mixed the two: `len()` was a character count while
`f.seek()` took a byte offset, so any non-ASCII context corrupted indexing or
raised mid-codepoint. `tests/test_context_store.py::TestByteOffsetSemantics`
covers emoji/CJK round-trips, slices, `lines()`, `grep()`, `chunk()` and the
mid-codepoint failure mode, with a property test against `str` ground truth.

### 10.3 In-Memory vs. Disk-Backed

- **Below `context_spill_threshold`:** An `_InMemoryContext` wraps the raw string.
  All operations are O(1) on the in-memory Python string.

- **Above `context_spill_threshold`:** The text is written to a temp file and a
  `Context` handle is returned. Slicing reads byte ranges; `grep()` streams
  line-by-line; `chunk()` reads fixed-size character windows; `lines(start)`
  seeks using a byte-offset line index built at ingest time.

The `ContextStore.ingest()` method makes this decision automatically:

```python
store = ContextStore(spill_threshold=1_000_000)

# 500-char context: stays in memory
ctx = store.ingest("short text")
# → _InMemoryContext

# 2M-char context: spills to disk
ctx = store.ingest("A" * 2_000_000)
# → Context backed by temp file
```

The spill decision is made on **bytes** (`len(text.encode("utf-8"))`), matching
the byte-addressed contract above.

**The disk handle reaches the REPL worker (R1).** When the context is
disk-backed, `REPLSandbox.start()` sends the worker a *file reference*
(`{"kind": "file", "path": ..., "total": ...}`) instead of the text. The worker
binds a lazy reader over the same file, so `len(context)`, `context[...]`,
`grep()`, `chunk()` and `lines()` in a cell all read from disk, and the bytes on
the wire stay constant no matter how large the context is. Before R1 the harness
called `str(context)` and shipped the whole blob, so the worker held a plain
`str` and RAM was O(context) in both processes regardless of the spill
threshold.

Ownership: `ContextStore` writes the file and `cleanup()` deletes it. The worker
only ever reads — it does not delete or modify the file, and the file survives
`REPLSandbox.shutdown()`.

### 10.4 List Contexts

When `context` is passed as a `list[str]`, items are joined with newlines before
ingestion. The resulting handle is a single text blob, indexable and sliceable:

```python
ctx = store.ingest(["doc1 text", "doc2 text", "doc3 text"])
str(ctx)  # "doc1 text\ndoc2 text\ndoc3 text"
```

### 10.5 Cleanup

`ContextStore.cleanup()` removes all spilled context files and the temp directory.
`RootLoop.shutdown()` calls this automatically.

---

## 11. Prompts and Templates

### 11.1 Design Principle

All harness-generated text lives in two modules: `templates.py` (frozen message
strings) and `prompts.py` (the system prompt and few-shot examples). This
implements design requirement **R4.1**: every string the harness emits must be
templated and stable — identical wording for identical situations across tasks.
Free-form harness prose would break the token-level similarity that equivalence
class induction depends on.

When the `rlm-kernel` integration is active, these modules become **loaders**:
they consult the vault's contract pages first, falling back to the hardcoded
package-bundled defaults. This means the harness's own textual surface can be
evolved without a `pip install -U`.

### 11.2 Templates (`templates.py`)

Sixteen frozen constants, all `str.format()` templates. The most important:

| Constant | Purpose | Placeholders |
|---|---|---|
| `METADATA_TEMPLATE` | First user message describing the task | `{query}`, `{context_type}`, `{context_len}`, `{sub_budget}`, `{max_turns}` |
| `PROLOGUE` | Decomposition nudge (turn 0) | (none — static) |
| `TURN_HEADER` | Per-turn prefix | `{turn}`, `{max_turns}` |
| `TURN_ZERO_SAFEGUARD` | Extra turn-0 instruction | (none — static) |
| `REPL_RESULT_TEMPLATE` | Formatted REPL output | `{block_label}`, `{stdout}`, `{stderr}` |
| `NUDGE_NO_BLOCK` | Retry nudge: no code found | (none — static) |
| `NUDGE_NARRATION` | Retry nudge: code described but not emitted | (none — static) |
| `NUDGE_EMPTY_ANSWER` | Retry nudge: answer ready but empty content | (none — static) |
| `SUBCALL_OVERSIZE_WARNING` | Sub-call exceeds recommended size | `{size}`, `{budget}` |
| `SUBCALL_COUNT_EXHAUSTED` | Call budget exhausted | `{used}`, `{max_subcalls}` |
| `SUBCALL_CHAR_EXHAUSTED` | Character budget exhausted | (none — static) |
| `SHORTCUT_WARNING` | Anti-shortcut guard triggered | `{pct}` |
| `NUDGE_STDERR_ERROR` | Retry nudge after a cell raised | `{error_kind}`, `{errors}`, `{max_errors}` |
| `FORCED_FINALIZATION_PROMPT` | Final forced-finalization message | (none — static) |
| `CELL_TIMEOUT_ERROR` | Cell exceeded time limit | `{timeout}` |
| `CELL_STDOUT_TRUNCATED` | Output truncated marker | `{cap}` |
| `CELL_STDERR_TRUNCATED` | Middle of a long traceback elided | `{elided}` |
| `REPL_WORKER_RESTARTED` | Two consecutive timeouts; namespace lost | (none — static) |
| `NO_ANSWER_PRODUCED`, `FINALIZATION_FAILED` | Terminal placeholders (never return an empty answer) | (none — static) |

`tests/test_templates.py::TestTemplateDiscipline::test_no_dead_templates` walks
the constants in this module and fails if any is not referenced by
`src/rlm_local/` — a template that no code path can emit is a lie about what the
harness says. `REPL_READY` and `REPL_FINAL_ANSWER` were exactly that and were
deleted (R7).

#### 11.2.1 Vault-First Template Loading — REMOVED (historical)

> **Historical (removed 2026-07-26, R3-D10).** An earlier revision of this
> manual documented a `load_template(name, vault=)` helper that resolved a
> template constant through `contract/templates/<name>.md`. **That function does
> not exist** — it was deleted the same day this manual was written, and no
> caller ever reached it. The template constants in §11.2 are frozen
> package-bundled strings; they are **introspection-only** and are not loaded
> from the vault. (The kernel manual already states this correctly.)
>
> What *is* live from the vault is the system prompt: `contract/repl-contract.md`,
> `contract/how-to-work.md`, the active-helper listing, and the few-shots — see
> §11.3.1 and §14.3. Do not reintroduce a template loader without a caller.

### 11.3 System Prompt (`prompts.py`)

The system prompt is a `str.format()` template. All capacity claims are
config-injected so they are never lies:

```python
"You have {max_turns} turns. Plan accordingly."
# → "You have 12 turns. Plan accordingly."
```

Three sections:

1. **REPL contract** — available variables and functions, output truncation rules.
2. **How to work** — PROBE → PLAN → EXECUTE → SUBMIT, with small-model guidance.
3. **Few-shot example** — one worked transcript demonstrating the format.

#### 11.3.1 Vault-First System Prompt Loading

```python
from rlm_local.prompts import load_system_prompt_from_vault

system = load_system_prompt_from_vault(prompt_vars, vault=vault)
```

The vault-first assembly order:
1. `contract/repl-contract.md` body (rendered with `{repl_cap}` slots).
2. One-line summaries of active helper pages from `helper/` (singular — see the
   kernel manual §4.7).
3. `contract/how-to-work.md` body.
4. Falls back to the hardcoded `SYSTEM_PROMPT` if any vault page is missing.

### 11.4 Few-Shot Example

The few-shot is **load-bearing**. Small models need the format demonstrated, not
described. The example shows:

- Probing the context with `peek()`
- Searching with `grep()`
- Verifying results before finalization
- Setting `answer["content"]` and `answer["ready"] = True`

The example uses a **generic, synthetic query** ("What color is mentioned?") to
avoid content interference — the model should learn the *format*, not the *answer*.

**Vault few-shots are live (R8).** `load_fewshots_from_vault(vault,
prompt_char_budget=...)` appends **at most one** active `fewshot/` page after the
builtin example, and only if the added user/assistant pair fits within a quarter
of the sub-prompt character budget. Two body shapes are recognised, both of which
the repository itself writes:

```markdown
## Query
<the user turn>

## Answer
<the assistant turn>
```

or one or more `## Example` sections, each split at a `### Assistant` (or
`### Response` / `### Answer`) sub-heading. Anything else is ignored rather than
guessed at; an unrecognised page degrades to the builtin example.

Before R8 this function returned the hardcoded example unconditionally, so K4's
`bootstrap_fewshots` promoted pages that could never influence a prompt.

The K4 GEPA optimizer can evolve few-shots against held-out eval suites.

### 11.5 `build_messages()`

`build_messages(query, context_len, context_type, prompt_vars)` constructs the
initial message list for a `RootLoop`. It assembles the byte-stable prefix:
system prompt, metadata, prologue, and few-shot. The resulting list is the
starting point for the turn loop.

When a kernel bridge is active, the system prompt is loaded vault-first before
`build_messages` is called, and the context type string includes the core-memory
summary.

---

## 12. Trajectory Logger

### 12.1 Role

`TrajectoryLogger` writes a JSONL file recording every interaction in a
completion. This is the data source for:

- **Equivalence-class measurement** (R4.2): compute trajectory similarity across
  structurally similar tasks.
- **Distillation data export**: filter trajectories with correct answers for
  QLoRA fine-tuning.
- **Debugging**: replay exactly what the model saw at each turn.
- **Optimization feedback**: GEPA-style mutation operators (K4) use trajectory
  data as raw material for reflection.

### 12.2 Record Format

Each line is a JSON object with an `event` field and a `timestamp`. Event types:

| Event | Key Fields | Emitted When |
|---|---|---|
| `start` | `query`, `context_len`, `config` | `completion()` begins |
| `turn_start` | `turn`, `max_turns` | Each turn begins |
| `root_message` | `turn`, `role`, `content` | Any message sent to or received from root model |
| `repl_result` | `turn`, `stdout`, `stderr`, `final_answer`, `warnings`, `answer_state` | After each REPL cell executes |
| `subcall` | `turn`, `index`, `prompt`, `response`, `schema`, `cached` | After each sub-LLM call |
| `guardrail` | `turn`, `guardrail`, `detail` | Parser nudges, rescue actions, warnings |
| `corpus_served` | `turn`, `verb`, `query`, `addresses`, `chars`, `ok` | After each `corpus_*` helper call in a corpus run |
| `end` | `elapsed_s`, `final_answer`, `turns_used`, `subcalls_used`, `forced` | `completion()` ends |

`corpus_served` is the structured form of what a helper handed over (RO10): each
entry in `addresses` is `{"address": …, "band": …}`, where the band is the search's
own label for that hit (`strong`/`partial`/`weak`/`none`) or `null` for a helper
that judged nothing (`corpus_read`). A failed call is recorded with `ok=false` and
no addresses — asking for an address is not being served it. The sibling
`corpus_search_quality` guardrail records the *distribution*
(`served weak=3 chars=1234`); this records *which* addresses, which is what makes a
citation auditable after the fact rather than merely countable.

`answer_state` is the scaffold `answer` as it stood when the cell ended — `is_dict`,
`keys`, `ready`, `content_set`, `content_len`, or `{"is_dict": false, "type": …}`
when the cell rebound it, or `{"error": …}` when it could not be inspected at all.
It is optional: a timed-out cell reports none, and a consumer must read `null` as
"unknown", never as "unchanged". It exists so a diagnosis can be made from a
*stored* trajectory, which is how the P4 probe distinguishes "the submission line
never ran" from "it ran and could not take effect".

### 12.3 Usage

```python
from rlm_local import TrajectoryLogger

logger = TrajectoryLogger("logs/traj.jsonl")

# Pass to completion()
answer = rlm_local.completion(
    query, context, logger=logger, profile="tiny"
)

# Or use convenience parameter
answer = rlm_local.completion(
    query, context, log_path="logs/traj.jsonl"
)
```

**Default location and retention (R22).** With no path, a logger writes to
`logs/trajectories/trajectory_<ns>.jsonl` **under the working directory** — not a
shared system temp directory, which is where records used to land under a
predictable name that nothing ever cleaned up. That directory is gitignored.

Records contain full prompts and responses, so nothing deletes them implicitly.
Retention is explicit:

```python
TrajectoryLogger.prune()                  # keep the 50 newest .jsonl files
TrajectoryLogger.prune(keep=200)          # or pick your own bound
```

`prune()` returns the number of files removed and is a no-op for a missing
directory, so it is safe to call unconditionally from a cron job or a wrapper.

### 12.4 Analysis Example

```python
import json

with open("logs/traj.jsonl") as f:
    events = [json.loads(line) for line in f]

# Count sub-calls
subcalls = [e for e in events if e["event"] == "subcall"]
print(f"Total sub-calls: {len(subcalls)}")

# Extract root trajectory for equivalence-class measurement
root_messages = [
    (e["role"], e["content"])
    for e in events
    if e["event"] == "root_message"
]
```

### 12.5 Reading a trajectory back: `rlm_local.traceview` (RO10)

Hand-parsing a JSONL is fine for a count and hopeless for an assessment. `traceview`
renders a trajectory as Markdown — one page per run plus an index — with the
question, the outcome, the counts, a **citation audit** (cited-and-answering,
cited-but-non-answering, cited-but-never-served, served-and-never-cited) and the
passage behind every cited or served address, resolved read-only through the
corpus mount:

```python
from rlm_local.traceview import collect_passages, read_trajectory, render_run_markdown

run = read_trajectory("logs/traj.jsonl")
page = render_run_markdown(run, collect_passages(run, corpus_bridge))
```

The CLI wrapper is `rlm trace render <path> --out-dir DIR [--corpus-root … --corpus-index …]`
and `rlm trace summary <path>`. Two properties are enforced rather than intended:
the rendered directory is refused inside the corpus root and written 0600 in a 0700
directory (the pages contain corpus text), and `render_summary` carries no
question, address or quote, so it is the form that may leave the machine
(`AGENTS.md` §1.9). An audit says it is **partial** when the trajectory cannot
support it — no `corpus_served` events, or a torn final line — instead of
reporting an unverifiable "nothing was fabricated".

---

## 13. Hardware Profiles

### 13.1 Why Profiles?

Decode speed varies 5× across "CPU-only" machines. A profile bundles every
capacity-dependent constant so the harness adapts to available hardware without
per-task tuning.

### 13.2 Profile Comparison

| Setting | `tiny` (8 GB RAM, CPU) | `laptop` (16 GB, M-series) | `workstation` (32 GB, GPU) |
|---|---|---|---|
| **Context windows** | | | |
| Root ctx (tokens) | 8,192 | 16,384 | 32,768 |
| Sub ctx (tokens) | 8,192 | 16,384 | 32,768 |
| **Budgets** | | | |
| Sub-prompt chars | 8,000 | 16,000 | 24,000 |
| REPL output cap | 2,000 | 4,000 | 8,000 |
| Max turns | 12 | 15 | 20 |
| Max sub-calls | 30 | 60 | 100 |
| Max sub-call chars | 1,000,000 | 4,000,000 | 12,000,000 |
| **Concurrency** | | | |
| Max concurrent sub-calls | 1 | 2 | 4 |
| **Limits** | | | |
| Cell timeout | 60 s | 60 s | 120 s |
| Context spill threshold | 500,000 | 1,000,000 | 1,000,000 |

### 13.3 Choosing a Profile

Start with `laptop`. If you run out of RAM (model + KV cache + OS headroom
exceed 16 GB), drop to `tiny`. If you have a discrete GPU with 6+ GB VRAM,
upgrade to `workstation`.

The KV cache is the hidden memory consumer. At FP16, 32K context for an 8B model
costs ~4 GB. At q8_0 (recommended), that drops to ~2 GB. The rule of thumb:
**model file size + KV cache + 4 GB ≤ total RAM.**

### 13.4 Expected Latency

For the `laptop` profile with a 4B Q4_K_M model at ~25 tok/s decode:

- 10 root turns × (~300 tok decode each, prompt-eval cached) ≈ 2 minutes
- 24 sub-calls × (~2K tok eval + ~150 tok decode each) ≈ 2–4 minutes
- **Total: 4–8 minutes per query**

This is the fundamental trade: the harness exchanges wall-clock time for
capability. Tasks impossible for the base model become slow-but-possible.

---

## 14. Kernel Integration

### 14.1 Overview

The `rlm-kernel` package (documented fully in `docs/rlm-kernel-manual.md`)
provides an evolvable layer over `rlm_local`. The integration is **surgical and
backward-compatible**: without a kernel bridge, `rlm_local` behaves exactly as
before.

### 14.2 Activation

```python
from rlm_kernel.repl_bridge import KernelBridge
from rlm_kernel.vault import LocalVault
from rlm_kernel.seed import seed_vault

# One-time setup
vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")
seed_vault(vault)  # creates contracts, templates, builtin helpers

# Per-completion
bridge = KernelBridge(vault, vault.root / ".index" / "meta.sqlite")
answer = rlm_local.completion(query, context, kernel_bridge=bridge)
```

### 14.3 What Changes with Kernel Active

| Feature | Without Kernel | With Kernel |
|---|---|---|
| REPL helpers | Hardcoded in `_WORKER_SCRIPT` | Vault helper pages + hardcoded set |
| System prompt | Hardcoded `SYSTEM_PROMPT` | `contract/repl-contract.md` body |
| How-to-work | Hardcoded in system prompt | `contract/how-to-work.md` body |
| Template messages | Hardcoded constants | **Unchanged — hardcoded constants** (vault template loading was removed, R3-D10; see §11.2.1) |
| Few-shots | Hardcoded example | Vault `fewshot/` pages |
| `search()` in REPL | Not available | Proxied to vault BM25 index |
| `propose()` in REPL | Not available | Writes to `quarantine/` for gate review |
| Core memory | None | `memory/notes/core-memory.md` summary in metadata |
| Prompt evolvability | Requires code change | Edit `repl-contract` / `how-to-work` page → re-seed → live |

### 14.4 The Forth Dictionary Property

A key invariant: user-authored helpers in the vault are structurally
indistinguishable from the builtin helpers shipped with the package.
Both are pages with `## Signature`, `## Implementation`, and
`## Usage example` sections. The `HelperDef.from_page()` extractor
treats them identically. Adding a capability is authoring a page, not
editing `repl.py`.

### 14.5 Search and Introspection

With kernel integration, the root model gains a `search()` function in the
REPL that queries the vault's BM25 index. It returns a **formatted string**, not
a list, so print it rather than iterating:

```python
# Inside a repl block:
print(search("how do I submit an answer", k=3))
```

Each hit is one line: `[n] kind/name: title — summary`.

This makes the system **self-describing** (P6): the model can read its own
contract, discover available helpers, and find relevant definitions — all
through the same search mechanism the user uses.

### 14.6 The Gate in the REPL

The `propose()` function lets the model author new content that passes
through the verification gate:

```python
# Inside a repl block:
path = propose("helper", "extract-dates",
               "## Implementation\n```python\ndef extract_dates(text):\n    import re\n    return re.findall(r'\\d{4}-\\d{2}-\\d{2}', text)\n```\n",
               "Extracts ISO 8601 dates from text")
print(f"Proposed to {path}")
```

The proposed helper goes to `quarantine/`, passes through **static** validation —
AST parse, import allowlist, blocked-pattern scan, and a static check that the
code defines a callable with the page's name whose signature matches the declared
one — and awaits human review before promotion. This is P8 in action: all growth
passes through an evaluator gate.

Two things to be precise about (S3/R19 and the kernel manual's §7.3.1):

- **The sandbox is opt-in.** Execution of model-authored helper code happens only
  with `validate(..., execute=True)`, which `rlm-kernel review --execute` sets.
  Plain `review` and `promote` never execute the code.
- **The gate is a quality gate, not containment.** Restricted-builtin `exec` is
  escapable on CPython — `().__class__.__bases__[0].__subclasses__()` walks past
  the substring blocklist — and the sandbox only *defines* the function, it never
  calls it, so a helper whose body raises passes even with `--execute`. Treat the
  gate as a review step, not a jail.

### 14.7 Core Memory

When a `memory/notes/core-memory.md` page exists in the vault, its summary
is included in the root model's metadata message at the start of every
completion:

```
Your context is a str of 5000 total characters.
A sub-LLM call handles roughly 8000 characters well. You have 8 turns.

Core memory: This instance specializes in Python code analysis.
It prefers aggressive decomposition with batched sub-calls.
```

This gives the model a durable, cross-session identity — the MemGPT core
block pattern, adapted to the RLM harness.

### 14.8 Integration Architecture

```
completion(query, context, kernel_bridge=bridge)
    │
    ▼
RootLoop.__init__(..., kernel_bridge=bridge)
    │
    ▼
RootLoop.run()
    ├── kernel_bridge.get_helper_definitions()
    │   → vault helper pages → REPL worker namespace
    ├── kernel_bridge.get_core_memory_summary()
    │   → injected into metadata message
    ├── load_system_prompt_from_vault(prompt_vars, vault)
    │   → contract pages → system prompt (vault-first)
    │
    ├── REPLSandbox.start(ctx, subcall_mgr, definitions=defs)
    │   └── worker exec()s each helper into globals()
    │
    └── Turn loop:
        └── REPLSandbox.execute(code)
            ├── subcall → SubcallManager
            ├── search → KernelBridge.handle_search()
            ├── propose → KernelBridge.handle_propose()
            └── result → REPLResult
```

---

## 15. Failure Modes and Mitigations

### 15.1 Model Narrates Instead of Emitting Code

**Symptom:** Model says "I would run chunk(context) and then..." but emits no
` ```repl ` block.

**Frequency:** ~25% of turns on models below 4B.

**Mitigation:** Rescue parse (step 2 of the extraction pipeline, §9.2) catches
unclosed fences. If no code at all,
a templated nudge is appended: "You described code without emitting it..." After
2 consecutive nudges, counted as an error.

### 15.2 Whole-Context Single Sub-Call

**Symptom:** Model passes the entire context to one `llm_query()` call. Works for
small contexts, fails to generalize.

**Mitigation:** The anti-shortcut guard (§7.6) appends a warning when the sub-call
exceeds 60% of the context size. The decomposition prologue (§11.2) urges the
model to plan before coding.

### 15.3 Root History Bloat

**Symptom:** REPL output grows large, pushing the root context out of distribution.

**Mitigation:** Per-block REPL output is truncated to `repl_output_char_cap`
(default 4K chars). `show_vars()` lists names and types, not values. Sub-call
results stay in REPL variables — the model must deliberately `print()` to see them.

### 15.4 Broken Python from Root Model

**Symptom:** `NameError`, `SyntaxError`, `IndentationError` from model-generated code.

**Mitigation:** The traceback is fed back to the model in the next REPL-result
message, **and** the root loop appends a templated `NUDGE_STDERR_ERROR` that names
the exception class and the error count (§9.5). Small models are surprisingly
good at fixing their own code when shown the traceback.
Budget: `max_consecutive_errors` (default 3), then forced finalization.

### 15.5 Runaway Cell

**Symptom:** `while True:` or huge list allocation hangs the REPL.

**Mitigation:** Subprocess isolation + per-cell `cell_timeout`. On timeout the
harness returns a templated `CELL_TIMEOUT_ERROR` while **the worker keeps
running** — it is not killed, because killing it would discard the namespace the
model may still be relying on. The late result is then discarded by `cell_id`
rather than being handed to the model as the next cell's output (R4). Only after
**two consecutive timeouts** is the worker restarted, and the model is told its
variables were lost.

### 15.6 Sub-Call Quality Collapse

**Symptom:** Sub-call responses become incoherent when prompts exceed the model's
comfortable context size.

**Mitigation:** Per-prompt size warnings (soft). Sub-tier uses a model with large
native context. The system prompt teaches a staging idiom: coarse filter first,
then fine extraction on the survivors.

### 15.7 Prompt-Cache Misses

**Symptom:** Every turn takes 5–10× longer than expected because the server
re-evaluates the entire prefix.

**Mitigation:** All harness messages use `.format()` with identical ordering.
No timestamps, no per-turn randomness, no changing headers. `--cache-ram` on
the server. With kernel integration, the vault is consulted once at the start
of `run()` — the resulting prompt text is cached for all turns.

### 15.8 Chat-Template Drift

**Symptom:** Model behaves differently than expected because the server's chat
template doesn't match the model's training format.

**Mitigation:** The conformance suite (Chapter 16) runs per model×server pair
before any evaluation. Pinned server versions in the project lockfile.

### 15.9 Kernel Bridge Unavailable

**Symptom:** `kernel_bridge` provided but vault is corrupted or missing.

**Mitigation:** All kernel features degrade gracefully. If vault pages are
missing, prompt/template loading falls back to hardcoded constants. If the
vault itself is unreachable, `get_helper_definitions()` returns an empty
list and the REPL uses hardcoded helpers only. No kernel feature is required
for basic harness operation.

### 15.10 Vault Search Timeout

**Symptom:** `search()` in the REPL takes too long on large vaults.

**Mitigation:** FTS5 BM25 search over 100K pages is measured at 176 ms p95
(load-test report), an order of magnitude inside the 300 ms target. The search
call is bounded by the per-cell timeout (default 60 s). If the index is missing
the search returns no results and the CLI tells you to rebuild it.

---

## 16. Testing and Conformance

### 16.1 Test Suite Structure

872 tests collected; 12 marked `slow` (real-model integration + load corpus).

```
tests/
├── conftest.py                    # Shared fixtures (tiny_config, laptop_config)
├── test_config.py                 # Profile loading, overrides, prompt_vars
├── test_parser.py                 # Block extraction, rescue parsing, nudges, stderr, JSON repair
├── test_prompts.py                # System prompt injection, few-shot validation, vault few-shots
├── test_templates.py              # R4.1 discipline: no dead templates, no inline harness strings
├── test_context_store.py          # Byte-offset contract, disk spill, grep/chunk/lines
├── test_subcall_manager.py        # Budgets, memoization order, batched calls, exhaustion
├── test_repl.py                   # Sandbox, cell correlation, caps, lazy disk context, subcalls
├── test_root_loop_integration.py  # Turn loop, guardrail wiring, cap promise, nudge budgets
├── test_model_backend.py          # Endpoint normalization, guarded extraction, retry, TLS warning
├── test_logger.py                 # JSONL records, thread safety, default location, prune
├── test_chat.py                   # Interactive session commands and wiring
├── test_cli.py                    # CLI front-end (ask/search/get/tag/ingest/vault pass-through)
├── test_model_check.py            # P1–P9 probe battery against good/bad stubs
├── test_web.py                    # Routes, auth coverage, SSE auth, escaping, bounded stores
├── test_integration.py            # Real llama-server tests (marked slow, skips if unreachable)
├── evals/                         # Eval suites (regex-verified tasks) + pattern guards
│   ├── counting.py                #   counting/aggregation (anchored numeric patterns)
│   ├── fact_extraction.py         #   fact extraction
│   ├── multi_hop.py               #   multi-hop reasoning
│   ├── needle_search.py           #   needle-in-haystack
│   ├── test_eval_patterns.py      #   enforces the pattern convention (§16.5)
│   └── *.json                     #   data-only mirror of each suite (fallback loader)
├── load/                          # Load-gate benchmarks (marked slow + load)
└── rlm_kernel/
    ├── conftest.py                    # temp_vault fixture (singular kind dirs)
    ├── test_schema.py                 # frontmatter validation, parsing, helper extraction
    ├── test_vault.py                  # CRUD, atomic writes, wikilinks, round-trip
    ├── test_containment.py            # S4/R20 path containment + name validator
    ├── test_index.py                  # build, FTS search, incremental update, rebuild
    ├── test_search.py                 # keyword search, kind filter, card budget, edge cases
    ├── test_migration.py              # PRAGMA user_version migrations
    ├── test_gate.py                   # propose/validate/promote/demote state machine
    ├── test_gate_execution_policy.py  # R19: static by default, opt-in sandbox, trust model
    ├── test_promote_occupancy.py      # R16 occupancy guards, newest-first quarantine order
    ├── test_memory.py                 # decay-ordered search, forget filters, compaction
    ├── test_optimize.py               # GEPA promotion state hazards (R11)
    ├── test_optimize_tls.py           # R18: no process-global litellm ssl_verify
    ├── test_seed.py                   # seed idempotency and singular directories
    ├── test_repl_bridge.py            # helper definitions, search/propose proxying
    └── test_kernel_cli.py             # rlm-kernel init/index/review/promote/demote/search
```

`tests/rlm_kernel/` was five files before the 2026-09 remediation and is fifteen
after; the additions are what make each fixed defect falsifiable, including
`test_containment.py` and `test_gate_execution_policy.py` for the two security
findings (S4/R20 and S3/R19).

### 16.2 Running Tests

```bash
# Unit tests only (fast, no server required, no load-gate benchmarks)
uv run pytest -m "not slow and not load" -v

# All tests including integration (requires running llama-server)
uv run pytest tests/ -v

# Specific module
uv run pytest tests/test_parser.py -v

# Kernel tests only
uv run pytest tests/rlm_kernel/ -v

# Fallback parity check — verify kernel changes don't break rlm_local
uv run pytest tests/ -m "not slow" -k "not rlm_kernel" -v
```

Select the fast suite with **`-m`** (markers), not `-k` (names). `-k` is a
substring match against the node id, so `-k "not slow and not load"` also drops
20 tests that merely mention "load" in their name — `test_ingest_loads_file`,
the upload-cap tests, `test_base_template_loads_no_scripts` — and reports them
as "deselected" rather than running them.

Load-gate benchmarks live in `tests/load/` and carry **both** markers
(`slow` and `load`), so they are excluded by `-m "not slow"` and by
`-m "not load"`. Select them explicitly with `-m load` or
`uv run pytest tests/load/test_load.py -v`.

### 16.2.1 Documentation checks

```bash
# Structural lint over the living docs (fast, no dependencies)
uv run python scripts/check_docs.py
```

It flags the defect class R25 had to repair by hand: unbalanced ``` fences (a
stray trailing fence), a `§x.y` reference that resolves to no heading and names
no document, a relative link whose target is missing, and UTF-8 damage (a stray
BOM or U+FFFD — what a careless PowerShell round-trip leaves behind on
Windows). Only the *living* documents are checked; `docs/2026*.md` are
point-in-time records, so stale claims in them are history rather than defects.

### 16.3 Test Conventions

- **Unit tests** use fake backends (`FakeBackend`/`StubBackend`) and mock subcall
  managers (`MockSubcallMgr`). Individual modules run in well under a second; the
  whole fast suite is around five minutes because the root-loop and REPL tests
  run the real turn loop against a stub model.
- **Kernel tests** use temporary vaults (`temp_vault` fixture) with `init_git=False`.
- **Integration tests** (`tests/test_integration.py`) run against a real
  `llama-server`, and resolve their target from the environment:

  | Variable | Default |
  |---|---|
  | `RLM_TEST_ENDPOINT` | `Profile.root_endpoint` — `https://localhost:9010/v1` |
  | `RLM_TEST_MODEL` | `Profile.root_model` — `Qwen3.5-4B-Abliterated` |

  They skip (rather than fail) when the endpoint is unreachable, so a machine
  without a model server still gets a clean fast-suite-minus-`slow` run. To point
  them at a server elsewhere:

  ```bash
  RLM_TEST_ENDPOINT=https://lunacode:9010/v1 \
  RLM_TEST_MODEL=Qwen3.5-4B-Abliterated \
  uv run pytest tests/test_integration.py -m slow -q
  ```

  They are marked `@pytest.mark.slow` and take about eight seconds against a
  warm server.
- **REPL tests** launch real subprocess workers and exercise the full TCP
  protocol, cell-id correlation, subcall proxying, and state persistence.

### 16.4 Model-check probes (`model_check.py`) — scoring and known weaknesses

`rlm check <model>` runs the P1–P9 battery: a weighted average, normalised to
100, with verdict bands at ≥75 SUITABLE / 50–74 MARGINAL / <50 NOT SUITABLE.

**Weighting is a named profile, not a constant** (`WEIGHT_PROFILES`, selected
with `--weights` or `RLM_CHECK_WEIGHTS`, recorded in every result):

| Profile | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 |
|---|---|---|---|---|---|---|---|---|---|
| `default` | 10 | 15 | 15 | 20 | 10 | 20 | 5 | 5 | 0 (report-only) |
| `p1-heavy` | 20 | 15 | 15 | 15 | 10 | 15 | 5 | 5 | 0 (report-only) |

The default moved 10 points from P1 to P4 and P6 after the 2026-09-11 router
sweep: **P1 is saturated** — all ten models on that router, down to 0.8B, scored
20/20 — so its weight only compressed the spread produced by P4 (voluntary
submission) and P6 (needle retrieval), which is where the entire SUITABLE /
MARGINAL split came from. `p1-heavy` is the pre-change scale, kept so the scores
in `docs/20260911-0050-router-model-assessment.md` stay reproducible. Because
`_score_model` normalises by the total weight, a profile only matters through
the *ratios* between probes: on the quick battery a model that passes P1 alone
scores 40/100 under `p1-heavy` and 20/100 under `default`, while one that passes
P4 and P6 but not P1 scores 60 vs 80.

**P4 is multi-trial, like P1.** It runs `P4_QUERIES` — three different questions,
the first being the original single-shot query so older runs stay comparable at
the trial level — and scores the mean credit: 15 points for a voluntary
submission per trial, `8/15` of that if the submission only happened after forced
finalization, 0 for none. `passed` requires a *majority* of trials to submit
voluntarily, so a model that does it in two of three runs passes with 10/15 and
its flakiness is visible in the score. Per-trial outcomes are reported in the
evidence, and `RLM_CHECK_P4_TRIALS` (1–3, unusable values fall back to 3) trades
wall time for stability — a run using fewer trials says so.

The reason is recorded, not stylistic: P4 was single-shot, and a live run of
`Qwen3.5-2B-Instruct` scored 46.7 (NOT SUITABLE) then 86.7 (SUITABLE) minutes
later on the same prompt, so one sample decided a verdict band
(`docs/20260911-1359-p4-live-confirmation.md`).

*Residual, pinned by a test:* the `forced`-submission credit cannot fire today —
the loop logs `forced=True` only when no REPL submission happened, so live P4
outcomes are voluntary or nothing. The credit is kept for continuity in case the
loop changes, and `TestP4ForcedSubmissionIsUnreachable` pins the unreachability.

**P4 also reports where a submission line went.** When submission text is present
in the transcript and no submission executed, the probe attaches a
`DIAGNOSTIC [code]` per affected trial, ordered by evidence quality — a block the
interpreter actually ran outranks a stray prose mention, and within an executed
block the text/code verdict outranks a traceback elsewhere in the cell:

| Code | Meaning |
|---|---|
| `submission_block_raised` | The line is in an executed block whose cell raised (the traceback's last line is quoted). |
| `submission_answer_rebound` | The cell ended with `answer` rebound to something that is not a dict, so no submission could have taken effect. Needs the cell-end state below. |
| `submission_not_reached_at_runtime` | The line is a real statement in a block that ran clean and never reached it. When the cell-end state proves the scaffold survived the cell, the detail says so; without it, the detail lists the possibilities instead of asserting one. |
| `submission_state_inconsistent` | `answer` was ready at cell end yet no submission was recorded, or `answer` could not be inspected — a harness inconsistency or limitation, not a model verdict. |
| `submission_text_is_quoted_or_commented` | The line is *text* inside the block — in a string literal (`print("answer['ready'] = True")`) or after a `#`. The regex matches it, the interpreter never runs it as a statement. Observed live in `Qwen3.5-2B-Instruct`. |
| `submission_text_in_unexecutable_fence` | The line is inside a fence tag the parser does not execute (only `repl`, `python` and untagged fences run). |
| `submission_text_outside_fence` | The line is prose; the parser extracts only fenced blocks. |

Precedence is by evidence quality: an executed block outranks a stray prose
mention of the same line; within an executed block the text/code verdict outranks
a traceback (the line never being a statement is the actionable fact), and a
traceback outranks the end-state readings, because a traceback is an observed
event while an end state is an interpretation of what was left behind.

The diagnostic does not change a trial's credit: a trial in which nothing
submitted scores 0 for that trial, now with a reason.

### 16.4.1 Probe scoring that was tightened on 2026-09-12

Three probe behaviours used to be documented, deferred weaknesses. They were
tightened as roadmap items 4 and 5 (`docs/20260912-1155-roadmap.md`), on the
owner's decision, because each one moved score semantics. The old behaviour and
the reasoning for deferring it stay on the record in the 2026-09-10 validation
(§7) and the 2026-09-03 analysis (F15) — they are history now, not description.

- **P2 — an unterminated call is no longer valid (BS3).** `_extract_call_text`
  returns `""` for a call that is never closed, and `_balanced_parens("")` is
  `True`, so `grep('never closed` used to cost the model nothing. P2 now requires
  a call it can actually delimit. `_balanced_parens` itself still returns `True`
  for the empty string: it is a predicate about parens, and emptiness is the
  caller's business.
  *(The earlier P2 defect in the same area — an escaped quote inside a call
  truncating the extraction at a `)` in the string — was fixed during the
  remediation; `tests/test_model_check.py::TestParenLexerEscape` still pins it.)*
- **P3 — no free credit for a model that executes nothing (BS1).** A model that
  never ran a cell scored 15/15, exactly like a model that recovered from an
  error. It now scores 0: emitting nothing is not recovering. Cells that all run
  clean still score 15/15 — executing working code is not a failure to recover.
- **P3 — recovery is time-ordered and behavioural (BS2).** The scan used to
  accept a balanced `grep(` call from *any* assistant message, including one made
  before the failing turn. Recovery is now "a cell *after* the failure ran
  without a traceback", which needs no regex and measures the behaviour rather
  than a proxy for it. A valid helper call after the failure is still reported,
  as a supporting signal.

Effect on recorded numbers: the sweep's quick batteries (P1+P4+P6) are unaffected.
Full-battery scores recorded before 2026-09-12 are on the old P2/P3 semantics, and
P3 in particular was worth 15 unearned points to a model that produced no code —
so those composites are not comparable with new ones. The change is pinned by
`tests/test_model_check.py::TestProbeP3`,
`::TestProbeP2NowRejectsUnterminatedCalls` and `::TestTurnOrdering`, and its
falsifiability by four new mutations in `scripts/check_guard_nonvacuity.py`.

### 16.5 Needle and eval-pattern matching

Answer matching is case-, hyphen-, and whitespace-insensitive — the documented
`O-Negative` / `O Negative` evaluator bug cost 5 points for a correct answer:

- **P6 (`model_check.py`)** compares `_normalize_for_match(answer)` against
  `_normalize_for_match(needle)`: lowercase, smart quotes and dashes folded to
  ASCII, dashes → spaces, whitespace collapsed.
- **Eval suites (`tests/evals/*.py`)** must anchor every pattern at a token
  boundary, because the evaluator is an unanchored `re.search`: `"35"` used to
  pass for `"135"`. Word tokens use `\b`; numeric tokens use `(?<!\d)` /
  `(?!\d)` so that `25.8C` still matches. The convention and the enforcing tests
  live in `tests/evals/__init__.py` and `tests/evals/test_eval_patterns.py`.

---

## 17. API Reference

### 17.1 `rlm_local` (top-level)

```python
def completion(
    query: str,
    context: str | list[str],
    *,
    profile: str = "laptop",
    backend: ModelBackend | None = None,
    config: Config | None = None,
    logger: TrajectoryLogger | None = None,
    log_path: str | None = None,
    kernel_bridge: Any = None,
    **overrides: Any,
) -> str:
```

### 17.2 `rlm_local.config`

```python
@dataclass(frozen=True)
class Profile:
    name: str
    root_model: str
    sub_model: str
    root_endpoint: str
    sub_endpoint: str
    root_ctx_size: int
    sub_ctx_size: int
    sub_prompt_char_budget: int
    repl_output_char_cap: int
    max_turns: int
    max_concurrent_subcalls: int
    max_subcalls: int
    max_subcall_chars: int
    max_depth: int
    cell_timeout: float
    context_spill_threshold: int
    max_consecutive_errors: int
    max_consecutive_nudges: int
    shortcut_warn_fraction: float

@dataclass
class Config:
    profile: Profile
    overrides: dict[str, Any]
    example_chunking_idiom: str
    def prompt_vars(self) -> dict[str, Any]: ...

PROFILES: dict[str, Profile]  # {"tiny": ..., "laptop": ..., "workstation": ...}

def load_config(
    profile_name: str = "laptop",
    config_path: str | None = None,
    **overrides: Any,
) -> Config: ...
```

### 17.3 `rlm_local.model_backend`

```python
class ModelBackend(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...

class HTTPModelBackend:
    def __init__(
        self,
        root_endpoint: str = "https://localhost:9010/v1",
        sub_endpoint: str = "",
        root_model: str = "",
        sub_model: str = "",
        verify: bool = False,
        timeout: float = 300.0,
        headers: dict[str, str] | None = None,
    ) -> None: ...
    def chat(self, messages, *, tier, max_tokens, temperature, response_schema) -> str: ...
    def close(self) -> None: ...
```

`normalize_endpoint(url)` and `ModelBackendError` are part of this module's public
surface (R9).

### 17.4 `rlm_local.repl`

```python
@dataclass
class REPLResult:
    stdout: str
    stderr: str
    final_answer: str | None
    warnings: list[str]

class REPLSandbox:
    def __init__(self, cell_timeout: float = 60.0,
                 stdout_cap: int = 256 * 1024,
                 restart_after_consecutive_timeouts: int = 2) -> None: ...
    def start(self, context: Any, subcall_manager: Any,
              definitions: list[dict[str, str]] | None = None) -> None: ...
    def execute(self, code: str) -> REPLResult: ...
    def restart_worker(self) -> None: ...
    def shutdown(self) -> None: ...
```

`RootLoop` constructs this with `stdout_cap=config.repl_output_char_cap`, so the
2 000/4 000/8 000-char profile cap is what the prompt advertises *and* what is
enforced (R10).

### 17.5 `rlm_local.subcall_manager`

```python
class SubcallManager:
    def __init__(
        self,
        backend: ModelBackend,
        *,
        max_concurrent: int = 2,
        max_calls: int = 60,
        max_chars: int = 4_000_000,
        prompt_char_budget: int = 16000,
        context_total_chars: int = 0,
        shortcut_warn_fraction: float = 0.60,
    ) -> None: ...
    def llm_query(self, prompt: str, *, schema: dict[str, Any] | None = None) -> str: ...
    def llm_query_batched(self, prompts: list[str], *, schema: dict[str, Any] | None = None) -> list[str]: ...
    @property
    def calls_used(self) -> int: ...
    @property
    def chars_used(self) -> int: ...
    @property
    def calls_remaining(self) -> int: ...
    @property
    def cache_hits(self) -> int: ...
    def shutdown(self) -> None: ...
```

### 17.6 `rlm_local.parser`

```python
@dataclass
class ParseResult:
    blocks: list[str]
    final_answer: str | None
    warnings: list[str]
    nudge: str | None
    raw_text: str

class Parser:
    def __init__(self, max_consecutive_nudges: int = 2, max_consecutive_errors: int = 3) -> None: ...
    def parse(self, text: str, *, turn: int = 0) -> ParseResult: ...
    def parse_stderr(self, text: str, stderr_text: str) -> ParseResult | None: ...
    def check_answer_in_block(self, block: str) -> tuple[str | None, bool]: ...
    @property
    def consecutive_nudges(self) -> int: ...
    @property
    def consecutive_errors(self) -> int: ...
    def reset(self) -> None: ...
    def reset_errors(self) -> None: ...

def repair_json(text: str, schema: dict[str, Any] | None = None) -> str: ...
```

`parse_stderr()` is the design §5.6 stage-4 entry point the root loop calls after
a cell produced a traceback; `reset_errors()` is called after a clean cell.
`parse()` deliberately does **not** reset the error streak (R5).

### 17.7 `rlm_local.context_store`

```python
class Context:
    """Disk-backed, byte-addressed lazy handle (R1/R2)."""
    def __init__(self, path: Path, total_bytes: int,
                 line_offsets: list[int] | None = None) -> None: ...
    def __len__(self) -> int: ...            # UTF-8 byte count
    def __getitem__(self, key: int | slice) -> str: ...   # byte offsets
    def __iter__(self) -> Iterator[str]: ...
    def __str__(self) -> str: ...
    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]: ...
    def grep(self, pattern: str, max_hits: int = 50) -> list[str]: ...
    def chunk(self, size: int | None = None, by: str | None = None) -> list[str]: ...

class _InMemoryContext:
    """Same API and the same byte-addressing contract, for inputs below the
    spill threshold."""

class ContextStore:
    def __init__(self, spill_threshold: int = 1_000_000, temp_dir: str | None = None) -> None: ...
    def ingest(self, context: str | list[str] | Sequence[str]) -> Context | _InMemoryContext: ...
    def cleanup(self) -> None: ...
```

### 17.8 `rlm_local.logger`

```python
class TrajectoryLogger:
    """Thread-safe JSONL logger. Default path is logs/trajectories/ under the
    working directory; retention is explicit via prune()."""
    def __init__(self, path: str | Path | None = None) -> None: ...
    @property
    def path(self) -> Path: ...
    @staticmethod
    def prune(directory: str | Path = DEFAULT_LOG_DIR, keep: int = 50) -> int: ...
    def log_start(self, query: str, context_len: int, config: dict[str, Any]) -> None: ...
    def log_turn_start(self, turn: int, max_turns: int) -> None: ...
    def log_root_message(self, role: str, content: str) -> None: ...
    def log_repl_result(self, turn: int, stdout: str, stderr: str,
                        final_answer: str | None, warnings: list[str]) -> None: ...
    def log_subcall(self, turn: int, index: int, prompt: str, response: str,
                    schema: dict[str, Any] | None = None, cached: bool = False) -> None: ...
    def log_guardrail(self, turn: int, guardrail: str, detail: str) -> None: ...
    def log_end(self, final_answer: str, turns_used: int, subcalls_used: int,
                forced: bool = False) -> None: ...
```

### 17.9 `rlm_local.prompts`

```python
SYSTEM_PROMPT: str                          # .format() template
FEWSHOT_EXAMPLE: list[tuple[str, str]]      # (role, content) pairs: the builtin
                                            # needle-search example plus the R25.6
                                            # voluntary-submission example
EXAMPLE_2_HEADER: str
FEWSHOT_SUBMISSION_EXAMPLE: list[tuple[str, str]]

def build_system_prompt(prompt_vars: dict) -> str: ...
def build_messages(
    query: str, context_len: int, context_type: str, prompt_vars: dict,
) -> list[dict[str, str]]: ...

# Kernel-enabled vault-first loading
def load_system_prompt_from_vault(
    prompt_vars: dict, vault: object | None = None,
) -> str: ...
def parse_fewshot_body(body: str) -> list[tuple[str, str]]: ...
def load_fewshots_from_vault(
    vault: object | None = None,
    prompt_char_budget: int | None = None,
) -> list[tuple[str, str]]: ...
```

### 17.10 `rlm_local.templates`

All constants are `str` values, most are `.format()` templates. See §11.2 for the
complete catalog. **Introspection-only:** there is no vault-first template
loader. `load_template(name, vault=)` was documented here and in §11.2.1 but was
deleted (R3-D10) before it ever had a caller — see §11.2.1 for the historical
note. Vault-first loading applies to the system prompt, the helper listing, and
the few-shots (`prompts.py`), not to these constants.

---

## Appendix A: Glossary

| Term | Definition |
|---|---|
| **RLM** | Recursive Language Model. A model that answers queries by writing and executing code in a REPL, delegating sub-tasks to itself. |
| **LID** | Locally In-Distribution. Every individual LM call sees a prompt within its training distribution, even when the overall task is out-of-distribution. |
| **Context offloading** | Storing the user's data in the REPL (or on disk) rather than in the model's conversation history. |
| **Equivalence class** | Two tasks are equivalent under the harness if they produce structurally identical root-model trajectories, enabling compositional generalization. |
| **Root model** | The orchestrator that plans, decomposes, writes code, and aggregates results. Runs at `temperature=0`. |
| **Sub-call** | A plain LM call issued from REPL code via `llm_query()`. No history, no system prompt, self-contained. |
| **Few-shot** | Worked examples embedded in the system prompt demonstrating the exact message format. |
| **Decomposition nudge** | The mandatory turn-0 requirement to probe + plan before coding. Prevents the "one big sub-call" shortcut. |
| **Forced finalization** | When turn or error budgets are exhausted, a final prompt asks the model to give its best answer with what it has. |
| **Rescue parse** | Mechanical fixes for common model output errors (unclosed fences, wrong fence type) applied before spending a retry turn. |
| **Nudge** | A templated user message appended to the conversation asking the model to fix a specific format error. |
| **Byte-stable prefix** | The initial messages (system prompt, metadata, prologue, few-shots) are identical across all calls and tasks to maximize prompt-cache hits. |
| **Kernel bridge** | The `KernelBridge` object that connects `rlm_local` to an `rlm-kernel` vault, enabling vault-first prompt loading, helper injection, and search/propose proxying. |
| **Vault** | The git-versioned directory of markdown pages that serves as the system's persistent "image." |
| **Gate** | The quarantine → validate → promote lifecycle that governs how model-authored content enters the live system. |
| **Core memory** | A pinned page (`memory/notes/core-memory.md`) whose summary is always included in the root model's metadata message. |

---

## Appendix B: Design Document Cross-Reference

Every design requirement from the architecture document maps to specific
implementation locations:

| Requirement | Summary | Implemented In |
|---|---|---|
| R1.1 | Root model never sees raw context | `prompts.py:build_messages()` — context length, not content, in metadata |
| R1.2 | Every sub-call gets a bounded, self-contained prompt | `subcall_manager.py:_do_call()` — no history, no system prompt |
| R1.3 | Root history stays short; aggressive REPL output truncation | `templates.py:REPL_RESULT_TEMPLATE`, `config.py:repl_output_char_cap` |
| R1.4 | Prompts are boring: plain prose + fenced code | `prompts.py:SYSTEM_PROMPT`, `templates.py` — no exotic markup |
| R2.1 | Context lives in REPL, not root prompt | `root_loop.py:run()` — context ingested to `ContextStore`, not messages |
| R2.2 | Root model's first view is task-agnostic | `prompts.py:build_messages()` — metadata template, same format for all tasks |
| R3.1 | Sub-calls are Python functions, not JSON tool-calling | `repl.py` worker — `llm_query`/`llm_query_batched` as callable names |
| R3.2 | Sub-call outputs land in REPL variables | `repl.py` worker — `exec(code, globals())` for state persistence |
| R3.3 | Helper library: `peek`, `grep`, `chunk`, `map_query`, `show_vars` | `repl.py` worker script — each ≤ 15 lines |
| R4.1 | All harness text is templated and frozen | `templates.py` — 16 constants; `prompts.py` — config-injected system prompt |
| R4.2 | Trajectory logging records root context verbatim | `logger.py:TrajectoryLogger` |
| R5.1 | Decomposition nudge always on | `prompts.py:build_messages()` — `PROLOGUE` appended unconditionally |
| R5.2 | Turn 0 forced to probe + plan | `templates.py:TURN_ZERO_SAFEGUARD` |
| R5.3 | Anti-shortcut: >60% context sub-call flagged | `subcall_manager.py:_do_call()` — `SHORTCUT_WARNING` |
| R6.1 | `max_depth = 1` default | `config.py:Profile.max_depth` |
| R7.1 | Drop-in `completion(query, context) -> str` | `__init__.py:completion()` |

---

## Appendix C: Kernel Integration Cross-Reference

| Feature | Kernel Module | rlm_local Integration Point |
|---|---|---|
| Vault-first prompt loading | `prompts.py:load_system_prompt_from_vault()` | `root_loop.py:run()` |
| ~~Template vault loading~~ | **removed (R3-D10)** — `templates.py:load_template()` does not exist; see §11.2.1 | — |
| Helper injection | `repl_bridge.py:KernelBridge.get_helper_definitions()` | `repl.py:REPLSandbox.start(definitions=)` |
| `search()` in REPL | `repl_bridge.py:KernelBridge.handle_search()` | `repl.py:REPLSandbox.execute()` |
| `propose()` in REPL | `repl_bridge.py:KernelBridge.handle_propose()` | `repl.py:REPLSandbox.execute()` |
| Core memory summary | `repl_bridge.py:KernelBridge.get_core_memory_summary()` | `root_loop.py:run()` |
| Vault seeding | `seed.py:seed_vault()` | `cli.py` and first-run scripts |

---

*RLM Local is built on the principles articulated by Zhang & Khattab in
"Language Model Harnesses Are Compositional Generalizers" (July 2026) and
"Recursive Language Models" (October 2025). The harness architecture,
prompt design, guardrails, and evaluation methodology adapt those principles
to the constraints of consumer hardware and small open-weight models.*

*RLM Kernel adds the evolvability layer — the convergence of LISP's
metacircular eval, Smalltalk's image, Forth's dictionary, and Nock's frozen
core — making the system's prompts, helpers, memory, and ontology into
human-readable, versioned, self-describing content.*
