# RLM Local: A Recursive Language Model Harness for Small Models

**Version 0.1.0 | July 2026**

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
14. [Failure Modes and Mitigations](#14-failure-modes-and-mitigations)
15. [Testing and Conformance](#15-testing-and-conformance)
16. [API Reference](#16-api-reference)

---

## 1. Architecture Overview

The RLM harness wraps a single local model and presents it as a
**drop-in `completion()` function**—one call, one answer. Everything else is internal.

### 1.1 The Big Picture

```
User ──► completion(query, context)
              │
              ▼
     ┌──────────────────────────────────────────────────┐
     │                   RootLoop                       │
     │                                                  │
     │  messages = [system + few-shot + metadata]       │
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
              │
              ▼
     TrajectoryLogger (JSONL)
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
look structurally identical to the root model—a property called
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
```

---

## 2. Quick Start

### 2.1 Installation

```bash
git clone <repo> rlm-local
cd rlm-local
uv sync
```

Dependencies: Python ≥ 3.12, `httpx`, `setuptools`. The harness is pure Python with
no native extensions.

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

### 2.4 What Happens Internally

When you call `completion()`:

1. **Profile loaded.** The `"laptop"` profile sets context windows, budgets, turn
   limits, and timeouts.
2. **Context ingested.** The raw text is wrapped in a `Context` handle and made
   available in the REPL as `context`.
3. **Messages built.** The system prompt, task metadata (query + context *size*,
   never the content itself), decomposition prologue, and a few-shot example are
   assembled into a byte-stable message list.
4. **REPL started.** A subprocess Python interpreter is launched, connected over
   a local TCP socket.
5. **Turn loop begins.** The root model receives the messages, emits code in
   ` ```repl ` blocks, the harness executes that code in the REPL, and the output
   is appended to the conversation.
6. **Termination.** The model sets `answer["ready"] = True`, writes a `FINAL:` line,
   or exhausts its turn budget (triggering forced finalization).
7. **Cleanup.** REPL subprocess killed, context files removed, HTTP client closed.
8. **Answer returned.** A plain string.

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
#     "repl_cap": 2000,          # repl_output_char_cap
#     "sub_budget": 8000,        # sub_prompt_char_budget
#     "max_turns": 12,           # max_turns
#     "example_chunking_idiom": "...",
#     "root_ctx_size": 8192,
#     "sub_ctx_size": 8192,
# }
```

### 4.4 The `Profile` Dataclass

`Profile` is a frozen (`@dataclass(frozen=True)`) dataclass. Instances are
immutable after creation—profiles are constants, not mutable state.

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

### 5.2 Constructor

```python
HTTPModelBackend(
    root_endpoint: str = "https://localhost:9010/v1",
    sub_endpoint: str = "",
    root_model: str = "",
    sub_model: str = "",
    verify: bool = False,
    timeout: float = 300.0,
)
```

| Parameter | Default | Description |
|---|---|---|
| `root_endpoint` | `"https://localhost:9010/v1"` | Base URL for root-tier chat completions |
| `sub_endpoint` | `""` (same as root) | Base URL for sub-tier chat completions |
| `root_model` | `""` (from config) | Model name for root tier |
| `sub_model` | `""` (same as root) | Model name for sub tier |
| `verify` | `False` | TLS certificate verification (self-signed certs on localhost) |
| `timeout` | `300.0` | HTTP request timeout in seconds |

### 5.3 Two-Tier Routing

The `tier` parameter on `chat()` selects the endpoint and model:

- `"root"` → `root_endpoint` + `root_model`
- `"sub"` → `sub_endpoint` + `sub_model`

When `sub_endpoint` is empty and `sub_model` is empty, both tiers use the same
server and model. This is the common case: one `llama-server` process, one model,
handling both orchestration and bulk sub-calls.

For advanced deployments, point `sub_endpoint` at a second server running a
smaller/faster model. The harness does not manage model lifecycle—start your
servers externally.

### 5.4 Prefix-Cache Discipline

The system prompt is byte-identical across all calls. Conversation history is
append-only. The harness contains **no timestamps, no per-turn randomness, no
changing headers**. This ensures llama.cpp's `--cache-ram` prompt caching hits
on every turn after the first, reducing prompt evaluation latency by 5–10×.

> **Warning:** A single changing character anywhere in the prefix silently
> invalidates the entire cache. Do not add timestamps or dynamic text to any
> message that appears before the latest turn.

### 5.5 Response Schema (Constrained Decoding)

When `response_schema` is passed, the backend sets `response_format` to
`{"type": "json_schema", ...}` per the OpenAI structured output spec.
llama.cpp servers with GBNF grammar support will enforce the schema.

The harness uses constrained decoding **only for sub-calls** and only when
explicitly requested via `llm_query(prompt, schema=...)`. The root model's
free-form ` ```repl ` output is never grammar-constrained—rescue parsing
(Chapter 9) is cheaper and safer there.

---

## 6. The REPL Sandbox

### 6.1 Design Rationale

The REPL is a **subprocess-isolated** Python interpreter, not an in-process
`exec()`. This decision has three motivations:

1. **Killability.** A small model emitting `while True:` must not hang the harness.
   The subprocess can be killed on timeout.
2. **Memory isolation.** The worker's memory is bounded by the OS, not by the
   harness process.
3. **Restricted builtins.** `input`, `eval`, `exec`, `compile`, `globals`, and
   `locals` are blocked; `open` is restricted to the task temp directory.

### 6.2 Communication Protocol

The harness and worker communicate over a **bidirectional TCP socket** on
`localhost` using a length-prefixed JSON protocol:

```
[4-byte big-endian length][UTF-8 JSON payload]
```

Message types sent by the harness:
- `{"cmd": "init", "context": "<raw text>"}` — initialize the worker
- `{"cmd": "exec", "code": "<python source>"}` — execute a cell
- `{"cmd": "shutdown"}` — terminate the worker

Message types sent by the worker:
- `{"type": "result", "stdout": "...", "stderr": "...", "final_answer": null}` — cell result
- `{"type": "subcall", "prompt": "...", "schema": null}` — request a sub-LLM call
- `{"type": "subcall_batched", "prompts": [...], "schema": null}` — request batched sub-calls

The subcall messages are **interleaved** with execution: the worker sends a
subcall request, the harness proxies it to the `SubcallManager`, sends the
response back, and the worker continues executing the cell.

### 6.3 REPL Namespace

When a cell executes, the following names are available in the worker's global
scope:

| Name | Type | Description |
|---|---|---|
| `context` | `str` | The full user context (as a plain string in the worker) |
| `answer` | `dict` | `{"content": "", "ready": False}` — set to signal completion |
| `llm_query(prompt, *, schema=None)` | function | One-shot sub-LLM call; no history, no system prompt |
| `llm_query_batched(prompts, *, schema=None)` | function | Batched sub-calls with bounded parallelism |
| `peek(n=2000)` | function | Print and return the first `n` characters of context |
| `grep(pattern, max_hits=50)` | function | Regex search over context lines; prints and returns hits |
| `chunk(size=None, by=None)` | function | Split context into chunks; `by="paragraph"` for paragraph splitting |
| `map_query(items, template, batch=True)` | function | Apply a template to items and call `llm_query_batched` |
| `show_vars()` | function | Print all user-defined variables in the REPL |

### 6.4 The `answer` Dict Mechanism

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

### 6.5 State Persistence

Variables defined in one cell are available in the next. The worker uses
`exec(code, globals())` so that assignments survive across cells:

```python
# Cell 1
x = 42

# Cell 2
print(x)  # → 42
```

### 6.6 Cell Limits

| Limit | Default | Behavior on Violation |
|---|---|---|
| Wall-clock timeout | 60 s (`tiny`/`laptop`), 120 s (`workstation`) | Worker killed; error returned as stderr |
| stdout capture | 256 KB | Truncated with `[... output truncated to N characters ...]` marker |
| stderr capture | 256 KB | Truncated similarly |

### 6.7 REPLSandbox Class

```python
class REPLSandbox:
    def __init__(self, cell_timeout: float = 60.0, stdout_cap: int = 256 * 1024): ...
    def start(self, context: Any, subcall_manager: Any) -> None: ...
    def execute(self, code: str) -> REPLResult: ...
    def shutdown(self) -> None: ...
```

`start()` launches the subprocess, accepts the TCP connection, and sends the
initial context.

`execute()` sends code, handles interleaved subcall requests, and returns a
`REPLResult`:

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
    sub_model: str = "",
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

```python
# First call: goes to server
result1 = mgr.llm_query("Summarize: The quick brown fox...")

# Second call with identical prompt: cache hit
result2 = mgr.llm_query("Summarize: The quick brown fox...")
assert result1 is result2  # same string object
```

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
| `calls_used` | `int` | Total sub-calls executed |
| `chars_used` | `int` | Total prompt characters sent |
| `calls_remaining` | `int` | `max_calls - calls_used` (floor 0) |
| `cache_hits` | `int` | Number of distinct cached prompts |

---

## 8. The Root Loop

### 8.1 Role

`RootLoop` is the orchestrator. It owns the turn-by-turn conversation with the
root model, dispatches code to the REPL, interprets results, and decides when
to terminate.

### 8.2 Message Layout

The message list is built once at startup and grown append-only. The byte-stable
prefix is critical for prompt caching:

```
┌──────────────────────────────────────────────┐
│ system     │ SYSTEM_PROMPT (config-injected)  │  ← byte-stable
├────────────┼─────────────────────────────────┤
│ user       │ METADATA (query + context size) │  ← byte-stable
├────────────┼─────────────────────────────────┤
│ user       │ PROLOGUE (decomposition nudge)  │  ← byte-stable
├────────────┼─────────────────────────────────┤
│ (few-shot) │ user/assistant pairs            │  ← byte-stable
├────────────┼─────────────────────────────────┤
│ user       │ Turn 1/15. (safeguard)          │  ← first novelty
├────────────┼─────────────────────────────────┤
│ assistant  │ model response                  │
├────────────┼─────────────────────────────────┤
│ user       │ REPL output: ...                │
├────────────┼─────────────────────────────────┤
│ user       │ Turn 2/15.                      │
├────────────┼─────────────────────────────────┤
│ ...        │ ...                             │
└────────────┴─────────────────────────────────┘
```

### 8.3 Turn Flow

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
   `repl.execute(block)`.

6. **Termination checked.** If `result.final_answer` is set, the loop breaks.

7. **Output formatted.** The REPL stdout/stderr is converted to a templated
   `"REPL output:"` message and appended.

8. **Error budget checked.** If `consecutive_errors > max_consecutive_errors`,
   the loop breaks into forced finalization.

### 8.4 Termination Paths

The loop terminates by one of four mechanisms, in priority order:

1. **answer-dict signal (highest priority).** `answer["ready"] == True` detected
   in post-execution REPL state. Returns `answer["content"]`.

2. **Courtesy `FINAL:` line.** The parser detects a line matching
   `^FINAL:\s*(.+)$`. Returns the captured text.

3. **Turn exhaustion.** All `max_turns` consumed without termination. Triggers
   forced finalization (see below).

4. **Error budget exhaustion.** `max_consecutive_errors` exceeded. Triggers
   forced finalization.

### 8.5 Forced Finalization

When the turn loop ends without a final answer, a single forced-finalization
call is made:

```
"Based on everything you have learned so far, provide your best final
answer now. Summarize your findings in plain text."
```

The model's response to this prompt becomes the final answer. If the response
contains a `FINAL:` line, that is extracted; otherwise the raw text is returned.

Forced finalization **never returns empty**. If the model produces nothing,
the string `"(No answer produced — forced finalization failed)"` is returned.

### 8.6 Lifecycle

```python
loop = RootLoop(config, backend, logger)
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
when the model drifts off-format. Every rule is mechanical and cheap—the harness
never asks the model to re-do work it can fix itself.

### 9.2 Extraction Pipeline

The `parse()` method applies these rules in order, returning on the first match:

| Step | Rule | Example Input |
|---|---|---|
| 1 | Extract ` ```repl ` fenced blocks (DOTALL regex) | ```` ```repl\nprint("hi")\n``` ```` |
| 2 | Rescue: unclosed final fence | ```` ```repl\nprint("hi") ```` |
| 3 | Rescue: ` ```python ` or bare ` ``` ` fences | ```` ```python\nprint("hi")\n``` ```` |
| 3b | Rescue: unclosed bare fence (narration pattern) | ```` ```python\nprint("hi") ```` |
| 4 | Courtesy `FINAL:` line | `FINAL: The answer is 42.` |
| 5 | Narration nudge (code keywords, no fences) | `I would run llm_query to find...` |
| 6 | Generic no-block nudge | `The answer is 42.` |

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
budget, the parser emits no nudge—the turn counts as an error and the error
budget is checked separately.

### 9.5 Error Tracking

`Parser` tracks `consecutive_errors` (incremented on stderr from REPL) and
`consecutive_nudges` (incremented on unparseable output). Both counters reset
to zero on any successful parse or reset. The `RootLoop` checks the error
counter against `max_consecutive_errors` to trigger forced finalization.

### 9.6 Answer-in-Block Detection

`check_answer_in_block(block)` scans a code block for the answer-dict pattern:

```python
content, ready = parser.check_answer_in_block(code)
# content: str | None — the value assigned to answer["content"]
# ready: bool — whether answer["ready"] = True appears
```

This is used as a fast-path check before REPL execution, though the definitive
check is the post-execution REPL state.

### 9.7 JSON Repair

`repair_json(text, schema=None)` repairs malformed JSON from sub-call responses
(§5.6 item 6). It strips markdown fences, finds the first `{`, balances braces,
and returns the extracted object. If no JSON is found, the text is returned
as-is.

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

### 10.3 In-Memory vs. Disk-Backed

- **Below `context_spill_threshold`:** An `_InMemoryContext` wraps the raw string.
  All operations are O(1) on the in-memory Python string.

- **Above `context_spill_threshold`:** The text is written to a temp file and a
  `Context` handle is returned. Slicing reads byte ranges; `grep()` streams
  line-by-line.

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
templated and stable—identical wording for identical situations across tasks.
Free-form harness prose would break the token-level similarity that equivalence
class induction depends on.

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
| `FORCED_FINALIZATION_PROMPT` | Final forced-finalization message | (none — static) |
| `CELL_TIMEOUT_ERROR` | Cell exceeded time limit | `{timeout}` |
| `CELL_STDOUT_TRUNCATED` | Output truncated marker | `{cap}` |

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

### 11.4 Few-Shot Example

The few-shot is **load-bearing**. Small models need the format demonstrated, not
described. The example shows:

- Probing the context with `peek()`
- Searching with `grep()`
- Verifying results before finalization
- Setting `answer["content"]` and `answer["ready"] = True`

The example uses a **generic, synthetic query** ("What color is mentioned?") to
avoid content interference—the model should learn the *format*, not the *answer*.

### 11.5 `build_messages()`

`build_messages(query, context_len, context_type, prompt_vars)` constructs the
initial message list for a `RootLoop`. It assembles the byte-stable prefix:
system prompt, metadata, prologue, and few-shot. The resulting list is the
starting point for the turn loop.

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

### 12.2 Record Format

Each line is a JSON object with an `event` field and a `timestamp`. Event types:

| Event | Key Fields | Emitted When |
|---|---|---|
| `start` | `query`, `context_len`, `config` | `completion()` begins |
| `turn_start` | `turn`, `max_turns` | Each turn begins |
| `root_message` | `turn`, `role`, `content` | Any message sent to or received from root model |
| `repl_result` | `turn`, `stdout`, `stderr`, `final_answer`, `warnings` | After each REPL cell executes |
| `subcall` | `turn`, `index`, `prompt`, `response`, `schema`, `cached` | After each sub-LLM call |
| `guardrail` | `turn`, `guardrail`, `detail` | Parser nudges, rescue actions, warnings |
| `end` | `elapsed_s`, `final_answer`, `turns_used`, `subcalls_used`, `forced` | `completion()` ends |

### 12.3 Usage

```python
from rlm_local import TrajectoryLogger

logger = TrajectoryLogger("/tmp/traj.jsonl")

# Pass to completion()
answer = rlm_local.completion(
    query, context, logger=logger, profile="tiny"
)

# Or use convenience parameter
answer = rlm_local.completion(
    query, context, log_path="/tmp/traj.jsonl"
)
```

### 12.4 Analysis Example

```python
import json

with open("/tmp/traj.jsonl") as f:
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

## 14. Failure Modes and Mitigations

### 14.1 Model Narrates Instead of Emitting Code

**Symptom:** Model says "I would run chunk(context) and then..." but emits no
` ```repl ` block.

**Frequency:** ~25% of turns on models below 4B.

**Mitigation:** Rescue parse (step 3b) catches unclosed fences. If no code at all,
a templated nudge is appended: "You described code without emitting it..." After
2 consecutive nudges, counted as an error.

### 14.2 Whole-Context Single Sub-Call

**Symptom:** Model passes the entire context to one `llm_query()` call. Works for
small contexts, fails to generalize.

**Mitigation:** The anti-shortcut guard (§7.6) appends a warning when the sub-call
exceeds 60% of the context size. The decomposition prologue (§11.2) urges the
model to plan before coding.

### 14.3 Root History Bloat

**Symptom:** REPL output grows large, pushing the root context out of distribution.

**Mitigation:** Per-block REPL output is truncated to `repl_output_char_cap`
(default 4K chars). `show_vars()` lists names and types, not values. Sub-call
results stay in REPL variables—the model must deliberately `print()` to see them.

### 14.4 Broken Python from Root Model

**Symptom:** `NameError`, `SyntaxError`, `IndentationError` from model-generated code.

**Mitigation:** stderr is fed back verbatim to the model on the next turn. Small
models are surprisingly good at fixing their own code when shown the traceback.
Budget: `max_consecutive_errors` (default 3), then forced finalization.

### 14.5 Runaway Cell

**Symptom:** `while True:` or huge list allocation hangs the REPL.

**Mitigation:** Subprocess isolation + per-cell `cell_timeout`. The worker is
killed on timeout and the error is reported as stderr.

### 14.6 Sub-Call Quality Collapse

**Symptom:** Sub-call responses become incoherent when prompts exceed the model's
comfortable context size.

**Mitigation:** Per-prompt size warnings (soft). Sub-tier uses a model with large
native context. The system prompt teaches a staging idiom: coarse filter first,
then fine extraction on the survivors.

### 14.7 Prompt-Cache Misses

**Symptom:** Every turn takes 5–10× longer than expected because the server
re-evaluates the entire prefix.

**Mitigation:** All harness messages use `.format()` with identical ordering.
No timestamps, no per-turn randomness, no changing headers. `--cache-ram` on
the server. Cache-hit rate is exported as a metric (future instrumentation).

### 14.8 Chat-Template Drift

**Symptom:** Model behaves differently than expected because the server's chat
template doesn't match the model's training format.

**Mitigation:** The conformance suite (Chapter 15) runs per model×server pair
before any evaluation. Pinned server versions in the project lockfile.

---

## 15. Testing and Conformance

### 15.1 Test Suite Structure

```
tests/
├── conftest.py              # Shared fixtures (tiny_config, laptop_config)
├── test_config.py           # Profile loading, overrides, prompt_vars
├── test_parser.py           # Block extraction, rescue parsing, nudges, JSON repair
├── test_prompts.py          # System prompt injection, few-shot validation, templates
├── test_context_store.py    # In-memory context, disk spill, grep/chunk
├── test_subcall_manager.py  # Budgets, memoization, batched calls, exhaustion
├── test_repl.py             # Sandbox lifecycle, state persistence, helpers, subcall proxying
└── test_integration.py      # Real llama-server tests (marked @pytest.mark.slow)
```

### 15.2 Running Tests

```bash
# Unit tests only (fast, no server required)
uv run pytest tests/ -k "not slow" -v

# All tests including integration (requires running llama-server)
uv run pytest tests/ -v

# Specific module
uv run pytest tests/test_parser.py -v
```

### 15.3 Test Conventions

- **Unit tests** use fake backends (`FakeBackend`) and mock subcall managers
  (`MockSubcallMgr`). They run in under 2 seconds total.
- **Integration tests** use the real `llama-server` at `localhost:9010` with the
  `LFM2.5-VL-1.6B` model. They are marked `@pytest.mark.slow` and take ~8 seconds
  total.
- **REPL tests** launch real subprocess workers and exercise the full TCP
  protocol, subcall proxying, and state persistence.

---

## 16. API Reference

### 16.1 `rlm_local` (top-level)

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
    **overrides: Any,
) -> str:
```

### 16.2 `rlm_local.config`

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

### 16.3 `rlm_local.model_backend`

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
    ) -> None: ...

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...

    def close(self) -> None: ...
```

### 16.4 `rlm_local.repl`

```python
@dataclass
class REPLResult:
    stdout: str
    stderr: str
    final_answer: str | None
    warnings: list[str]

class REPLSandbox:
    def __init__(self, cell_timeout: float = 60.0, stdout_cap: int = 256 * 1024) -> None: ...
    def start(self, context: Any, subcall_manager: Any) -> None: ...
    def execute(self, code: str) -> REPLResult: ...
    def shutdown(self) -> None: ...
```

### 16.5 `rlm_local.subcall_manager`

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
        sub_model: str = "",
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

### 16.6 `rlm_local.parser`

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
    def check_answer_in_block(self, block: str) -> tuple[str | None, bool]: ...

    @property
    def consecutive_nudges(self) -> int: ...
    @property
    def consecutive_errors(self) -> int: ...
    def reset(self) -> None: ...

def repair_json(text: str, schema: dict[str, Any] | None = None) -> str: ...
```

### 16.7 `rlm_local.context_store`

```python
class Context:
    def __len__(self) -> int: ...
    def __getitem__(self, key: int | slice) -> str: ...
    def __iter__(self) -> Iterator[str]: ...
    def __str__(self) -> str: ...
    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]: ...
    def grep(self, pattern: str, max_hits: int = 50) -> list[str]: ...
    def chunk(self, size: int | None = None, by: str | None = None) -> list[str]: ...

class ContextStore:
    def __init__(self, spill_threshold: int = 1_000_000, temp_dir: str | None = None) -> None: ...
    def ingest(self, context: str | list[str] | Sequence[str]) -> Context: ...
    def cleanup(self) -> None: ...
```

### 16.8 `rlm_local.logger`

```python
class TrajectoryLogger:
    def __init__(self, path: str | Path | None = None) -> None: ...
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

### 16.9 `rlm_local.prompts`

```python
SYSTEM_PROMPT: str                          # .format() template
FEWSHOT_EXAMPLE: list[tuple[str, str]]      # (role, content) pairs

def build_system_prompt(prompt_vars: dict) -> str: ...
def build_messages(
    query: str,
    context_len: int,
    context_type: str,
    prompt_vars: dict,
) -> list[dict[str, str]]: ...
```

### 16.10 `rlm_local.templates`

All constants are `str` values, most are `.format()` templates. See §11.2 for the
complete catalog.

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

*RLM Local is built on the principles articulated by Zhang & Khattab in
"Language Model Harnesses Are Compositional Generalizers" (July 2026) and
"Recursive Language Models" (October 2025). The harness architecture,
prompt design, guardrails, and evaluation methodology adapt those principles
to the constraints of consumer hardware and small open-weight models.*
