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
schema_version: 1
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
| `schema_version` | `int` | Auto | Schema version. Currently only `1`. Renamed from `schema` (R23, 2026-09-12) because the old name shadowed pydantic's deprecated `BaseModel.schema`. **Pages carrying the old key still load** — both are accepted on read — and `rlm-kernel migrate-schema` rewrites them (dry-run by default). |
| `id` | `str` | Auto | 26-character ULID, stable forever. Auto-generated if omitted. |
| `kind` | `str` | Yes | One of: `contract`, `template`, `definition`, `helper`, `fewshot`, `note`, `topic`, `cache`. |
| `name` | `str` | Yes | Machine name, unique within its kind for helper and template pages. Must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` — no path separators, no leading dot or whitespace (S4/R20), because it becomes the page path on promotion. |
| `title` | `str` | Yes | Human-readable title. Max 256 chars. |
| `summary` | `str` | Yes | One-sentence description. Max 200 chars. Indexed in FTS5; shown in search cards. |
| `tags` | `list[str]` | No | Zero or more tags for filtering and discovery. |
| `version` | `int` | Auto | Monotonic version counter. Starts at 1; bumped on every promotion. |
| `hash` | `str` | No | `sha256:<64-hex>` content hash. Computed from stable identity fields + body; excludes timestamps. |
| `status` | `str` | Auto | Lifecycle state: `active`, `deprecated`, `superseded`, or `pending`. Default: `active`. |
| `superseded_by` | `str` | No | Page path of the replacement (P3 anti-rot). |
| `created` | `datetime` | Auto | ISO 8601 creation timestamp (UTC). |
| `updated` | `datetime` | Auto | ISO 8601 last-modified timestamp (UTC). |
| `access_count` | `int` | No | Memory-decay bookkeeping: number of search hits. Default `0`; absent on pages written before schema 1 gained the field (R12). |
| `last_access` | `datetime` | No | Memory-decay bookkeeping: timestamp of the last search hit. Default `null`; when null, decay falls back to `created`. Both access fields are excluded from `content_hash`, so a search hit never looks like a content change to the index. |

### 3.3 Page Kinds and Their Roles

#### `contract`
The frozen layer *as content*. Contract pages define the system's self-description:
the REPL rules, the "How to Work" orchestrator addendum, and template texts.
These are the pages that `prompts.py` and `templates.py` load from when a vault
is present. Editing contract pages goes through the gate — this is how the
harness's own wording becomes evolvable while maintaining per-version stability.

#### `template`
Individual template strings (nudge messages, warning texts, headers). Each
corresponds to one constant in `rlm_local.templates`. Template pages are
seeded into the vault under `contract/templates/` as **introspection-only
content** (P6: the model can read its own conventions via `search()`).
Behavior wiring — loading template text from vault pages at runtime — is
deferred to a later phase; currently, `rlm_local` uses its hardcoded
package-bundled template constants.
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

# Parse a page from markdown (the pre-R23 `schema:` key is still accepted)
raw = """---
schema_version: 1
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
├── helper/                           # Capabilities: executable Python
│   ├── peek.md
│   ├── grep.md
│   ├── chunk.md
│   ├── map_query.md
│   └── show_vars.md
├── fewshot/                          # Worked transcripts
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
page = vault.get("helper/grep.md")
vault.put(page, "helper/grep.md")
vault.delete("definitions/obsolete.md")
exists = vault.exists("contract/repl-contract.md")

# List with filtering
all_helpers = vault.list(kind="helper")
definitions = vault.list(prefix="definitions/")

# Wikilinks (case-insensitive)
target = vault.resolve_wikilink("blue-widget")
```

#### 4.3.1 Path containment (S4/R20)

Every path handed to `get`, `put`, `delete` and `exists` goes through
`LocalVault._resolve()`, which:

1. rejects an empty or non-string path;
2. rejects **absolute** paths (POSIX `/…`, Windows `C:\…` / `C:/…`, UNC);
3. rejects any `..` segment after normalization;
4. confirms the resolved path is still inside the resolved vault root
   (`is_relative_to`) — the same containment pattern the web `/docs` route uses.

A violation raises `ValueError`. `exists()` raises too rather than returning
`False`: a traversal attempt is an error, not a "no".

