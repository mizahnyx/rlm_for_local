# rlm-kernel — An Evolvable, Smalltalk-style Layer for `rlm_for_local` — System Specification

**Working name:** `rlm-kernel` (package `rlm_kernel`, a sibling layer over the existing `rlm_local` package)
**Date:** 2026-07-24
**Status:** Ready for implementation (executor agent, TDD)
**Companion documents:** paradigms research report (`20260724-1736-llm-programming-paradigms-vs-rlm.md` — the evidence base for every principle here), harness design spec, memory-wiki spec (`20260723-1324` — the north star this re-scopes).
**Target codebase:** `~/Documents/Misc/rlm_for_local/` (`src/rlm_local/`: `repl.py`, `root_loop.py`, `subcall_manager.py`, `prompts.py`, `templates.py`, `parser.py`, `config.py`, `context_store.py`, `model_backend.py`, `logger.py`).

---

## 1. Vision and scope

Make an `rlm_for_local` instance **evolvable the way a Smalltalk image is evolvable**: its ontology (vocabulary of concepts), capabilities (helpers/tools), prompts, few-shots, and memory are **content** — human-readable pages in a versioned wiki — rather than code frozen in the package. The system can describe itself to itself (its own contract and conventions are searchable pages), grow new vocabulary by authoring pages, and improve its own textual surface through a verification gate — all on small local models, incrementally, with TDD.

This spec **re-scopes** the memory-wiki system (`20260723-1324`) primitive-first:

| Wiki-spec element | Fate |
|---|---|
| Vault (markdown + frontmatter + git) | **K0** — kept, simplified (single-box, local path) |
| FTS5 index + hybrid search | **K0** — lexical first; vectors deferred |
| Memory notes (A-MEM-style) | **K3** |
| Capability registry + LATM gate | **K2** |
| GEPA offline optimization | **K4** (was absent from the wiki spec; added from the paradigms research) |
| Remote vault over HTTP (LAN service), media pipelines, mounts, web UI, job queue | **Deferred** — the kernel's `VaultStore` protocol leaves the seam; the LAN service becomes a remote backend later (§10) |

