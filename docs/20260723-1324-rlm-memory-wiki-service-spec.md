# RLM Memory Wiki Service — System Specification

**Working name:** `rlm-wiki` (daemon: `rlm-wikid`)
**Date:** 2026-07-23
**Status:** Ready for implementation (hand to executor agent)
**Companion documents:** `20260721-1034-rlm-harness-small-models-design.md` (harness design), `20260722-2118-rlm-memory-retrieval-research.md` (memory/retrieval evidence), `20260722-2118-rlm-capability-extension-research.md` (tools/security evidence). Technology choices below are verified against the July 2026 research round; residual uncertainties are flagged **[VERIFY]** with a load-test or prototype task.

---

## 1. Purpose

`rlm-wiki` is a **personal, fully-local, long-term memory service** for the `rlm_local` RLM harness. It ingests hundreds of thousands of heterogeneous items (texts, PDFs, office docs, audio, video, web scrapes), digests them into a **human-readable markdown wiki**, and serves hybrid search + memory operations over HTTP to the harness — so the user can "talk and reason with their data," including data **too confidential for any external service**. Everything runs on two LAN boxes; no cloud dependencies anywhere.

## 2. Deployment context (user-confirmed, 2026-07-22/23)

| Fact | Decision |
|---|---|
| Box A (workstation) | Runs `rlm_local` and **all LLM inference** for now (llama-server fleet with tuned configs). Evolution path: federated, load-aware routers on both boxes (§7.3). |
| Box B (old HP laptop, Arch Linux, 16 GB RAM, Intel iGPU, no discrete GPU) | Runs `rlm-wikid`, all storage, indexing, ingestion orchestration. **No LLMs resident** (for now); it is an inference *client* of Box A over LAN. |
| Wiki access | Obsidian-flavored interlinked markdown graph + a **built-in lightweight web UI** (no SMB share required). |
| Mounts | A **uniform provider interface** (VFS-like, fsspec-based) — "feels like mounts," **not** OS-level mounts. |
| Ingestion window | Overnight batch work; **pauseable/resumable over weeks-to-months**. |
| Capability indexing | Tools/extractors are documented as wiki pages and indexed like the rest of the corpus. |

## 3. Requirements

### 3.1 Functional

- **F1 — Wiki as source of truth.** All digested knowledge lives in a git-versioned tree of Obsidian-flavored markdown pages (wikilinks, `#tags`, YAML frontmatter). Machine indexes are derived and rebuildable from the pages.
- **F2 — Multi-source ingestion.** (a) Human-in-the-loop browser scraping: a userscript the user runs on X/Twitter, Instagram, etc., streaming captured items (favorites, threads, quotes, videos) to the service. (b) Bulk import of preexisting backup corpora (directory trees of texts/PDFs/audio/video, informative or artistic). (c) Scheduled/ad-hoc pulls (yt-dlp, gallery-dl). (d) Manual note creation/edit via web UI.
- **F3 — Media handling.** Audio → transcript; video → keyframes + transcript + visual description; images → caption/OCR; PDFs/office/HTML → clean markdown. Media files stored in a content-addressed cache; derived text summarized and classified into wiki pages.
- **F4 — Cache namespace.** A designated `cache/` vault namespace holds raw source captures (text-only or text+media refs) with provenance frontmatter.
- **F5 — Mount abstraction.** Every external source/extraction method is a `MountProvider` with a uniform `list/read/stat` interface and **lazy, cached fetch** (fetch-on-read into the CAS).
- **F6 — Resumable indexing.** All ingestion/enrichment work is a durable, checkpointed job queue: pause/resume/stop, idempotent re-entry, crash-safe, schedulable in nightly windows, horizon of months.
- **F7 — Retrieval API.** Hybrid search (lexical + semantic + filters), page fetch, memory add/search/note, and capability (`tools`) search — consumed by `rlm_local` REPL helpers.
- **F8 — Capability registry.** Tools, extractors, and helpers are documented as wiki pages in a `tools/` namespace, indexed with everything else; LATM-style **LLM-authored extractors** persist through a quarantine→review→promote pipeline.
- **F9 — Model access by task type.** All inference is addressed by *task* (`summarize`, `transcribe`, `vision-caption`, `embed`, `chat-sub`…), resolved to concrete models by configuration — so topology changes (Box A today → federated later) never touch pipeline code.
- **F10 — Web UI.** Lightweight: browse/read pages, edit markdown, search, backlinks, link graph, job dashboard.

### 3.2 Non-functional

