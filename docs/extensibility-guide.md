# Extending the RLM Harness — A Guide to Tool-like Capabilities

**Comprehensive documentation for the RLM ecosystem**

---

## Foreword

Most AI systems expose tools through JSON schemas — rigid structures that
the model must emit as structured tokens, parsed by a server-side handler.
This works for frontier models trained on tool-calling formats. It works
poorly for the small, local, quantized models this harness targets.

The RLM harness takes a different path. Tools are **plain Python functions**
defined in **human-readable Markdown pages**. The model discovers them by
**search**, calls them by **name** in code blocks, and the harness **executes
them in a separate worker process**. This is the Forth dictionary property:
user-authored helpers are structurally indistinguishable from builtins.

Two claims this guide makes no attempt to dress up, and both are load-bearing
for reading it correctly: the verification gate is a **quality gate, not
containment** (§5.2.1), and the worker is a **process boundary, not a sandbox**
(§8.1). Everything else here is downstream of those two facts.

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
3. The harness executes the code in a separate worker process — killable and
   memory-bounded, though not a permission boundary (§8).
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
Markdown page in `helper/my-helper.md` is **structurally identical** to
the builtin `peek`, `grep`, `chunk`, `map_query`, and `show_vars` helpers
shipped with the kernel. They share the same page format, the same
extraction mechanism, the same injection path, and the same execution
worker. Adding a capability to the system is authoring a page — not
editing `repl.py`, not registering a JSON schema, not touching the harness
code at all.

---

## 2. The Helper Page Model

### 2.1 Anatomy of a Helper Page

