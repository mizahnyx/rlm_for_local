"""Vault seeding — first-run initialization of contracts, templates, and
builtin helpers (§3.1, §3.2).

Idempotent: skips pages that already exist in the vault.
"""

from __future__ import annotations

from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import VaultStore


def seed_vault(vault: VaultStore) -> None:
    """Seed a vault with canonical pages. Idempotent — skips existing pages.

    Creates three categories of pages:
    1. Contracts: repl-contract, how-to-work
    2. Templates: nudge/warning/header texts under contract/templates/
    3. Builtin helpers: peek, grep, chunk, map_query, show_vars
    """
    _seed_contracts(vault)
    _seed_templates(vault)
    _seed_helpers(vault)
    _seed_fewshots(vault)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _put_if_missing(vault: VaultStore, page: Page, path: str) -> bool:
    """Put page at *path* if it doesn't already exist. Returns True if created."""
    if vault.exists(path):
        return False
    vault.put(page, path)
    return True


def _fm(
    kind: PageKind,
    name: str,
    title: str,
    summary: str,
    tags: list[str] | None = None,
) -> Frontmatter:
    """Create a Frontmatter with canonical defaults."""
    return Frontmatter(
        kind=kind,
        name=name,
        title=title,
        summary=summary,
        tags=tags or [],
        status=PageStatus.ACTIVE,
    )


def _page(
    kind: PageKind,
    name: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | None = None,
) -> Page:
    """Create a Page with given frontmatter + body."""
    return Page(frontmatter=_fm(kind, name, title, summary, tags), body=body)


# ── Contracts ─────────────────────────────────────────────────────────────────


def _seed_contracts(vault: VaultStore) -> None:
    """Seed contract pages: repl-contract and how-to-work."""

    # ── repl-contract ─────────────────────────────────────────────────────
    _put_if_missing(
        vault,
        _page(
            kind=PageKind.CONTRACT,
            name="repl-contract",
            title="REPL Contract",
            summary="The REPL execution contract: capabilities, helpers, and output rules.",
            tags=["contract", "repl"],
            body=_REPL_CONTRACT_BODY,
        ),
        "contract/repl-contract.md",
    )

    # ── how-to-work ───────────────────────────────────────────────────────
    _put_if_missing(
        vault,
        _page(
            kind=PageKind.CONTRACT,
            name="how-to-work",
            title="How to Work (Orchestrator Addendum)",
            summary="Orchestrator rules: probe, plan, execute, submit workflow with turn budgeting.",
            tags=["contract", "orchestrator"],
            body=_HOW_TO_WORK_BODY,
        ),
        "contract/how-to-work.md",
    )


_REPL_CONTRACT_BODY = """\
You are a Recursive Language Model: you answer a query whose (potentially huge)
context lives in a Python REPL, not in this conversation. You act turn by turn
until you submit an answer.

## REPL Contract

- Write code in ```repl fences. The REPL persists across turns.
- Available bindings:
  - `context` — the data being processed (str, list, dict, or custom object).
  - `answer` — a dict; set `answer["content"]` and `answer["ready"] = True` to submit.
  - `llm_query(prompt)` — invoke a sub-LLM with a self-contained prompt.
  - `llm_query_batched(list_of_prompts)` — batched sub-LLM invocation.
- Builtin helpers:
  - `peek(n=2000)` — return first *n* characters of str(context).
  - `grep(pattern, max_hits=50)` — regex search over context lines.
  - `chunk(size=None, by=None)` — split context into chunks (by character count
    or by "paragraph").
  - `map_query(items, template, batch=True)` — apply a template to each item
    and batch-query.
  - `show_vars()` — print user-defined REPL variables.
- Only `print()` output is shown back to you, truncated to `{repl_cap}` characters.
  Never print large slices of context — pass slices to `llm_query` instead.
- `llm_query` sees ONLY the prompt you give it (no REPL, no history). Give it a
  self-contained chunk and ask for a short, specific answer. It handles about
  `{sub_budget}` characters well.
"""