**Three primitives** (the user's formulation, validated by the research):
1. **REPL evaluation** — exists in `rlm_local`; extended so its namespace is *assembled from definition pages*.
2. **Advanced search** — one hybrid search over the vault; doubles as the system's introspection.
3. **Human-readable indexed wiki** — the persistent "image": git-versioned markdown pages, machine indexes derived and rebuildable.

## 2. Design principles (from the research report §9)

P1 one universal medium (text) · P2 one eval (the REPL) · P3 the image is a human-readable, versioned wiki · P4 vocabulary grows by definition pages, not code changes · P5 every capability is content retrievable by search · P6 the system describes itself to itself · P7 frozen kernel, liquid above · P8 all growth passes through an evaluator gate.

Non-negotiables inherited from the harness spec: LID observations (root never sees bulk content), templated stability of the frozen layer (R4.1), prefix-cache discipline, minimal dependencies (httpx + stdlib today; kernel adds **PyYAML + git binary only**), small-model guardrails.

## 3. The page model (the universal medium)

### 3.1 Vault layout (local, single-box first)

```
$VAULT_ROOT/                      # default: ~/.local/share/rlm-kernel/vault (config)
├── .git/                         # pages only; indexes excluded
├── contract/                     # FROZEN-class pages: the system's self-description
│   ├── repl-contract.md          #   REPL rules, answer dict, budgets (source of prompts.py text)
│   ├── how-to-work.md            #   orchestrator addendum content
│   └── templates/                #   nudge/warning/header texts (source of templates.py text)
├── definitions/                  # the ONTOLOGY: concept/vocabulary pages
│   └── <slug>.md
├── helpers/                      # CAPABILITIES: executable definition pages (Python)
│   ├── peek.md  grep.md  chunk.md  map_query.md  show_vars.md   # builtins, as pages
│   └── <user/authored>.md
├── fewshots/                     # worked transcripts (load-bearing few-shots)
├── memory/                       # K3: notes, topics/MOCs, core-memory.md, orientation caches
│   ├── notes/  topics/  caches/
└── quarantine/                   # the gate's waiting room (not indexed, not callable)
    └── <pending pages>
└── .index/                       # DERIVED, rebuildable: meta.sqlite (+ fts)
```

### 3.2 Page anatomy (schema versioned; `schema: 1`)

```markdown
---
schema: 1
id: 01K2…                     # ULID, stable
kind: definition              # contract | template | definition | helper | fewshot | note | topic | cache
name: grep                    # machine name (helpers/templates; unique within kind)
title: "grep — regex search over context"
summary: "…one sentence…"     # indexed; shown in search cards (≤200 chars)
tags: [builtin, search]
version: 3                    # bump on every promotion
hash: sha256:…                # content hash at promotion (contract/helper/template kinds)
status: active                # active | deprecated | superseded
superseded_by: null           # page path (P3 anti-rot)
created: / updated: …
---
# Body: human-readable documentation.

## Signature (helpers only)
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```

## Implementation (helpers only — ONE fenced python block, extracted at load)
```python
def grep(pattern, max_hits=50):
    ...
```

## Usage example (exactly one — the doc standard from the capability research)
```repl
hits = grep('blue', max_hits=3)
```
```

Rules: filenames `slug.md`; atomic writes (tmp+fsync+rename); frontmatter validated by pydantic; `contract/` and builtin `helpers/` pages ship with the package (seeded on first run, then owned by the vault — upgrades only via the gate, so the instance's evolution is never clobbered by `pip install -U`).

### 3.3 Kinds and their roles

- **`contract`** — the frozen layer *as content*. `prompts.py` and `templates.py` stop being string literals and become **loaders** for these pages (with package-bundled fallbacks if the vault is missing). Editing contract pages is possible only via the gate (§7) — this is how the harness's own wording becomes evolvable while R4.1 stability is preserved *within* a version.
- **`definition`** — pure ontology: concepts, conventions, entities, "how we do X here." No code. Authored by human or model (K3 note-construction is a special case).
- **`helper`** — a capability: one Python function + doc + one example. Builtins (`peek`, `grep`, `chunk`, `map_query`, `show_vars`) are migrated from `repl.py`'s hardcoded worker script into pages — **user-authored helpers are structurally indistinguishable from builtins** (the Forth dictionary property).
- **`fewshot`** — worked transcripts, selected via bootstrap (K4).
- **`note` / `topic` / `cache`** — K3 memory pages; `cache` = PEEK-style bounded orientation caches for recurring contexts.

## 4. The index and search (primitive #2)

`meta.sqlite` (stdlib sqlite3, WAL): `pages(id, path, kind, name, title, summary, hash, version, status, updated)`, `links(src, dst)`, `tags(page, tag)`, plus FTS5 external-content table over `(title, summary, body)` (unicode61; trigram table added in K3 if needed).

**`search(query, k=5, kinds=None, detail="card")`** — BM25 over FTS5 + filters; returns compact cards `{path, kind, name, title, summary, score}` (≤400 chars each — LID invariant). `detail="full"` returns the page body. Vector tier (embeddings of title+summary) is a **K3 option**, behind a `SearchBackend` protocol, off by default — the research says lexical-first (BEIR; SQLite FTS5 at 500K pages is fine **[load-test gate K0]**).

**Introspection (P6):** search covers `contract/` and `helpers/` too — the root model can `search("how do I submit an answer")` and read its own conventions. The system prompt states this in one line.

**Rebuild:** `rlm-kernel index --rebuild` walks the vault and reconstructs everything (markdown = truth). Service start reconciles external edits (hash-compare → reindex delta).

## 5. The REPL, assembled from definitions (primitive #1, extended)

### 5.1 Change to `rlm_local` (small, surgical)

`repl.py` today: `_WORKER_SCRIPT` hardcodes helper functions; `init` sends only the context string. The kernel adds an **optional definitions payload** to `init`:

```python
# REPLSandbox.start(..., definitions: list[HelperDef] | None = None)
_send_msg(sock, {"cmd": "init", "context": ctx_str,
                 "helpers": [{"name": h.name, "code": h.code} for h in definitions]})
```

Worker `init` handler: `exec(code, globals())` per helper (in a restricted builtins sandbox as today), replacing the hardcoded helpers when provided. **No `init` helpers → today's behavior** (full backward compatibility; `rlm_local` remains standalone).

New builtins injected alongside: `search(query, k=5)` (proxied to the vault like `llm_query` is proxied to SubcallManager — one new `cmd` in the socket protocol) and `define(...)` / `propose(...)` (§7), also proxied.

### 5.2 Prompt assembly from pages

`prompts.py` becomes a renderer: system prompt = `contract/repl-contract.md` body (with `{repl_cap}`-style slots) + helper one-liners **generated from active helper pages** (name + signature + summary, capped at ~30 lines — the progressive-disclosure budget) + `contract/how-to-work.md` + one `fewshots/` transcript (bootstrap-selected, K4). The helper list is therefore *content*: adding a helper page makes it known to the model on the next run — P4 in action. Fallback: if vault absent, today's literal prompts are used.

The root model is **not** told about quarantined/deprecated helpers; `status` and the gate control visibility (P8).

## 6. The frozen contract (the Nock layer)

Frozen (versioned as `contract_version`, changes only by deliberate release + migration): the socket protocol verbs (`exec`, `init`, `shutdown`, `subcall`, `subcall_batched`, + new `search`, `propose`); the `answer` dict semantics; the page **schema** (frontmatter keys); template *names* (not their text); helper *signatures* of builtins; the gate's states.

Liquid (content, evolves): all prompt/template wording; helper set; few-shots; ontology; memory; budgets (config).

Per the research: freeze *late* — `contract_version: 0.x` marks everything provisional until K4's eval suites exist to prove stability.

## 7. The gate (P8) — how anything enters the image

All writes from the *model* go through `propose` (human writes via editor are trusted; service reconciles):

```python
propose(kind: str, name: str, body: str, rationale: str) -> str   # REPL-proxied; returns page path in quarantine/
```

1. **Quarantine** (`quarantine/<ulid>.md`): written with `status: pending`; not indexed into search; **callable-in-sandbox only** (a proposed helper can be test-run by its author via `search(..., include_quarantine=True)` + explicit `exec` — liveliness preserved, blast radius zero).
2. **Auto-validation** (deterministic, no model): schema validation; for helpers — import allowlist check, fixture run (execute against a tiny canned input in the REPL sandbox, assert no exception, output shape check, no-network assertion); for contract/template pages — slot variables still present (`{repl_cap}` etc.), length within cap; for definitions/notes — links resolve.
3. **Review**: `rlm-kernel review` CLI lists pending pages with validation reports; human runs `rlm-kernel promote <path>` (or `--all-validated`). Promotion: bump version, content-hash, move to target namespace, reindex, git commit (`kernel: promote <name> (vN)`).
4. **Rejection/demotion:** `reject` (delete), `demote` (active → deprecated; `superseded_by` links old → new).

**Eval-gated promotion for behavior text (K4):** contract/template/fewshot changes additionally require beating the incumbent on the held-out suite (§8). The *evaluator itself* and the gate code are human-only — never model-editable (the DGM lesson).

## 8. Offline optimization (K4) — GEPA as the mutation operator

- Runner: `rlm-kernel optimize --target {prologue|how-to-work|nudges|fewshots|helper-docs}` wraps `rlm_local.completion` as the evaluator via `gepa.optimize_anything` (zero-rewrite) or a thin `dspy.Module` (lineage perks). Student = sub-tier 4B; reflection = root-tier 8B (both already in config tiers).
- Eval suites: verifiable tasks only (needle regex, OOLONG-style numeric tolerance) held in `tests/evals/`; **30–70% success band** (saturation trap). Metric returns score + **feedback text = the harness's own templated warnings** (parse-rescue counts, shortcut warnings, budget notices) — the strings already exist in `templates.py`.
- Budget: `max_metric_calls` 150–300; `tiny`-profile turn/call caps during runs; one overnight for sub-call-tier text, a weekend for root-tier.
- Promotion: candidate must beat incumbent on the **held-out split** → gate auto-promotes with lineage record (`optimized_by: gepa-run-<id>` in frontmatter); rollback = `git revert` + demote.
- Few-shot bootstrap: separate cheap path — replay trainset, keep verified-correct trajectories, auto-select 2–3 canonical transcripts into `fewshots/` (no reflection LM needed); doubles as the distillation pool (design spec §11).

## 9. Memory (K3) — ontology that grows itself

- `memory.add(text)` → note construction via `llm_query` (A-MEM-style: summary/keywords/tags) → `memory/notes/<slug>.md` through the **gate's auto-validation** (notes are low-risk: validation = schema + link resolution; auto-promote after N clean proposals, configurable).
- `memory.search` = primitive #2 filtered to `kind: note|topic|cache`.
- **Anti-rot (P3 risk):** decay arithmetic on `last_access`/`access_count` (MemoryBank); `superseded_by` chains; weekly compaction job (merge near-duplicate notes via sub-tier, human-reviewable diff); `memory/caches/<corpus>.md` = PEEK-style orientation cache with a **fixed token budget** and Distill→Cartograph→Evict maintenance for recurring contexts.
- `core-memory.md` — pinned page whose summary is *always* included in the metadata user message (MemGPT core block, minus tool calling): the instance's durable identity/preferences.

## 10. Seams left open (deliberately)

- `VaultStore` protocol (`get/put/list/search/reindex`) with a `LocalVault` implementation now; `HttpVault` later → the LAN memory-wiki service becomes a backend, absorbing the big spec's API design.
- `SearchBackend` protocol → vector tier, or Box-B-powered hybrid, later.
- Media/mounts/web UI/job queue (wiki spec §§5–7, 10): later phases **on top of the kernel** — mounts as helper pages (`mount_localtree.md`), ingestion as jobs calling `propose(...)`, the UI reading the same vault. The kernel does not need them to be useful.

## 11. Module map (new package, minimal edits to `rlm_local`)

```
src/rlm_kernel/
├── schema.py        # pydantic page/frontmatter models (schema: 1)
├── vault.py         # page CRUD, atomic writes, wikilink resolution, git batch-committer
├── index.py         # meta.sqlite + FTS5; build/rebuild/incremental
├── search.py        # BM25 cards, filters, SearchBackend protocol
├── seed.py          # first-run seeding of contract/ + builtin helper pages from package data
├── gate.py          # propose/validate/review/promote/demote state machine
├── repl_bridge.py   # definitions payload assembly; search/propose proxy handlers
├── optimize.py      # K4: GEPA runner, eval-suite loading, held-out gate, lineage
├── memory.py        # K3: note construction, decay, compaction, caches
└── cli.py           # rlm-kernel {init,index,review,promote,demote,optimize,search}
```

**Edits to `rlm_local` (kept minimal, backward-compatible):**
- `repl.py`: `init` accepts optional `helpers` payload; socket protocol gains `search`/`propose` proxy verbs; worker execs injected helpers.
- `prompts.py`/`templates.py`: become loaders with vault-first, package-fallback resolution.
- `root_loop.py`: when kernel present, pass `definitions` into `REPLSandbox.start`; include `core-memory` summary in the metadata message; one system-prompt line about `search`/`propose`.
- `subcall_manager.py`: use the existing `sub_model` param for task routing (no behavior change otherwise).

## 12. Testing strategy (TDD)

- **Unit:** schema validation (valid/invalid frontmatter matrices); atomic-write crash safety; wikilink resolution; index rebuild ≡ incremental (property test); gate state machine (property test: all proposal paths end promoted/rejected/quarantined, never callable-while-pending outside sandbox); decay arithmetic.
- **Contract:** REPL protocol (init with helpers → helper callable → same results as builtin); prompt assembly (helper added to vault → appears in system prompt next run; deprecated → disappears); search cards ≤400 chars; fallback parity (no vault → today's behavior, byte-identical prompts).
- **Integration:** `FakeModelServer` (from the existing test suite) + fixture vault; end-to-end: model proposes a helper in a completion → quarantine → validate → promote → next completion uses it; memory add → note page → searchable; optimize dry-run against fake metric promotes only on held-out win.
- **Load gate (K0 acceptance):** 100K synthetic pages → reindex < 2 h on the target laptop; search p95 < 300 ms; `git status` < 2 s.
- **Determinism:** no real LLM, network, or clock in tests (fake backend + seeded vault + frozen time).

## 13. Phased roadmap (each phase shippable, TDD'd)

| Phase | Deliverable | Acceptance |
|---|---|---|
| **K0** | schema, vault, index, search, seed, CLI; package-fallback loaders in `prompts.py`/`templates.py` | load gate §12; existing `rlm_local` tests green (fallback parity) |
| **K1** | repl_bridge + `repl.py`/`root_loop.py` edits; prompt assembly from pages; builtin helpers migrated to pages | contract tests; helper added/edited in vault changes behavior next run |
| **K2** | the gate (propose/validate/review/promote) + CLI review flow | property tests; e2e propose→promote→use cycle |
| **K3** | memory notes, decay, compaction, orientation caches, core-memory | integration tests; anti-rot compaction on a 5K-note fixture |
| **K4** | optimize.py (GEPA) + eval suites + few-shot bootstrap | dry-run gate test; one real overnight run report (before/after on held-out) |
| **K5 (later)** | `HttpVault` backend → LAN service; then media/mounts/UI per the north-star spec | seam demo: two boxes, one vault |

**Sizing honesty:** K0–K2 are weeks, not months — that is the point of the re-scope. K3–K4 each stand alone and can ship in either order. K5 reconnects to the full vision.

## 14. Risks

1. **Root-model discipline**: a 4B may underuse `search`/`propose` — mitigate via the prologue line + a worked few-shot that *uses* `search` (content, so K4 can optimize it).
2. **Gate fatigue**: too many pending proposals → auto-promotion rules per kind (notes first) + validation strictness knobs.
3. **Self-description drift**: contract pages edited beyond model comprehension — eval gate (K4) is the guard; `contract_version` migrations are human-only.
4. **Image rot**: §9 measures are heuristics; accepted — the research shows no system solves consolidation; the human-readable vault keeps manual cleanup cheap.
5. **Scope creep back toward the mega-spec**: every deferred feature must enter as *content or a K5+ phase*, never as kernel edits (P7 discipline).

---

## Appendix A — The frozen contract v0.1 (summary)

Socket verbs: `exec, init(context, helpers[]), shutdown, subcall, subcall_batched, search, propose`. Answer: `answer["content"]`, `answer["ready"]=True` inside code. Page schema keys: §3.2 (`schema: 1`). Builtin helper signatures: `peek(n)`, `grep(pattern, max_hits)`, `chunk(size, by)`, `map_query(items, template, batch)`, `show_vars()`, `search(query, k, detail)`, `propose(kind, name, body, rationale)`. Template names: as in `templates.py` today. Gate states: `pending → validated → promoted | rejected`; `active → deprecated → superseded`.

## Appendix B — What the research says this gets us

LISP's homoiconicity (text universality) and metacircular eval (REPL contract as content); Smalltalk's image (the vault) minus its unreadability; Forth's dictionary (helpers as pages, user ≡ builtin); Nock's frozen core (Appendix A) with a jets hatch (host-side helpers validated against REPL semantics); Unix's pipe discipline (REPL variables, bulk out of the prompt); the 2026 compounding pattern (persistent artifact × retrieval × gate: Skills/LATM/GEPA/DGM); GEPA as the affordable mutation operator (overnight, local); PEEK as bounded orientation memory. Nothing in this kernel is speculative beyond what those receipts show — the speculative part (small models holding the root role) is already this project's daily bread, and K4's eval suites are how we *measure* it improving.
