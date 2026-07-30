# Extending the RLM Harness — A Guide to Tool-like Capabilities

**O'Reilly-quality documentation for the RLM ecosystem**

---

## Foreword

Most AI systems expose tools through JSON schemas — rigid structures that
the model must emit as structured tokens, parsed by a server-side handler.
This works for frontier models trained on tool-calling formats. It works
poorly for the small, local, quantized models this harness targets.

The RLM harness takes a different path. Tools are **plain Python functions**
defined in **human-readable Markdown pages**. The model discovers them by
**search**, calls them by **name** in code blocks, and the harness **executes
them in a sandbox**. This is the Forth dictionary property: user-authored
helpers are structurally indistinguishable from builtins.

This book covers the full lifecycle: how to write a helper, how it's
discovered, how it passes through the verification gate, how to test it,
and how to think about capability architecture at scale.

---

## Table of Contents

1. [Philosophy: Why Code-as-Interface](#1-philosophy-why-code-as-interface)
2. [The Helper Page Model](#2-the-helper-page-model)
3. [Writing Your First Helper](#3-writing-your-first-helper)
4. [How Helpers Are Loaded and Injected](#4-how-helpers-are-loaded-and-injected)
5. [The Gate: From Proposal to Production](#5-the-gate-from-proposal-to-production)
6. [Discovery: Search, Not the Library](#6-discovery-search-not-the-library)
7. [Advanced Patterns](#7-advanced-patterns)
8. [Security Engineering](#8-security-engineering)
9. [Testing Custom Helpers](#9-testing-custom-helpers)
10. [Helper Design Patterns](#10-helper-design-patterns)
11. [The Capability Architecture at Scale](#11-the-capability-architecture-at-scale)
12. [Reference: Builtin Helper Signatures](#12-reference-builtin-helper-signatures)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Philosophy: Why Code-as-Interface

### 1.1 The Problem with JSON Tool Calling

Consider a typical JSON tool-calling interaction. The model must emit:

```json
{
  "tool": "search_documents",
  "arguments": {
    "query": "treaty of westphalia",
    "max_results": 5
  }
}
```

This requires the model to: understand the JSON schema, format keys correctly,
quote strings properly, balance braces, and — critically — do all of this
*in a single token sequence* with no opportunity to recover from a formatting
error. On a 4B-parameter quantized model, this fails 15–40% of the time. A
single misplaced quote or missing brace, and the entire tool call is lost.

The industry data is stark. CodeAct (ICML 2024) measured up to **+20.7
percentage points of success with ~30% fewer turns** when models used code
instead of JSON for tool interactions — but the benefit concentrated in
frontier models. On 2024-era 7B models, both code and JSON scored near zero.
The difference maker is not the format alone; it's the **execution environment**
that keeps intermediate results out of the model's limited context window.

### 1.2 The RLM Answer: Execution-Environment-Mediated Context Management

The RLM harness does not ask the model to emit tool-call tokens. Instead:

1. The model writes **ordinary Python code** in ` ```repl ` blocks.
2. That code calls helper functions — just like any Python program.
3. The harness executes the code in a sandboxed subprocess.
4. Results land in **REPL variables**, not the conversation history.
5. The model pulls in only what it needs via deliberate, small `print()` calls.

This means a small model with a 8K–16K token context window never sees the
bulk of the data. It sees metadata, its own code, and tiny verification
samples. Two tasks with 10-million-character contexts look structurally
identical to the model — the **equivalence class induction** property that
makes RLMs generalize across lengths and domains.

### 1.3 The Forth Dictionary Property

In the Forth programming language, every word you define becomes a
first-class citizen of the language. There is no distinction between
"built-in words" and "user-defined words." The dictionary is flat, and
`: myword ... ;` creates something indistinguishable from the words
shipped with the system.

The RLM kernel adopts this property for helpers. A helper you author as a
Markdown page in `helpers/my-helper.md` is **structurally identical** to
the builtin `peek`, `grep`, `chunk`, `map_query`, and `show_vars` helpers
shipped with the kernel. They share the same page format, the same
extraction mechanism, the same injection path, and the same execution
sandbox. Adding a capability to the system is authoring a page — not
editing `repl.py`, not registering a JSON schema, not touching the harness
code at all.

---

## 2. The Helper Page Model

### 2.1 Anatomy of a Helper Page

Every helper is a Markdown file with YAML frontmatter and three required
sections. Here is the complete template:

```markdown
---
schema: 1
kind: helper
name: extract-dates
title: "extract-dates — ISO 8601 date extractor"
summary: "Extract ISO 8601 dates from text using regex."
tags: [extraction, dates, regex]
version: 1
status: active
---
# extract-dates — ISO 8601 date extractor

Extract ISO 8601 dates (YYYY-MM-DD) from context text.

## Signature
```python
def extract_dates(text: str, max_results: int = 20) -> list[str]: ...
```

## Implementation
```python
def extract_dates(text, max_results=20):
    import re
    pattern = r'\b\d{4}-\d{2}-\d{2}\b'
    matches = re.findall(pattern, str(text))
    results = list(dict.fromkeys(matches))[:max_results]
    for r in results:
        print(r)
    return results
```

## Usage example
```repl
dates = extract_dates(context, max_results=5)
print(f"Found {len(dates)} dates")
```
```

### 2.2 Frontmatter Fields

| Field | Required | Description |
|---|---|---|
| `schema` | Yes | Always `1` (current schema version) |
| `kind` | Yes | Always `helper` for capabilities |
| `name` | Yes | Machine name — must be unique among helpers, used as the Python function name in the REPL |
| `title` | Yes | Human-readable title shown in search cards |
| `summary` | Yes | One-sentence description (≤200 chars). Indexed for search. Shown in the system prompt's helper list |
| `tags` | No | Zero or more tags for discovery. `builtin` for shipped helpers, your own taxonomy for custom ones |
| `version` | Auto | Monotonic counter. Starts at 1; bumped on every promotion through the gate |
| `status` | Auto | `active`, `deprecated`, `superseded`, or `pending`. Only `active` helpers are loaded |

### 2.3 Required Sections

**`## Signature`** — The function signature as a Python type-annotated stub.
This is documentation for both humans and models. The stub should include
type hints and default values. It is extracted by the system and displayed
in search results and the helper list.

**`## Implementation`** — The actual Python code. This is what gets `exec()`'d
into the REPL worker's namespace. The code runs in a restricted sandbox with
blocked imports (`os`, `subprocess`, `socket`, `ctypes`) and no network
access. It has access to the REPL's `context` variable and can call other
helpers and `llm_query()`.

**`## Usage example`** — Exactly one ` ```repl ` block showing how to call
the helper. This is load-bearing documentation: small models learn the
usage pattern from this example when they discover the helper via search.

### 2.4 Helper Page Lifecycle

```
┌──────────────┐
│   Authoring   │  Human writes helpers/<name>.md directly (trusted path)
│   (human)     │  OR model calls propose() in REPL → quarantine/<ulid>.md
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Validate    │  Schema check + import allowlist + sandbox execution test
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Promote     │  Moves to helpers/<name>.md, bumps version, sets active
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Active      │  Loaded into REPL on every completion. Searchable.
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Deprecate    │  Status → deprecated. Still callable, not shown by default.
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Supersede    │  Status → superseded. Points to replacement via
│               │  superseded_by field. Anti-rot: old pages never go stale;
└───────────────┘  they link to their successors.
```

---

## 3. Writing Your First Helper

### 3.1 The Simplest Helper

Let's write a helper that counts words in the context. Create
`helpers/word-count.md`:

```markdown
---
schema: 1
kind: helper
name: word_count
title: "word_count — count words in context"
summary: "Count the total number of words in the context text."
tags: [text, counting]
---
# word_count — count words in context

Counts words in the context or any provided text.

## Signature
```python
def word_count(text: str | None = None) -> int: ...
```

## Implementation
```python
def word_count(text=None):
    source = text if text is not None else str(context)
    count = len(source.split())
    print(f"Word count: {count}")
    return count
```

## Usage example
```repl
wc = word_count()
print(f"The context has {wc} words")
```
```

### 3.2 Installing the Helper

**Human-authored path (trusted):**

Place the file directly in the vault and rebuild the index:

```bash
cp word-count.md ~/.local/share/rlm-kernel/vault/helpers/
uv run python -m rlm_local.cli vault index --rebuild
```

The helper is available in the next completion. No code changes, no restarts.

**Model-authored path (gated):**

If the model writes a helper during a completion using `propose()`:

```python
# Inside a ```repl block:
path = propose(
    "helper",
    "word-count",
    """## Signature
```python
def word_count(text=None) -> int: ...
```

## Implementation
```python
def word_count(text=None):
    source = text if text is not None else str(context)
    return len(source.split())
```

## Usage example
```repl
wc = word_count()
print(f"Words: {wc}")
```""",
    "Counts words in context text."
)
print(f"Proposed to {path}")
```

The proposed helper goes to `quarantine/<ulid>.md` with `status: pending`.
It must pass validation and human review before becoming active.

### 3.3 Verifying the Helper Works

After installation, verify it appears in search:

```bash
uv run python -m rlm_local.cli search "word count" --kind helper
```

And test it in a completion:

```bash
uv run python -m rlm_local.cli ask \
    "How many words are in the context?" \
    --context-file my_document.md \
    --max-turns 3
```

The model should discover `word_count` via the helper list in the system
prompt and call it naturally.

---

## 4. How Helpers Are Loaded and Injected

### 4.1 The Injection Path

Understanding the injection path is essential for debugging and for
writing helpers that interact correctly with the harness. Here is the
exact sequence:

```
1. RootLoop.run() starts
       │
2. KernelBridge.get_helper_definitions()
       │  Queries the index for all active helper paths
       │  Loads each page from the vault
       │  Extracts code via extract_helper_code() using regex
       │  Returns [{"name": "grep", "code": "def grep(...): ..."}, ...]
       │
3. REPLSandbox.start(context, subcall_mgr, definitions=defs)
       │  Writes the worker script (with encoding="utf-8")
       │  Launches the subprocess
       │  Sends {"cmd": "init", "context": ..., "helpers": defs}
       │
4. Worker main loop — init handler
       │  Receives context string
       │  For each helper in the "helpers" array:
       │      exec(helper["code"], globals())
       │  Also injects builtin proxies: search, propose
       │  Sends {"type": "result", "status": "ok"}
       │
5. Worker is ready — helpers available in exec() scope
```

### 4.2 What the Worker Can Access

Inside a ` ```repl ` block, the model's code executes in a namespace with:

| Name | Source | Description |
|---|---|---|
| `context` | Harness | The user's context data (str) |
| `answer` | Harness | Dict with `content` and `ready` keys |
| `llm_query(prompt)` | Worker | Sub-LLM call proxy |
| `llm_query_batched(prompts)` | Worker | Batched sub-LLM call proxy |
| `peek(n=2000)` | Builtin helper | Preview first N chars of context |
| `grep(pattern, max_hits=50)` | Builtin helper | Regex search over context |
| `chunk(size, by)` | Builtin helper | Split context into chunks |
| `map_query(items, template)` | Builtin helper | Apply template + batch query |
| `show_vars()` | Builtin helper | Print user-defined variables |
| `search(query, k=5)` | Worker proxy | Vault search via BM25 |
| `propose(kind, name, body)` | Worker proxy | Propose new page to quarantine |
| `<your-helper>()` | Vault helpers | Any active helper page |

### 4.3 Helper Visibility Rules

Only helpers meeting **all** of these conditions are injected:

1. `kind: helper` in frontmatter
2. `status: active` (not deprecated, superseded, or pending)
3. Page has a valid `## Implementation` section with parseable Python
4. The `HelperDef.from_page()` extraction succeeds

Deprecated helpers remain callable if the model knows their name, but
they are excluded from the system prompt's helper list and from default
search results. This is progressive deprecation — existing code that
references them continues to work while new code is steered toward
replacements.

---

## 5. The Gate: From Proposal to Production

### 5.1 Why a Gate?

The gate (P8 in the kernel's design principles) exists because
model-authored code is untrusted by default. A small model proposing a
helper that calls `os.system("rm -rf /")` — or more plausibly, one with
a subtle bug that silently corrupts data — must never reach the live
system without review.

The gate provides deterministic, model-free validation. No LLM is involved
in deciding whether code is safe. The checks are mechanical, fast, and
explainable.

### 5.2 The Validation Pipeline

When `validate(page)` is called on a helper page, these checks run in order:

**1. Schema validation (all kinds).** Frontmatter must parse, `kind` must be
valid, required fields present, summary ≤ 200 chars.

**2. Status sanity.** Page must be in `quarantine/` with `status: pending`.

**3. Import allowlist (helpers only).** The implementation code is scanned
for import statements. Only stdlib modules are permitted:

```
Allowed:   re, json, math, collections, itertools, functools,
           hashlib, pathlib, datetime, textwrap, difflib, string,
           typing, dataclasses, enum, heapq, bisect, random,
           statistics, decimal, fractions, copy, pprint
Blocked:   os, subprocess, socket, ctypes, importlib, sys,
           shutil, tempfile, pickle, shelve, multiprocessing,
           threading, asyncio, http, urllib, ftplib, smtplib
```

The allowlist is intentionally conservative. If your helper genuinely
needs a stdlib module not listed, add it to `gate.ALLOWED_IMPORTS`.

**4. Blocked pattern scan.** The code is checked for dangerous substrings
(case-insensitive): `os.system`, `subprocess`, `socket`, `ctypes`, `importlib`.
These are caught even if obfuscated through string concatenation in most
cases.

**5. Sandbox execution test.** The helper is `exec()`'d in a restricted
namespace (no `__builtins__` beyond safe defaults, no file I/O, no network).
It is then called with a tiny fixture input (an empty string or a short
test string). The test asserts:
- No exception is raised
- No network access is attempted
- No file system access beyond the jailed temp directory
- The function returns without hanging (2-second timeout)

**6. Signature match (advisory).** If a `## Signature` section is present,
the validator extracts the function name and compares it against the actual
callable produced by `exec()`. Mismatches generate a warning, not an error.

### 5.3 Promotion

When validation passes (`report.passed == True`), the page can be promoted:

```bash
# Review pending proposals
uv run python -m rlm_local.cli vault review

# Promote a specific proposal
uv run python -m rlm_local.cli vault promote quarantine/01KYC6B498FYY1R7DKCDB7Z3VQ.md
```

Promotion performs these steps atomically:
1. Computes the target path: `helpers/<name>.md`
2. If an active page already exists at the target, the promotion is **refused**
   (demote the existing page first)
3. Updates frontmatter: `status: active`, `version += 1`, `hash = content_hash`
4. Writes the page to the target path
5. Deletes the quarantine copy
6. Git commit: `kernel: promote <name> (v<N>)`

After promotion, the helper is active. The next index rebuild (or delta
reindex) picks it up, and the next completion injects it.

### 5.4 Rejection and Demotion

```bash
# Reject (permanently delete from quarantine)
uv run python -m rlm_local.cli vault demote quarantine/bad-helper.md

# Demote an active helper to deprecated
uv run python -m rlm_local.cli vault demote helpers/old-helper.md

# Demote with a replacement link
uv run python -m rlm_local.cli vault demote helpers/old-helper.md \
    --by helpers/new-helper.md
```

---

## 6. Discovery: Search, Not the Library

### 6.1 The Progressive Disclosure Principle

A key architectural decision: **never put the library in context; put a
search tool in context.** The system prompt includes a one-line summary of
each active helper (capped at ~30 lines), but the full documentation for
each helper lives in the vault and is retrievable on demand.

The model discovers helpers through three channels:

1. **The helper list** in the system prompt — compact one-liners shown at
   the start of every completion. This is the "hot" set: 3–5 most relevant
   helpers for the current task profile.

2. **`search(query, k=5)` in the REPL** — the model can search the vault
   for helpers by keyword. This returns cards with name, summary, and
   usage example. The model typically does this during the PROBE phase
   when it discovers it needs a capability it doesn't already know about.

3. **Helper-to-helper calls** — one helper can call another. If
   `extract_dates()` needs word counting, it can `import` or reference
   `word_count()` directly (both live in the same `globals()` namespace).
   This is composition without the model's involvement.

### 6.2 When the Model Searches

The decomposition prologue (turn 0) encourages the model to probe the
context and plan before coding. A well-tuned model will naturally search
when it encounters an unfamiliar task:

```
Turn 1:
I need to extract dates from this context. Let me check if there's a
helper for that.

```repl
search("date extraction", k=3)
```

REPL output:
[1] helper/extract-dates: extract-dates — ISO 8601 date extractor
    — Extract ISO 8601 dates (YYYY-MM-DD) from context text.
[2] helper/grep: grep — regex search over context
    — Search context lines with a regex pattern.
[3] helper/chunk: chunk — split context
    — Split context into chunks by size or paragraph.
```

The model then calls `extract_dates(context)` — it has discovered and
used a capability without that capability ever being listed in its
system prompt.

### 6.3 Helper Documentation as Search Content

The quality of your helper's `summary` field directly determines whether
the model finds it. A good summary:

- States **what** the helper does in one sentence
- Uses the **same vocabulary** the model would use to search
- Includes **concrete terms** (not abstract ones)
- Is ≤ 200 characters (the search card budget)

**Good:** `"Extract ISO 8601 dates (YYYY-MM-DD) from text using regex."`
**Bad:** `"A utility for temporal data extraction."`

The `## Usage example` section is also indexed. The model searching for
"print(f"Found" will find your helper if your example includes that
pattern. Make examples realistic.

---

## 7. Advanced Patterns

### 7.1 Helpers That Call LLMs

Helpers can use `llm_query()` and `llm_query_batched()` — they run in the
same REPL namespace. This enables **compound capabilities**: a single
helper that orchestrates multiple sub-calls.

```python
def classify_and_extract(text, categories=None):
    """Classify text into categories, then extract key entities."""
    if categories is None:
        categories = ["date", "name", "amount", "location"]

    # Step 1: classify
    cls_prompt = f"Classify this text into one of: {', '.join(categories)}.\n\nText: {text[:2000]}"
    category = llm_query(cls_prompt).strip()

    # Step 2: extract based on category
    extract_prompt = f"Extract the {category} from this text. Return only the value.\n\nText: {text[:2000]}"
    value = llm_query(extract_prompt).strip()

    print(f"Category: {category}, Value: {value}")
    return {"category": category, "value": value}
```

This helper reduces a two-turn workflow (classify, then extract) into a
single call. The model's root loop sees one turn instead of two — fewer
opportunities for errors, fewer turns consumed, faster completion.

### 7.2 Helpers That Compose Other Helpers

Since all helpers share the same `globals()` namespace, composition is
natural:

```python
def analyze_document(max_items=10):
    """Full document analysis pipeline."""
    # Step 1: Get basic stats
    total_words = word_count()

    # Step 2: Extract structured data
    dates = extract_dates(str(context), max_results=max_items)
    emails = grep(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
                  max_hits=max_items)

    # Step 3: Summarize if needed
    summary = None
    if total_words > 5000:
        chunks = chunk(size=3000)
        summaries = map_query(chunks[:3], "Summarize this text in one sentence:")
        summary = "; ".join(summaries)

    result = {
        "total_words": total_words,
        "dates_found": len(dates),
        "emails_found": len(emails),
        "summary": summary,
    }
    for k, v in result.items():
        if v is not None:
            print(f"{k}: {v}")
    return result
```

This is the LATM (Learning to Augment with Tools) pattern: a complex
workflow is authored once (by a human or a capable model), validated
through the gate, and then reused cheaply by any model tier. The
authoring cost is paid once; reuse is nearly free.

### 7.3 Stateful Helpers

Helpers can maintain state across calls within a single completion by
storing data in module-level variables:

```python
# In helpers/counter.md — Implementation section:
_counter_state = {"calls": 0, "last_result": None}

def track_calls(data=None):
    """Track how many times this helper has been called."""
    global _counter_state
    _counter_state["calls"] += 1
    if data is not None:
        _counter_state["last_result"] = str(data)[:100]
    print(f"Called {_counter_state['calls']} times")
    return _counter_state.copy()
```

**Warning:** State persists only within a single completion. When the REPL
worker exits, all state is lost. For cross-completion persistence, store
data in the vault via `propose()` or through `memory.add()`.

### 7.4 Helpers as Workflow Templates

The most powerful pattern: a helper that encodes a **reusable workflow**
the model would otherwise have to re-derive each time:

```python
def batch_needle_search(needles, max_per_needle=3):
    """Search for multiple needles in parallel.
    
    Args:
        needles: List of regex patterns to search for.
        max_per_needle: Max hits per pattern.
    
    Returns:
        Dict mapping pattern -> list of matching lines.
    """
    results = {}
    for pattern in needles:
        try:
            hits = grep(pattern, max_hits=max_per_needle)
            results[pattern] = hits
        except Exception as e:
            results[pattern] = [f"Error: {e}"]
    
    # Print summary
    for pattern, hits in results.items():
        print(f"{pattern}: {len(hits)} matches")
    
    return results
```

This collapses a common N-turn pattern (loop over needles, grep each,
aggregate) into a single call. The model's root context stays shorter,
and the trajectory is more uniform across tasks — feeding the
equivalence-class generalization mechanism.

---

## 8. Security Engineering

### 8.1 The Threat Model

The REPL executes **model-written code**. That code can call helpers. Those
helpers can access the context data. The moment a helper touches the network
or filesystem, the attack surface expands to include prompt injection via
tool outputs, data exfiltration, and malicious helper code.

The security posture has three layers:

1. **Sandboxing** — the REPL runs in an isolated subprocess with restricted
   builtins. Helpers run in the same sandbox. Code cannot escape to the host.

2. **The gate** — model-authored helpers pass through deterministic
   validation before reaching the live system. Import allowlist, blocked
   pattern scan, and sandbox execution test.

3. **Architectural boundaries** — secrets live in the harness process, never
   in the REPL. Network access is via explicit proxy functions, not raw
   sockets. The bindings pattern: the REPL has no network and no secrets;
   the only outside access is RPC to host-side tool functions that hold
   credentials.

### 8.2 What the Sandbox Blocks

The REPL worker's `exec()` runs with these restrictions:

| Blocked | Why |
|---|---|
| `import os` | File system escape, process control |
| `import subprocess` | Arbitrary command execution |
| `import socket` | Network escape |
| `import ctypes` | Memory manipulation, FFI escape |
| `import importlib` | Dynamic import bypass |
| `input()` | Interactive blocking |
| `eval()`, `exec()`, `compile()` | Code-generation escape |
| `globals()`, `locals()` | Namespace inspection |
| `open()` (unrestricted) | Jailed to task temp directory |

### 8.3 Writing Secure Helpers

1. **Never hardcode secrets.** Credentials live in host-side configuration.
   If your helper needs an API key, pass it through the harness, not through
   the REPL.

2. **Validate inputs.** The model may pass anything to your helper.
   Type-check and range-check inputs at the top of the function:

   ```python
   def safe_helper(items, max_results=10):
       if not isinstance(items, (list, str)):
           print("Error: items must be a list or string")
           return []
       max_results = min(max(max_results, 1), 100)
       ...
   ```

3. **Cap output sizes.** A helper that prints 10,000 lines floods the
   root model's context. Truncate with explicit limits:

   ```python
   for i, item in enumerate(results[:max_display]):
       print(item)
   if len(results) > max_display:
       print(f"... and {len(results) - max_display} more")
   ```

4. **Handle errors gracefully.** Return error strings, not raised
   exceptions. The root model can self-correct on error strings;
   unhandled exceptions skip to stderr and cost a turn.

5. **Use the allowlisted imports only.** If you genuinely need a blocked
   module, add it to the gate's allowlist and document why. The default
   set covers ~95% of useful helpers.

### 8.4 The Lethal Trifecta (and How We Avoid It)

Simon Willison's "lethal trifecta" for AI agents: **private data access +
untrusted content + external communication = trivial exfiltration.**

The RLM harness avoids this by construction:
- **Private data** lives in `context` in the REPL — it never reaches the
  model's conversation history.
- **Untrusted content** (scraped pages, user uploads) is treated as data,
  not instructions. The system prompt orders the model to treat retrieved
  text as data.
- **External communication** is gated through explicit proxy functions
  with host allowlists. The REPL has no raw network access.

---

## 9. Testing Custom Helpers

### 9.1 Unit Testing a Helper in Isolation

Since helpers are plain Python functions, test them directly:

```python
# tests/test_my_helper.py
import pytest
from rlm_kernel.schema import HelperDef, parse_page

HELPER_MD = """---
schema: 1
kind: helper
name: word_count
title: "Word Count"
summary: "Count words."
---
## Signature
```python
def word_count(text=None) -> int: ...
```

## Implementation
```python
def word_count(text=None):
    source = text if text is not None else "default"
    return len(source.split())
```

## Usage example
```repl
wc = word_count("hello world")
```
"""

def test_word_count_basic():
    page = parse_page(HELPER_MD, "helpers/word_count.md")
    hd = HelperDef.from_page(page)
    
    # Execute the helper code in a test namespace
    ns = {}
    exec(hd.code, ns)
    assert ns["word_count"]("hello world") == 2
    assert ns["word_count"]("one two three four") == 4

def test_word_count_empty():
    page = parse_page(HELPER_MD, "helpers/word_count.md")
    hd = HelperDef.from_page(page)
    ns = {}
    exec(hd.code, ns)
    assert ns["word_count"]("") == 1  # split() on empty returns ['']
```

### 9.2 Testing Gate Validation

Test that your helper passes (or correctly fails) validation:

```python
def test_helper_passes_validation(temp_vault):
    from rlm_kernel.gate import propose, validate
    from rlm_kernel.vault import LocalVault
    
    vault = LocalVault(temp_vault, init_git=False)
    
    body = """## Implementation
```python
def safe_helper():
    return 42
```"""
    
    path = propose(vault, "helper", "safe-helper", body, "A safe helper")
    page = vault.get(path)
    report = validate(page, vault=vault)
    
    assert report.passed, f"Validation failed: {report.errors}"

def test_blocked_import_fails_validation(temp_vault):
    from rlm_kernel.gate import propose, validate
    
    vault = LocalVault(temp_vault, init_git=False)
    
    body = """## Implementation
```python
def bad_helper():
    import os
    os.system("echo pwned")
```"""
    
    path = propose(vault, "helper", "bad-helper", body, "A bad helper")
    page = vault.get(path)
    report = validate(page, vault=vault)
    
    assert not report.passed
    assert any("os" in e.lower() for e in report.errors)
```

### 9.3 Testing End-to-End in the REPL

The definitive test: does the model actually call your helper?

```python
def test_model_uses_helper(seeded_vault, bridge):
    """Integration test: model discovers and uses a custom helper."""
    from rlm_local import completion
    
    # The helper should already be in the vault (seeded or promoted)
    answer = completion(
        "Count the words in: hello world",
        "hello world",
        profile="tiny",
        kernel_bridge=bridge,
        max_turns=4,
    )
    
    # The word_count helper should return 2
    assert "2" in answer
```

---

## 10. Helper Design Patterns

### 10.1 The Single-Responsibility Helper

Each helper does one thing well. `extract_dates` extracts dates. It doesn't
also classify the document or generate a summary. Composition happens at
the model level or through helper-to-helper calls.

**Rationale:** Small models understand single-purpose functions better than
multi-purpose ones. A helper that "analyzes the document" is too vague.
A helper that "extracts ISO 8601 dates" is concrete and testable.

### 10.2 The Stateless-First Pattern

Prefer stateless helpers that take explicit inputs and return explicit
outputs. Stateful helpers (module-level variables) complicate testing and
debugging. If you need state, make it explicit:

```python
# Bad: implicit state
_counter = 0

def count():
    global _counter
    _counter += 1
    return _counter

# Good: explicit accumulator
def count_calls(accumulator=None):
    if accumulator is None:
        accumulator = {"count": 0}
    accumulator["count"] += 1
    return accumulator
```

### 10.3 The Print-and-Return Pattern

Helpers should both `print()` a human-readable summary and `return()` the
structured data. The `print()` is what the root model sees (keeping its
context small). The `return()` is what other helpers consume (passing
structured data programmatically):

```python
def extract_emails(text, max_results=20):
    import re
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    matches = list(dict.fromkeys(re.findall(pattern, str(text))))[:max_results]
    
    # For the model: human-readable summary
    for i, email in enumerate(matches, 1):
        print(f"  [{i}] {email}")
    print(f"Found {len(matches)} unique email addresses")
    
    # For other helpers: structured data
    return matches
```

### 10.4 The Degrade-Gracefully Pattern

Every helper should handle edge cases without crashing:

```python
def safe_grep(pattern, max_hits=50):
    if not isinstance(pattern, str):
        print("Error: pattern must be a string")
        return []
    
    import re
    try:
        regex = re.compile(pattern)
    except re.error as e:
        print(f"Error: invalid regex pattern: {e}")
        return []
    
    hits = []
    try:
        text = str(context)
    except Exception:
        text = ""
    
    for line in text.splitlines():
        if regex.search(line):
            hits.append(line)
            if len(hits) >= max_hits:
                break
    
    for h in hits:
        print(h)
    return hits
```

---

## 11. The Capability Architecture at Scale

### 11.1 How Many Helpers Is Too Many?

The helper list in the system prompt is capped at ~30 lines (~1,500
characters). If you have more helpers than that, only the first 30
alphabetically are shown. The rest are discoverable only through
`search()`.

At 100+ helpers, consider organizing them into **namespaced families**
and providing a **meta-helper** that searches by category:

```python
def find_helper(task_description):
    """Search for helpers matching a task description."""
    query = " ".join(task_description.split()[:5])
    results = search(query, k=5)
    for r in results:
        print(f"  {r['name']}: {r['summary']}")
    return results
```

### 11.2 The Compounding Library

The LATM (Learning to Augment with Tools) economics apply: each validated
helper becomes a **functional cache** — a capability that cost N turns to
author but costs 1 turn to reuse. Over months of use, a vault accumulates
a library of battle-tested helpers, each making the next task easier.

The gate ensures this compounding is safe. No unvalidated code enters the
library. Deprecated helpers stay callable (not breaking existing workflows)
but steer new usage toward replacements via `superseded_by` links.

### 11.3 When to Author a Helper vs. Let the Model Figure It Out

| Scenario | Author a Helper | Let the Model Handle It |
|---|---|---|
| Pattern repeats across 3+ completions | ✓ | |
| Requires external API / structured parsing | ✓ | |
| Model gets it wrong >30% of the time | ✓ | |
| Model gets it right >90% of the time | | ✓ |
| One-off task, never seen again | | ✓ |
| Trivially expressed in 1–2 lines of code | | ✓ |

The heuristic: **author a helper when the model wastes turns re-deriving
the same solution.** The helper pays for itself in reduced turn count
within a handful of uses.

---

## 12. Reference: Builtin Helper Signatures

These five helpers ship with the kernel and are always available:

### `peek(n=2000)`
Preview the first `n` characters of the context. Prints and returns the text.
```python
def peek(n: int = 2000) -> str: ...
```

### `grep(pattern, max_hits=50)`
Regex search over context lines. Prints matching lines and returns them.
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```

### `chunk(size=None, by=None)`
Split context into chunks. `size=N` for character-based, `by="paragraph"`
for paragraph-based splitting.
```python
def chunk(size: int | None = None, by: str | None = None) -> list[str]: ...
```

### `map_query(items, template, batch=True)`
Apply a template string or callable to each item and run sub-LLM queries.
If `batch=True`, uses `llm_query_batched` for parallelism.
```python
def map_query(items: list, template: str | callable, batch: bool = True) -> list: ...
```

### `show_vars()`
Print all user-defined variables in the REPL (names, types, and truncated
values). Useful for debugging and for the model to inspect its own state.
```python
def show_vars() -> None: ...
```

---

## 13. Troubleshooting

### Helper not appearing in the system prompt

**Check:** Is `status: active` in the frontmatter? Does the page have a
valid `## Implementation` block? Run `rlm vault index --rebuild` and
check `rlm search "<helper-name>" --kind helper`.

### Helper code has a syntax error

**Symptom:** `SyntaxError` in the REPL output when the model tries to
use the helper.

**Fix:** Edit the helper page directly, fix the code, rebuild the index.
No need to restart anything. The next completion picks up the fixed code.

### Helper uses an import that's blocked

**Symptom:** Validation fails with "import not in allowlist."

**Fix:** Either rewrite the helper to avoid the blocked import, or add
the module to `gate.ALLOWED_IMPORTS` in `src/rlm_kernel/gate.py` and
re-run validation.

### Model doesn't discover the helper

**Symptom:** The model writes its own implementation instead of calling
the available helper.

**Fix:** Improve the helper's `summary` — use the same vocabulary the
model uses when describing the task. Add a `## Usage example` that
matches the model's likely usage pattern. If the helper list is too
long (30+ helpers), consider organizing with namespaces.

### Helper works in tests but not in the REPL

**Symptom:** Unit tests pass, but the model gets errors calling the helper.

**Fix:** The REPL sandbox is more restrictive than a test environment.
Check for: use of blocked builtins, file I/O outside the jail, reliance on
module-level imports that aren't in the worker's restricted namespace.

### Multiple helpers with the same name

**Symptom:** Only one helper appears despite having multiple pages.

**Fix:** Helper names must be unique. The `promote` function enforces this
for gate-promoted helpers. For manually authored helpers, ensure unique
`name` fields. Use `superseded_by` to link old versions to replacements.

---

*The capability architecture described here adapts principles from LATM
(learning to augment with tools), Voyager (persistent skill libraries),
DynaSaur (runtime tool accumulation), and the Forth dictionary model to
the constraints of small, local, CPU-only language models. The key insight
is that tools should be content — discoverable, versioned, reviewable
pages — not code frozen in the harness.*
