# The corpus, through the harness (roadmap RO3 + RO4)

**Created 2026-09-12 16:10.** Point-in-time record of what landed in the commit
that gave `rlm_local` a read-only corpus capability, what it is verified against,
and what it deliberately does not claim yet. The constraint it serves is stated
in `AGENTS.md` §1.8 (the corpus is read-only, enforced, not intended) and
`AGENTS.md` §1.9 (corpus-derived data does not leave the machine holding the
corpus); the plan it advances is `docs/20260912-1155-roadmap.md` §7.

---

## 1. The problem this solves

The corpus is 4,972,610 entries (~4.28M files, 649,537 directories, 41,361
symlinks, ~1.01 TB of file bytes) on an external LUKS drive on `lunacode`,
exposed read-only at `/srv/corpus`. Reading it *by hand* is what this record
replaces. Two things were wrong with doing it by hand:

- **Every hand-typed command is a chance to write.** The owner's constraint is
  absolute, and a shell session is exactly where an accident happens.
- **The tree is far too large to look at.** One `find` pass took 33 minutes. An
  agent that has to walk it has already lost the turn.

So the corpus became something the harness offers as a tool, and the tool is the
only interface: `rlm ask --corpus-root /srv/corpus --corpus-index …` runs the
harness next to the corpus, model cells reach it through five helpers, and no
corpus content crosses the network to another machine.

---

## 2. What was built

### 2.1 `rlm_kernel/corpus.py` — names, not contents

`CorpusIndex` is a SQLite table of *paths* (`path`, `parent`, `name`, `kind`,
`size`, `mtime`) built by **one** streaming walk and read by every later
question. It never opens a file: a test indexes a corpus through a spy mount
whose read methods raise, so any build path that read contents turns that test
red. That is the cheap half of RO3 — no text extraction, no LLM, no content
scanning — and it is what makes `corpus_find` and `corpus_count` O(query)
instead of O(tree).

`CorpusBridge` answers the tool calls. It reads through `LocalTreeMount`, so the
code that serves a model cell has no write verb to call, and it holds the caps
that keep a tool result inside a context window (`READ_BYTES_MAX` 200 KB,
`FIND_LIMIT_MAX` 200 rows).

### 2.2 Five helpers in the REPL

| Helper | Answers |
|---|---|
| `corpus_find(query, limit=20, kind=None, under="")` | matching relative paths, from the index |
| `corpus_list(rel="", limit=50)` | one directory level (not the subtree) |
| `corpus_stat(rel)` | kind, size, mtime of one path |
| `corpus_read(rel, max_bytes=20000)` | one file, bounded, truncation reported |
| `corpus_count(kind=None, under="")` | counts and byte totals, no listing |

They are answered in the **parent** process over the existing socket protocol —
the same channel `search` and `propose` already use. The worker subprocess never
touches the corpus: read-only enforcement, containment and the byte caps all live
in one place, next to the type that has no write method. A cell cannot reach
around them, and a knocked-over helper is restored by the scaffold repair that
already existed (the five names are registered with it).

### 2.3 The prompt says so, and says what it cannot do

A run with a corpus configured appends a capability block to the system prompt
(root_loop.py), in **both** prompt paths — the packaged `SYSTEM_PROMPT` and a
vault-assembled one, because a vault whose contract page predates this feature
would otherwise leave the model uninformed about helpers its cells can call. The
block names the five verbs, warns that the tree must never be walked from a cell
("it holds millions of files"), states that the corpus is read-only, and says
when no index exists. A test checks the advertised names against the worker's
actual namespace: a prompt that advertises a helper the worker does not define is
a lie a small model will act on.

### 2.4 The operator's side

```
rlm corpus index  --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite
rlm corpus status --corpus-index ~/rlm-derived/corpus.sqlite     # aggregates only
rlm corpus count  --corpus-index ~/rlm-derived/corpus.sqlite [--kind file] [--under DIR]
rlm corpus find   --corpus-index ~/rlm-derived/corpus.sqlite QUERY
rlm corpus read   --corpus-root /srv/corpus REL
```

`index` and `read` require both paths; the index-backed subcommands need only
`--corpus-index` and open no mount, so they work wherever the index happens to
be. Nothing is ever guessed: an index path **inside** the corpus is refused by
`assert_derived_outside_corpus` rather than accepted, in the CLI and in the
bridge alike. `status` prints aggregates only — counts, kinds, byte totals — and
is the form that is safe to quote outside the corpus machine (`AGENTS.md` §1.9).

---

## 3. What is verified

- **969 tests pass** in the fast suite (`-m "not slow and not load"`,
  5 skipped, 12 deselected), including 80 new ones: 38 in
  `tests/rlm_kernel/test_corpus.py`, 26 in `tests/test_corpus_repl.py`, 16 in
  `tests/test_cli_corpus.py`.
- **13 new mutation entries**, all red when the guard is removed (full table:
  86 entries, all red). They cover: containment refusal, one-level listing
  (a subtree walk instead), no-walk-without-index, LIKE-wildcard escaping, the
  byte/row caps, both derived-state assertions, "the index build never reads a
  file", the REPL dispatch, the worker export, scaffold repair, the prompt
  advertisement, and `ask` handing the bridge to the run.
- **Containment is tested against a real target**: the escape test creates the
  file *outside* the corpus first, so containment is the only thing that can
  refuse it. (The first version of the mount tests passed with paths that did not
  exist, which is why the rule exists.)
- **A live cell, in a real worker subprocess, against a real directory**: it
  searches, reads, lists, stats and counts, cannot read outside the root, and
  leaves every path's size and mtime identical afterwards.

Not verified, and labelled as such: the pre/post `find -newer` manifest over the
real 4.97M-entry corpus has **not** been run yet (it is the RO2 proof obligation
from `AGENTS.md` §1.8, and it belongs with the first live run on `lunacode`), and
no model has yet answered a corpus question through the router.

---

## 4. Where it runs, and why there

The harness is deployed at `~/Misc/rlm_for_local` on `lunacode` (venv on Python
3.14, `uv pip install -e . --group dev`), with derived state at `~/rlm-derived`
(mode 0700, outside the corpus and outside the mount). The model server is the
llama.cpp router on the same host, so a corpus excerpt reaches only
`127.0.0.1:9010` — never this development machine, and never a hosted provider.
That placement is the whole point of `AGENTS.md` §1.9: analysis runs where the
data is.

---

## 5. Deliberately not in this slice

- **Text extraction and FTS over contents** (the other half of RO3). The owner's
  scope call on archives, PDFs and source code is still open
  (`docs/20260912-1155-roadmap.md` §7). The path index is the prerequisite that
  makes that work a per-format decision rather than a blocker.
- **Semantic recall** (OD2, embeddings). Lexical path search first, because it
  needs no model and no additional host budget.
- **Media** (RO9) and **selective enrichment** (RO6): unchanged, still pending.
- **A web-console surface** for corpus queries: `rlm ask` is the entry point
  today, and `rlm chat` does not yet carry the helpers.