Every helper is a Markdown file with YAML frontmatter and three conventional
sections — of which the code enforces only one (§2.3). Here is the complete
template:

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
| `name` | Yes | Machine name — must be unique among helpers, used as the Python function name in the REPL. Must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (S4/R20): no path separators, no leading dot or whitespace, no `..` — the name is model-controlled and becomes a path at promotion |
| `title` | Yes | Human-readable title shown in search cards |
| `summary` | Yes | One-sentence description (≤200 chars, the schema's hard cap). Indexed for search. Shown in the system prompt's helper list, truncated to 120 chars in a REPL search card |
| `tags` | No | Zero or more tags for discovery. `builtin` for shipped helpers, your own taxonomy for custom ones |
| `version` | Auto | `1` on a hand-authored page; `0` on a proposed page. Each promotion sets `max(version of the page being replaced, current) + 1` |
| `status` | Auto | `active`, `deprecated`, `superseded`, or `pending`. Only `active` helpers are injected into the REPL and listed in the system prompt (§4.3) |

### 2.3 Required Sections

Only one of the three is actually required by code: `HelperDef.from_page()`
needs an `## Implementation` block, and the gate errors without one. A missing
`## Signature` merely skips the signature check; `## Usage example` is read by no
code path at all. Both are conventions this guide keeps because a page without
them is much harder for a human to review and much less useful to a searching
model.

**`## Signature`** — The function signature as a Python type-annotated stub.
This is documentation for humans and for the gate: the validator parses the
stub and compares its declared parameter list against the implementation's
`def` line, warning on a mismatch. It is *not* shown to the model — the system
prompt's helper list prints `name: summary`, and a REPL search card carries
`title` and `summary` only.

**`## Implementation`** — The actual Python code. This is what gets `exec()`'d
into the REPL worker's globals when the worker initialises. The worker is a
separate OS process, **not** a restricted-builtin namespace: it has the normal
builtins and can import anything the interpreter can. The gate refuses to
*promote* a helper that imports outside its eight-module allowlist, but that is
a quality tripwire, not containment (§5.2.1, §8.1). The code has access to the
REPL's `context` variable, and can call other injected helpers and
`llm_query()`.

**`## Usage example`** — Exactly one ` ```repl ` block showing how to call
the helper. The whole page body — this section included — is what the BM25
index matches a query against, so the words you write here are searchable. What
the model is *shown* when a search hits is the one-line card
(`title — summary`), so vocabulary that must be discoverable belongs in the
`summary`.

### 2.4 Helper Page Lifecycle

```
┌──────────────┐
│   Authoring   │  Human writes helper/<name>.md directly (trusted path)
│   (human)     │  OR model calls propose() in REPL → quarantine/<ulid>.md
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Validate    │  Static by default: AST parse + import allowlist +
│               │  blocked-pattern scan + define/signature checks.
│               │  Sandbox exec only with execute=True (§5.2.1)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Promote     │  Moves to helper/<name>.md, bumps version, sets active.
│               │  Refused if the target is occupied (see §5.3)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Active      │  Loaded into REPL on every completion. Searchable.
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Deprecate    │  Status → deprecated. No longer injected; still searchable
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Supersede    │  Status → superseded. Points to replacement via
│               │  superseded_by field. Anti-rot: old pages never go stale;
└───────────────┘  they link to their successors.
```

One clarification the diagram cannot show: **the validate and promote boxes are
the model-authored path only.** A page you write by hand is live as soon as it
is in the vault directory — nothing validates it, nothing bumps its version, and
nothing sets `status` for you (the schema default is `active`, which is why the
hand-authored examples in this guide omit the field). Deprecating a
hand-authored page is likewise a manual `demote` call (§5.4).

### 2.5 The Other Page Kind That Changes the Prompt: Few-Shots

Helpers are not the only thing you can author. A `fewshot/<name>.md` page is a
second lever, and it acts on the prompt rather than on the REPL:

```markdown
---
schema: 1
kind: fewshot
name: submission-discipline
title: "Few-shot: submit on verification"
summary: "Worked example that submits in the same turn the evidence is verified."
tags: [fewshot]
---
# Submit as soon as the evidence is verified

## Query
What is the vault access code?

## Answer
One step is enough: grep for the code, then submit in the same turn.

```repl
hits = grep('access code')
answer['content'] = hits[0] if hits else 'not found'
answer['ready'] = True
```
```

What the harness does with it — `load_fewshots_from_vault()`, called once per run
from `RootLoop.run()` when a kernel bridge is present:

- The **package-bundled examples always come first**: `FEWSHOT_EXAMPLE`, two
  worked transcripts (needle search, and voluntary submission). A vault page is
  appended *after* them.
- **At most one** active `fewshot/` page is used. The loader takes the first page
  in path order that parses into message pairs and then stops, so adding a second
  page changes nothing — edit the page that is winning instead.
- **Two body shapes parse** (`parse_fewshot_body`): `## Query` + `## Answer`
  (yielding one user/assistant pair), or one or more `## Example` / `## Examples`
  sections each split by a `### Assistant` sub-heading (also `### Response` or
  `### Answer`) — everything before that heading is the user turn, optionally
  narrowed by a `### User` / `### Query` / `### Question` / `### Prompt`
  sub-heading. A body in any other shape contributes **nothing** and the page is
  skipped.
- **The pair must fit the budget.** The combined length of its turns must be
  ≤ `sub_prompt_char_budget // 4`: 2 000 characters on `tiny` (8 000 ÷ 4),
  4 000 on `laptop`, 6 000 on `workstation`. An oversized page is skipped without
  a word of complaint.
- The page must be `status: active`. Its messages are inserted once, before the
  turn loop begins, as part of the byte-stable prefix.

Two consequences worth knowing. First, a few-shot is the highest-leverage and
highest-risk content you can put in a vault: it is replayed to the root model on
every turn as conversation history, so a misleading example teaches misleading
behaviour at least as effectively as a good one teaches the intended shape.
Second, the gate's few-shot check (§5.2) looks only for `## Example` /
`## Examples`, so a `## Query` / `## Answer` page — the shape `seed_vault`
writes — validates with a warning about a section it does not actually need.

---

## 3. Writing Your First Helper

### 3.1 The Simplest Helper

Let's write a helper that counts words in the context. Create
`helper/word-count.md`:

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
cp word-count.md ~/.local/share/rlm-kernel/vault/helper/
uv run python -m rlm_kernel.cli index --rebuild
```

The helper is available in the next completion. No code changes, no restarts.
The index rebuild is not optional: `KernelBridge.get_helper_definitions()`
answers from `.index/meta.sqlite` whenever that file exists and only falls back
to a full vault walk when it does not, so a hand-copied page with a stale index
is invisible to the next completion.

Nothing validates this page: a hand-authored helper is live on trust. The
`## Implementation` block is the only part that matters for injection (§4.3),
and it is `exec()`'d without a syntax check — a typo means the helper silently
does not exist in the namespace (see §13).

**Model-authored path (gated):**

If the model writes a helper during a completion using `propose()`:

```python
# Inside a ```repl block:
path = propose(
    "helper",
    "word_count",
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

Two things about the arguments, both enforced by the gate rather than by
`propose()`:

- **`name` must equal the Python function name.** The static define check
  (§5.2) errors with "does not define a callable named …" if the page is called
  `word-count` while the code defines `word_count`. Use the identifier.
- **`name` must be a legal page name** — `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.
  A name containing `/`, `..`, or a leading dot raises a pydantic
  `ValidationError` (a `ValueError`) inside the *harness* process, which
  surfaces as a failed completion rather than an error string in the REPL.

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
prompt — `Available helpers:` followed by one `name: summary` line per active
page — and call it naturally. That list is truncated at 30 entries (§6.1), so
on a large vault your `summary` wording is what has to earn the slot.

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
       │  Queries the index for active helper paths (kind="helper",
       │  status="active"), falling back to a full vault walk when
       │  .index/meta.sqlite does not exist
       │  Loads each page from the vault
       │  Extracts code via HelperDef.from_page() → extract_helper_code()
       │  (a regex over the first ```python block under ## Implementation)
       │  Returns [{"name": "grep", "code": "def grep(...): ..."}, ...]
       │
3. REPLSandbox.start(ctx_handle, subcall_mgr, definitions=defs)
       │  Writes the worker script (with encoding="utf-8")
       │  Launches the subprocess
       │  Sends {"cmd": "init", "context": ..., "helpers": defs},
       │  where "context" is the text for a small input and a lazy file
       │  reference {"kind": "file", "path": ..., "total": <bytes>} when
       │  the input spilled to disk (R1)
       │
4. Worker main loop — init handler
       │  Binds `context` (the text itself, or a lazy reader over the
       │  spilled file), receives the "helpers" array
       │  For each helper in the "helpers" array:
       │      exec(helper["code"], globals())
       │  (an exception here is swallowed: the helper is simply absent)
       │  llm_query, llm_query_batched, search, propose and the five
       │  builtin helpers were already defined at module level
       │  Sends {"type": "result", "cell_id": null, "status": "ok"}
       │
5. Worker is ready — helpers available in exec() scope
```

### 4.2 What the Worker Can Access

Inside a ` ```repl ` block, the model's code executes in a namespace with:

| Name | Source | Description |
|---|---|---|
| `context` | Harness | The context handle — see the note below the table |
| `answer` | Harness | Dict with `content` and `ready` keys |
| `llm_query(prompt, schema=None)` | Worker | Sub-LLM call proxy |
| `llm_query_batched(prompts, schema=None)` | Worker | Batched sub-LLM call proxy |
| `peek(n=2000)` | Worker builtin | Preview first N chars of context |
| `grep(pattern, max_hits=50)` | Worker builtin | Regex search over context |
| `chunk(size=None, by=None)` | Worker builtin | Split context into chunks |
| `map_query(items, template, batch=True)` | Worker builtin | Apply template + batch query |
| `show_vars()` | Worker builtin | Print user-defined variables |
| `search(query, k=5, kinds=None)` | Worker proxy | Vault search via BM25. Returns a **formatted multi-line string** of cards (`[n] kind/name: title — summary`), not a list — print it to see it |
| `propose(kind, name, body, rationale="")` | Worker proxy | Propose new page to quarantine. Returns the quarantine path, or an `Error: …` string |
| `<your-helper>()` | Vault helpers | Any active helper page (§4.3) |

**`context` is a handle, not necessarily text.** A small input arrives as a
plain `str`; an input above `context_spill_threshold` arrives as a **lazy file
reference** (R1) — the worker binds a reader over the spilled file and the text
never crosses the socket. Both handle types are **byte-addressed** (R2):

| Expression | Meaning |
|---|---|
| `len(context)` | UTF-8 **byte** count, not a character count |
| `context[i]` | The single character *starting* at byte offset `i` |
| `context[i:j]` | Text decoded from byte offsets `i`..`j` (step 1 only) |
| `context[a]` mid-codepoint | Raises `UnicodeDecodeError` |
| `context.grep(p, n)` / `context.chunk(s, by)` / `context.lines(start, count)` | Streaming reads that never materialise the blob |
| `str(context)` | The full text — the one operation that defeats the spill |

So a worker-side helper should feature-detect rather than assume text:
`if hasattr(context, "grep"): hits = context.grep(pattern, max_hits)` else fall
back to `str(context).splitlines()`. That is exactly what the builtin `grep`
and `chunk` do, and it is the pattern an injected helper should copy.

### 4.3 Helper Visibility Rules

Only helpers meeting **all** of these conditions are injected:

1. `kind: helper` in frontmatter
2. `status: active` (not deprecated, superseded, or pending)
3. `HelperDef.from_page()` succeeds — which requires only that the body has an
   `## Implementation` section with a fenced ` ```python ` block

Note what condition 3 is *not*: a syntax check. Extraction is a regex; the
worker then runs `exec(helper["code"], globals())` inside a bare
`try/except Exception: pass`. A helper whose code does not parse, or whose
module-level code raises, is **silently missing** from the namespace — the
model gets a `NameError` and no explanation appears anywhere. Pages written by
hand never pass through the gate, so nothing else catches it either; if you
author pages directly, run them once yourself (`exec` the extracted code) or
keep a test (§9).

Status is what actually gates visibility, and only partly:

- **Injection** — only `active` helpers reach the REPL namespace. A page that
  is `deprecated`, `superseded`, or `pending` is *not* callable in the REPL,
  even if the model knows its name.
- **The system prompt's helper list** — also `active` only, capped at 30 entries.
- **Search** — **not filtered by status.** The query runs against the FTS table
  (`path, kind, name, title, summary, body`), which has no status column and
  never joins the `pages` metadata table, so a deprecated page still matches
  `search()` and still returns a card. Deprecation steers the model away from a
  page; it does not make the page unfindable. If a page must disappear from
  view, delete it.

Progressive deprecation therefore means: existing *code* that referenced a
deprecated helper breaks, because the name is gone from the namespace. Deprecate
a helper only once nothing calls it, and keep the replacement's `name` distinct
so both can coexist during the transition.

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

`validate(page, vault=None, *, execute=False)` is **static by default** (S3/R19).
Nothing is executed unless the caller asks for it. The checks:

**1. Mode disclosure.** When `execute` is false the report always carries a
warning: the code was parsed, not executed, and the sandbox is a quality check
rather than a containment boundary.

**2. Status and location sanity (advisory).** A page that is not
`status: pending`, or whose path is not under `quarantine/`, produces a
**warning** — not an error. Validation reports; the refusal happens at
promotion (§5.3).

**3. Frontmatter validity is a precondition, not a step.** A page whose
frontmatter fails `Frontmatter` validation cannot be read into a `Page` at all —
`vault.get()` raises — so `summary ≤ 200 chars`, `kind` membership, and the
`name` pattern (S4/R20) are enforced at parse time. By the time `validate()`
sees a page, its frontmatter is already valid.

**4. AST parse (helpers).** The `## Implementation` code must parse. A
`SyntaxError` is an error and stops the helper checks there.

**5. Import allowlist (helpers).** Every `import x` / `from x import y` is
checked against the module part. The allowlist is eight modules:

```
Allowed:   re, json, math, collections, itertools, functools, hashlib, pathlib
```

Anything else is an error — including perfectly harmless stdlib modules such as
`datetime`, `string`, or `typing`. If your helper genuinely needs one, add it to
`gate.ALLOWED_IMPORTS` in `src/rlm_kernel/gate.py`; that is the one gate
constant an author is expected to edit.

**6. Blocked pattern scan (helpers).** A case-insensitive substring scan for
`os.system`, `subprocess`, `socket`, `ctypes`, `importlib`. Each hit is an
error. This is a **tripwire for the obvious cases, not a control**: it is a
substring match, so it neither understands obfuscation nor resists it —
`().__class__.__bases__[0].__subclasses__()` contains none of those substrings
and passes every static check (§5.2.1).

**7. Static define and signature checks (helpers).** The parsed code must define
a callable whose name equals the page's `name`, or it is an error. If a
`## Signature` section is present, its declared parameter list is compared with
the implementation's `def` line; a mismatch is a **warning**, never an error.

**8. Sandbox execution (helpers, opt-in).** Only with `execute=True` is the code
`exec()`'d in a namespace whose `__builtins__` is an explicit safe subset, with
the eight allowlisted modules pre-imported. `report.executed` tells the caller
which mode produced the verdict. This sandbox lives inside `gate.validate` and is
**not** the REPL worker — helpers you actually use run unrestricted in the worker
(§8.1).

The other kinds are lighter:

| Kind | Checks |
|---|---|
| `contract` / `template` | Body ≤ 8192 **bytes** (UTF-8, not characters); slot variables warned about when absent (`{{name}}` / `{name}`) and when duplicated |
| `definition` / `note` | `[[wikilinks]]` resolve (needs `vault=`); a link into quarantine or nowhere is a warning |
| `fewshot` | Body must not be empty; warns when it has no `## Example` / `## Examples` section |
| `topic` / `cache` | Basic sanity only |

`report.passed` is exactly `len(errors) == 0` — warnings never block promotion.

#### 5.2.1 Trust model: the gate is a quality gate, not containment

This is the part of the gate that is easiest to get wrong, so it is stated
plainly (the canonical version is kernel manual §7.3.1):

- **Execution is opt-in.** `validate()` does not run helper code unless you pass
  `execute=True`; `rlm-kernel review` prints `Review mode: static validation only`
  and only runs the sandbox with `--execute`.
- **The sandbox is not a security boundary.** Restricted-builtin `exec` is
  escapable on CPython: a `().__class__.__bases__[0].__subclasses__()` chain
  reaches arbitrary classes, and the substring blocklist has no entry for it.
  Static validation **passes** such a helper, and the report says so instead of
  showing a silent green.
- **`exec` defines, it does not call.** The sandbox never invokes the helper it
  validates, so a helper whose body raises is not caught even with
  `execute=True`. Only definition-time failures — a bad decorator, a
  module-level raise, an import — are visible.
- **The allowlist and the pattern scan are tripwires**, useful for keeping
  malformed, mis-signed, or obviously hostile content out of the live prompt and
  for forcing every model-authored page through a review step.

Treat `--execute` as a convenience for code you already trust, never as a
sandbox you can point at hostile input. `tests/rlm_kernel/test_gate_execution_policy.py`
pins all of this, including the two limitations above.

### 5.3 Promotion

When validation passes (`report.passed == True`), the page can be promoted:

```bash
# Review pending proposals (static; add --execute to also run the sandbox)
uv run python -m rlm_kernel.cli review

# Promote a specific proposal
uv run python -m rlm_kernel.cli promote quarantine/01KYC6B498FYY1R7DKCDB7Z3VQ.md
```

Promotion performs these steps:

1. **Validates the page again — statically.** `promote` calls
   `validate(page, vault=vault)` with no `execute=True`, so promotion parses
   helper code and never runs it. A page with errors raises `ValueError`
   instead of being promoted (the CLI does not catch it — you get a traceback,
   so run `review` first).
2. Computes the target path: `f"{kind}/{name}.md"` — `helper/<name>.md` for a
   helper (kernel manual §4.7), unless the caller passed an explicit
   `target_path` (the optimizer's replace-in-place path).
3. Applies the occupancy guard, when no explicit `target_path` was given:

   | Occupant at the target path | Result |
   |---|---|
   | `active` | `ValueError` — demote it first or choose a different name |
   | `deprecated` / `superseded` | `ValueError` unless `force=True` (CLI: `--force`) |
   | nothing | promoted |

   Silently overwriting a deprecated page would destroy the page that records
   *what replaced it*, so that one takes an explicit decision. An explicit
   `target_path` skips the guard — the caller has taken responsibility.
4. Sets `status: active`, `hash = content_hash`, refreshes `updated`, and sets
   `version = max(version of the occupant, current version) + 1` — the version
   lineage follows the page being replaced, so repeated replacements count up
   instead of resetting to 1.
5. Writes the page to the target path (atomic: temp file + `fsync` + rename) and
   deletes the quarantine copy.
6. Stages the new file with `git add` if the vault has git. **No commit is
   made** — neither `promote` nor the CLI commits. Commit yourself when you are
   ready (`vault.git_commit("kernel: promote <name> (v<N>)")`).

After promotion the page is in the live namespace. Whether the *next completion*
sees it depends on the index: `promote` calls `index.reindex_delta(vault)` only
when the caller passed an `Index`, and the CLI does not. With an index present,
run `uv run python -m rlm_kernel.cli index --rebuild` before the next run;
without one, the helper-definition walk finds the page immediately.

### 5.4 Rejection and Demotion

**Rejection has no CLI verb.** `demote` is *not* a reject: a `pending` page is
left untouched by `demote` (the function returns without writing anything), so
`... vault demote quarantine/bad-helper.md` prints `Demoted: …` and deletes
nothing. Deleting a proposal is a library call:

```python
from pathlib import Path
from rlm_kernel.gate import reject
from rlm_kernel.vault import LocalVault

vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")
page = vault.get("quarantine/01KYC6B498FYY1R7DKCDB7Z3VQ.md")
reject(vault, page)          # permanently deletes it from quarantine/
```

Demoting a live page, from the CLI:

```bash
# active → deprecated
uv run python -m rlm_kernel.cli demote helper/old-helper.md

# deprecate and record the replacement at the same time
uv run python -m rlm_kernel.cli demote helper/old-helper.md \
    --by helper/new-helper.md
```

`demote` performs **one transition per call** — `active → deprecated`, then
`deprecated → superseded` — so reaching `superseded` takes two calls. A page
already `superseded` or `pending` is left unchanged. `--by` writes
`superseded_by` on the page being demoted; it does not touch the replacement.
Remember from §4.3 that neither status removes the page from `search()`.

---

## 6. Discovery: Search, Not the Library

### 6.1 The Progressive Disclosure Principle

A key architectural decision: **never put the library in context; put a
search tool in context.** With a kernel vault attached, the system prompt
carries one `name: summary` line per active helper page, but the full
documentation for each helper stays in the vault and is read on demand.

The model discovers helpers through three channels:

1. **The helper list** in the system prompt — `Available helpers:`, then one
   `name: summary` line per active helper page, truncated to the first 30 in
   path order (`helpers[:30]`, so effectively alphabetical within `helper/`).
   It is not ranked by relevance and not selected per profile, and it only
   exists in the vault-first prompt: without a kernel bridge the hardcoded
   `SYSTEM_PROMPT` merely names the five builtins inline.

2. **`search(query, k=5)` in the REPL** — vault-wide BM25 over `path`, `kind`,
   `name`, `title`, `summary` and the full page body, filtered by `kinds` if
   given. It returns a **string** of one-line cards —
   `[n] kind/name: title — summary` — so the model has to `print()` it, and the
   card carries no usage example. The model typically does this during the
   PROBE phase when it discovers it needs a capability it doesn't know about.

3. **Helper-to-helper calls** — one helper can call another. If
   `extract_dates()` needs word counting, it can reference `word_count()`
   directly (both live in the same `globals()` namespace). This is composition
   without the model's involvement.

### 6.2 When the Model Searches

The decomposition prologue (turn 0) encourages the model to probe the
context and plan before coding. A well-tuned model will naturally search
when it encounters an unfamiliar task:

```
Turn 1:
I need to extract dates from this context. Let me check if there's a
helper for that.

```repl
print(search("date extraction", k=3))
```

REPL output:
[1] helper/extract-dates: extract-dates — ISO 8601 date extractor — Extract ISO 8601 dates (YYYY-MM-DD) from context text.
[2] helper/grep: grep — regex search over context — Regex search over context lines, returning up to max_hits matches.
[3] helper/chunk: chunk — split context into chunks — Split context into chunks by character count or paragraph boundaries.
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
- Is ≤ 200 characters — the schema's hard cap on `summary`. The REPL card
  truncates it further, to 120 characters, so front-load the distinctive words

**Good:** `"Extract ISO 8601 dates (YYYY-MM-DD) from text using regex."`
**Bad:** `"A utility for temporal data extraction."`

The whole page body is indexed too — the FTS table carries `title`, `summary`
**and** `body` — so a query matching words inside your `## Usage example` will
still find the page. What comes back is only `title — summary`, so a match that
lives *only* in the example gets the model a card it cannot read the useful part
of. Put discoverable vocabulary in the `summary`; keep the example realistic for
the human reading the vault page.

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
# In helper/counter.md — Implementation section:
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
worker exits, all state is lost (and a second consecutive cell timeout restarts
the worker mid-completion, which loses it early). There is **no REPL-side path to
durable storage**: `propose()` writes a *page* to quarantine for review, and the
memory API (`rlm_kernel.memory.MemoryManager.add`) is harness-side only —
nothing in the worker can reach it. A helper is re-created from its page at every
completion, so the only state that survives is state you write into the page
itself (or into the context the caller passes in).

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

The REPL executes **model-written code**. That code can call helpers, and those
helpers access the context data — which is often the private part of the task.
So the honest starting point is this: **the REPL worker is a separate process,
not a permission boundary.**

- It is a child process of the harness, launched with `sys.executable` and the
  parent's environment (`env={**os.environ, "RLM_REPL_HOST": …, "RLM_REPL_PORT": …}`),
  running as the same user.
- Its cells are executed with `exec(code, globals())`. Since 2026-09-12 the
  dynamic-execution family is removed from the model's builtins (`eval`, `exec`,
  `compile`, `globals`, `locals` — `RLM_REPL_ALLOW_DYNAMIC=1` restores them) and
  the worker can be memory-bounded with `RLM_REPL_MEMORY_MB` where the OS allows
  it, but there is **no import block and no `open()` jail**: `import os` (and
  therefore `subprocess`, `socket`, and the filesystem) all still work, by design
  and by record — the `open` jail was retired as unenforceable rather than
  claimed (roadmap DG2). The worker's own names remain reachable from a cell
  through `globals()` (roadmap DG10).
- The worker even *needs* a socket: it connects back to the harness over
  loopback TCP and uses that connection to request sub-calls and vault searches.

What follows from that, stated without decoration:

1. **Process isolation buys killability and memory bounds, not safety.** A cell
   that loops forever is killed on timeout; a cell that allocates wildly dies
   with the worker. A cell that reads a file reads it.
2. **The gate is a quality gate, not containment** (§5.2.1). It keeps malformed,
   mis-signed, or obviously hostile pages out of the live prompt and forces
   model-authored content through review. It cannot make untrusted code safe.
3. **Nothing in this system is a sandbox for hostile input.** `rlm_local` is a
   local-first harness for a model you run yourself, on data you chose to hand
   it. Treat the *model's own output* as the untrusted input — that is what the
   gate and the guardrails are for — and treat a prompt-injected context
   document as code that will run with your privileges.

Two consequences worth knowing before you author a helper that does anything
interesting: the worker inherits the harness's **environment variables** (so an
`*_API_KEY` exported into the run is visible to a cell), and it has ordinary
**filesystem and network access** (so a helper that fetches a URL really does
fetch it).

### 8.2 What the REPL Process Actually Guarantees

There is no restricted-builtin table to publish here, because none exists in the
code. What the harness enforces is bounded *damage to the run*:

| Enforced | Where | Effect |
|---|---|---|
| Cell wall-clock timeout (`cell_timeout`: 60 s / 60 s / 120 s by profile) | `REPLSandbox.execute` | The cell is abandoned with a templated timeout error; the worker keeps running, and its late result is discarded by `cell_id` (R4) |
| Two consecutive timeouts → worker restart | `REPLSandbox._on_timeout` | The model is told the namespace was lost (`REPL_WORKER_RESTARTED`) |
| stdout cap = `repl_output_char_cap` (2 000 / 4 000 / 8 000) | `REPLSandbox._build_result` | Head-truncated with an explicit marker (R10) |
| stderr cap = same value, **head and tail kept** | `REPLSandbox._build_result` | The elided middle is marked; a traceback's last line survives |
| Only `print()` output returns to the model | worker `exec` handler | Assignments stay in the worker; nothing reaches the root model unless printed |
| Vault path containment | `LocalVault._resolve` (S4/R20) | Absolute paths, `..` segments, and escapes raise `ValueError` — this protects the *vault*, and it runs in the harness, not the worker |

Nothing in that table stops a cell from reading the filesystem, opening a
socket, or spawning a process. If you need real containment, you need an
OS-level boundary (a container, a job object / sandbox profile, a separate
user) around the harness process itself — the harness does not provide one, and
this guide should not be read as claiming it does.

### 8.3 Writing Secure Helpers

1. **Never hardcode secrets.** The vault is a git repository; a key committed in
   a helper page is a key in your history. Note that the *worker* inherits the
   harness environment, so `os.environ` inside a helper is also a path to
   secrets — do not put a credential in the environment of a run that executes
   model-written code.

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

3. **Cap output sizes.** The stdout cap (2 000–8 000 chars by profile) bounds
   the damage, but it truncates from the **end** — a helper that prints the raw
   input first and its findings last delivers the findings to nobody. Print a
   small, deliberate summary:

   ```python
   for i, item in enumerate(results[:max_display]):
       print(item)
   if len(results) > max_display:
       print(f"... and {len(results) - max_display} more")
   ```

4. **Handle errors gracefully.** Return error strings, not raised
   exceptions. The root model can self-correct on error strings; an unhandled
   exception goes to stderr and costs a turn. Worse, an exception raised while
   the helper is being *injected* at worker init is swallowed, so a helper can
   vanish from the namespace entirely (§4.3).

5. **Expect only the allowlisted imports to survive the gate.** The eight
   modules are `re`, `json`, `math`, `collections`, `itertools`, `functools`,
   `hashlib`, `pathlib`. A helper that imports anything else will promote-fail;
   the *REPL* would happily run it (the worker is unrestricted), so a page that
   only ever runs by hand can carry a wider import — but then nothing checks it
   and nothing documents the exception. Prefer adding the module to
   `gate.ALLOWED_IMPORTS` in `src/rlm_kernel/gate.py`, in one reviewable place.

### 8.4 The Lethal Trifecta (and How We Avoid It)

Simon Willison's "lethal trifecta" for AI agents: **private data access +
untrusted content + external communication = trivial exfiltration.**

The harness breaks the trifecta in one place only, and it is worth being precise
about which:

- **Private data** lives in `context` inside the REPL and only reaches the root
  model through the small `print()` calls the model chooses. That is a context
  *economy*, and a real privacy gain at the design level.
- **Untrusted content** (scraped pages, user uploads) is passed to sub-calls as
  a self-contained prompt; the system prompt tells the model to treat retrieved
  text as data. That is a guardrail against confusion, not a defence against a
  determined injection.
- **External communication** is *not* architecturally barred. The worker has
  network access, so a cell — model-written, or proposed by a model and promoted
  without reading — can exfiltrate anything it can read. The gate's import
  allowlist removes the ordinary routes (`socket`, `urllib`, …) from *promoted*
  helpers, and that is the whole of the mitigation today.

If your threat model includes a hostile context document, the controls you
actually have are: run the harness with no credentials in its environment and no
network you care about, review every promoted page by hand, and treat cells that
touch the network as an incident.

### 8.5 Calling External Services — The Bindings Pattern in Practice

> **Status: not implemented.** This subsection describes a **design sketch**.
> There is no `call_api` binding in the REPL worker and no
> `KernelBridge.handle_api_call()` in `src/rlm_kernel/repl_bridge.py` — that
> class has exactly `get_helper_definitions`, `get_helper_summaries`,
> `handle_search`, `handle_propose`, and `get_core_memory_summary`. The
> socket protocol has no `api_call` verb, and `REPLSandbox._handle_request`
> would answer one with `Error: unsupported REPL request`. Nothing below can be
> used today; it is kept because it is the shape the project intends to grow
> into (`docs/20260903-2107-memanto-vs-rlm-behaviour-as-content.md` lists the
> bindings pattern as stated future work).
>
> **What does exist is the same *mechanism*, used for four things.** The worker
> already speaks RPC to the harness over its loopback socket, and that is the
> seam a bindings layer would extend:

| Worker-side name | Socket verb | Harness-side handler | Returns |
|---|---|---|---|
| `llm_query(prompt, schema=None)` | `subcall` | `SubcallManager.llm_query` | response text |
| `llm_query_batched(prompts, schema=None)` | `subcall_batched` | `SubcallManager.llm_query_batched` | list of response texts |
| `search(query, k=5, kinds=None)` | `search` | `KernelBridge.handle_search` | formatted card string |
| `propose(kind, name, body, rationale="")` | `propose` | `KernelBridge.handle_propose` | `Proposed: <path>` or an `Error: …` string |

`REPLSandbox._handle_request` dispatches on the message's `cmd`/`type`; an
unrecognised verb gets an error reply rather than a crash. That dispatch table
is the place a real `api_call` verb would be added — together with a worker-side
`_harness_*` function, a `KernelBridge` handler, and an allowlist.

The rest of this subsection is the sketch: what such a layer *would* have to do,
written the way the other sections of this guide describe what the code does.
Read every "does"/"is" below as "would".

When a helper needs to call an external API — a weather service, a search
endpoint, a database — the harness would mediate the call through a **proxy
function**. The harness would hold credentials and enforce policies; the model
would write code that calls the proxy; the proxy would be the boundary.

#### 8.5.1 Architecture

*Sketch — the boxes below the worker do not exist.*

```
│  REPL Worker (separate process; not sandboxed)      │
│                                                      │
│  model code:                                         │
│    result = call_api("get_weather",                  │
│                      {"city": "Tokyo"})              │
│                          │                           │
│                          │ proxy function (absent)   │
│                          ▼                           │
│  _harness_api_call(name, args)      [not in worker]  │
│      │                                               │
│      │ sends "api_call" to harness  [verb unused]    │
└──────│───────────────────────────────────────────────┘
       │
┌──────│───────────────────────────────────────────────┐
│      ▼                                               │
│  KernelBridge.handle_api_call()     [does not exist] │
│      │                                               │
│      ├─ validate: is this API in the allowlist?      │
│      ├─ validate: are the arguments safe?            │
│      ├─ inject credentials (never exposed to REPL)   │
│      └─ call external service                        │
│                                                      │
│  External service ◄── httpx with TLS, timeout        │
└──────────────────────────────────────────────────────┘
```

The intent: the model never sees credentials, and the harness is the single
chokepoint where external access is mediated. Note that the second half of that
claim is contradicted by the worker as it exists — the worker *does* open a
socket (to the harness, over loopback) and would have to be prevented from
opening others for the sketch to hold.

#### 8.5.2 The Allowlist

*Sketch.* In such a design, every external API would have to be explicitly
registered before the model could call it — deny-by-default, so a missing entry
fails before any network access occurs:

```python
# In the harness-side handler (part of KernelBridge or a dedicated module)
API_ALLOWLIST = {
    "get_weather": {
        "url": "https://api.weather.example/v1/current",
        "method": "GET",
        "credential_env": "WEATHER_API_KEY",
        "allowed_params": {"city", "units"},
        "timeout": 10,
        "max_response_chars": 2000,
    },
    "search_web": {
        "url": "https://search.example/api/v1/query",
        "method": "POST",
        "credential_env": "SEARCH_API_KEY",
        "allowed_params": {"q", "max_results", "safe_search"},
        "timeout": 15,
        "max_response_chars": 4000,
    },
}
```

Each entry declares: the endpoint URL, HTTP method, credential source
(an environment variable name — never a hardcoded secret), the set of
allowed parameter names, a timeout, and a response size cap.

#### 8.5.3 The Worker-Side Proxy

*Sketch.* The REPL worker would need a proxy function that sends a socket
message to the harness and waits for the response, following the same pattern as
the four `_harness_*` functions that exist today (`_harness_llm_query`,
`_harness_llm_query_batched`, `_harness_search`, `_harness_propose`):

```python
# Would go in WORKER_SCRIPT, alongside the existing _harness_* functions
def _harness_api_call(name, args=None):
    _send({"cmd": "api_call", "name": name, "args": args or {}})
    resp = _recv()
    result = resp.get("result", {})
    if isinstance(result, dict) and "error" in result:
        print(f"API error: {result['error']}")
    return result

# Inject into globals so exec'd code can use it
call_api = _harness_api_call
```

For reference, the proxy names the worker actually injects today are
`llm_query`, `llm_query_batched`, `search`, and `propose` (plus the five builtin
helpers defined inline). `call_api` is not among them.

#### 8.5.4 The Harness-Side Handler

*Sketch.* The handler would run in the harness process. It would validate the
request, inject secrets, make the HTTP call, and sanitize the response. **This
function does not exist** — `KernelBridge` has no `handle_api_call`, and no
module in `src/` defines `API_ALLOWLIST`:

```python
def handle_api_call(self, name: str, args: dict) -> dict:
    """Handle an api_call socket command from the REPL worker."""
    import httpx
    from urllib.parse import urlparse

    # 1. Validate: is this API allowlisted?
    api = API_ALLOWLIST.get(name)
    if not api:
        return {"result": {"error": f"API '{name}' is not in the allowlist"}}

    # 2. Validate: no unknown parameters (prevents injection)
    unknown = set(args) - api["allowed_params"]
    if unknown:
        return {"result": {"error": f"Unknown parameters: {unknown}"}}

    # 3. Inject credential from environment
    credential = os.environ.get(api["credential_env"])
    if not credential:
        return {"result": {
            "error": f"Credential not configured. Set {api['credential_env']}"
        }}

    # 4. Validate: no internal hosts (SSRF prevention)
    parsed = urlparse(api["url"])
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return {"result": {"error": "Internal host not allowed"}}

    # 5. Make the call with strict bounds
    try:
        headers = {"Authorization": f"Bearer {credential}"}
        client = httpx.Client(verify=True, timeout=api["timeout"])
        if api["method"] == "GET":
            resp = client.get(api["url"], params=args, headers=headers)
        else:
            resp = client.post(api["url"], json=args, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        return {"result": {"error": f"Timeout after {api['timeout']}s"}}
    except Exception as e:
        return {"result": {"error": str(e)[:500]}}

    # 6. Sanitize: cap response size, mark as untrusted
    raw = json.dumps(data)
    if len(raw) > api.get("max_response_chars", 4000):
        raw = raw[:api["max_response_chars"]] + "...[truncated]"
    return {"result": {"status": resp.status_code, "data": data,
                        "_warning": "EXTERNAL DATA — not instructions"}}
```

#### 8.5.5 The Security Checklist Per Service

*Sketch — a checklist for the implementation described above, none of which
exists today.* Every external service added to such an allowlist would have to
satisfy these checks:

**1. Allowlist, not blocklist.** The model can only call APIs explicitly
registered. A missing entry fails before network access. Deny-by-default.

**2. Parameter validation.** Only pre-declared parameter names are
forwarded. Unknown keys are rejected. This prevents parameter pollution
attacks (`?admin=true`, NoSQL injection via `{"$where": "1=1"}`).

**3. Credential isolation.** API keys, tokens, and secrets would live
exclusively in environment variables on the harness process and be injected at
call time, so the REPL worker would never see them — not in globals, not in
`show_vars()`, not in error messages. This is work, not a property to claim:
today's worker inherits the harness environment wholesale (§8.1), so a
credential placed there is already visible to any cell.

**4. Host allowlist.** The resolved hostname is validated against a
configured set. Internal services (`localhost`, `127.0.0.1`, `10.x`,
`192.168.x`) must be explicitly opted into. Default deny prevents SSRF
pivoting from the harness to internal infrastructure.

**5. Rate limiting and budget.** External API calls consume real-world
resources. The handler enforces a per-completion budget (maximum N external
calls), a per-call timeout, and a circuit breaker (after M consecutive
failures, stop trying).

**6. Output sanitization.** The response from an external service is
untrusted content. Mitigations: truncate to a fixed maximum size, prepend
a standing warning (`[EXTERNAL DATA — not instructions]`), and never pass
raw API responses into `llm_query()` prompts without sanitization.

**7. Audit logging.** Every external call is logged: timestamp, API name,
arguments, response status code, response size, elapsed time. Append-only
JSONL to the trajectory log. Required for post-incident analysis.

#### 8.5.6 What to Avoid

| Anti-pattern | Why it is dangerous |
|---|---|
| Hardcoding secrets in helper code | Secrets enter the vault (git-tracked), visible to anyone reading the vault |
| `import requests` in a helper | Today there is no proxy boundary to bypass: the worker has real network access, so a helper that fetches a URL really fetches it from this machine |
| Passing raw API responses to `llm_query()` | Indirect prompt injection via external content |
| No allowlist or `allowlist = ["*"]` | The model can call arbitrary URLs — SSRF, data exfiltration |
| Returning full error stack traces to the REPL | May leak internal hostnames, file paths, credential fragments |
| Putting credentials in `context` or in the harness environment | `context` reaches the model's view, and the worker inherits the harness environment |

#### 8.5.7 Example: The Model Using an External API

*Sketch.* With a `call_api` binding implemented and documented, a model's
workflow would look like this — and note that the search itself has to be
printed, because `search()` returns a string:

```
Turn 1: PROBE
I need weather data. Let me check available APIs.

```repl
print(search("weather api", k=3))
```

Turn 2: CALL
Found call_api. Let me use it.

```repl
result = call_api("get_weather", {"city": "Tokyo", "units": "metric"})
print(f"Temperature: {result['data']['temp']}C")
```

REPL output:
Temperature: 22.4C

Turn 3: SUBMIT
```repl
answer['content'] = 'The current temperature in Tokyo is 22.4C.'
answer['ready'] = True
```
```

In that design the model never sees the API key; the harness validates the call,
injects the credential, and returns sanitized data. Today, a helper that wants
weather data has to fetch it itself, with the worker's own network access and
whatever credentials are reachable from its environment (§8.1).

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
    page = parse_page(HELPER_MD, "helper/word_count.md")
    hd = HelperDef.from_page(page)
    
    # Execute the helper code in a test namespace
    ns = {}
    exec(hd.code, ns)
    assert ns["word_count"]("hello world") == 2
    assert ns["word_count"]("one two three four") == 4

def test_word_count_empty():
    page = parse_page(HELPER_MD, "helper/word_count.md")
    hd = HelperDef.from_page(page)
    ns = {}
    exec(hd.code, ns)
    assert ns["word_count"]("") == 0  # "".split() is [], not ['']
```

A caveat that catches most hand-written tests of this shape: the test namespace
is a plain dict, so `exec(hd.code, ns)` gives the code the *real* builtins and no
`context`. A helper that reaches for the REPL's `context` — as most useful ones
do — raises `NameError` here. Either pass what it needs explicitly (the test
above relies on the helper's own parameter), or seed the namespace:
`ns = {"context": "hello world"}`. Testing a helper against a bare `str` also
misses the byte-addressed handle it will actually receive at runtime (§4.2).

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
    
    # name MUST equal the function the code defines (the static define check)
    path = propose(vault, "helper", "safe_helper", body, "A safe helper")
    page = vault.get(path)
    report = validate(page, vault=vault)        # static: parses, never runs
    
    assert report.passed, f"Validation failed: {report.errors}"
    assert report.executed is False             # nothing was executed

def test_blocked_import_fails_validation(temp_vault):
    from rlm_kernel.gate import propose, validate
    from rlm_kernel.vault import LocalVault
    
    vault = LocalVault(temp_vault, init_git=False)
    
    body = """## Implementation
```python
def bad_helper():
    import os
    os.system("echo pwned")
```"""
    
    path = propose(vault, "helper", "bad_helper", body, "A bad helper")
    page = vault.get(path)
    report = validate(page, vault=vault)
    
    assert not report.passed
    # Two independent errors: 'os' is off the import allowlist, and the
    # 'os.system' substring is a blocked pattern.
    assert any("os" in e.lower() for e in report.errors)

def test_sandbox_is_opt_in(temp_vault):
    """execute=True adds the sandbox run; it does not call your helper."""
    from rlm_kernel.gate import propose, validate
    from rlm_kernel.vault import LocalVault
    
    vault = LocalVault(temp_vault, init_git=False)
    body = """## Implementation
```python
def safe_helper():
    return 42
```"""
    page = vault.get(propose(vault, "helper", "safe_helper", body, "A safe helper"))
    
    report = validate(page, vault=vault, execute=True)
    assert report.executed is True
```

`temp_vault` is the fixture defined in `tests/rlm_kernel/conftest.py`, which
hands you a temporary vault root as a `Path`; because a `conftest.py` only
supplies fixtures to the directory it sits in and below, put these tests under
`tests/rlm_kernel/`. Note what they can and cannot prove: a passing static
report means the page parses, imports only allowlisted modules, avoids five
substrings, and defines a callable with the right name. It says nothing about
whether the code *works* — `exec` defines, it never calls
(§5.2.1). Test behaviour separately, the way §9.1 does.

### 9.3 Testing End-to-End in the REPL

The definitive test: does the model actually call your helper? Two ways to ask
it, and the fast one is the one this repository uses.

**Against a stub backend (fast, deterministic).** The root-loop integration
tests define a local `StubBackend` — a class whose `chat(messages, *, tier="root",
max_tokens=1500, temperature=0.0, response_schema=None)` returns scripted
responses in sequence — and drive `RootLoop` with it. Scripting a cell that calls
your helper answers one narrow, checkable question: was the page extracted,
injected, and callable in a real cell? No model server required.

The trick to making it a real test is to let the helper *produce* the answer, so
a missing injection shows up as a failure rather than a silently different path:

```python
def test_helper_is_injected_and_callable(tiny_cfg, bridge):
    """Helper page → REPL namespace → runs in a real cell."""
    from rlm_local.root_loop import RootLoop

    backend = StubBackend([
        "```repl\n"
        "answer['content'] = str(word_count('hello world'))\n"
        "answer['ready'] = True\n"
        "```",
    ])
    loop = RootLoop(tiny_cfg, backend, kernel_bridge=bridge)
    try:
        answer = loop.run("Count the words in the context.", "hello world")
    finally:
        loop.shutdown()

    # If word_count were absent the cell raises NameError, nothing is
    # submitted, and the stub's fallback response ("42") comes back instead.
    assert answer == "2"