- **N1 — Privacy.** No external network egress from the service except explicit, configured mount fetches. All inference on LAN. Data confidentiality is a hard requirement.
- **N2 — Modest hardware.** Box B: 16 GB RAM, CPU-only. Service RSS budget ≤ 2 GB steady-state (indexes mmap'd). Box A does all inference; Box B never loads an LLM in v1.
- **N3 — Scale.** 500K wiki pages, 1M CAS objects, 5 TB media as design envelope. Search p95 < 300 ms for lexical, < 1 s for hybrid at 500K pages **[VERIFY via load test M2]**.
- **N4 — Human-readable first.** A person with a markdown editor can navigate and edit the vault without the service running; the service repairs/rebuilds its indexes afterward.
- **N5 — TDD & best practices.** Test-first development, ≥85% coverage on core packages, deterministic tests (fake model server), typed Python (mypy), lint (ruff), conventional commits, ADRs for structural decisions.

---

## 4. Architecture overview

```
┌─────────────────────────── Box A (workstation) ───────────────────────────┐
│ rlm_local harness ──► REPL helpers (memory/wiki/tools) ──┐                │
│ llama-server fleet (chat 4B/8B, embed, Qwen-VL, whisper) │ (later:        │
│                   ▲                                      │  llama-swap    │
└───────────────────┼──────────────────────────────────────┼────────────────┘
                    │ OpenAI-compatible HTTP, LAN          │
┌───────────────────┼──────────────────────────────────────┼────────────────┐
│                   ▼                                      │   Box B (Arch) │
│  ┌─────────────────────────────────┐                     │                │
│  │ ModelClient (task-typed models) │◄────────────────────┘                │
│  └─────────────────────────────────┘                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Ingestion    │  │ Job queue     │  │ Mount VFS    │  │ Retrieval    │  │
│  │ pipelines    │─►│ (SQLite,      │─►│ (fsspec      │─►│ engine       │  │
│  │ (per-type)   │  │  resumable)   │  │  providers)  │  │ (FTS5+vec    │  │
│  └──────────────┘  └───────────────┘  └──────────────┘  └──────────────┘  │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │ Vault (git): pages/ CAS objects/  SQLite indexes (derived)         │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│  FastAPI service: /api/wiki /api/memory /api/search /api/tools            │
│                   /api/ingest /api/jobs /api/ui (web UI)                  │
└───────────────────────────────────────────────────────────────────────────┘
```

Modules (Python package `rlm_wiki`, src layout, Python ≥3.12):

| Module | Responsibility |
|---|---|
| `vault.py` | Page model, wikilink/frontmatter parsing, atomic writes, path rules, git versioning |
| `cas.py` | Content-addressed store for media/raw blobs |
| `index.py` | SQLite meta + FTS5 + vector indexes; build/rebuild/incremental update |
| `search.py` | Hybrid retrieval (BM25 ∪ trigram ∪ vector, RRF fusion, filters) |
| `mounts/` | MountProvider interface + providers (localtree, yt-dlp, gallery-dl, push-buffer, http) |
| `ingest/` | Per-type extraction pipelines (text, pdf, office, html, image, audio, video) |
| `enrich.py` | Classify → summarize → tag → wikify → link (LLM jobs via ModelClient) |
| `jobs.py` | Durable job queue, item state machine, scheduler windows, pause/resume |
| `modelclient.py` | Task-typed OpenAI-compatible client (retries, timeouts, circuit breaker) |
| `tools.py` | Capability registry: tool pages, `search_tools`, `save_extractor` pipeline |
| `api.py` | FastAPI surface (REST + auth) |
| `webui/` | Server-rendered UI (Jinja2 + htmx + vis-network graph) |
| `config.py` | TOML config, profiles, secrets refs |
| `audit.py` | Append-only audit log |
| `push.py` | Userscript generator + push-buffer endpoints |

---

## 5. Storage architecture

### 5.1 Vault layout (source of truth)

```
$VAULT_ROOT/
├── .git/                        # markdown-only repo (§5.4)
├── pages/
│   ├── notes/                   # atomic knowledge notes (A-MEM-style, §6.4)
│   ├── sources/                 # one page per ingested source item
│   ├── media/                   # one page per media object (caption, transcript, classification)
│   ├── topics/                  # MOCs ("maps of content"), human+model curated
│   ├── people/, places/, projects/   # optional entity namespaces
│   └── journals/                # optional date-based capture log
├── cache/                       # F4 cache namespace: raw captures (text extracts + media refs)
│   └── <mount>/<path-derived>.md
├── tools/                       # F8 capability pages (extractors, helpers, REPL functions)
├── attachments/                 # curated media copied INTO the vault for UI rendering
│   └── ab/cd/sha256.ext         # (excluded from git)
├── objects/                     # CAS: ab/cd/<sha256>  (bulk media; excluded from git)
└── index/
    ├── meta.sqlite              # pages, links, jobs, objects, mounts tables
    ├── fts.sqlite               # FTS5 external-content indexes
    └── vec.sqlite               # sqlite-vec embeddings (int8)
```

### 5.2 Page anatomy

Every page is markdown with YAML frontmatter. Example (`pages/sources/2026-07-22-x-thread-1715.md`):

```markdown
---
id: 01JZK4…                 # ULID, stable forever
type: source                # note | source | media | topic | tool | cache
title: "Thread: small-model agent evals"
created: 2026-07-22T21:03:11+02:00
updated: 2026-07-23T09:14:00+02:00
source_uri: mount://twitter/thread/171500…
content_hash: sha256:9f2c…  # hash of canonical source text (dedup key)
tags: [llm, agents, evals]
links: [[small-model-tool-use]], [[eval-methodology]]
media: []                   # refs to media pages / CAS hashes
confidential: true
summary_model: "task:summarize@2026-07-23"
enrich_state: done          # pending|partial|done (see jobs)
---
# Thread: small-model agent evals

> [!summary]
> …(3–8 sentence LLM summary, bounded)…

## Key points
- …

## Source excerpt
(first ~2K chars of extracted text; full text in cache page/CAS)
```

Rules: filenames are `slug(-hash6).md`, lowercase, collisions resolved with the ULID suffix; pages are written **atomically** (tmp + fsync + rename); frontmatter schema is validated by `vault.py` (pydantic models); `[[wikilinks]]` are resolved case-insensitively by basename with a `path → id` map in `meta.sqlite`.

### 5.3 Content-addressed store (`cas.py`)

- Layout `objects/<hex[0:2]>/<hex[2:4]>/<sha256>` (restic/git pattern); atomic write (tmp+rename); dedup free by construction.
- `meta.sqlite.objects(hash, size, mime, created, source_uri, refcount, last_access)`.
- Two residence tiers: **CAS** (bulk, everything fetched) and **attachments/** (curated copies the UI renders inline; promoted by pipeline or user). Eviction (LRU, size cap from config) applies **only to mount-fetched objects marked re-fetchable**; user-owned imports are never evicted (`pinned: true`).
- Large-file support: streamed writes, no full-file buffering (Box B has 16 GB but files may be multi-GB video).

### 5.4 Versioning

- Single git repo covering `pages/` and `tools/` (text only). `objects/`, `attachments/`, `index/` excluded via `.gitignore`.
- **Batched commits**: a debounced committer (default every 120 s of changes or 500 dirty files, and on job-window end) with message `wikid: N pages updated (job J-id)`; `core.fsmonitor=true`, `core.untrackedCache=true`, `commit-graph` on. GitHub's sparse-index data shows the index dominates at ~2M files; at 500K text files with fsmonitor this stays sub-second **[VERIFY at M1 load test]**.
- Human edits via external editors are first-class: a `git status` sweep on service start reconciles external changes into the indexes (hash-compare → reindex delta).
- If git proves inadequate at scale, ADR fallback: mtime+hash journaling in `meta.sqlite` (no branch/merge). Media versioning is deliberately out of scope (CAS is immutable).

### 5.5 Derived indexes (`index.py`) — always rebuildable

- `meta.sqlite` (SQLAlchemy-free, stdlib `sqlite3`, WAL mode): `pages(id, path, type, title, hash, created, updated)`, `links(src_id, dst_id, kind)`, `tags(page_id, tag)`, `objects(…)`, `jobs(…)`, `mount_items(…)`, `embeddings_meta(page_id, model, embedded_at)`.
- `fts.sqlite`: two external-content FTS5 tables — `fts_pages` (unicode61, token search, BM25) and `fts_pages_tri` (trigram, substring search; `detail=column` to bound size). Content provider = `pages` text; `INSERT … rebuild` supported; incremental triggers replaced by explicit index-queue writes from `vault.py` (simpler than triggers, testable).
- `vec.sqlite`: [sqlite-vec](https://alexgarcia.xyz/sqlite-vec/features/knn.html) int8-quantized embeddings of **page summaries + titles only** (not full bodies — bounds vectors to ≤1/page ≈ ≤500K, brute-force KNN ≈ tens of ms at 100K / ~1 s at 500K int8 **[VERIFY]**; fallback ADR: hnswlib sidecar). sqlite-vec is pre-v1 — pin version, encapsulate behind `VectorStore` protocol.
- **Embedding policy**: embed lazily at enrich time via `task:embed` on Box A; if A is unreachable, index degrades to lexical-only (flagged in health endpoint).

---

---

## 6. Mount subsystem (`mounts/`) — sources as uniform, lazily-cached namespaces

Per the user's clarification: mounts are a **common interface**, not OS mounts. Implementation: [fsspec](https://filesystem-spec.readthedocs.io/en/latest/features.html)-style provider protocol (fsspec is active and ships exactly the caching semantics needed — `filecache`, `simplecache`, DirCache); we implement the protocol subset we need rather than depending on all of fsspec **[decision: dependency-light; ADR if full fsspec adopted]**.

### 6.1 Provider protocol

```python
class MountProvider(Protocol):
    scheme: str                       # "localtree", "twitter", "yt", "gdrive"…
    def list(self, path: str) -> Iterable[MountItem]: ...   # lazy listings (DirCache'd)
    def stat(self, path: str) -> MountItem: ...             # size, mtime, hash?, mime
    def read(self, path: str) -> BinaryIO: ...              # FETCHES on demand
    def fetchable(self, path: str) -> bool: ...             # can re-fetch later?
```

- URIs: `mount://<scheme>/<provider-specific-path>` (e.g. `mount://localtree/backups/2023/photos/img.jpg`, `mount://twitter/favorites/1715…`).
- **Lazy caching semantics** (rclone `--vfs-cache-mode full` inspired): `read()` → CAS lookup by `(scheme,path,etag/mtime)` → on miss, stream-fetch → CAS put → return handle. Listings cached in `meta.sqlite.mount_items` with TTL. Cache state machine per item: `ABSENT → FETCHING → CACHED → STALE → (evictable if fetchable)`.
- All provider I/O is sandboxed by egress policy (§11.4): only configured allowlisted hosts.
- Providers are **capabilities** (F8): each is documented by a `tools/mount-<scheme>.md` page and indexed.

### 6.2 v1 providers

| Provider | Backing | Notes |
|---|---|---|
| `localtree` | directory walk of backup drives/NAS mounts on Box B | hash-on-demand (xxhash for pre-filter, sha256 canonical); `fetchable=true` |
| `push-buffer` | items arriving via `/api/ingest/push` (browser userscript) | not lazily fetchable (data already local); `fetchable=false`, `pinned=true` |
| `yt` | [yt-dlp](https://github.com/yt-dlp/yt-dlp) CLI wrapper | X/Twitter works unauthenticated (guest tokens); Instagram needs `--cookies-from-browser` |
| `gallery` | [gallery-dl](https://github.com/mikf/gallery-dl) CLI wrapper | image sets/profiles; cookies via browser extraction |
| `http` | single-file HTTP(S) GET with etag/last-modified | allowlisted hosts only |

Out of scope for v1 (deferred): Playwright `storage_state` scraping (ban risk; last resort), OS-level mounting (rclone+WinFsp irrelevant on Arch; a `rclone serve webdav` bridge may be added later if desktop apps need file access).

---

## 7. Ingestion pipeline (`ingest/`, `enrich.py`, `jobs.py`)

### 7.1 Acquisition paths

1. **Browser push (human-in-the-loop scraping).** The service generates a **Tampermonkey/Violentmonkey userscript** (`push.py`) with embedded API token + target endpoint. The user runs it on a logged-in site (x.com, instagram.com); the script walks the visible DOM (favorites, threads, quotes, video URLs) and POSTs batches to `POST /api/ingest/push`. Userscripts via `GM_xmlhttpRequest` bypass page CORS — the established pattern (Karakeep extension → localhost POST) **[verified 2026-07]**. Protocol:
   ```json
   {"source": "x.com/favorites", "cursor": "opaque", "items": [
      {"kind": "thread|tweet|quote|video_url", "html": "…", "text": "…",
       "url": "…", "author": "…", "ts": "…", "media_urls": ["…"]}]}
   ```
   Server: dedup by `sha256(url + text)`, stores raw into `cache/<source>/…`, enqueues enrichment, returns `{accepted, dupes, next_cursor}`. Video/media binaries are **not** pushed; their URLs become `mount://yt/…` items fetched lazily.
2. **Bulk import**: `rlm-wiki import /path --mount localtree` registers a tree; items flow through the queue lazily (nothing copied up front).
3. **Scheduled pulls**: cron-like jobs (`yt-dlp <list>`, gallery-dl profiles) with cookies sourced from a configured browser profile path (documented; user-owned).
4. **Manual**: web UI "new note/page" + file upload (→ CAS + media pipeline).

### 7.2 Per-type extraction (verified tool choices, July 2026)

| Input | Pipeline | Tools (verified) |
|---|---|---|
| Plain text/md | normalize, chunk | stdlib |
| HTML (scrapes, bookmarks) | readability → markdown | **trafilatura** 2.1 (Apache-2.0; best-in-benchmark) |
| PDF born-digital | text layer → markdown | **pymupdf4llm** (fast; AGPL-3.0 — fine for local use) |
| PDF scanned/complex | layout+OCR → markdown | **Docling** v2 (MIT, CPU-capable; EasyOCR/RapidOCR pluggable) — fallback tier, seconds/page on CPU **[VERIFY throughput]** |
| Office/EPUB/.msg | convert → markdown | **markitdown** (MIT; docx/pptx/xlsx/epub/msg), **pandoc** for tricky docx/epub; mbox via stdlib `mailbox`; PST via libpst **[VERIFY]** |
| Images | caption + OCR + EXIF | `task:vision-caption` (Qwen2.5-VL GGUF on Box A via llama-server mtmd **[verified supported]**); PaddleOCR PP-OCRv6 CPU where pixel-level OCR needed |
| Audio | VAD chunk → transcribe → diarize(optional) | `task:transcribe` on Box A: **faster-whisper int8 small/medium (~3–6× realtime CPU)** or sherpa-onnx Parakeet int8; silero-vad; whisperX for diarization |
| Video | keyframes + audio track → caption frames → merge | ffmpeg scene-detect (`select='gt(scene,…)'`) → frames → `task:vision-caption` + audio → `task:transcribe` → merge via `task:summarize` |

Artistic/pleasurable media (music, films): same pipelines, but classification favors mood/genre/subject tags over transcript detail; music = metadata + short description (no lyrics transcription by default).

### 7.3 Enrichment (`enrich.py`) — bounded LLM jobs only

Per item, after extraction (all LLM calls are `ModelClient` task calls with strict token caps; none run on Box B):

1. **Classify** (`task:classify`, few-shot, JSON-ish output, rescue-parsed): `{domain, kind: informative|artistic|personal|reference, topics[≤5], confidentiality_guess}`.
2. **Summarize** (`task:summarize`): short docs → one call; long docs → **map-reduce**: chunk (default 8K chars) → per-chunk notes (batched) → reduce to 3–8 sentence summary. Aggregates computed programmatically, never in prose (harness spec §8).
3. **Tag/link**: candidate `[[links]]` = top-k hybrid search over existing titles/summaries; link insertion is **suggestive** (frontmatter `links:` + "Related" section) — never destructive to page body.
4. **Wikify**: emit source page (`pages/sources/…`), cache page (raw extract in `cache/…`), media page(s) for each attachment (`pages/media/…` linking CAS hash + caption/transcript), and optionally update/create **atomic notes** (`pages/notes/…`) when the source adds durable knowledge (A-MEM-style note construction, the one LLM-judgment step — bounded prompt, evidence: viable at 1.5–3B).
5. **Embed** (`task:embed`): title+summary → vec index.
6. **Mark `enrich_state=done`**; index entries flushed.

### 7.4 Job queue and the months-long horizon (`jobs.py`)

- **Durable queue in `meta.sqlite`**: `jobs(id, kind, payload_json, priority, state, run_after)`, `items(job_id, uri, state, attempt, last_error, checkpoint_json)`.
- **Per-item state machine**: `discovered → cached → extracted → classified → summarized → wikified → indexed → done` (plus `failed`, `skipped_dup`, `deferred`). Each transition is a pure function writing its outputs + checkpoint atomically; **any item can be re-entered at any state** (idempotency keys = content hashes).
- **Pause/resume**: `POST /api/jobs/{id}/pause` checkpoints in-flight items (chunk-level for map-reduce); service stop/start resumes from `meta.sqlite` — a kill -9 mid-item loses at most one item's current state; months-long jobs are first-class.
- **Scheduling windows**: config `[[schedule]]` (e.g. `nightly = "23:30–06:30"`, `weekend_all_day`), CPU niceness + `ionice`, max parallel items (default 1–2 on the HP laptop), throughput self-tuning (EWMA of items/h; back off on thermal/load — read `/proc` loadavg).
- **Backpressure with Box A**: ModelClient concurrency caps + 429-aware retry (llama-swap semantics later; today llama-server queue). Ingestion must never starve interactive harness use — a **priority channel**: interactive `memory.*` calls preempt batch enrichment (weighted fair queuing at ModelClient).

---

## 8. Model access layer (`modelclient.py`)

### 8.1 Task-typed virtual models

All inference is addressed by task, never by model name:

```toml
# config.toml — v1: everything points at Box A
[models.endpoint]
base_url = "http://boxa.lan:8080/v1"
api_key  = "env:RLM_WIKI_MODEL_KEY"

[models.tasks]
chat_sub       = { model = "qwen3-4b-instruct",  max_tokens = 1024, temperature = 0.0 }
summarize      = { model = "qwen3-4b-instruct",  max_tokens = 768,  temperature = 0.0 }
classify       = { model = "qwen3-4b-instruct",  max_tokens = 256,  temperature = 0.0 }
vision_caption = { model = "qwen2.5-vl-7b",      max_tokens = 512 }
embed          = { model = "qwen3-embedding-0.6b" }
transcribe     = { model = "whisper-small-int8", endpoint = "http://boxa.lan:9000/v1" }  # speaches/whisper.cpp server
```

Client behavior: httpx with connection pooling; per-task timeout & retry (3×, exponential backoff, jitter); **circuit breaker** per endpoint (open after 5 consecutive failures → jobs degrade gracefully: embed/classify skipped, summarize retried next window); structured errors returned as strings (harness convention); request/response digests to audit log.

### 8.2 Media upload to Box A

Audio/video frames ship over LAN: `POST {endpoint}/v1/audio/transcriptions` (file, multipart) for whisper-family; vision captioning via chat-completions with base64 image parts (mtmd). Payload caps enforced (audio chunked by VAD to ≤10 min segments; frames ≤ 1536 px long edge).

### 8.3 Future: ModelRouter / federation (spec'd now, built later)

- **Phase 2 (load-aware routing on Box A)**: adopt [llama-swap](https://github.com/mostlygeek/llama-swap) (v241+, active; Windows/Linux/macOS binaries): on-demand process swap behind one OpenAI port, per-model `ttl`, `matrix` concurrency, hold-during-load (no 503 storms), `/v1/embeddings` + multimodal upstreams, **speaches** as OpenAI-compatible ASR upstream. Expected swap latency ~2–6 s NVMe **[VERIFY on Box A]**; config ships as `deploy/llama-swap.yaml`. `rlm-wikid` changes nothing — task table points at the router URL.
- **Phase 3 (federated)**: llama-swap on both boxes; a thin `router.py` in `rlm-wikid` (or the harness) picks per task: local-first, overflow to the other box; load signals from llama-swap `/running` + `/metrics`. This is the user's stated direction; the task-typed indirection makes it a config-and-router change only.

---

---

## 9. Retrieval engine and HTTP API

### 9.1 Hybrid search recipe (`search.py`)

```
candidates = FTS5_BM25(query, k=40) ∪ FTS5_trigram(substring_terms, k=20) ∪ vec_KNN(title+summary, k=40)
fused      = reciprocal_rank_fusion(candidates, k=60)
filtered   = WHERE type IN (...) AND tag IN (...) AND created BETWEEN ... AND confidential <= caller_level
ranked     = fused ⊕ recency/strength decay (MemoryBank arithmetic: score *= exp(-λ * age_days) * log(1+access_count))
return     = top k (default 5) as compact cards: {path, title, type, summary, tags, score, snippet}
```

All steps in stdlib-SQLite + numpy; no new services. Every returned card is small by contract (≤ 400 chars) — the harness's LID invariant depends on it. `access_count` updates are async (write-behind) so reads never block on writes.

### 9.2 API surface (FastAPI, `/api`, bearer-token auth, JSON)

| Endpoint | Purpose |
|---|---|
| `GET /api/wiki/{path}` | raw markdown + parsed frontmatter |
| `PUT /api/wiki/{path}` | create/update page (validated, atomic, git-tracked) |
| `GET /api/wiki/list?prefix=&type=&limit=` | path listing |
| `GET /api/wiki/backlinks/{path}` | inbound `[[links]]` |
| `POST /api/search` | `{query, k, filters, mode: hybrid|lexical|semantic}` → cards |
| `POST /api/memory/add` `{text, tags?}` | note-construction (sub-tier) → note page + index |
| `POST /api/memory/search` `{query, k}` | hybrid over notes (+sources if `include_sources`) |
| `POST /api/memory/note` `{chunk}` | explicit gist-to-note |
| `POST /api/tools/search` `{query, detail: name|desc|full}` | capability search (progressive disclosure; capability report §6.1) |
| `GET /api/tools/doc/{name}` | full tool page |
| `POST /api/tools/save_extractor` `{name, code, doc}` | → **quarantine** (§9.4) |
| `POST /api/ingest/push` | browser-userscript batches (§7.1) |
| `POST /api/ingest/import` `{path, mount}` | bulk register |
| `GET/POST /api/jobs`, `POST /api/jobs/{id}/pause|resume|cancel` | queue control |
| `GET /api/jobs/{id}/items?state=` | item drill-down |
| `GET /api/health` | index stats, model-endpoint status, queue depth, disk |
| `GET /api/ui/…` | web UI (§10) |

Conventions: all list endpoints paginate by cursor; all mutating endpoints are idempotent where natural (push dedup by hash; PUT is upsert); errors as `{error: {code, message}}`; request logs to `audit.py` (append-only JSONL: ts, endpoint, caller, args digest, result digest).

### 9.3 rlm_local integration (the consumption contract)

`rlm_local` gains helpers (thin httpx wrappers, registered in `repl.py` namespace; full docs are tool pages in the wiki — the registry eats its own dog food):

```python
memory.search(query, k=5)          # → /api/memory/search   (cards printed compactly)
memory.add(text, tags=None)        # → /api/memory/add
memory.note(chunk)                 # → /api/memory/note
memory.write_core(text)            # → PUT /api/wiki/pages/notes/core-memory.md
wiki.get(path)                     # → /api/wiki/{path}
wiki.list(prefix="sources/2026")   # → /api/wiki/list
wiki.search(query, k=8)            # → /api/search
cache.get(path_or_hash)            # → /api/wiki/cache/… (text) or CAS bytes for media pages
tools.search(query, detail="desc") # → /api/tools/search
tools.doc(name)                    # → /api/tools/doc/{name}
```

The harness's decomposition prologue gains one line: *"Durable knowledge may exist in the memory wiki — `memory.search` before large decomposition jobs, and `memory.add` durable findings afterward."* Cross-session memory achieved without touching the harness's per-task `ContextStore`.

### 9.4 Capability registry and LATM-style authored extractors (`tools.py`)

- **Tool pages** (`tools/*.md`): frontmatter `{name, namespace, signature, one_liner, when_to_use, example_call, origin: builtin|authored|imported, promoted: bool, hash}`. Indexed like all pages (F8); `search_tools` returns name/desc/full per `detail` level.
- **Authored extractors** (LATM pattern, security-gated per capability report §5.3 #11):
  1. `save_extractor` → code + doc stored in `quarantine/` (not callable), tool page created with `promoted: false`.
  2. **Auto-validation** in a sandboxed worker (§11.3): syntax check, import allowlist, run against a fixture item, output schema check, no-network assertion.
  3. **Review gate**: web UI shows diff + validation report; human clicks *promote* (or a configured trusted model reviews, human countersigns).
  4. Promotion → content-hash pinned, moved to `extractors/`, callable by jobs; every run logs `{extractor, hash, item}` to audit. Persisted code runs under the same sandbox forever.

---

## 10. Web UI (`webui/`) — lightweight by design

Server-rendered (Jinja2), htmx for interactivity, no build step, < 15 static assets. Pages:

- **Read**: rendered markdown (python-markdown-it + wikilink resolution), frontmatter panel, backlinks list, "Related" (hybrid search over summary), media attachments inline (img/audio/video tags against `/api/ui/blob/{hash}`).
- **Edit**: textarea + preview + save (same `PUT /api/wiki` path as API; optimistic-lock by `updated` timestamp; conflicts shown, never silently clobbered).
- **Search**: one box → grouped results (notes/sources/media/tools), filters (type, tag, date, confidential), keyboard-first.
- **Graph**: local neighborhood graph (vis-network, canvas; depth-2 from current page; global graph is offline-precomputed layout of top-2K hubs to stay light on the laptop).
- **Jobs**: queue dashboard — states, items/h, ETA, pause/resume buttons, error drill-down with one-click retry; nightly-window indicator.
- **Review**: quarantined extractors pending promotion (§9.4), suggested links pending acceptance.

Auth: same bearer token (cookie session for UI); CSRF tokens on mutating UI routes; no JS frameworks, no CDN assets (privacy N1 — fonts/scripts served locally).

---

## 11. Security and privacy

Threat model: the service handles the user's most confidential data; scraped content is hostile-untrusted; authored extractors are untrusted code; Box A and Box B are on a home LAN (possibly with untrusted other devices).

1. **Network**: binds to LAN interface only (config `host`); bearer token on every `/api` call (TLS optional via user-provided reverse proxy — document, don't force); CORS locked to the UI origin; **egress policy**: default-deny outbound from `rlm-wikid` except allowlisted mount hosts (enforced at the HTTP-client layer + documented `nftables` snippet for Arch).
2. **Secrets**: tokens via env vars / `pass`-compatible files with 0600 perms; never in git, never in pages, never forwarded to models. Secret-leak scan on outbound model payloads and on wiki writes (capability report §5.3 #10).
3. **Extractor sandbox** (Arch): each extractor/pipeline run executes in **bubblewrap**: `--unshare-net --ro-bind /usr /usr --bind <jail> /work --die-with-parent` + rlimits (AS/CPU/NPROC/FSIZE) + scrubbed env + import allowlist enforced by a wrapper interpreter **[verified bubblewrap as best effort/security ratio on Linux]**. Media tools (ffmpeg, yt-dlp) run in the same jail profile.
4. **Untrusted content taint**: scraped/extracted text is stored as data; any page whose `type` derives from external content carries frontmatter `untrusted: true`; when such text is included in model prompts (summarize/classify), it is wrapped in delimiters with a standing "data, not instructions" instruction (partial mitigation — CaMeL-style control/data separation noted as future work).
5. **Confidentiality labels**: `confidential: bool` (+ optional `sealed: true` = never included in model prompts, never leaves the box in any form; retrieval returns metadata card only). All inference is LAN-local by architecture (N1), so "too confidential for external services" is satisfied by construction.
6. **Audit**: append-only JSONL audit log (API calls, job transitions, extractor runs, promotions, auth failures) with daily rotation + hash chaining (tamper-evident).
7. **Backups**: nightly `restic` (content-addressed, encrypted) of vault + SQLite to a user-configured local target; restore tested in M8. Wiki text is git-recoverable; CAS is re-fetchable only for mount sources — user-owned imports **must** be in backup scope.

---

## 12. Configuration and operations

- `config.toml` at `$XDG_CONFIG_HOME/rlm-wiki/` (schema validated by pydantic; example in appendix): vault root, model tasks/endpoints, budgets, schedule windows, mount definitions, egress allowlist, UI settings.
- **systemd** (Arch): `rlm-wikid.service` (uvicorn, `Nice=10`, `IOSchedulingClass=idle`, `MemoryMax=3G`, restart=on-failure) + `rlm-wiki-jobs.timer` optional nightly kick; `loginctl enable-linger` note for user services.
- **Dependencies** (v1, pinned in `pyproject.toml`): fastapi, uvicorn, httpx, pydantic, python-frontmatter, markdown-it-py, jinja2, htmx (static), numpy, sqlite-vec, trafilatura, pymupdf4llm, markitdown, pillow, xxhash; system: git, ffmpeg, yt-dlp, gallery-dl, pandoc, bubblewrap, restic. Docling/PaddleOCR optional extras (`[ocr]`).
- **Observability**: `/api/health` + Prometheus-style `/metrics` (queue depth, items/h, index sizes, endpoint breaker states); structured logs via `logging` → journald.
- **Upgrade/migration**: index rebuild command (`rlm-wiki index --rebuild`) is always safe (markdown = truth); schema migrations via `PRAGMA user_version` steps.

---

---

## 13. Testing strategy (TDD — contract for the executor)

**Rule: no module lands without its tests written first.** Deterministic by construction — no real LLM, network, or clock in tests.

### 13.1 Test pyramid

- **Unit (≈70%)**: vault page model & frontmatter validation; wikilink parse/resolve (custom markdown-it rule); CAS put/get/dedup/eviction; index CRUD + rebuild-from-pages equivalence; search fusion (fixed candidate sets → fixed RRF order); job state-machine transitions; config schema; taint/secret scanners.
- **Contract (≈20%)**: every `/api` endpoint against a schema test (request/response shapes, error envelope, pagination, idempotency of push/PUT); mount-provider conformance suite (any provider passing `list/stat/read/fetchable` semantics can be registered); ModelClient against a **fake model server** (below).
- **Integration/E2E (≈10%)**: full pipeline on a fixture corpus (≈200 items: md/txt/pdf/html/png/mp3/mp4 tiny samples) — import → extract → enrich (fake models) → wikify → search finds expected items; userscript push → dedup → cache pages; quarantine→promote extractor flow; service restart mid-job → resumes to identical end state.

### 13.2 Key fixtures and harnesses

- **`FakeModelServer`** (pytest fixture, httpx ASGI transport): serves canned, hash-keyed responses per task type (`summarize`, `classify`, `vision_caption`, `embed`, `transcribe`); records calls; can be put into failure modes (timeouts, 429s, garbage JSON) deterministically. All enrichment tests run against it.
- **Fixture corpus** in `tests/fixtures/corpus/` (tiny but real files, incl. a scanned PDF, a 5 s audio, a 3 s video with scene cut, an HTML page with boilerplate, an mbox with 3 mails).
- **Property tests** (hypothesis): (a) job state machine — random interleavings of pause/kill/resume always converge to `done|failed`, never corrupt (assert via vault+CAS invariants); (b) dedup — random item permutations yield identical final store; (c) frontmatter round-trip.
- **Load tests** (not in CI, `make loadtest`): synthetic generator (100K pages ≈ realistic size distribution, 20K CAS objects) → asserts N3 targets: FTS p95 < 300 ms, hybrid p95 < 1 s, index RSS within budget, `git status` < 2 s, rebuild-from-markdown < 2 h on the HP laptop. **[VERIFY gates M1/M2]**
- **Mutation spot-checks** (manual, monthly): mutmut on `jobs.py` + `vault.py`.

### 13.3 Engineering practices

uv-managed env; ruff + mypy (strict on `vault/jobs/index`, gradual elsewhere); pre-commit; conventional commits; CI (GitHub Actions or Woodpecker) running unit+contract on push, integration nightly; ADRs in `docs/adr/` for: git-vs-journal versioning, sqlite-vec-vs-hnswlib, fsspec-subset-vs-full, bubblewrap profile, task-typed model table.

### 13.4 Acceptance gates per milestone

Each milestone in §14 has binary, testable acceptance criteria; the executor does not proceed with a red gate.

---

## 14. Milestones

| MS | Deliverable | Acceptance criteria |
|---|---|---|
| **M0 — Scaffold** | uv project, config, logging, audit, CI, `FakeModelServer`, fixture corpus | CI green; fake server serves all 5 task types |
| **M1 — Vault core** | `vault.py` (pages, wikilinks, atomic write), git batch-committer, `meta.sqlite`, FTS5 build/rebuild, 100K-page load test | round-trip property test; rebuild ≡ incremental index; N3 lexical targets met; git ops < 2 s at 100K files |
| **M2 — API + read UI** | `/api/wiki|search`, auth, lexical search UI, backlinks | contract tests; p95 search targets at 100K pages |
| **M3 — CAS + mounts** | `cas.py`, provider protocol, `localtree`, `http`, lazy-cache state machine, eviction | provider conformance suite; kill-during-fetch resumes correctly |
| **M4 — Jobs + text pipelines** | `jobs.py` (state machine, windows, pause/resume), text/html/office/pdf(pymupdf4llm) pipelines, enrichment chain vs fake models | property tests pass; 200-item fixture corpus fully wikified; pause for 24 h resumes to identical state |
| **M5 — Media pipelines** | audio (Box A whisper), image+video (Box A vision), cache→media pages, embeddings in search (hybrid on) | fixture audio/video produce transcript+caption pages; hybrid search beats lexical-only on 20 labeled semantic queries |
| **M6 — Push + pulls** | userscript generator, `/api/ingest/push`, `yt`/`gallery` providers, Docling/PaddleOCR optional tier | scripted replay of a captured X favorites session dedups + wikifies; IG cookie path documented + tested with fixture export |
| **M7 — Tools registry + rlm_local glue** | tool pages, `/api/tools/*`, quarantine→promote flow, `rlm_local` helpers package | end-to-end: harness REPL `memory.search`/`tools.search` against live service; authored extractor survives sandbox validation + promotion |
| **M8 — Ops + polish** | restic backup/restore drill, metrics, graph view, jobs dashboard, load-aware-router design memo (Phase 2 llama-swap on Box A) | restore test passes; dashboard shows live queue; memo reviewed |

Suggested staffing: one executor agent working sequentially; M1–M4 are the spine (do not parallelize ahead of them).

---

## 15. Risks and open questions

1. **Enrichment quality at 4B** (classification, note construction): the A-MEM small-model evidence is encouraging, but validate on the *actual* models — M4 includes a 50-item human-scored sample; if note quality is poor, restrict auto-notes and keep source/media pages only (degrade gracefully).
2. **Throughput on the HP laptop**: Docling on CPU is seconds/page; hours of audio at ~3–6× RT means ~10–20 min transcription per hour of audio **on Box A**, near-zero cost on B. Overnight windows suffice for steady-state; the initial 100K-file backlog is exactly why the queue is months-horizon. No action — the design already assumes this.
3. **sqlite-vec pre-v1**: pin version, wrap in `VectorStore` protocol, hnswlib fallback ADR ready.
4. **Embedding coverage vs lexical**: embeddings cover title+summary only; semantic recall over full bodies is bounded by summary quality. Accepted trade (keeps ≤1 vector/page).
5. **Git at 500K files**: mitigations in §5.4; load-test gate M1; journal fallback ADR.
6. **Instagram/X churn**: scrapers break; the push-userscript path (user-driven, DOM-visible data) is the resilient primary; yt-dlp/gallery-dl are secondary. Treat provider breakage as routine ops, not incidents.
7. **Windows-only file locks on Box A irrelevant here**; Arch-only ops documented (bubblewrap, systemd, pacman deps).
8. **Open (needs user decision later)**: encryption at rest (LUKS on Box B recommended, out of service scope); exposing the UI beyond LAN (recommend: don't — Tailscale if ever needed).

---

## 16. Appendix

### 16.1 Example `config.toml` (abridged)

```toml
vault_root = "/srv/rlm-wiki/vault"
host = "0.0.0.0"            # LAN only; token required
port = 8777
token_file = "/etc/rlm-wiki/token"   # 0600

[models.endpoint]
base_url = "http://boxa.lan:8080/v1"

[models.tasks]
summarize = { model = "qwen3-4b-instruct", max_tokens = 768, timeout_s = 120 }
classify  = { model = "qwen3-4b-instruct", max_tokens = 256 }
vision_caption = { model = "qwen2.5-vl-7b", max_tokens = 512 }
embed     = { model = "qwen3-embedding-0.6b" }
transcribe = { model = "whisper-small", endpoint = "http://boxa.lan:9000/v1" }

[jobs]
max_parallel_items = 2
model_concurrency = 2
[[jobs.windows]]
name = "nightly"
start = "23:30"; end = "06:30"

[[mounts]]
scheme = "localtree"; root = "/mnt/backups"; fetchable = true
[[mounts]]
scheme = "yt"; cookies_from_browser = "firefox:~/.mozilla/firefox/default"

[egress]
allow = ["twitter.com", "x.com", "instagram.com", "i.ytimg.com"]

[cache]
max_gb = 200
evict = "lru_fetchable_only"
```

### 16.2 Userscript sketch (generated by `/api/ui/push-script`, token embedded at generation time)

```javascript
// ==UserScript== @match *://x.com/* @grant GM_xmlhttpRequest ==/UserScript==
// Walks visible [data-testid=tweet] nodes in batches of 20; POSTs to
// http://boxb.lan:8777/api/ingest/push with Authorization: Bearer <token>;
// stores cursor in GM_setValue so re-runs resume; STOP/START floating button.
```

### 16.3 Verified tool/version anchors (July 2026 research)

Docling v2 (MIT) · pymupdf4llm (AGPL) · Marker 2.0 (Apache-2.0, GPU-lean) · trafilatura 2.1 · markitdown 0.1.6 · faster-whisper 1.2.1 (int8 CPU) · whisper.cpp 1.9.1 · sherpa-onnx Parakeet · silero-vad 6.2 · whisperX 3.8.6 · PaddleOCR 3.7 · yt-dlp 2026.07.04 (X OK, IG cookies) · gallery-dl 1.32.7 · llama-swap v241 · speaches · llama.cpp mtmd (Qwen2.5-VL/Omni GGUF verified) · sqlite FTS5 trigram (≥3.34) · sqlite-vec 0.1.9 (pre-v1) · fsspec 2025.12 · python-frontmatter 1.3 · markdown-it-py 4.2 · obsidiantools 0.11 · restic · bubblewrap · Karakeep (push-pattern prior art) · SilverBullet/Khoj (files-as-truth prior art).

### 16.4 What this spec deliberately reuses from the companion research

- Memory shape: Mem0-2026 (ADD-only, hybrid retrieval) + A-MEM note construction (proven at 1.5–3B) + MemoryBank decay arithmetic + Zep's `superseded_by` idea (frontmatter field) — memory/retrieval report §4.
- Retrieval: hybrid BM25+vector as the *only* retrieval that earns its keep; map-reduce over retrieval for aggregation/global queries — memory/retrieval report §6.2.
- Capabilities: progressive disclosure (`search_tools` detail levels), tool doc standard (namespaced names, one example call), LATM economics with a quarantine gate — capability report §6.
- Security: bindings pattern (no secrets in REPL/jobs), bubblewrap tier, taint marking, audit log, symlink-realpath jailing — capability report §5.3.