_HOW_TO_WORK_BODY = """\
## How to Work (follow this order)

1. **PROBE** — Print small samples and counts to learn the context's structure.
2. **PLAN** — In prose, state how the task decomposes: what each turn computes
   and which sub-calls it issues — before writing more code.
3. **EXECUTE** — One small step per turn; print a tiny verification sample after each.
4. **SUBMIT** — Only after you have printed your candidate answer. If turns run
   low, submit your best inference rather than nothing.

## Rules for a Small Context Window (yours)

- Your own window is small: delegate reading, summarizing, classifying, and
  extracting to `llm_query`; keep only small results and decisions yourself.
- Prefer one `llm_query_batched` over a loop of `llm_query` calls.
- If a keyword/regex search answers it directly, skip sub-calls.
- Chunk big work: {example_chunking_idiom}.

You have `{max_turns}` turns. Plan accordingly.
"""


# ── Templates ─────────────────────────────────────────────────────────────────


def _seed_templates(vault: VaultStore) -> None:
    """Seed nudge, warning, and header template pages under contract/templates/."""

    templates: list[tuple[str, str, str, str, str]] = [
        # ── Nudges ──────────────────────────────────────────────────────
        (
            "nudge-no-block",
            "Nudge: No REPL Block",
            "Nudge emitted when the model response contains no ```repl fence.",
            "contract/templates/nudge-no-block.md",
            "No ```repl block found. Emit exactly one ```repl block with your next step.",
        ),
        (
            "nudge-empty-answer",
            "Nudge: Empty Answer",
            "Nudge emitted when answer['ready'] is True but answer['content'] is empty.",
            "contract/templates/nudge-empty-answer.md",
            'answer["ready"] was set to True but answer["content"] is empty. '
            "Populate answer[\"content\"] with your result before setting ready=True.",
        ),
        (
            "nudge-narration",
            "Nudge: Narration Without Code",
            "Nudge emitted when the model describes code without emitting a ```repl block.",
            "contract/templates/nudge-narration.md",
            "You described code without emitting it. "
            "Emit exactly one ```repl block containing the code you intend to run.",
        ),
        # ── Warnings ────────────────────────────────────────────────────
        (
            "subcall-oversize-warning",
            "Warning: Sub-call Oversize",
            "Warning emitted when a sub-call prompt exceeds the recommended budget.",
            "contract/templates/subcall-oversize-warning.md",
            "[WARNING] This sub-call prompt ({size} chars) exceeds the recommended "
            "{budget}-char budget. Quality may degrade.",
        ),
        (
            "subcall-count-exhausted",
            "Warning: Sub-call Count Exhausted",
            "Warning emitted when the sub-call count budget is exhausted.",
            "contract/templates/subcall-count-exhausted.md",
            "Error: sub-call budget exhausted ({used}/{max_subcalls} calls used). "
            "Finalize with what you have.",
        ),
        (
            "shortcut-warning",
            "Warning: Shortcut",
            "Warning emitted when a sub-call receives a large fraction of the full context.",
            "contract/templates/shortcut-warning.md",
            "[WARNING] This sub-call received {pct:.0f}% of the full context. "
            "Consider decomposing into smaller chunks for better results.",
        ),
        # ── Headers ─────────────────────────────────────────────────────
        (
            "metadata-header",
            "Header: Metadata Template",
            "Header template for turn metadata display.",
            "contract/templates/metadata-header.md",
            "Context: {context_type}, {context_len} chars | "
            "Turn {turn}/{max_turns} | Sub-budget: {sub_budget} chars",
        ),
        (
            "turn-header",
            "Header: Turn",
            "Header template for turn count display.",
            "contract/templates/turn-header.md",
            "Turn {turn}/{max_turns}.",
        ),
        # D-K4-2: Real prologue page (matches rlm_local.templates.PROLOGUE)
        (
            "prologue",
            "Prologue: Decomposition Nudge",
            "Mandatory turn-0 decomposition nudge — PROBE + PLAN before code.",
            "contract/templates/prologue.md",
            "Before writing code, describe in 1-2 sentences:\n"
            "1. What you need to learn about the context (probe plan).\n"
            "2. How the overall answer decomposes into smaller sub-problems.\n\n"
            "Then emit exactly one ```repl block with your probing code.",
        ),
    ]
    for name, title, summary, path, body in templates:
        _put_if_missing(
            vault,
            _page(
                kind=PageKind.TEMPLATE,
                name=name,
                title=title,
                summary=summary,
                body=body,
                tags=["template"],
            ),
            path,
        )