```

The helper page must already be in the vault — `seeded_vault` seeds only the five
builtins, so promote the `word_count` page from §3 first — and the `bridge`
fixture rebuilds the index, which is the same path any promoted helper takes.
`tests/test_repl.py` shows how this repository asserts on cells and namespaces
directly when you want finer-grained evidence.

**Against a real model (slow).** Whether the *model* chooses your helper is a
model-behaviour question, and belongs behind `@pytest.mark.slow` with a reachable
`llama-server` (the `slow` marker is excluded by the default
`-k "not slow"` run):

```python
@pytest.mark.slow
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

`seeded_vault` and `bridge` are the fixtures in
`tests/test_root_loop_integration.py` (a seeded temp vault plus a `KernelBridge`
over a rebuilt index). The `assert "2" in answer` is deliberately loose — a
small model can produce the right answer by other means, so treat a pass as
weak evidence and inspect the trajectory log if the point is that *your* helper
was used.

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

The `str(context)` line above is the honest fallback, but it is also the one
operation that materialises a disk-backed context in full (§4.2). If the helper
may run against a spilled context, prefer the builtin's shape:

```python
if hasattr(context, "grep"):
    hits = context.grep(pattern, max_hits)     # streams off disk
else:
    ...                                        # str(context) path
```

---