```python
vault.get("../escape.md")   # ValueError
vault.put(page, "/etc/x")   # ValueError
vault.exists("a/../../x")   # ValueError

vault.get("helper/my.helper-v2.md")   # fine — dotted names are legitimate
```

Page **names** are constrained independently by
`schema.NAME_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`), so
`promote`'s default `f"{kind}/{name}.md"` target cannot escape by construction —
`name` is model-controlled, and a validator is the right place to stop it.
Leading dots, separators, whitespace and traversal sequences are rejected at
parse time, which means a page carrying `name: ../../foo` cannot even be read
into a `Page`.

Before R20 neither check existed: paths were computed with a bare
`root / path`, and `name` was only length-limited.

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

### 4.7 Directory Convention (and the One-Time Migration)

**Every page lives in a directory named for its own kind, in the singular** —
`helper/<name>.md`, `fewshot/<name>.md`, `definition/<name>.md`,
`contract/<name>.md`. This is not decoration: it is exactly what
`gate.promote` computes for a page with no explicit `target_path`
(`f"{page.kind.value}/{page.name}.md"`), so a proposed page and a seeded page
of the same kind land side by side instead of in two competing trees. (The
template pages are the one deliberate exception: they sit under
`contract/templates/` because they are contract-layer text — see §10.2.)

**Pre-R13 vaults are not migrated automatically.** Vaults seeded before the
convention was unified contain `helpers/` and `fewshots/`. Re-seeding will not
move them (`seed_vault` skips paths that already exist, and the new singular
paths are different paths — you would get a duplicate copy of every builtin).
Move them once, by hand, before your next index rebuild:

```bash
# One-time migration: plural kind dirs → singular (run at the vault root).
cd "$VAULT_ROOT"                      # e.g. ~/.local/share/rlm-kernel/vault
for pair in helpers:helper fewshots:fewshot; do
  old="${pair%%:*}"; new="${pair##*:}"
  [ -d "$old" ] || continue
  mkdir -p "$new"
  git mv "$old"/*.md "$new"/ 2>/dev/null || mv "$old"/*.md "$new"/
  rmdir "$old" 2>/dev/null || true
done
rm -rf .index                          # derived — rebuilt from markdown
```

Then rebuild the index (`rlm-kernel index --rebuild`) and confirm the pages are
visible under their kind (`rlm-kernel search "regex" --kind helper`). Nothing in
the page content refers to the old directories — wikilinks resolve by filename
stem (§4.6), not by path — so the move is content-preserving.

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
**Incremental update** (`reindex_delta`) walks the vault, compares each page
against its indexed entry, and only modifies pages that have been added,
changed, or deleted. The comparison key is **content hash *and* page ULID**
(R14): `content_hash` intentionally excludes the identity fields, so a page
rewritten with a fresh ULID but identical content compares hash-equal — keying
on the hash alone skipped that page and left the index (plus every `tags` row,
which is keyed by `page_id`) pointing at a ULID that no longer existed.

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

**Query semantics (changed in R14).** The query string is split on whitespace
and every token is individually quoted and OR-ed together, so `regex search`
becomes `"regex" OR "search"`:

| Query | FTS5 expression | Meaning |
|---|---|---|
| `regex` | `"regex"` | pages containing the token `regex` |
| `regex search` | `"regex" OR "search"` | pages containing *either* token; BM25 ranks pages containing both highest |
| `blue widget` | `"blue" OR "widget"` | as above |

Quoting each token keeps the original exact-match intent — no FTS5 operator
injection (`NOT`, `NEAR`, `*`), no prefix or stemming surprises, and embedded
double quotes are escaped by doubling. What changed is the *joining* operator.
The previous implementation wrapped the entire query in one pair of quotes
(`"regex search"`), which FTS5 parses as a **phrase** query: it matched only
adjacent tokens. Every multi-word search therefore lost recall — a page holding
"regex" and "search" in different sentences was silently invisible — while
still *looking* like it worked, because single-word queries were unaffected.
OR-expansion restores multi-word recall; relevance ordering is still BM25, so
a page containing more of the tokens outranks one containing fewer. A query
with no tokens (empty or whitespace-only) returns no results.

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
report = validate(page, vault=vault)              # static validation only
report_exec = validate(page, vault=vault, execute=True)  # also run the sandbox