# ── Builtin Helpers ──────────────────────────────────────────────────────────


def _seed_helpers(vault: VaultStore) -> None:
    """Seed builtin helper pages under helpers/."""

    helpers: list[tuple[str, str, str, str, str, str]] = [
        (
            "peek",
            "peek",
            "Return first n characters of str(context).",
            _PEEK_SIGNATURE,
            _PEEK_IMPL,
            _PEEK_USAGE,
        ),
        (
            "grep",
            "grep",
            "Regex search over context lines, returning up to max_hits matches.",
            _GREP_SIGNATURE,
            _GREP_IMPL,
            _GREP_USAGE,
        ),
        (
            "chunk",
            "chunk",
            "Split context into chunks by character count or paragraph boundaries.",
            _CHUNK_SIGNATURE,
            _CHUNK_IMPL,
            _CHUNK_USAGE,
        ),
        (
            "map_query",
            "map_query",
            "Apply a template to each item, then batch-query via llm_query_batched.",
            _MAP_QUERY_SIGNATURE,
            _MAP_QUERY_IMPL,
            _MAP_QUERY_USAGE,
        ),
        (
            "show_vars",
            "show_vars",
            "Print all user-defined variables in the REPL global namespace.",
            _SHOW_VARS_SIGNATURE,
            _SHOW_VARS_IMPL,
            _SHOW_VARS_USAGE,
        ),
    ]

    for name, title, summary, sig, impl, usage in helpers:
        body = f"## Signature\n\n```python\n{sig}\n```\n\n## Implementation\n\n```python\n{impl}\n```\n\n## Usage example\n\n{usage}\n"
        _put_if_missing(
            vault,
            _page(
                kind=PageKind.HELPER,
                name=name,
                title=title,
                summary=summary,
                body=body,
                tags=["builtin"],
            ),
            f"helpers/{name}.md",
        )


# ── Helper: peek ─────────────────────────────────────────────────────────────

_PEEK_SIGNATURE = """\
def peek(n: int = 2000) -> str:"""

_PEEK_IMPL = """\
def peek(n=2000):
    \"\"\"Return first n characters of context as a string.\"\"\"
    s = str(context)[:n]
    print(s)
    return s"""

_PEEK_USAGE = """\
```python
# See the first 500 characters of context
peek(500)

# Default: first 2000 characters
peek()
```"""


# ── Helper: grep ─────────────────────────────────────────────────────────────

_GREP_SIGNATURE = """\
def grep(pattern: str, max_hits: int = 50) -> list[str]:"""

_GREP_IMPL = """\
def grep(pattern, max_hits=50):
    \"\"\"Regex search over context lines, returning up to max_hits matches.\"\"\"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        print(f"Error: invalid regex: {e}")
        return []
    hits = []
    if hasattr(context, 'grep'):
        hits = context.grep(pattern, max_hits)
    else:
        for line in str(context).splitlines():
            if regex.search(line):
                hits.append(line)
                if len(hits) >= max_hits:
                    break
    for h in hits:
        print(h)
    return hits"""

_GREP_USAGE = """\
```python
# Find lines containing "error" (case-sensitive)
grep("error")

# Find lines matching a regex, limit to 10 hits
grep(r"\\d{4}-\\d{2}-\\d{2}", max_hits=10)
```"""


# ── Helper: chunk ────────────────────────────────────────────────────────────

_CHUNK_SIGNATURE = """\
def chunk(size: int | None = None, by: str | None = None) -> list[str]:"""