## 11. The Capability Architecture at Scale

### 11.1 How Many Helpers Is Too Many?

The helper list in the system prompt is capped at **30 entries**
(`helpers[:30]`), with no character budget and no relevance ranking: the cap
falls on path order, which for pages under `helper/` is alphabetical by name. If
you have more helpers than that, everything past the cut is discoverable only
through `search()` — there is no namespace mechanism, only naming discipline.

A meta-helper is a legitimate way to spend one of the 30 slots. Remember that
`search()` returns a formatted string, so a helper wrapping it should print or
return that string rather than index into it:

```python
def find_helper(task_description):
    """Search the vault for helpers matching a task description."""
    query = " ".join(task_description.split()[:5])
    cards = search(query, k=5)   # a formatted string of [n] kind/name: …
    print(cards)
    return cards
```

### 11.2 The Compounding Library

The LATM (Learning to Augment with Tools) economics apply: each validated
helper becomes a **functional cache** — a capability that cost N turns to
author but costs 1 turn to reuse. Over months of use, a vault accumulates
a library of battle-tested helpers, each making the next task easier.

The gate is what makes this compounding *reviewable*: no model-authored page
reaches the live namespace without passing the static checks and a human
`promote` (§5.3). It is not what makes it *safe* — the checks are tripwires, not
containment (§5.2.1). Two further properties matter once the library is large:
hand-authored pages bypass the gate entirely, and deprecation removes a helper
from the REPL namespace (§4.3) while leaving it findable in search, so steer new
usage toward replacements with `superseded_by` and only demote a helper once
nothing calls it.

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