print(report.passed)    # True if no errors
print(report.errors)    # Fatal issues blocking promotion
print(report.warnings)  # Advisory issues
print(report.executed)  # False → the verdict came from static checks only
```

Validation is **deterministic and model-free** — no LLM involved.
Kind-specific checks:

| Kind | Validation Rules |
|---|---|
| **helper** | AST parse; import allowlist (stdlib only; block `os.system`, `subprocess`, `socket`, `ctypes`, `importlib`); blocked-pattern scan; static check that the code defines a callable with the page's name; static signature match between `## Signature` and the implementation's `def`. With `execute=True` the code is additionally `exec`'d in a restricted namespace. |
| **contract / template** | Slot variables present (`{repl_cap}` etc.). Body length within the 8192-**byte** cap (measured as UTF-8 bytes, so 3000 CJK characters = 9000 bytes does not fit). |
| **definition / note** | Wikilinks resolve to existing pages (when vault provided). |
| **fewshot** | Body is non-empty. A page without an `## Example`/`## Examples` section produces a **warning**, not an error — which matters because the shape `load_fewshots_from_vault` actually accepts for a single-pair page is `## Query` / `## Answer` (R8), and such a page still gets warned about the Example section it does not need. |
| **all** | Status must be `pending`. Must be in `quarantine/`. |

#### 7.3.1 Trust model — the gate is a quality gate, not containment

**Execution is opt-in.** `validate()` is static by default (`execute=False`);
`rlm-kernel review` prints `Review mode: static validation only` and only runs
the sandbox when `--execute` is passed.

The reason is that the sandbox is **not a security boundary**. It is restricted
builtins plus a substring blocklist, and:

- restricted-builtin `exec` is escapable on CPython — a
  `().__class__.__bases__[0].__subclasses__()` chain reaches arbitrary classes
  and the substring blocklist has no entry for it. Static validation *passes*
  such a helper, and the report says so rather than showing a silent green;
- `exec` only **defines** the function. It never calls it, so a helper whose body
  raises is not caught even with `execute=True`. Only definition-time failures
  (bad decorators, module-level raises, imports) are visible;
- the import allowlist and pattern scan are tripwires for the obvious cases, not
  a policy engine.

What the gate *is* good for: keeping malformed, mis-signed, or obviously hostile
content out of the live prompt, and forcing every model-authored page through a
review step. Treat `--execute` as a convenience for code you already trust, and
never as a sandbox you can point at untrusted input.

`tests/rlm_kernel/test_gate_execution_policy.py` pins all of this, including the
two limitations above.

### 7.4 Promote

```python
from rlm_kernel.gate import promote

new_path = promote(vault, page)
# Moves from quarantine/01KYC....md to helper/extract-dates.md
# Bumps version, updates hash, sets status: active, stages the file with git add
```

Promotion performs:
1. Validates the page (again) and raises `ValueError` if it does not pass — a
   rejected page never reaches the live namespace. Validation here is the
   **static** mode (§7.3.1): promotion never executes model-authored code.
2. Computes the target path based on kind and name (`helper/extract-dates.md`),
   or uses `target_path` verbatim when the caller supplied one.
3. Reads the page already occupying the target path, if any, and sets
   `version = max(occupant.version, page.version) + 1` — **version lineage follows
   the page being replaced**. (Proposed pages start at `version: 0`; a bare
   `version + 1` reset the counter to 1 on every promoted replacement, so after N
   optimizer runs the live prompt still claimed version 1.)
4. Updates frontmatter: `status: active`, `hash = content_hash`, timestamps refreshed.
5. Writes the promoted page to its target namespace.
6. Removes the quarantined original.
7. Stages both changes with `git add` / `git rm`. **Promotion never commits** —
   `vault.git_commit()` (or the CLI) does, so a batch of promotions lands as one
   history entry.
8. Optionally runs `index.reindex_delta(vault)` when an `Index` was passed.
9. Returns the new path.

**Occupancy guards.** With no `target_path`, promotion is refused when the target
path is occupied:

| Occupant status | Result |
|---|---|
| `active` | `ValueError` — demote it first or choose a different name |
| `deprecated` / `superseded` | `ValueError` — pass `force=True` (CLI: `--force`) to overwrite. Silently replacing a page that records "this was replaced by X" destroys the provenance chain, so it takes an explicit decision. |
| none | promoted |