_CHUNK_IMPL = """\
def chunk(size=None, by=None):
    \"\"\"Split context into chunks by character count or paragraph boundaries.\"\"\"
    if hasattr(context, 'chunk'):
        chunks = context.chunk(size=size, by=by)
    else:
        text = str(context)
        if by == 'paragraph':
            chunks = [p.strip() for p in re.split(r'\\n\\s*\\n', text) if p.strip()]
        else:
            sz = size or 3000
            chunks = [text[i:i+sz] for i in range(0, len(text), sz)]
    print(f"Produced {len(chunks)} chunks")
    return chunks"""

_CHUNK_USAGE = """\
```python
# Split into ~3000-char chunks (default)
chunks = chunk()

# Split into ~1000-char chunks
chunks = chunk(size=1000)

# Split by paragraph boundaries
chunks = chunk(by="paragraph")
```"""


# ── Helper: map_query ────────────────────────────────────────────────────────

_MAP_QUERY_SIGNATURE = """\
def map_query(items: list, template: str | callable, batch: bool = True) -> list:"""

_MAP_QUERY_IMPL = """\
def map_query(items, template, batch=True):
    \"\"\"Apply a template to each item, then dispatch via llm_query / llm_query_batched.\"\"\"
    if callable(template):
        prompts = [template(item) for item in items]
    else:
        prompts = [template.replace('{text}', str(item)) for item in items]
    if batch:
        return llm_query_batched(prompts)
    return [llm_query(p) for p in prompts]"""

_MAP_QUERY_USAGE = """\
```python
# Classify each chunk with batched sub-calls
chunks = chunk(size=2000)
results = map_query(chunks, "Classify this text as NEWS, OPINION, or OTHER: {text}")

# Using a callable template
results = map_query(chunks, lambda item: f"Summarize: {item[:500]}")
```"""


# ── Helper: show_vars ────────────────────────────────────────────────────────

_SHOW_VARS_SIGNATURE = """\
def show_vars() -> None:"""

_SHOW_VARS_IMPL = """\
def show_vars():
    \"\"\"Print all user-defined variables in the REPL global namespace.\"\"\"
    import inspect
    frame = inspect.currentframe()
    if frame and frame.f_back:
        g = frame.f_back.f_globals
        _ignore = frozenset({
            "answer", "context", "__builtins__", "llm_query", "llm_query_batched",
            "_SHOW_VARS_IGNORE",
        })
        for name in sorted(g):
            if name.startswith('_') or name in _ignore:
                continue
            val = g[name]
            t = type(val).__name__
            try:
                s = str(val)
                if len(s) > 80:
                    s = s[:80] + '...'
            except Exception:
                s = '<unprintable>'
            print(f"{name}: {t} = {s}")"""

_SHOW_VARS_USAGE = """\
```python
# Show all user-defined variables in the REPL
show_vars()
```"""


# ── Few-shots ──────────────────────────────────────────────────────────────

_FEWSHOT_EXAMPLE_BODY = """\
# Example Few-Shot Transcript

This is a worked example demonstrating the exact message format.
It shows probing, searching, verifying, and submitting an answer.

## Query
What color is mentioned in the context?

## Context
The sky appeared bright and clear today. The dominant color observed was blue.

## Worked Transcript

Turn 1: Probe
```repl
print(f'Context length: {len(context)}')
print(f'First 200 chars: {context[:200]}')
```

Turn 2: Search
```repl
hits = grep('blue')
print(f'Found {len(hits)} matches for blue')
```

Turn 3: Submit
```repl
answer['content'] = 'The color blue is mentioned in the context.'
answer['ready'] = True
```
"""


def _seed_fewshots(vault: VaultStore) -> None:
    """Seed a canonical few-shot example page (D-K4-2)."""
    _put_if_missing(
        vault,
        _page(
            kind=PageKind.FEWSHOT,
            name="example",
            title="Example Few-Shot: Needle Search",
            summary="Worked few-shot transcript demonstrating probe→search→submit.",
            body=_FEWSHOT_EXAMPLE_BODY,
            tags=["fewshot", "example"],
        ),
        "fewshots/example.md",
    )