These five helpers are defined in the REPL worker script itself, so they exist
with or without a kernel vault. (A vault page of the same name is `exec()`'d
after them at init, so it **shadows** the builtin — which is why the seeded
copies are byte-identical to the worker's versions.)

### `peek(n=2000)`
Preview the first `n` characters of the context. Prints and returns the text.
```python
def peek(n: int = 2000) -> str: ...
```
Note that the implementation is `str(context)[:n]`: on a disk-backed context it
materialises the whole text before slicing. Use `grep`, `chunk`, or
`context[a:b]` when the input is large (§4.2).

### `grep(pattern, max_hits=50)`
Regex search over context lines. Prints matching lines and returns them.
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```
Streams via `context.grep()` when the context handle offers it. An invalid regex
prints `Error: invalid regex: …` and returns `[]`.

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

Every fix below assumes the vault is at the default
`~/.local/share/rlm-kernel/vault`; pass `--vault PATH` if yours is elsewhere.

### Helper not appearing in the system prompt

**Check:** Is the frontmatter `status: active`? Does the page have an
`## Implementation` section with a fenced ` ```python ` block? Is there more than
one page claiming the same `name`?

**Fix:** Rebuild the index and search for it:

```bash
uv run python -m rlm_kernel.cli index --rebuild
uv run python -m rlm_kernel.cli search "<helper-name>" --kind helper
```

`index --rebuild` is the important step: helper definitions and the prompt's
helper list are both answered from `.index/meta.sqlite` whenever it exists, so a
page you just copied in is invisible until the index knows about it. Also note
that the list is truncated at 30 entries in path order — a helper that sorts
late may simply not be listed (§11.1).

### Helper code has a syntax error

**Symptom:** `NameError: name 'my_helper' is not defined` when the model calls
it — *not* a `SyntaxError`. See the explanation below.

**Fix:** Edit the helper page, fix the code, rebuild the index. No restarts
needed; the next completion picks up the fixed code.

**Why there is no error message:** helpers are injected at worker init with
`exec(helper["code"], globals())` inside a bare `try/except Exception: pass`, so
a helper that does not compile (or that raises at import time) is skipped
silently and simply does not exist in the namespace. A page promoted through the
gate cannot be in this state — the static AST parse rejects it (§5.2) — so this
only happens to hand-edited pages. `show_vars()` in a cell will confirm which
names actually made it into the namespace.

### Helper uses an import that's blocked

**Symptom:** Validation (or a refused promotion) saying
`Import 'x' not in allowlist`.

**Fix:** Either rewrite the helper to avoid the import, or add the module to
`gate.ALLOWED_IMPORTS` in `src/rlm_kernel/gate.py` and re-run validation. The
allowlist is eight modules: `re`, `json`, `math`, `collections`, `itertools`,
`functools`, `hashlib`, `pathlib`.

Worth knowing before you rewrite: the allowlist binds **promotion**, not
execution. The REPL worker is unrestricted, so a hand-authored page importing
`datetime` runs fine — it just cannot pass the gate. If you want it in the
library, add the module to the allowlist rather than keeping a page that only
works by hand.

### Model doesn't discover the helper

**Symptom:** The model writes its own implementation instead of calling
the available helper.

**Fix:** Improve the helper's `summary` — it is the only text the model sees in
the prompt's helper list, and the 120-character head of it is all a search card
shows. Use the same vocabulary the model uses when describing the task, and put
the distinctive words first. The `## Usage example` is indexed for *matching*
but is not displayed, so it cannot carry discoverability on its own (§6.3). If
the list is at its 30-entry cap, a late-sorting helper will never be listed: rely
on `search()`, or rename it so it sorts earlier.

### Helper works in tests but not in the REPL

**Symptom:** Unit tests pass, but the model gets errors calling the helper.

**Fix:** The REPL supplies a different *world* than a bare test namespace, and it
is worth checking each difference in turn:

- **`context` is not a plain `str`.** It is a context handle: byte-addressed, with
  `lines()` / `grep()` / `chunk()` and no `split()` or `find()`. A helper that
  calls `context.split("\n")` works in a test that passes a string and raises
  `AttributeError` in the REPL. Use `str(context)` (materialises the text),
  `context.grep(...)`, or feature-detect with `hasattr(context, "grep")` (§4.2).
- **`len(context)` is a byte count**, and `context[i]` is the character at byte
  offset `i` — a mid-codepoint offset raises `UnicodeDecodeError` (R2).
- **Your helper may not be in the namespace at all** if it failed to compile or
  imported something unavailable at init — that failure is swallowed (§4.3).
  `show_vars()` will show you.
- **Your output may have been capped.** stdout is head-truncated at 2 000 /
  4 000 / 8 000 characters by profile, so a helper that prints a large
  intermediate result can hide its own findings (R10).
- **A cell can time out** (60 s / 120 s by profile) — a helper that does per-line
  regex work over a large context in one call may exceed it, and the second
  consecutive timeout restarts the worker, losing all variables.

### Multiple helpers with the same name

**Symptom:** Only one helper appears despite having multiple pages.

**Fix:** Helper names must be unique, because the injected name *is* the page's
`name`: two pages that declare the same `name` are both injected in path order
and the last one silently wins. `promote` cannot create this state — the
occupancy guard refuses an active occupant at `helper/<name>.md` (§5.3) — so it
arises only from hand-authored pages. Rename one, or use `superseded_by` to link
an old helper to its replacement before demoting it.

---

*The capability architecture described here adapts principles from LATM
(learning to augment with tools), Voyager (persistent skill libraries),
DynaSaur (runtime tool accumulation), and the Forth dictionary model to
the constraints of small, local, CPU-only language models. The key insight
is that tools should be content — discoverable, versioned, reviewable
pages — not code frozen in the harness.*