An explicit `target_path` means the caller takes responsibility for the
replacement — that is how the optimizer replaces an incumbent in place (D-K4-1a),
and the occupancy guard does not apply.

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
demote(vault, page, superseded_by="helper/extract-dates-v2.md")
```

Demotion transitions `active → deprecated` or `active → superseded`. A
superseded page carries a `superseded_by` field pointing to the replacement
path. Deprecated and superseded pages remain in the vault and in the index.

**They still match search.** `Index.fts_search` queries the full-text table with
no status predicate (`search_vault` filters only by `kind` and `tags`), so a
deprecated page can appear in `search()` results — and a superseded page can
outrank its replacement. What demotion *does* control is behaviour that reads
status explicitly: only `active` helper pages are injected into the REPL
namespace and listed in the system prompt. Treat demotion as "stop using this",
not "make this invisible", and read `superseded_by` when a hit looks stale.

### 7.7 Quarantine Isolation

The gate enforces separation between quarantined and live pages:

```python
from rlm_kernel.gate import verify_quarantine_isolation

# Verify no quarantine pages leak into normal search
assert verify_quarantine_isolation(vault, index_path)
```

`verify_quarantine_isolation` compares the **index against a fresh walk of the
vault** (R14) and returns `False` if any of these hold:

1. A path under `quarantine/` appears in the index's `pages` table.
2. An indexed path has no corresponding file in the vault walk — an orphan row
   left behind by a deleted or moved page (the index was never reconciled).
3. A quarantine file found by the vault walk also appears in the index.

The second check is the substantive one. Before R14 it compared
`search_quarantine(vault)` against `vault.list(prefix="quarantine")` — the same
call twice, under a different name — so the function could only ever detect a
quarantine path that was *also* caught by the first check, and could not detect
index drift at all. Run `rlm-kernel index --rebuild` to repair a vault that
fails this check.

Related guarantees:

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
    print(f"{r['name']}: {r['summary']} (decay={r['decay']})")
```

Memory search filters to `kind: note|topic|cache`, retrieves a BM25 pool four
times wider than `k`, then re-ranks that pool by **decay-weighted relevance**
(see §8.5) before trimming to `k`:

$$\text{combined} = \frac{1}{1 + |\text{bm25 rank}|} \times \text{decay\_score}$$

Each returned card carries the `decay` value it was ranked by, so ranking is
inspectable rather than implicit. Retrieving the wider pool first is what makes
the decay term load-bearing: a plain `k`-limited BM25 cut would already have
discarded the freshly-accessed notes before decay saw them.

Returning a hit **records the hit**: `access_count += 1` and
`last_access = now` are written back through the same atomic `vault.put` path
as every other page mutation. This is what gives `decay_score` real inputs —
before R12 those fields existed nowhere, so the decay curve always ran on
synthetic defaults. Only the notes actually returned are touched; the rest of
the corpus is left alone.

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
count = mgr.forget(vault, older_than=timedelta(days=30))

# Both filters combine with AND: old notes that ALSO match the query
count = mgr.forget(vault, query="obsolete widget", older_than=timedelta(days=30))

# Forget all non-core notes
count = mgr.forget(vault)
```

The `forget` method removes or deprecates notes matching the criteria.
Core memory is never targeted. `older_than` takes a `timedelta` (not a bare
day count), and when both `query` and `older_than` are supplied a note must
satisfy **both** — a query match alone is not enough, and age alone is not
enough. This matches the docstring; before R12 the code silently let `query`
win and ignored `older_than` whenever a query was given.

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
# Preview: exactly the notes a merge would absorb
candidates = mgr.compact(vault, index_path, similarity_threshold=0.8, dry_run=True)
print(f"{len(candidates)} note(s) would be absorbed")

# Merge near-duplicate notes by title similarity
merge_count = mgr.compact(vault, index_path, similarity_threshold=0.8, dry_run=False)
assert merge_count == len(candidates)  # same clustering, by construction
```

Compaction clusters notes under `memory/notes/` by title similarity
(`SequenceMatcher` ratio ≥ threshold, greedy single-linkage from the newest
note) and merges each cluster: the newest note absorbs the others' bodies
under a `<!-- merged from <path> -->` marker, and the older ones are marked
`superseded` with `superseded_by` pointing at the keeper. This prevents
unbounded note accumulation ("image rot") without requiring LLM judgment.

