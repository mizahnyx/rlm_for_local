# RLM Kernel — An Evolvable, Smalltalk-style Layer for `rlm_for_local`

**Comprehensive System Documentation**  
**Version 0.1.0 | July 2026**

---

## Foreword

Software that uses language models faces a peculiar problem: the very instructions
that govern its behavior — prompts, templates, examples, conventions — are
frozen in the source code at release time. Every improvement requires a new
`pip install`. Every customization requires a code change. The system cannot
describe itself to itself, cannot grow new vocabulary, and cannot improve its
own textual surface without a human editing Python files.

This is backwards. A system built around language models should treat language
as *content*, not code. Its prompts should be pages in a wiki. Its capabilities
should be documents the model can discover by search. Its memory should be
notes it authors itself. And all of it should be versioned, reviewable, and
evolvable — the way a Smalltalk image evolves through its system browser, but
human-readable.

**RLM Kernel** is that layer. It wraps the `rlm_local` RLM harness and
transforms it from a fixed program into a living, self-describing system. This
book documents every module, every design decision, every API, and every
failure mode. It is written for the engineer who needs to understand *why* a
thing works, not just *that* it works.

---

## Table of Contents

1. [Architecture and Philosophy](#1-architecture-and-philosophy)
2. [Quick Start](#2-quick-start)
3. [The Page Model](#3-the-page-model)
4. [The Vault](#4-the-vault)
5. [The Index and Search](#5-the-index-and-search)
6. [The REPL Bridge](#6-the-repl-bridge)
7. [The Gate](#7-the-gate)
8. [Memory](#8-memory)
9. [Offline Optimization](#9-offline-optimization)
10. [Prompt and Template Loading](#10-prompt-and-template-loading)
11. [CLI Reference](#11-cli-reference)
12. [Integration with rlm_local](#12-integration-with-rlm_local)
13. [Testing Strategy](#13-testing-strategy)
14. [Failure Modes and Mitigations](#14-failure-modes-and-mitigations)
15. [API Reference](#15-api-reference)

---

## 1. Architecture and Philosophy

### 1.1 The Big Picture

```
┌──────────────────────────────────────────────────────────────────┐
│                        rlm_local Harness                         │
│  completion(query, context) ─► RootLoop ─► REPL ─► answer        │
│                                                      │           │
│  ┌───────────────────────────────────────────────────┼───────────┤
│  │                    rlm_kernel                     │           │
│  │                                                  ▼           │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │  │  Vault   │  │  Index   │  │  Search  │  │ KernelBridge │  │
│  │  │ (pages)  │◄─┤ (FTS5)   │◄─┤ (BM25)   │──┤ (REPL proxy) │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
│  │       │                                                │     │
│  │       ▼                                                ▼     │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │  │   Gate   │  │  Memory  │  │Optimize  │  │     CLI      │  │
│  │  │(propose) │  │ (notes)  │  │ (GEPA)   │  │  (rlm-kernel)│  │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

The kernel sits *beside* the harness, not inside it. `rlm_local` continues
to work exactly as before — the kernel is an opt-in layer that enriches it
with evolvability. When a `KernelBridge` is provided at completion time,
the harness gains:

- **A persistent vocabulary.** Helper functions, conventions, and ontology
  live as markdown pages in a git-versioned vault — not hardcoded in
  `repl.py`.
- **Self-description.** The root model can `search("how do I submit an answer")`
  and read its own contract. The system's rules are content the system can
  inspect.
- **A verification gate.** Model-authored content (helpers, notes, definitions)
  passes through quarantine → validation → promotion, preventing unverified
  code from entering the live system.
- **Memory.** Cross-session notes with decay arithmetic, compaction, and a
  pinned core-memory page.
- **Offline optimization.** GEPA-style text evolution that improves the
  harness's own prompts against held-out eval suites — overnight, on local
  hardware.

### 1.2 The Three Primitives

The kernel reduces the entire evolvable system to three primitives:

1. **REPL evaluation** — Extended so its namespace is assembled from helper
   definition pages in the vault rather than hardcoded in the worker script.
   Adding a helper is authoring a page, not editing `repl.py`.

2. **Advanced search** — One hybrid search over the vault's index that doubles
   as the system's introspection mechanism. The model searches for its own
   conventions the same way it searches for user data.

3. **Human-readable indexed wiki** — The persistent "image": git-versioned
   markdown pages with YAML frontmatter. Machine indexes are derived and
   rebuildable from the pages — markdown is always the source of truth.

### 1.3 Design Principles

These eight principles, distilled from the paradigms research report, govern
every design decision in the kernel:

| # | Principle | Implementation |
|---|---|---|
| P1 | One universal medium: text | All state, code, knowledge, and procedures are markdown pages |
| P2 | One eval: the REPL | A single locus of execution; everything else is data |
| P3 | The image is a human-readable, versioned wiki | Git-tracked markdown; indexes derived and rebuildable |
| P4 | Vocabulary grows by definition pages | Extending ontology = authoring a page, not changing code |
| P5 | Every capability is content retrievable by search | Search tool in context, never the library in context |
| P6 | The system describes itself to itself | Contract pages are searchable; the model reads its own rules |
| P7 | Frozen kernel, liquid above | Socket protocol + page schema frozen; prompts/content liquid |
| P8 | All growth passes through an evaluator gate | Quarantine → validation → review → promotion |

### 1.4 Module Map

```
src/rlm_kernel/
├── __init__.py       # Package identity, version
├── schema.py         # Pydantic page/frontmatter models (schema version 1)
├── vault.py          # Page CRUD, atomic writes, git, VaultStore protocol
├── index.py          # meta.sqlite + FTS5; build/rebuild/incremental
├── search.py         # BM25 cards, filters, SearchBackend protocol
├── seed.py           # First-run vault seeding (contracts, templates, helpers)
├── repl_bridge.py    # Helper definitions payload, search/propose proxy
├── gate.py           # Propose/validate/review/promote/demote state machine
├── memory.py         # Notes, decay, compaction, core-memory
├── optimize.py       # GEPA runner, eval suite loading
└── cli.py            # rlm-kernel CLI
```

---

## 2. Quick Start

### 2.1 Installation

The kernel is a sibling package to `rlm_local` in the same project:

```bash
git clone <repo> rlm-local
cd rlm-local
uv sync
```

Dependencies: Python ≥ 3.12, `httpx`, `pydantic`, `pyyaml`, `python-ulid`,
plus `git` on the system PATH.

### 2.2 Initialize a Vault

```bash
# Create and seed a vault at the default location (~/.local/share/rlm-kernel/vault)
uv run python -m rlm_kernel.cli init

# Or specify a custom path
uv run python -m rlm_kernel.cli init --vault /path/to/my-vault
```

This creates the vault directory tree, seeds it with contract pages
(the REPL contract, the "How to Work" addendum), template pages
(nudge messages, warnings, headers), and five builtin helper pages
(`peek`, `grep`, `chunk`, `map_query`, `show_vars`). It also
initializes a git repository for versioning.

### 2.3 Build the Search Index

```bash
uv run python -m rlm_kernel.cli index --rebuild
```

### 2.4 Search the Vault

```bash
uv run python -m rlm_kernel.cli search "regex search" --kind helper
```

### 2.5 Use the Kernel with rlm_local

```python
import rlm_local
from rlm_kernel.repl_bridge import KernelBridge
from rlm_kernel.vault import LocalVault
from pathlib import Path

# Open the vault
vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")
bridge = KernelBridge(vault, vault.root / ".index" / "meta.sqlite")

# Run a completion with kernel awareness
answer = rlm_local.completion(
    "What color is mentioned?",
    "The sky was bright blue with scattered clouds.",
    profile="laptop",
    kernel_bridge=bridge,
)
```

When a `kernel_bridge` is provided, the harness:
- Injects vault-defined helpers into the REPL namespace
- Loads the system prompt from contract pages (vault-first, fallback to hardcoded)
- Includes the core-memory summary in the metadata message
- Makes `search()` and `propose()` available in the REPL

---

## 3. The Page Model

Every piece of knowledge in the kernel — contracts, helpers, definitions,
templates, memory notes — is a **page**: a markdown file with YAML frontmatter.

### 3.1 Page Anatomy

```markdown
---
schema: 1
id: 01KYC5MTDQJ9SRSPDWC79XZNCM
kind: helper
name: grep
title: "grep — regex search over context"
summary: "Search context lines with a regex pattern, returning up to max_hits matches."
tags: [builtin, search]
version: 1
hash: sha256:a1b2c3...
status: active
superseded_by: null
created: 2026-07-25T08:00:00Z
updated: 2026-07-25T08:00:00Z
---
# grep — regex search over context

Search context lines with a regex pattern.

## Signature
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```

## Implementation
```python
def grep(pattern, max_hits=50):
    import re
    regex = re.compile(pattern)
    hits = []
    for line in str(context).splitlines():
        if regex.search(line):
            hits.append(line)
            if len(hits) >= max_hits:
                break
    for h in hits:
        print(h)
    return hits
```

## Usage example
```repl
hits = grep("blue", max_hits=3)
```
```

### 3.2 Frontmatter Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `schema` | `int` | Yes | Schema version. Currently only `1`. |
| `id` | `str` | Auto | 26-character ULID, stable forever. Auto-generated if omitted. |
| `kind` | `str` | Yes | One of: `contract`, `template`, `definition`, `helper`, `fewshot`, `note`, `topic`, `cache`. |
| `name` | `str` | Yes | Machine name, unique within its kind for helpers/templates. Max 128 chars. |
| `title` | `str` | Yes | Human-readable title. Max 256 chars. |
| `summary` | `str` | Yes | One-sentence description. Max 200 chars. Indexed in FTS5; shown in search cards. |
| `tags` | `list[str]` | No | Zero or more tags for filtering and discovery. |
| `version` | `int` | Auto | Monotonic version counter. Starts at 1; bumped on every promotion. |
| `hash` | `str` | No | `sha256:<64-hex>` content hash. Computed from stable identity fields + body; excludes timestamps. |
| `status` | `str` | Auto | Lifecycle state: `active`, `deprecated`, `superseded`, or `pending`. Default: `active`. |
| `superseded_by` | `str` | No | Page path of the replacement (P3 anti-rot). |
| `created` | `datetime` | Auto | ISO 8601 creation timestamp (UTC). |
| `updated` | `datetime` | Auto | ISO 8601 last-modified timestamp (UTC). |

### 3.3 Page Kinds and Their Roles

#### `contract`
The frozen layer *as content*. Contract pages define the system's self-description:
the REPL rules, the "How to Work" orchestrator addendum, and template texts.
These are the pages that `prompts.py` and `templates.py` load from when a vault
is present. Editing contract pages goes through the gate — this is how the
harness's own wording becomes evolvable while maintaining per-version stability.

#### `template`
Individual template strings (nudge messages, warning texts, headers). Each
corresponds to one constant in `rlm_local.templates`. The vault-first loader
maps template names to `contract/templates/<name>.md`.

#### `definition`
Pure ontology: concepts, conventions, entities, "how we do X here." No code.
Authored by human or model. Example: a definition page for "decomposition"
explaining the probe→plan→execute→submit workflow.

#### `helper`
A capability: one Python function plus documentation and examples. The key
invariant (the Forth dictionary property): **user-authored helpers are
structurally indistinguishable from builtins.** Both are pages with a
`## Signature` section, a `## Implementation` section, and a `## Usage example`
section. The REPL bridge extracts the implementation and injects it into the
worker's namespace.

#### `fewshot`
A worked transcript demonstrating the exact message format. Few-shots are
load-bearing for small models — they need the format demonstrated, not
described. K4's GEPA optimizer can evolve few-shots against held-out eval
suites.

#### `note` / `topic` / `cache`
K3 memory pages. `note` = atomic knowledge notes (A-MEM-style), `topic` =
maps of content (MOCs), `cache` = PEEK-style bounded orientation caches
for recurring contexts. The memory manager handles their lifecycle.

### 3.4 The Page Model in Code

```python
from rlm_kernel.schema import (
    Frontmatter, Page, PageKind, PageStatus,
    HelperDef, parse_page,
    extract_helper_signature, extract_helper_code,
)

# Create a page programmatically
fm = Frontmatter(
    schema=1,
    kind="definition",
    name="blue-widget",
    title="Blue Widget",
    summary="A widget that is blue in color.",
    tags=["widget", "color"],
)
page = Page(fm, "# Blue Widget\n\nDocumentation about blue widgets.")

# Parse a page from markdown
raw = """---
schema: 1
kind: note
name: my-note
title: "My Note"
summary: "A note about something."
---
Body text here."""
page = parse_page(raw, "notes/my-note.md")

# Extract helper code
hd = HelperDef.from_page(helper_page)
print(hd.name)  # "grep"
print(hd.code)  # "def grep(pattern, max_hits=50):\n    ..."
```

### 3.5 Content Hash

The `content_hash` property computes a SHA-256 digest of the page's stable
identity: `{kind}:{name}:{title}:{summary}:{sorted_tags}\n{body}`. Timestamps,
version numbers, IDs, and status are excluded — the hash captures *what* the
page is, not *when* it was written. This is used by the index's incremental
update to detect content changes.

---

## 4. The Vault

### 4.1 Vault Layout

```
$VAULT_ROOT/                          # default: ~/.local/share/rlm-kernel/vault
├── .git/                             # Git repository (pages only)
├── .gitignore                        # Excludes .index/, *.tmp
├── contract/
│   ├── repl-contract.md              # REPL rules, answer dict, budgets
│   ├── how-to-work.md                # Orchestrator addendum
│   └── templates/                    # Nudge/warning/header texts
│       ├── metadata.md
│       ├── prologue.md
│       ├── turn-header.md
│       └── ...
├── definitions/                      # Ontology: concept pages
│   └── <slug>.md
├── helpers/                          # Capabilities: executable Python
│   ├── peek.md
│   ├── grep.md
│   ├── chunk.md
│   ├── map_query.md
│   └── show_vars.md
├── fewshots/                         # Worked transcripts
│   └── <slug>.md
├── memory/
│   ├── notes/                        # Atomic knowledge notes
│   │   └── core-memory.md            # Pinned identity page
│   ├── topics/                       # Maps of content
│   └── caches/                       # Orientation caches
├── quarantine/                       # The gate's waiting room
│   └── <ulid>.md                     # Pending proposals
└── .index/                           # DERIVED, rebuildable
    └── meta.sqlite                   # pages + FTS5 index
```

### 4.2 VaultStore Protocol

The vault is accessed through a protocol, leaving a seam for a remote HTTP
backend in a future phase (K5):

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class VaultStore(Protocol):
    def get(self, path: str) -> Page | None: ...
    def put(self, page: Page, path: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]: ...
    def resolve_wikilink(self, name: str) -> Page | None: ...
```

### 4.3 LocalVault

The `LocalVault` class implements `VaultStore` on a local filesystem:

```python
from rlm_kernel.vault import LocalVault

vault = LocalVault(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")

# CRUD
page = vault.get("helpers/grep.md")
vault.put(page, "helpers/grep.md")
vault.delete("definitions/obsolete.md")
exists = vault.exists("contract/repl-contract.md")

# List with filtering
all_helpers = vault.list(kind="helper")
definitions = vault.list(prefix="definitions/")

# Wikilinks (case-insensitive)
target = vault.resolve_wikilink("blue-widget")
```

### 4.4 Atomic Writes

Every `put()` operation uses the write-to-temp-then-rename pattern:

1. Content is serialized to markdown (YAML frontmatter + body).
2. A temporary file is created in the same directory as the target
   (ensuring atomic rename within the same filesystem).
3. The content is written and `fsync`'d to disk.
4. The temp file is atomically renamed over the target via `os.replace()`.

This guarantees that readers never see a partially-written file.
On error, the temp file is cleaned up — no stale artifacts remain.

### 4.5 Git Versioning

When `init_git=True` (the default), the vault initializes a git repository
on first access. A `.gitignore` excludes the `.index/` directory and `*.tmp`
files. Each `put()` stages the file with `git add`; each `delete()` stages
removal with `git rm`. Commits are triggered explicitly via `vault.git_commit()`
or by the CLI.

```python
vault = LocalVault(root, init_git=True)
vault.put(page, "definitions/new.md")
vault.git_commit("kernel: add new definition page (v1)")
```

If git is not available on the system PATH, the vault degrades gracefully
— all CRUD operations still work, only versioning is skipped.

### 4.6 Wikilink Resolution

Pages can reference each other using `[[wikilink]]` syntax. The
`resolve_wikilink(name)` method performs a case-insensitive search
across all `.md` files in the vault (excluding `.index/` and `quarantine/`),
matching by filename stem:

```python
# Resolves to the page whose filename stem matches "target-page"
# (case-insensitive)
page = vault.resolve_wikilink("target-page")
```

---

## 5. The Index and Search

### 5.1 Index Architecture

The index is a single SQLite database (`meta.sqlite`) stored in the vault's
`.index/` directory. It is **always rebuildable** from the vault's markdown
pages — the pages are the source of truth; the index is a derived artifact.

The database uses WAL journal mode for concurrent read access and contains
four tables:

**`pages`** — Metadata for every page in the vault:

| Column | Type | Description |
|---|---|---|
| `id` | `TEXT PK` | 26-char ULID |
| `path` | `TEXT UNIQUE` | Relative path in vault |
| `kind` | `TEXT` | Page kind |
| `name` | `TEXT` | Machine name |
| `title` | `TEXT` | Human-readable title |
| `summary` | `TEXT` | One-sentence description |
| `hash` | `TEXT` | Content hash for change detection |
| `version` | `INTEGER` | Monotonic version |
| `status` | `TEXT` | Lifecycle state |
| `updated` | `TEXT` | ISO 8601 timestamp |

**`tags`** — Many-to-many tag assignments:

| Column | Type |
|---|---|
| `page_id` | `TEXT` |
| `tag` | `TEXT` |

**`links`** — Wikilink edges:

| Column | Type |
|---|---|
| `src` | `TEXT` |
| `dst` | `TEXT` |

**`fts_pages`** — FTS5 full-text index over `(path, kind, name, title, summary, body)`.
Uses BM25 ranking (the default for FTS5).

### 5.2 Building and Rebuilding

```python
from rlm_kernel.index import Index, rebuild_index

# Full build from vault
idx = rebuild_index(vault, Path(".index/meta.sqlite"))
print(idx.page_count())  # number of indexed pages
idx.close()

# Or incrementally
idx = Index(index_path)
idx.build(vault)

# Incremental update — hash-compares, adds new, updates changed, removes deleted
idx.reindex_delta(vault)
```

**Rebuild** clears all tables and re-indexes every page from the vault.
**Incremental update** (`reindex_delta`) walks the vault, compares content
hashes against indexed entries, and only modifies pages that have been
added, changed, or deleted.

### 5.3 Full-Text Search

```python
from rlm_kernel.index import Index

idx = Index(index_path)
results = idx.fts_search("regex search", limit=40, kinds=["helper", "definition"])
for r in results:
    print(f"{r['kind']}/{r['name']}: {r['title']} (score={r['score']})")
idx.close()
```

The FTS5 query uses BM25 ranking. Results are ordered by relevance score.
The optional `kinds` parameter filters to specific page kinds.

### 5.4 The Search API

The high-level search function adds card formatting and kind filtering:

```python
from rlm_kernel.search import search_vault

# Compact cards (default, ≤400 chars per result)
cards = search_vault(vault, index_path, "blue widget", k=5, kinds=["definition"])
for card in cards:
    print(f"[{card['kind']}] {card['name']}: {card['title']} — {card['summary']}")

# Full detail (returns body + frontmatter)
full = search_vault(vault, index_path, "grep", detail="full")

# Include quarantined pages (K2 gate use case)
all_results = search_vault(vault, index_path, "test", include_quarantine=True)
```

**Card format** (the LID invariant): each search result is at most 400 characters
when serialized. This ensures the root model's context stays bounded even when
searching large vaults. Fields: `path`, `kind`, `name`, `title`, `summary`, `score`.

**Full format** adds `body` and `frontmatter` (the complete parsed page).

### 5.5 The SearchBackend Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class SearchBackend(Protocol):
    def search(self, query: str, k: int = 5, kinds: list[str] | None = None,
               detail: str = "card") -> list[dict[str, Any]]: ...
```

This protocol is the seam for a future vector search tier (K3+). The current
implementation uses lexical BM25 only. A vector backend would implement the
same interface and be swappable via configuration.

---

## 6. The REPL Bridge

The `KernelBridge` is the integration point between the kernel's vault and
`rlm_local`'s REPL sandbox. It serves three roles:

### 6.1 Helper Definitions Assembly

When the REPL starts, the bridge collects all active helper pages from the
vault, extracts their implementations, and packages them for injection into
the worker's namespace:

```python
from rlm_kernel.repl_bridge import KernelBridge

bridge = KernelBridge(vault, index_path)
defs = bridge.get_helper_definitions()
# Returns: [{"name": "grep", "code": "def grep(...): ..."}, ...]

# These are passed to REPLSandbox.start(definitions=defs)
# The worker exec()s each helper into its globals()
```

Only helpers with `status: active` are included. Deprecated, superseded,
and quarantined helpers are excluded — the model cannot discover or call
them through normal operation.

### 6.2 Search Proxy

When the REPL worker calls `search(query, k=5)`, the bridge proxies this
to the vault's search index and returns formatted results as REPL stdout:

```python
# Inside a repl block:
hits = search("regex", k=3)
# Output:
# [1] helper/grep: grep — regex search over context — Search context lines...
# [2] contract/how-to-work: How to Work (Orchestrator Addendum) — ...
# [3] contract/repl-contract: REPL Contract — ...
```

The `handle_search` method on `KernelBridge` formats results as a
human-readable numbered list. The `detail` parameter controls verbosity.

### 6.3 Propose Proxy

When the REPL worker calls `propose(kind, name, body, rationale)`, the bridge
creates a quarantined page:

```python
# Inside a repl block:
path = propose("helper", "extract-dates",
               "## Implementation\n```python\ndef extract_dates(text):\n    ...\n```\n",
               "Extracts ISO dates from text using regex")
# Output:
# Proposed: quarantine/01KYC6B498FYY1R7DKCDB7Z3VQ.md
```

The proposed page is written to `quarantine/<ulid>.md` with `status: pending`.
It is **not indexed** into search and **not callable** through normal helper
resolution. It exists only for validation and promotion through the gate.

### 6.4 Core Memory

The bridge reads `memory/notes/core-memory.md` and returns its summary for
injection into the root model's metadata message:

```python
summary = bridge.get_core_memory_summary()
# Returns the summary field from core-memory.md, or None if absent
```

This implements the MemGPT core block pattern: a durable, pinned identity
page whose summary is always visible to the root model at the start of
every completion.

---

## 7. The Gate

The gate is the kernel's immune system. Every piece of model-authored
content passes through it before entering the live system.

### 7.1 State Machine

```
                  ┌──────────┐
                  │  propose │  Model writes to quarantine/
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │ pending  │  In quarantine/, not indexed
                  └────┬─────┘
                       │
                  ┌────▼─────┐
                  │ validate │  Schema + kind-specific checks
                  └────┬─────┘
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
         ┌────────┐ ┌──────┐ ┌──────────┐
         │ reject │ │promote│ │  pending │  (fix + re-propose)
         │(delete)│ │(live) │ │(stay)    │
         └────────┘ └──┬───┘ └──────────┘
                       │
                  ┌────▼─────┐
                  │  active  │  Live in vault, indexed, callable
                  └────┬─────┘
                       │
                  ┌────▼──────┐
                  │  demote   │
                  └────┬──────┘
                       │
              ┌────────┼────────┐
              ▼                 ▼
         ┌──────────┐    ┌────────────┐
         │deprecated│    │ superseded  │
         │(still     │    │(linked to   │
         │ callable) │    │ replacement)│
         └──────────┘    └────────────┘
```

### 7.2 Propose

```python
from rlm_kernel.gate import propose

path = propose(vault, kind="helper", name="extract-dates",
               body="## Implementation\n```python\ndef extract_dates(text):\n    ...\n```\n",
               rationale="Extracts ISO dates from text")
# Returns: "quarantine/01KYC6B498FYY1R7DKCDB7Z3VQ.md"
```

The `propose` function:
1. Generates a ULID for the page.
2. Prepends the rationale as an HTML comment (`<!-- rationale: ... -->`).
3. Creates a `Frontmatter` with `status: pending`, `version: 0`.
4. Writes the page to `quarantine/<ulid>.md`.
5. Returns the relative path.

### 7.3 Validate

```python
from rlm_kernel.gate import validate, ValidationReport

page = vault.get("quarantine/01KYC....md")
report = validate(page, vault=vault)

print(report.passed)   # True if no errors
print(report.errors)   # Fatal issues blocking promotion
print(report.warnings) # Advisory issues
```

Validation is **deterministic and model-free** — no LLM involved.
Kind-specific checks:

| Kind | Validation Rules |
|---|---|
| **helper** | Import allowlist (stdlib only; block `os.system`, `subprocess`, `socket`, `ctypes`, `importlib`). Sandbox execution: run in restricted namespace, verify no exceptions, no network access. Signature match: declared signature must correspond to actual callable. |
| **contract / template** | Slot variables present (`{repl_cap}` etc.). Body length within 8192-byte cap. |
| **definition / note** | Wikilinks resolve to existing pages (when vault provided). |
| **fewshot** | Must contain example sections (`## Probe`, `## Plan`, etc.). |
| **all** | Status must be `pending`. Must be in `quarantine/`. |

### 7.4 Promote

```python
from rlm_kernel.gate import promote

new_path = promote(vault, page)
# Moves from quarantine/01KYC....md to helpers/extract-dates.md
# Bumps version, updates hash, sets status: active, git commit
```

Promotion performs:
1. Computes the target path based on kind and name (`helpers/extract-dates.md`).
2. Conflict handling: if a page already exists at the target, increments version.
3. Updates frontmatter: `status: active`, `version += 1`, `hash = content_hash`, timestamps refreshed.
4. Removes the quarantined original.
5. Writes the promoted page to its target namespace.
6. Returns the new path.

### 7.5 Reject

```python
from rlm_kernel.gate import reject

reject(vault, page)
# Permanently deletes from quarantine/
```

### 7.6 Demote

```python
from rlm_kernel.gate import demote

# Demote to deprecated
demote(vault, page)

# Demote to superseded, linking to replacement
demote(vault, page, superseded_by="helpers/extract-dates-v2.md")
```

Demotion transitions `active → deprecated` or `active → superseded`. A
superseded page carries a `superseded_by` field pointing to the replacement
path. Deprecated/superseded pages remain in the vault and index but are
excluded from normal search results (status filter).

### 7.7 Quarantine Isolation

The gate enforces separation between quarantined and live pages:

```python
from rlm_kernel.gate import verify_quarantine_isolation

# Verify no quarantine pages leak into normal search
assert verify_quarantine_isolation(vault, index_path)
```

- **Normal search** (`search_vault` with `include_quarantine=False`): excludes `quarantine/`.
- **Quarantine-aware search** (`include_quarantine=True`): merges quarantined results at the end.
- **Direct access** (`search_quarantine`): searches only quarantine pages by text match.

---

## 8. Memory

The memory layer (K3) provides cross-session, durable knowledge management
inspired by A-MEM, Mem0, and MemoryBank.

### 8.1 MemoryManager

```python
from rlm_kernel.memory import MemoryManager

mgr = MemoryManager()
```

### 8.2 Adding Notes

```python
# Auto-extract: creates a note page with extracted title, summary, and keywords
path = mgr.add(vault, "The blue widget uses a 3-phase initialization protocol.")

# Explicit gisting: store a one-shot gist
path = mgr.note(vault, "Blue widget init: phase 1 binds socket, phase 2 loads config, phase 3 starts loop.")
```

`add()` performs lightweight extraction: the first markdown heading or first
line becomes the title, the first sentence becomes the summary, and capitalized
words become keyword tags. No LLM is needed — the extraction is purely
rule-based, keeping memory operations fast and deterministic.

### 8.3 Searching Memory

```python
# Hybrid search over notes/topics/caches
results = mgr.search(vault, index_path, "blue widget", k=5)
for r in results:
    print(f"{r['name']}: {r['summary']}")
```

Memory search filters to `kind: note|topic|cache` and applies decay-weighted
scoring (see §8.5).

### 8.4 Core Memory

```python
# Write the instance's durable identity
mgr.write_core(vault, "This instance specializes in Python code analysis. "
                      "It prefers aggressive decomposition with batched sub-calls.")

# Read back
page = vault.get("memory/notes/core-memory.md")
print(page.frontmatter.summary)
```

Core memory is a pinned page at `memory/notes/core-memory.md`. Its summary is
always injected into the root model's metadata message (via `KernelBridge`).
This gives the model persistent awareness of its own identity and preferences
across sessions — the MemGPT core block, minus the tool-calling loop.

Successive calls to `write_core` update the same page (version increments).
The page is protected from `forget()` — it cannot be accidentally deleted.

### 8.5 Forgetting

```python
# Forget by query match
count = mgr.forget(vault, query="obsolete widget")

# Forget by age
count = mgr.forget(vault, older_than=30)  # days

# Forget all non-core notes
count = mgr.forget(vault)
```

The `forget` method removes or deprecates notes matching the criteria.
Core memory is never targeted.

### 8.6 Decay Arithmetic

```python
from rlm_kernel.memory import decay_score

# Ebbinghaus-style forgetting curve
score = decay_score(access_count=5, last_access=some_datetime,
                    created=other_datetime)
```

The decay formula implements an exponential forgetting curve:

$$\text{score} = e^{-t / \text{strength}}$$

where $t$ is the time since last access (in seconds) and strength is
computed from access count:

$$\text{strength} = 86400 \times (1 + \log(1 + \text{access\\_count}))$$

This means frequently-accessed notes decay slowly; rarely-accessed notes
decay quickly. The decay is applied as a multiplicative factor during
memory search ranking.

### 8.7 Compaction

```python
# Merge near-duplicate notes by title similarity
merge_count = mgr.compact(vault, index_path, similarity_threshold=0.8)
```

Compaction finds pairs of notes with similar titles (Jaccard similarity
on word sets) and merges them: the newer note absorbs the older one's
body content, and the older one is deleted. This prevents unbounded
note accumulation ("image rot") without requiring LLM judgment.

---

## 9. Offline Optimization

The optimizer (K4) applies GEPA-style text evolution to the harness's
textual surface — prompts, templates, few-shots — using `rlm_local`
itself as the evaluator.

### 9.1 Eval Suites

```python
from rlm_kernel.optimize import EvalTask, EvalSuite, BUILTIN_SUITES

suite = BUILTIN_SUITES["needle_search"]
for task in suite.tasks:
    print(f"{task.name}: find '{task.expected_pattern}' in context")
```

An `EvalTask` defines a verifiable micro-benchmark:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Task identifier |
| `query` | `str` | The question to ask |
| `context` | `str` | The data to answer from |
| `expected_pattern` | `str` | Regex that must match the answer |
| `tolerance` | `float` | Numeric tolerance (for aggregation tasks) |

An `EvalSuite` groups tasks with an optional held-out split for
promotion gating.

### 9.2 Built-in Suites

| Suite | Tasks | Held-Out | Description |
|---|---|---|---|
| `needle_search` | 2 | 0 | Color and year needle-in-haystack tasks |
| `aggregation` | 1 | 1 | Counting items; held-out on different content |

### 9.3 Running Optimization

```python
from rlm_kernel.optimize import run_optimization

result = run_optimization(
    vault,
    target="how-to-work",
    max_iterations=20,
    profile="laptop",
)
print(f"Baseline: {result['baseline_score']:.1%}")
print(f"Best:     {result['best_score']:.1%}")
print(f"Status:   {result['status']}")
```

The optimizer:
1. Loads the target text from the vault (e.g., `contract/how-to-work.md`).
2. Evaluates baseline score against the eval suite.
3. Generates mutations (emphasis markers, added warnings, simplifications, expansions).
4. Evaluates each mutant; keeps the best.
5. If improvement on the held-out split, promotes the winner.
6. Otherwise, restores the original.

**Target options**: `prologue`, `how-to-work`, `nudges`, `fewshots`, `helper-docs`.

### 9.4 Local Feasibility

The optimizer is designed for overnight runs on CPU-only hardware:
- Each metric call ≈ 4–8 minutes (a full `rlm_local.completion()`)
- 20 iterations ≈ 1.5–3 hours
- All mutations are text — runs checkpoint naturally
- Keep eval tasks in the 30–70% success band for rich signal

---

## 10. Prompt and Template Loading

When a vault is present, the harness's textual surface becomes content.
The original hardcoded strings remain as package-bundled fallbacks.

### 10.1 System Prompt Assembly

```python
from rlm_local.prompts import load_system_prompt_from_vault

system_prompt = load_system_prompt_from_vault(prompt_vars, vault=vault)
```

The vault-first assembly order:
1. `contract/repl-contract.md` body, rendered with `{repl_cap}`-style slots.
2. One-line summaries of active helper pages (≤30 lines — progressive disclosure cap).
3. `contract/how-to-work.md` body.
4. Fallback: if any vault page is missing, the hardcoded `SYSTEM_PROMPT` is used.

### 10.2 Template Loading

```python
from rlm_local.templates import load_template

nudge = load_template("NUDGE_NO_BLOCK", vault=vault)
# Returns the body of contract/templates/nudge-no-block.md,
# or the hardcoded NUDGE_NO_BLOCK constant if vault is absent.
```

The template page map covers all 16 template constants. Each maps to a page
under `contract/templates/`. This is how the harness's own wording becomes
evolvable — edit the template page, re-seed, and the next completion uses
the new text. The GEPA optimizer can target these pages directly.

### 10.3 Byte-Stable Prefix Discipline

Even with vault-first loading, the byte-stable prefix invariant is maintained:
once loaded for a session, the system prompt and templates do not change
mid-completion. The vault is consulted once at the start of `RootLoop.run()`.
lcache hits remain consistent across turns.

---

## 11. CLI Reference

```
rlm-kernel — Evolvable RLM kernel CLI

Commands:
  init                  Seed a new vault with contract, template, and helper pages
  index --rebuild        Rebuild the full-text search index from vault pages
  review                 List all quarantined pages pending review
  promote PATH           Promote a quarantined page into the live namespace
  demote PATH [--by P]   Demote an active page (optionally superseded by another)
  search QUERY [--kind]  Search the vault by keyword
  optimize --target T    Run GEPA optimization on a text artifact
```

### 11.1 init

```bash
rlm-kernel init [--vault PATH]
```

Creates the vault directory tree, seeds 15 canonical pages (2 contracts,
8 templates, 5 builtin helpers), and initializes a git repository.
Idempotent — existing pages are never overwritten.

### 11.2 index

```bash
rlm-kernel index --rebuild [--vault PATH]
```

Walks the vault, clears the index, and re-indexes every page. Run after
manual edits or after `git pull` to reconcile.

### 11.3 review

```bash
rlm-kernel review [--vault PATH]
```

Lists all pages in `quarantine/` with their kind, name, and summary.
Human reviews these before running `promote`.

### 11.4 promote

```bash
rlm-kernel promote quarantine/01KYC....md [--vault PATH]
```

Promotes a quarantined page to its target namespace. The page must have
passed validation (promotion does not re-validate — run validation first).

### 11.5 demote

```bash
rlm-kernel demote helpers/old-helper.md [--by helpers/new-helper.md] [--vault PATH]
```

Marks a page as deprecated or superseded.

### 11.6 search

```bash
rlm-kernel search "regex search" --kind helper definition
```

Searches the vault with BM25 ranking. The `--kind` flag filters by page
kind (repeatable).

### 11.7 optimize

```bash
rlm-kernel optimize --target how-to-work [--vault PATH]
```

Runs GEPA optimization on the specified text artifact. Options:
`prologue`, `how-to-work`, `nudges`, `fewshots`, `helper-docs`.

---

## 12. Integration with rlm_local

### 12.1 Changes to rlm_local

The kernel extends `rlm_local` with minimal, backward-compatible changes:

#### `repl.py`

- `REPLSandbox.start()` accepts an optional `definitions` parameter:
  `list[dict[str, str]]` where each dict has `name` and `code` keys.
- The `init` socket command sends `helpers` alongside `context`.
- The worker script (`_WORKER_SCRIPT`) `exec()`s each helper into its
  globals on init. If no helpers are provided, behavior is identical to
  pre-kernel `rlm_local`.
- New socket verbs `search` and `propose` are handled in the worker's
  main loop and proxied back to the harness via `KernelBridge`.

#### `root_loop.py`

- `RootLoop.__init__()` accepts an optional `kernel_bridge` parameter.
- `run()` queries the bridge for helper definitions and core-memory summary.
- Core memory is appended to the metadata context type string.
- When a bridge is available, the system prompt is loaded vault-first.
- `REPLSandbox._kernel_bridge` is set before `start()` so `execute()` can
  proxy `search`/`propose` commands.

#### `prompts.py`

- `load_system_prompt_from_vault(prompt_vars, vault)` — vault-first assembly.
- `load_fewshots_from_vault(vault)` — vault-first few-shot loading.
- Both fall back to the hardcoded `SYSTEM_PROMPT` and `FEWSHOT_EXAMPLE` when
  no vault is available.

#### `templates.py`

- `load_template(name, vault)` — maps template constant names to
  `contract/templates/` pages. Falls back to the hardcoded constant.

#### `__init__.py`

- `completion()` accepts an optional `kernel_bridge` parameter, passed
  through to `RootLoop`.

### 12.2 The `completion()` API (Updated)

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
    kernel_bridge: Any = None,   # NEW
    **overrides: Any,
) -> str:
```

### 12.3 Backward Compatibility

All kernel features are opt-in. Without a `kernel_bridge`:
- `REPLSandbox.start()` is called without `definitions` — worker uses hardcoded helpers.
- System prompt is built from hardcoded strings — identical to pre-kernel behavior.
- No `search()` or `propose()` in the REPL.
- All 72 existing `rlm_local` tests pass unchanged.

---

## 13. Testing Strategy

### 13.1 Test Structure

```
tests/
├── rlm_kernel/
│   ├── conftest.py            # temp_vault fixture
│   ├── test_schema.py         # 26 tests: frontmatter validation, parsing, helper extraction
│   ├── test_vault.py          # 16 tests: CRUD, atomic writes, wikilinks, round-trip
│   ├── test_index.py          # 10 tests: build, FTS search, incremental update, rebuild
│   └── test_search.py         # 8 tests: keyword search, kind filter, card budget, edge cases
└── (rlm_local tests)          # 72 tests: unchanged, verifying fallback parity
```

### 13.2 Test Conventions

- **Unit tests** use temporary vaults (`temp_vault` fixture) with `init_git=False`
  to avoid git dependency. All are deterministic — no real LLM, network, or clock.
- **Property tests** (future): job state machine, dedup, frontmatter round-trip
  via hypothesis.
- **Load tests** (future): 100K synthetic pages → index rebuild < 2 h, search
  p95 < 300 ms on target hardware.

### 13.3 Running Tests

```bash
# All tests
uv run pytest tests/ -k "not slow"

# Kernel tests only
uv run pytest tests/rlm_kernel/

# rlm_local tests only (fallback parity check)
uv run pytest tests/ -k "not slow and not rlm_kernel"

# Specific module
uv run pytest tests/rlm_kernel/test_schema.py -v
```

---

## 14. Failure Modes and Mitigations

### 14.1 Vault Corruption

**Symptom:** A page file is truncated or unparseable due to disk error.

**Mitigation:** Atomic writes guarantee readers never see partial files.
Git versioning provides rollback. The `parse_page` function raises clear
`ValueError` on malformed content. `vault.list()` skips unparseable pages.

### 14.2 Index Drift

**Symptom:** Search returns stale results after manual page edits.

**Mitigation:** `rlm-kernel index --rebuild` reconstructs the index from
markdown. `reindex_delta()` uses content hash comparison for incremental
repair. The index is always rebuildable — markdown is truth.

### 14.3 Gate Bypass

**Symptom:** Model-authored code reaches the live vault without validation.

**Mitigation:** `propose()` always writes to `quarantine/` with
`status: pending`. The vault's `list(kind="helper")` filters to
`status: active`. `search_vault()` excludes `quarantine/` by default.
`verify_quarantine_isolation()` provides an assertion check.

### 14.4 Root-Model Discipline

**Symptom:** A 4B model may underuse `search()`/`propose()`.

**Mitigation:** The system prompt prologue mentions these capabilities.
The few-shot examples demonstrate `search()`. K4's GEPA optimizer can
evolve the prologue to improve utilization.

### 14.5 Git Unavailability

**Symptom:** `git` not found on PATH.

**Mitigation:** `LocalVault` degrades gracefully — `_init_git` catches
`FileNotFoundError` and sets `_git_available = False`. All CRUD
operations continue; only versioning is skipped.

### 14.6 Image Rot (Note Accumulation)

**Symptom:** Memory notes grow unbounded, degrading search quality.

**Mitigation:** Decay arithmetic deprioritizes stale notes. Compaction
merges near-duplicates. `forget()` provides explicit cleanup. The
human-readable vault makes manual cleanup cheap.

---

## 15. API Reference

### 15.1 `rlm_kernel.schema`

```python
class PageKind(str, Enum):
    CONTRACT = "contract"
    TEMPLATE = "template"
    DEFINITION = "definition"
    HELPER = "helper"
    FEWSHOT = "fewshot"
    NOTE = "note"
    TOPIC = "topic"
    CACHE = "cache"

class PageStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    PENDING = "pending"

class Frontmatter(BaseModel):
    schema: int = 1
    id: str                         # ULID, auto-generated
    kind: PageKind
    name: str                       # 1-128 chars
    title: str                      # 1-256 chars
    summary: str                    # 1-200 chars
    tags: list[str]
    version: int = 1
    hash: str | None                # sha256:<64-hex>
    status: PageStatus = ACTIVE
    superseded_by: str | None
    created: datetime               # UTC
    updated: datetime               # UTC

    def to_yaml(self) -> str: ...

class Page:
    frontmatter: Frontmatter
    body: str
    path: str

    @property
    def content_hash(self) -> str: ...
    def to_markdown(self) -> str: ...

def parse_page(content: str, path: str = "") -> Page: ...

class HelperDef:
    name: str
    signature: str | None
    code: str
    summary: str

    @classmethod
    def from_page(cls, page: Page) -> HelperDef: ...
    def to_dict(self) -> dict[str, str]: ...

def extract_helper_signature(body: str) -> str | None: ...
def extract_helper_code(body: str) -> str | None: ...
```

### 15.2 `rlm_kernel.vault`

```python
@runtime_checkable
class VaultStore(Protocol):
    def get(self, path: str) -> Page | None: ...
    def put(self, page: Page, path: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]: ...
    def resolve_wikilink(self, name: str) -> Page | None: ...

class LocalVault:
    def __init__(self, root: Path, init_git: bool = True) -> None: ...
    def get(self, path: str) -> Page | None: ...
    def put(self, page: Page, path: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]: ...
    def resolve_wikilink(self, name: str) -> Page | None: ...
    def git_commit(self, message: str = "vault: update") -> bool: ...
```

### 15.3 `rlm_kernel.index`

```python
class Index:
    def __init__(self, db_path: Path) -> None: ...
    def build(self, vault: VaultStore) -> None: ...
    def reindex_delta(self, vault: VaultStore) -> None: ...
    def fts_search(self, query: str, limit: int = 40,
                   kinds: list[str] | None = None) -> list[dict[str, Any]]: ...
    def page_count(self) -> int: ...
    def get_page(self, path: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...
    def __enter__(self) -> Index: ...
    def __exit__(self, *args) -> None: ...

def rebuild_index(vault: VaultStore, db_path: Path) -> Index: ...
```

### 15.4 `rlm_kernel.search`

```python
@runtime_checkable
class SearchBackend(Protocol):
    def search(self, query: str, k: int = 5, kinds: list[str] | None = None,
               detail: str = "card") -> list[dict[str, Any]]: ...

def search_vault(
    vault: VaultStore,
    index_path: Path,
    query: str,
    k: int = 5,
    kinds: list[str] | None = None,
    detail: str = "card",
    include_quarantine: bool = False,
) -> list[dict[str, Any]]: ...
```

### 15.5 `rlm_kernel.seed`

```python
def seed_vault(vault: VaultStore) -> None: ...
```

### 15.6 `rlm_kernel.repl_bridge`

```python
@dataclass
class KernelBridge:
    vault: Any          # VaultStore
    index_path: Path

    def get_helper_definitions(self) -> list[dict[str, str]]: ...
    def get_helper_summaries(self) -> list[str]: ...
    def handle_search(self, query: str, k: int = 5,
                      kinds: list[str] | None = None,
                      detail: str = "card") -> str: ...
    def handle_propose(self, kind: str, name: str, body: str,
                       rationale: str = "") -> str: ...
    def get_core_memory_summary(self) -> str | None: ...
```

### 15.7 `rlm_kernel.gate`

```python
@dataclass
class ValidationReport:
    passed: bool
    errors: list[str]
    warnings: list[str]

def propose(vault: VaultStore, kind: PageKind, name: str,
            body: str, rationale: str = "") -> str: ...
def validate(page: Page, vault: VaultStore | None = None) -> ValidationReport: ...
def promote(vault: VaultStore, page: Page) -> str: ...
def reject(vault: VaultStore, page: Page) -> None: ...
def demote(vault: VaultStore, page: Page,
           superseded_by: str | None = None) -> None: ...
def search_quarantine(vault: VaultStore, query: str,
                      kinds: list[PageKind] | None = None) -> list[Page]: ...
def verify_quarantine_isolation(vault: VaultStore, index_path: Path) -> bool: ...
```

### 15.8 `rlm_kernel.memory`

```python
class MemoryManager:
    def add(self, vault: VaultStore, text: str, tags: list[str] | None = None) -> str: ...
    def search(self, vault: VaultStore, index_path: Path,
               query: str, k: int = 5) -> list[dict[str, Any]]: ...
    def note(self, vault: VaultStore, chunk: str) -> str: ...
    def forget(self, vault: VaultStore, query: str | None = None,
               older_than: int | None = None) -> int: ...
    def write_core(self, vault: VaultStore, text: str) -> None: ...
    def compact(self, vault: VaultStore, index_path: Path,
                similarity_threshold: float = 0.8) -> int: ...

def decay_score(access_count: int = 0,
                last_access: datetime | None = None,
                created: datetime | None = None) -> float: ...
```

### 15.9 `rlm_kernel.optimize`

```python
@dataclass
class EvalTask:
    name: str
    query: str
    context: str
    expected_pattern: str
    tolerance: float = 0.0

@dataclass
class EvalSuite:
    name: str
    tasks: list[EvalTask]
    held_out: list[EvalTask]

def evaluate_task(task: EvalTask, completer: Callable) -> tuple[bool, str]: ...
def evaluate_suite(suite: EvalSuite, completer: Callable) -> dict[str, Any]: ...
def run_optimization(vault: Any, target: str = "how-to-work",
                     max_iterations: int = 20,
                     profile: str = "laptop") -> dict[str, Any]: ...

BUILTIN_SUITES: dict[str, EvalSuite]
```

### 15.10 `rlm_kernel.cli`

```python
def main(argv: list[str] | None = None) -> int: ...
```

---

*RLM Kernel is built on the principles articulated in the paradigms
research report — the convergence of LISP's metacircular eval, Smalltalk's
image, Forth's dictionary, and Nock's frozen core — adapted to the
constraints of small local models and consumer hardware.*
