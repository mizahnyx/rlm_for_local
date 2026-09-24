# Gate 1: the harness against the memory-wiki spec, and where a local OpenWiki fits

**Date:** 2026-09-24 (the filename continues the series' ordering; the machine clock runs behind it,
as `docs/20260923-1800-…` records). **Status:** reconciliation and framing — no decision taken, no
code written.

The decision this serves, in the owner's terms: whether the wiki is built **per its spec**,
**harness-first**, or **adopted** — the third arm exists because the owner supplied
[LangChain OpenWiki](https://docs.langchain.com/oss/openwiki/quickstart) as a **local** instance, and
supplied [T-Mem](https://arxiv.org/abs/2606.15405v2), [Laya](https://github.com/afshinm/laya-mps),
[memanto](https://chat.mcp.so/server/memanto/Moorcheh-ai),
[MindCache](https://github.com/faisalhussain-devs/MindCache), sparse distributed memory
([Attention Approximates SDM](https://proceedings.neurips.cc/paper/2021/file/8171ac2c5544a5cb54ac0f38bf477af4-Paper.pdf),
NeurIPS 2021) and the [CoALA](https://ar5iv.labs.arxiv.org/html/2309.02427) vocabulary as the frame.

## 1. The reconciliation, in one line and one table

**5 EXISTS · 18 PARTIAL · 15 ABSENT**, over the spec's 38 capabilities. The table below is the
capability-by-capability reading; §2 gives the three gaps that matter structurally.

| Spec | Capability | Verdict | Evidence / gap |
|---|---|---|---|
| §1–2 | `rlm-wikid` daemon, Box A/B split | **ABSENT** | no wiki process, no systemd unit; one host runs harness + kernel |
| §3.1 F1 | Wiki as source of truth | **PARTIAL** | `schema.Page/Frontmatter`, `vault.LocalVault` (atomic writes, git, `resolve_wikilink`); no `sources/ media/ topics/ tools/` |
| §3.2 N4 | Human-readable first | **EXISTS** | Markdown + frontmatter on disk; `rlm-kernel index` rebuilds from pages |
| §3.2 N3 | 500K pages / 1M CAS / p95 | **PARTIAL** | `tests/load/test_load.py`, `gen_corpus.py`; no CAS, no hybrid envelope |
| §3.2 N5 | TDD, typing, lint, ADRs | **PARTIAL** | broad suite, `ruff`, the mutation table; no mypy, coverage gate or ADRs |
| §5.1 | Vault layout | **PARTIAL** | `<kind>/<name>.md` under one root; no `cache/ tools/ attachments/ objects/` |
| §5.2 | Page anatomy | **PARTIAL** | id, kind, name, title, summary, tags, version, hash, status, timestamps, access_count; no `type: source/media/topic`, `source_uri`, `links`, `media[]`, `confidential`, `enrich_state` |
| §5.3 | CAS `objects/ab/cd/<sha256>` | **ABSENT** | no `cas.py`, object layout, refcount, eviction or streamed blobs |
| §5.3 | content-addressed **cache** | **EXISTS (substitute)** | `mine.DerivationCache`: sha256(source hash+version+params), atomic, 0600, space-checked, engine-tagged — text only |
| §5.4 | batched committer, fsmonitor, sweep | **PARTIAL** | git per put; no debounce, fsmonitor, untrackedCache, commit-graph, startup sweep |
| §5.5 | derived indexes | **PARTIAL** | `index.py` (pages/links/tags, `get_backlinks`), `textindex.py` (FTS5, BM25, provenance, coverage); no fts/vec split, trigram, embeddings |
| §6.1 | `MountProvider` protocol | **PARTIAL** | `mounts.LocalTreeMount`: stat/open_readonly/read/iter, containment, **no write verb**, `assert_derived_outside_corpus`; no `mount://`, `fetchable()`, lazy fetch |
| §6.2 | v1 providers | **ABSENT** | local tree only |
| §7.1 | acquisition paths | **PARTIAL** | bulk only (`mine plan`); no push endpoint, scheduler, manual capture |
| §7.2 | per-type extraction | **PARTIAL** | pdftotext, zip+XML, zip/tar/libarchive; no HTML, OCR, image, audio, video |
| §7.3.1 | model classify | **ABSENT** | `classify.py` is a byte sniffer, not an LLM classifier |
| §7.3.2 | summarize (map-reduce) | **PARTIAL** | `task_summarise` (injected engine, 32 KiB/400 tok, cached, indexed `ORIGIN_CACHE`), `rlm summarise --cited-only`; no map-reduce |
| §7.3.3–4 | tag/link suggestion, wikify | **ABSENT** | **no clustering, no page generation, no link suggestion anywhere** (see §3) |
| §7.3.5 | embed | **ABSENT** | not found in `src/` |
| §7.4 | durable queue, resume, idempotency | **PARTIAL** | see §3 |
| §7.4 | windows, self-tuning, backpressure | **ABSENT** | `run_queue` takes budget/deadline only |
| §8.1 | task-typed models, breaker | **PARTIAL** | `model_backend`, `Profile`, `engine_tag_for`; no task table, backoff or breaker |
| §8.2 | media upload | **ABSENT** | VLM/ASR/OCR are names with no handler |
| §8.3 | federation / llama-swap | **ABSENT** | local router only; `assess_router_models.py` screens |
| §9.1 | lexical retrieval, compact cards | **EXISTS** | `corpus.CorpusBridge` BM25 + bands + vendored count + coverage; `_make_card` ≤400 chars |
| §9.1 | hybrid fusion, decay, filters | **ABSENT** | no RRF, trigram, vectors, age decay or confidentiality filters |
| §9.2 | HTTP `/api` surface | **ABSENT** | see §2 |
| §9.3 | `rlm_local` contract | **ABSENT** | `memory.*`, `wiki.*`, `tools.*`, `cache.get` absent; substitute is the `corpus_*` helpers + `KernelBridge` |
| §9.4 | tool pages + quarantine gate | **PARTIAL** | `gate.propose/validate/promote/reject/demote`, AST allowlist; no `tools/*.md` schema |
| §9.4 | authored extractors | **ABSENT** | no sandbox, fixture run or run audit; `gate.validate` says itself "a quality gate, not containment" |
| §10 | web UI | **PARTIAL** | console exists (see §2); no editing, backlinks, graph, review queue |
| §11 | security and privacy | **PARTIAL** | session cookie, origin check, loopback, read-only mount, `check_privacy.py`; no bearer auth, egress allowlist, hash-chained audit |
| §12 | config, ops, observability | **PARTIAL** | profiles + TOML, uvicorn app; no wiki config schema, units, `/metrics` |
| §13.1 | test pyramid | **PARTIAL** | broad unit suite; no API contract tests, no 200-item E2E |
| §13.2 | fakes, fixtures, property tests | **ABSENT** | one `MockTransport`; no `hypothesis`, no fixtures tree |
| §13.2 | load tests outside CI | **EXISTS** | `tests/load/`, `scripts/run_load_gate_100k.py` |
| §13.3 | mutation spot-checks | **EXISTS** | `check_guard_nonvacuity.py` — **289 guards, 0 problems** |
| §13.4/§14 | milestone gates M0–M8 | **ABSENT** | the roadmap's `RO*` ledger plays that role |

## 2. The three gaps that decide the shape

1. **No service, no API.** There is no `rlm-wikid` and **not one `/api` route**; `src/rlm_web/app.py`
   is an HTML/Jinja2 + SSE console over the vault (auth by session cookie, origin-checked POSTs,
   read-only page view, Markdown ingest, job list with SSE). §9.2 is absent rather than partial.
2. **No generation stage.** Nothing clusters documents, emits pages or suggests links. What exists is
   generation's *input*: the injected-engine summariser, the served/cited value ranking in
   `enrich.py`, and per-call cost accounting.
3. **The durable store is a mining queue, not the spec's job machine.** `mine_queue` is genuinely
   crash-safe — per-item commit, `INSERT OR IGNORE` enqueue, content-hash idempotency keys, and an
   mtime-heartbeat lock (never `os.kill`, guard-tested) — but it has four states rather than eight,
   no checkpoints, no `jobs` table, no windows or backpressure. No CAS, no embeddings, no index
   split, no `mount://` provider layer.

## 3. The adopt arm, and what it collides with

A **local** OpenWiki instance is the closest existing product to RO7: wiki generation from a corpus,
code and personal modes, providers, incremental updates, a CLI and a visualizer. Adopting it is a
real third arm, and it collides with four things in this project — in order of severity:

1. **§1.9 is absolute.** Corpus-derived text must not leave the machine. The OpenWiki docs offer
   tracing and provider integrations, so a local deployment would have to be *proved* to make no
   outbound calls, with telemetry disabled — a precondition to test, not a preference to assume.
   This is the one collision that can rule the arm out outright.
2. **A second writer of derived state.** The harness and OpenWiki would both write, with different
   lock disciplines; this project has already paid once for "a window reported itself free while it
   was still counting". Single-writer, or shared lock and pause flag.
3. **Cost profile.** OpenWiki is built around API-scale generation; our arithmetic is **5.0–29.6
   minutes per fresh document** (measured cold, `20260923-2100`) and 10–65 minutes per question.
   Anything it generates, it generates at that speed.
4. **Authority.** A generated page is rewritten prose. If the LTM becomes what the model reads,
   answers will cite pages, and the citation chain ends at a paraphrase — the failure this project
   ranks below silence.

## 4. Write-time versus read-time — the frame the owner's material supplies

Today's harness is almost entirely **read-time**: search, bands, cards, citations, coverage. Its one
write-time artefact is the description cache (`rlm summarise`), and it has exactly one level of
contraction — 400 tokens. There is **no expander and no second contraction level**.

The literature the owner supplied sharpens why that matters. T-Mem's finding is that long-term
memory built on query-memory similarity is *reachability-bounded* and misses associative recall — the
fix is **write-time rehearsal**, not better reading. CoALA's split (working memory; LTM as
episodic/semantic/procedural) is the vocabulary for what a wiki is. MemGPT's paging is the vocabulary
for budgets. Attention-approximates-SDM is the reason an external store is not optional: attention is
a soft, capacity-bounded read, not a memory. A local OpenWiki supplies the wiki; **nothing in the
stack as described does the write-time rehearsal**, and that is the gap the literature names.

## 5. Where Laya fits, and what is unmeasured

Laya is a **typed-decision** model (`choice`, `score`, `noul`), non-autoregressive, Apache-2.0
weights, a plain `transformers` model rather than an Apple-only runtime — published at ~843 MB with
0.74 GiB / 171 ms (minimal) up to 2.30 GiB / 32 ms (full) **on an M5 Pro GPU**; CPU figures are
unpublished, and the ONNX path already exists in the ecosystem
([receptron/laya](https://github.com/receptron/laya), [open-jev-laya](https://github.com/killkli/open-jev-laya)).
Two constraints shape any use: **1 024 formatted tokens per question** (over-long input is rejected,
not truncated) and **one inference at a time**. The first is compatible with our shapes — decisions
would run over ≤400-character cards, which we already produce.

As a context controller its failure mode is the one this project has measured before: the density
measurement showed "prefer prose" is **not** the lever (prose 1.05 vs everything else 0.98 mean
band). A decision model needs a signal; in shadow mode against heuristics is the only honest way to
start. **The feasibility measurement needs the box, so it is staged and not run** — script ready at
`.tmp_probe/laya_feasibility.sh`.

## 6. The forks (owner's calls, not taken)

1. **Per spec / harness-first / adopt** — with the adopt arm now real, and its §1.9 precondition
   testable.
2. **If there are two write paths, where does the gate sit?** Today the gate fronts one.
3. **What is the LTM authoritative for?** A cache of interpretation with pointers back to source
   (my read of the constraints) — or, as written in its own spec, "wiki as source of truth".

## 7. How much of this I verified myself

I checked the fragment's load-bearing claims against the code rather than trusting the summary: the
**`/api` claim** (all 15 routes in `app.py` are console routes — zero `/api`), the **clustering
claim** (only `memory._cluster_notes`, merging existing notes), the **no-CAS claim** (no `objects/`
layout, no refcount), and the **`PageKind` claim** (`contract, template, definition, helper, fewshot,
note, topic, cache` — `topic` exists, `source` and `media` do not). The remaining per-module rows are
a subagent's reading, not mine, and are marked as such.

Unverified: everything requiring the corpus (all live figures are recording-derived), Laya on this
CPU, OpenWiki's outbound behaviour, and whether the spec's eight-state pipeline would earn its
complexity over `mine_queue`.