Dry-run and merge call the **same** `_cluster_notes()` function (R12), so the
dry-run count is the number of notes a merge would absorb — the two paths
cannot disagree, and one candidate row is emitted per absorbed note
(`title_a` = keeper, `title_b` = absorbed, `similarity`). The previous
implementation ran two different grouping loops and carried a `merged` set
that was never populated.

---

## 9. Offline Optimization (GEPA)

The optimizer (K4) uses GEPA (`gepa.optimize_anything`) to evolve the
harness's textual surface — prompts, templates, few-shots — using
`rlm_local` itself as the evaluator. The sub-tier model serves as the
student (runs tasks), and the root-tier model serves as the reflection
LM (proposes improvements).

### 9.1 Eval Suites

Eval suites are JSON files in `tests/evals/` containing verifiable tasks.
Each task has a query, context, expected answer pattern (regex), and
optional numeric tolerance. Tasks are deterministically split 70/30 into
train and held-out sets.

```python
from tests.evals import load_suite

suite = load_suite("needle_search")
print(f"Train: {len(suite.tasks)}, Held-out: {len(suite.held_out)}")
```

Built-in suites (22 tasks total):

| Suite | Tasks | Description |
|---|---|---|
| `needle_search` | 7 | Find specific facts, names, prices, emails in long documents |
| `counting` | 5 | Count items, sum prices, compute averages — numeric verification |
| `multi_hop` | 5 | Multi-step reasoning — "who wrote the book?", "capital of country" |
| `fact_extraction` | 5 | Extract ISBNs, phone numbers, IPs, currencies, dates |

### 9.2 Evaluator

The evaluator wraps `rlm_local.completion()` and returns a (score, feedback)
tuple for GEPA. Score is the fraction of tasks where the answer matches the
expected regex. Feedback includes per-task pass/fail with answer snippets.

```python
from rlm_kernel.optimize import evaluate_candidate

result = evaluate_candidate(
    candidate_text="PROBE the context first...",
    suite_name="needle_search",
    eval_dir=Path("tests/evals"),
    profile="tiny",
    max_turns=6,
)
print(f"Score: {result.score:.1%} ({result.passed}/{result.total})")
```

### 9.3 Running Optimization

```python
from rlm_kernel.optimize import run_optimization

result = run_optimization(
    vault,
    target="how-to-work",
    suite_name="needle_search",
    profile="tiny",
    max_metric_calls=150,
)
print(f"Status: {result['status']}")
print(f"Baseline: {result['baseline_score']:.1%}")
print(f"Best: {result['best_score']:.1%}")
```

The optimizer:
1. Reads the current target text from the vault as the seed candidate.
2. Evaluates baseline score on the train split.
3. GEPA's reflection LM proposes improved candidates.
4. Each candidate is evaluated against the train split.
5. The best candidate is validated on the held-out split.
6. If held-out score ≥ baseline and train score > baseline, the candidate is
   promoted through the gate with `optimized_by: gepa-run-<id>` lineage in the
   body and the `gepa-optimized` tag in frontmatter.
7. Instant rollback via git if needed.

**Promotion order is load-bearing (R11).** The sequence is strictly
`propose → validate → (pass?) demote incumbent → promote(target_path)`:

- The candidate is proposed into quarantine as a page of the **incumbent's
  kind** (read from the incumbent), not a hardcoded `contract` — optimizing
  the `fewshots` or `nudges` target must not mint a contract page.
- Validation runs **before** the incumbent is demoted. A candidate that fails
  validation (over-long body, broken slot variables) leaves the incumbent
  `active` and the run reports `validation_failed`. The earlier ordering
  demoted first, so a rejected candidate could leave the live prompt
  `deprecated` with nothing promoted — a broken system and an opaque
  `gate_error`.
- The promoted page's `version` is `incumbent.version + 1`, so lineage keeps
  counting across successive optimizations instead of resetting to 1.
- The GEPA evaluator mutates the live target page to inject a candidate, then
  restores it. That mutate → evaluate → restore window runs under a
  module-level `threading.Lock` and restores in a `finally`, so parallel
  evaluations (`EngineConfig.max_workers > 1`) cannot measure each other's
  candidate text and an evaluator exception cannot leave candidate text in the
  production vault. Evaluation is therefore serialized by design.

**Target options**: `prologue`, `how-to-work`, `nudges`, `fewshots`, `helper-docs`.

### 9.4 Few-Shot Bootstrap

```python
from rlm_kernel.optimize import bootstrap_fewshots

transcripts = bootstrap_fewshots(
    vault, suite_name="needle_search",
    profile="tiny", max_shots=3,
)
```

Replays the train split, keeps trajectories that reach verified-correct
answers, and selects 2–3 canonical transcripts. Selected few-shots are
stored through the gate as `fewshot/bootstrap-<task>.md` pages.

### 9.5 Local Feasibility

- Each metric call ≈ 4–8 minutes (a full `rlm_local.completion()`).
- 150 metric calls ≈ 10–20 hours (overnight on `tiny` profile).
- GEPA checkpoints candidates as text — runs resume naturally.
- Keep eval tasks in the 30–70% success band for rich optimization signal.
- The evaluator itself and the gate code are never optimizer-editable.
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
2. `contract/how-to-work.md` body.
3. One-line summaries of active helper pages from `helper/` (≤30 lines —
   progressive disclosure cap).
4. Fallback: if the contract pages are missing, the hardcoded `SYSTEM_PROMPT` is
   used.

The **few-shot** transcript is loaded separately by
`load_fewshots_from_vault(vault, prompt_char_budget=…)`, which `RootLoop` calls
alongside this function: the builtin example always comes first, then at most one
active `fewshot/` page, and only if the pair fits `sub_prompt_char_budget / 4`
(R8 — before that, vault few-shots were inert).

### 10.2 Template Pages (Introspection-Only)

Template pages are seeded under `contract/templates/` for **introspection**
(P6 — the model can `search("nudge no block")` and read its own conventions).
Behavior wiring — loading template text from vault pages at runtime — is
**deferred** to a later phase. Currently, `rlm_local` always uses its
hardcoded package-bundled template constants. The `load_template()` function
and `_TEMPLATE_PAGE_MAP` were removed in R3-D10 to eliminate dead code.

This is specifically about *template* pages. Two other vault kinds **do** affect
behavior today: `contract/repl-contract.md` and `contract/how-to-work.md` (§10.1),
`helper/` pages (injected into the REPL namespace and listed in the system
prompt), and `fewshot/` pages (§10.1, R8). Template pages remain the exception
because no code path loads them.

When vault-template loading is implemented (K3b+), the GEPA optimizer will
be able to evolve templates against held-out eval suites.

### 10.3 Byte-Stable Prefix Discipline

Even with vault-first loading, the byte-stable prefix invariant is maintained:
once loaded for a session, the system prompt and templates do not change
mid-completion. The vault is consulted once at the start of `RootLoop.run()`,
so prefix-cache hits remain consistent across turns.

---

## 11. CLI Reference

```
rlm-kernel — Evolvable RLM kernel CLI

Commands:
  init                  Seed a new vault with contract, template, and helper pages
  index --rebuild       Rebuild the full-text search index from vault pages
  review [--execute]    List quarantined pages pending review (static by default)
  promote PATH [--force] Promote a quarantined page into the live namespace
  demote PATH [--by P]  Demote an active page (optionally superseded by another)
  search QUERY [--kind] Search the vault by keyword
  optimize --target T   Run GEPA optimization on a text artifact
```

### 11.1 init

```bash
rlm-kernel init [--vault PATH]
```

Creates the vault directory tree, seeds 17 canonical pages (2 contracts,
9 templates, 5 builtin helpers, 1 few-shot transcript), and initializes a git
repository. Idempotent — existing pages are never overwritten.

### 11.2 index

```bash
rlm-kernel index --rebuild [--vault PATH]
```

Walks the vault, clears the index, and re-indexes every page. Run after
manual edits or after `git pull` to reconcile.

### 11.3 review

```bash
rlm-kernel review [--execute] [--vault PATH]
```

Lists all pages in `quarantine/` with their kind, name, and summary, validating
each one. Human reviews these before running `promote`.

**Static by default (S3/R19).** The command prints
`Review mode: static validation only` and performs no code execution. Pass
`--execute` to additionally run helper code in the restricted-builtin sandbox —
an opt-in convenience for trusted authors, never a containment boundary; see
§7.3.1 for the trust model and its two known limitations.

### 11.4 promote

```bash
rlm-kernel promote quarantine/01KYC....md [--force] [--vault PATH]
```

Promotes a quarantined page to its target namespace. `promote` runs static
validation itself and raises `ValueError` if the page does not pass, so a
rejected page never reaches the live namespace — `review` first if you want to
see the findings before promoting.

`--force` is required to overwrite a **deprecated or superseded** page already
at the target path; an **active** occupant always blocks promotion (demote it
first). See §7.4.

### 11.5 demote

```bash
rlm-kernel demote helper/old-helper.md [--by helper/new-helper.md] [--vault PATH]
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

All constants are `str` values, most are `.format()` templates. Template
pages exist in the vault for introspection only (P6); behavior wiring is
deferred. See §10.2.

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
│   ├── test_schema.py         # frontmatter validation, parsing, helper extraction,
│   │                          #   R12 access-field backward compatibility
│   ├── test_vault.py          # CRUD, atomic writes, wikilinks, round-trip
│   ├── test_index.py          # build, FTS query semantics (R14), incremental
│   │                          #   update + id-change delta, rebuild
│   ├── test_search.py         # keyword search, kind filter, card budget, edge cases
│   ├── test_gate.py           # propose/validate/promote/demote, quarantine
│   │                          #   isolation (R14), body-length units
│   ├── test_memory.py         # add/note/forget/compact, decay ordering (R12)
│   ├── test_optimize.py       # eval suites, evaluator, promotion state hazards (R11)
│   ├── test_seed.py           # seed layout convention (R13), idempotency
│   ├── test_repl_bridge.py    # helper payload + search/propose proxy handlers
│   ├── test_kernel_cli.py     # the rlm-kernel command surface
│   └── test_migration.py      # index migrations
└── (rlm_local tests)          # unchanged, verifying fallback parity
```

Run the kernel suite alone with `uv run pytest tests/rlm_kernel/ -q`. Counts drift;
prefer the suite itself over any number written down here.

### 13.2 Test Conventions

- **Unit tests** use temporary vaults (`temp_vault` fixture) with `init_git=False`
  to avoid git dependency. All are deterministic — no real LLM, network, or clock.
- **Property tests** (future): job state machine, dedup, frontmatter round-trip
  via hypothesis.
- **Load tests**: the 100K-page gate is **implemented and passing** — it is not
  a future item. Recorded result (2026-07-26, `scripts/run_load_gate_100k.py`
  on the target laptop; see `docs/20260726-1527-fts-quadratic-fix-validation.md`):

  | Metric | Budget | Recorded | Verdict |
  |---|---|---|---|
  | Full reindex, 100K pages | < 7,200 s | **582 s** (9.7 min) | PASS (12× under budget) |
  | FTS search p95 | < 300 ms | **116.9 ms** | PASS |
  | `git status` | < 2,000 ms | **38 ms** | PASS |

  The same gate previously recorded 12,680 s / 13,090 s for the rebuild — an
  FTS5 full-scan quadratic in the update path (F1/F2 in the validation doc).
  **Re-run this gate after any change to `index.py`**: it exists to catch
  exactly that class of regression, and it is the only test that runs at
  realistic corpus size.

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
markdown. `reindex_delta()` reconciles incrementally — it compares content
hash **and page id**, so an id rewrite cannot slip past it, and it removes rows
whose pages have vanished. `verify_quarantine_isolation()` compares the index
against a vault walk and fails on orphan rows (§7.7). The index is always
rebuildable — markdown is truth.

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
    name: str                       # ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ (S4/R20)
    title: str                      # 1-256 chars
    summary: str                    # 1-200 chars
    tags: list[str]
    version: int = 1
    hash: str | None                # sha256:<64-hex>
    status: PageStatus = ACTIVE
    superseded_by: str | None
    created: datetime               # UTC
    updated: datetime               # UTC
    access_count: int = 0           # memory-decay bookkeeping (R12)
    last_access: datetime | None = None

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
    """Every path goes through _resolve(): absolute paths, '..' segments and
    paths resolving outside the root raise ValueError (S4/R20)."""
    def __init__(self, root: Path, init_git: bool = True) -> None: ...
    def get(self, path: str) -> Page | None: ...
    def put(self, page: Page, path: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]: ...
    def resolve_wikilink(self, name: str) -> Page | None: ...
    def git_commit(self, message: str = "vault: update") -> bool: ...
```

`get`, `put`, `delete` and `exists` raise `ValueError` — not `False`/`None` — for a
path that escapes the vault root; see §4.3.1.

### 15.3 `rlm_kernel.index`

```python
class Index:
    def __init__(self, db_path: Path) -> None: ...
    def build(self, vault: VaultStore) -> None: ...
    def reindex_delta(self, vault: VaultStore) -> None: ...
    def fts_search(self, query: str, limit: int = 40,
                   kinds: list[str] | None = None,
                   tags: list[str] | None = None) -> list[dict[str, Any]]: ...
    def list_paths(self, kind: str | None = None,
                   status: str | None = None) -> list[str]: ...
    def page_count(self) -> int: ...
    def get_page(self, path: str) -> dict[str, Any] | None: ...
    def close(self) -> None: ...
    def __enter__(self) -> Index: ...
    def __exit__(self, *args) -> None: ...

def rebuild_index(vault: VaultStore, db_path: Path) -> Index: ...
```

`fts_search` splits the query on whitespace, quotes each token and joins with
`OR` (R14) — see §5.3 for the semantics change and why it exists.

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
def validate(page: Page, vault: VaultStore | None = None,
             *, execute: bool = False) -> ValidationReport: ...
def promote(vault: VaultStore, page: Page, index: Any | None = None,
            target_path: str | None = None,
            *, force: bool = False) -> str: ...
def reject(vault: VaultStore, page: Page) -> None: ...
def demote(vault: VaultStore, page: Page,
           superseded_by: str | None = None) -> None: ...
def search_quarantine(vault: VaultStore, query: str = "",
                      kind: str | None = None) -> list[Page]: ...
def verify_quarantine_isolation(vault: VaultStore, index_path: Path) -> bool: ...
```

`ValidationReport` carries `passed`, `errors`, `warnings` and — since R19 —
`executed`, which is `True` only when the helper sandbox actually ran.

`search_quarantine` returns pages **newest first** (sorted on the ULID filename
stem), matching its docstring; `vault.list` alone returns lexicographic order.

### 15.8 `rlm_kernel.memory`

```python
class MemoryManager:
    def add(self, vault: VaultStore, text: str, tags: list[str] | None = None) -> str: ...
    def search(self, vault: VaultStore, index_path: Path,
               query: str, k: int = 5) -> list[dict[str, Any]]:
        """BM25 pool (4×k) re-ranked by decay-weighted relevance; hits are
        recorded through vault.put as access_count/last_access. Cards carry a
        `decay` field (R12)."""
    def note(self, vault: VaultStore, chunk: str) -> str: ...
    def forget(self, vault: VaultStore, query: str | None = None,
               older_than: timedelta | None = None) -> int:
        """Both filters are combined (AND) when both are given (R12)."""
    def write_core(self, vault: VaultStore, text: str) -> None: ...
    def compact(self, vault: VaultStore, index_path: Path,
                similarity_threshold: float = 0.85,
                dry_run: bool = True) -> int | list[dict[str, Any]]:
        """Dry-run and merge share one clustering function, so the reported
        count and the merge cannot disagree (R12)."""

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
    # No `tolerance` field: the loader would ignore it, so numeric tasks encode
    # their tolerance in the anchored pattern instead (R24).

@dataclass
class EvalSuite:
    name: str
    tasks: list[EvalTask]
    held_out: list[EvalTask]

def evaluate_task(task: EvalTask, completer: Callable) -> tuple[bool, str]: ...
def evaluate_suite(suite: EvalSuite, completer: Callable) -> dict[str, Any]: ...
def run_optimization(vault: Any, target: str = "how-to-work",
                     max_iterations: int = 20,
                     profile: str = "laptop") -> dict[str, Any]:
    """Terminal statuses: `promoted`, `no_improvement`, `validation_failed`
    (a candidate the gate rejected, leaving the incumbent ACTIVE — R11), or
    `gate_error`."""

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
