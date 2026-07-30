# RLM × Advanced Memory & Retrieval Systems — Research Report

**Date:** 2026-07-22
**Status:** Research synthesis
**Companion to:** `20260721-1034-rlm-harness-small-models-design.md` (the design spec) and the `rlm_local` implementation at `~/Documents/Misc/rlm_for_local/`
**Method:** three parallel deep-research threads (agentic memory systems; retrieval-vs-decomposition evidence; codebase-scale digestion), primary sources where reachable. Every key claim carries a URL; items that could not be verified are flagged **[UNVERIFIED]**.

---

## 1. Executive summary

The research question: *what memory and retrieval machinery should complement an RLM harness targeting small local models, for digesting vast codebases and huge knowledge corpora?*

The answer, in one paragraph: **programmatic decomposition remains the engine; retrieval becomes a set of cheap, lazy REPL helpers; long-term memory becomes a SQLite-backed store exposed as Python functions.** The evidence strongly confirms the RLM thesis that retrieval-first architectures fail on aggregation/global/semantic-mapping tasks — but it also shows retrieval is the *correct first move* for exact needles, multi-hop candidate narrowing, and identifier lookup, at near-zero cost. For codebases specifically, the industry has converged on exactly the RLM-compatible pattern: lexical search + structural indexes (tree-sitter symbol graphs) + map-reduce summarization, with embeddings optional. Heavy machinery (graph databases, community-report GraphRAG, KV-cache memory models, Postgres-backed agent memory) is a dead end on weak hardware — but their *ideas* (temporal validity, forgetting curves, gist notes, personalized-PageRank association) are cheap to steal and fit in SQLite.

Top concrete recommendations (details in §6):

1. Add **`bm25(query, k)`** (BM25S or SQLite FTS5) and keep `grep` — lexical-first is the industry-proven floor for code and identifiers.
2. Add **optional `embed_search(query, k)`** with a small local embedder (EmbeddingGemma-300M or Qwen3-Embedding-0.6B GGUF) fused with BM25 — only for semantic needles / "find similar" queries.
3. Add a **`memory` helper** (Mem0-shaped: ADD-only fact notes + hybrid retrieval + forgetting arithmetic) backed by the existing SQLite `ContextStore` pointed at a persistent path — this is cross-session memory with zero new services.
4. Add a **`repo` helper family** for codebase digestion: tree-sitter outline/symbol/call-graph index in a single SQLite file, Aider-style ranked repo-map, map-reduce module summaries generated overnight by the sub-tier model.
5. Skip: GraphRAG community reports, Zep/Graphiti (graph DB + structured-output dependence), memoRAG (7B KV-cache memory model, 16–24 GB GPU), MemGPT's full runtime.

---

## 2. Framing: three distinct problems, one RLM answer

"Memory and retrieval" conflates three problems that the literature treats separately but an RLM harness unifies:

- **P-A: Within-task corpus digestion.** A huge context (documents, logs, a codebase) must be processed *now*. This is the RLM's home turf: context as REPL variable + decomposition. Retrieval here is a *cheap pre-filter* (`grep`, `bm25`) inside the REPL, not an architecture.
- **P-B: Cross-session / long-term memory.** Facts, preferences, and prior results must persist across tasks. The agentic-memory literature (MemGPT, Mem0, A-MEM, Zep, HippoRAG) attacks this; the small-model-viable subset reduces to *notes in a database + hybrid search + decay arithmetic* — i.e., Python functions over SQLite (§4).
- **P-C: Codebase-scale structure.** Source code has machine-extractable structure (symbols, imports, calls). The winning pattern is to *precompute* that structure into an index the RLM navigates with helpers, reserving LLM sub-calls for semantics (§5).

In all three, the root model stays an orchestrator seeing only metadata, helper one-liners, and small printed results — the LID principle from the design spec (P1/R1.x) is preserved because retrieval outputs enter the root context only as tiny, deliberately-printed excerpts.

---

## 3. The retrieval landscape: what beats what, and where

### 3.1 Long-context vs retrieval — the head-to-head evidence

- **Google LOFT** ([arXiv:2406.13121](https://arxiv.org/abs/2406.13121), Jun 2024): 128K-token frontier models rival specialized retrievers on single-target retrieval (Gemini 0.77 vs Gecko 0.76 Recall@1) and beat retrieve-and-read RAG on **multi-hop** QA (HotpotQA 0.75 vs 0.70) — but lose badly on **multi-target/set-operation** queries (QUEST 0.30 vs 0.54) and **SQL-like aggregation** (0.38 vs 0.65; *averaging is the hardest operator, equality easier than inequality*). Performance degrades 128K→1M, and the 128K eval alone cost 448M input tokens — long context is the expensive option even when it wins.
- **Self-Route** (Google DeepMind, [arXiv:2407.16833](https://arxiv.org/abs/2407.16833), Jul 2024): long-context beats RAG on average given resources, but RAG is far cheaper; routing queries to long-context only when the model judges RAG's answer insufficient matches LC quality at much lower cost. → The canonical *cheap-first, escalate-on-failure* pattern.
- **NVIDIA revisit** ([arXiv:2501.01880](https://arxiv.org/abs/2501.01880), Dec 2024): LC generally beats RAG on QA; **summarization-based retrieval performs comparably to LC while chunk-based retrieval lags** — retrieval that pre-digests (map-reduce summaries) closes most of the gap.
- **BrowseComp-Plus** ([arXiv:2508.06600](https://arxiv.org/abs/2508.06600), Aug 2025 — the RLM paper's own benchmark): open-source Search-R1 + BM25 scores **3.86%** vs GPT-5's 55.9%; GPT-5 + dense retriever reaches 70.1%. For hard agentic search, dense retrieval ≫ BM25 *and* the reasoning model dominates retriever choice.

**Reading for rlm_local:** the RLM harness *is* the "long-context" side of these trade-offs, minus the cost — decomposition gives LC-quality reasoning without LC prices. Retrieval's role is the Self-Route one: answer the cheap queries cheaply.

### 3.2 The failure-mode map (this is the design driver)

| Failure class | Evidence | Consequence for the harness |
|---|---|---|
| **Aggregation / counting / set ops** | OOLONG ([arXiv:2511.02817](https://arxiv.org/abs/2511.02817), Nov 2025): GPT-5, Claude-Sonnet-4, Gemini-2.5-Pro all **<50% at 128K**; RLM paper ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601)): BM25-ReAct fails, RLM works; LOFT SQL analysis concurs | Retrieval is *actively harmful* here (top-k drops rows). Pure programmatic decomposition: chunk → `llm_query_batched` map → Python reduce. Compute aggregates in Python, never in prose |
| **Semantic needles (no lexical overlap)** | NoLiMa ([arXiv:2502.05167](https://arxiv.org/abs/2502.05167), ICML'25): 11/13 long-context models drop below 50% of their short-context baseline at 32K; literal-match reliance afflicts attention *and* BM25 | This is the *one* place embeddings earn their keep |
| **Temporal ordering** | LongMemEval ([arXiv:2410.10813](https://arxiv.org/abs/2410.10813), ICLR'25): ~30% accuracy drop on temporal reasoning; fixes are indexing-side (time-aware keys, session decomposition) | Store timestamps as metadata; filter/sort programmatically |
| **Multi-target completeness (unions, differences/negation)** | LOFT QUEST/QAMPARI results above | Top-k retrieval structurally cannot promise completeness; use retrieval to narrow, then exhaustive programmatic passes over survivors |
| **Niche technical corpora** | FreshStack ([arXiv:2504.13128](https://arxiv.org/abs/2504.13128), Apr 2025): off-the-shelf retrievers far below oracle on niche code topics; rerankers didn't fix first-stage misses | Don't trust vector search alone for code |
| **Long-context claims vs reality** | RULER ([arXiv:2404.06654](https://arxiv.org/abs/2404.06654), COLM'24): only half of 17 models hold up at their claimed 32K | Size sub-call prompts to *verified* model behavior, not marketing context lengths (design spec §6 already does this) |
| **Operational RAG failures** | "Seven Failure Points" ([arXiv:2401.05856](https://arxiv.org/abs/2401.05856), Jan 2024): missing content, missed top-k, not-consolidated, not-extracted… | Most failure points vanish when the model can *iterate* programmatically instead of accepting one retrieval shot |

### 3.3 GraphRAG family — and why the small-model door is narrow

- **Microsoft GraphRAG** ([arXiv:2404.16130](https://arxiv.org/abs/2404.16130), Apr 2024): LLM extracts entity/relation graphs per chunk + hierarchical community summaries; global "sensemaking" queries answered map-reduce over community reports. Big comprehensiveness gains over vanilla RAG — the canonical "retrieval fails on global questions; precomputed aggregation wins" result.
- **LightRAG** ([arXiv:2410.05779](https://arxiv.org/abs/2410.05779), Oct 2024): drops community reports for dual-level keyword retrieval over the extracted graph. Verified cost comparison (5M-token legal corpus, GPT-4o-mini): indexing ≈ 1 LLM call per 1200-token chunk for both; **per-query global search: GraphRAG ≈ 610K tokens + hundreds of calls vs LightRAG <100 tokens + 1 call**; GraphRAG must rebuild communities on inserts, LightRAG unions-in new extractions.
- **Small-model viability — the decisive evidence is MiniRAG** ([arXiv:2501.06713](https://arxiv.org/abs/2501.06713), Jan 2025): with 1.5–4B models, simplified-graph MiniRAG scores ~48–53% on its benchmarks vs NaiveRAG 39–44%, LightRAG 19–40%, and **GraphRAG effectively fails outright with SLMs**. LightRAG's own README recommends **Qwen3-30B-A3B minimum** for extraction. [nano-graphrag](https://github.com/gusye1234/nano-graphrag) (~1100 LOC, Ollama-compatible) proves a GraphRAG clone *runs* locally, but regenerates community reports on every insert — the recurring indexing tax.

**Verdict:** LLM-driven graph extraction at 4B is unproven-to-poor, and the query-time benefits don't survive model weakness. If global sensemaking is needed, **map-reduce summaries via `llm_query_batched` deliver the same structural benefit at predictable cost** — no graph required. A HippoRAG-lite (triples + numpy PageRank) is a phase-3 option at best (§4.9).

### 3.4 Agentic / iterative retrieval — and a warning about prompted small models

- **IRCoT** ([arXiv:2212.10509](https://arxiv.org/abs/2212.10509), ACL'23): interleave reasoning steps with retrieval; +21 retrieval / +15 QA points on multi-hop benchmarks, works with Flan-T5-large. Decision rule is *structural*: retrieve after each reasoning step.
- **FLARE** ([arXiv:2305.06983](https://arxiv.org/abs/2305.06983), EMNLP'23): trigger retrieval on low-confidence generation.
- **Self-RAG** ([arXiv:2310.11511](https://arxiv.org/abs/2310.11511), Oct 2023): *learned* reflection tokens decide when to retrieve.
- **Search-R1** ([arXiv:2503.09516](https://arxiv.org/abs/2503.09516), Mar 2025): RL-trained 3B/7B interleaving search with reasoning, **+41% (7B) / +20% (3B)** over RAG baselines — and the paper notes prompting off-the-shelf models to do this is "often suboptimal."
- Practice consolidation: [Agentic RAG survey](https://arxiv.org/abs/2501.09136) (Jan 2025, rev. 2026); OpenAI Deep Research ([Feb 2025](https://openai.com/index/introducing-deep-research/), updated 2026 with MCP connectivity) — agentic retrieval is now a *trained-in* product capability.

**Warning for rlm_local:** retrieval *timing* is a trained skill. A prompted 4B will not improvise when to retrieve. The harness must encode the routing structurally — which is exactly the design spec's P5 philosophy (mandated decomposition plan). §6.2 turns this into a turn-0 routing table.

### 3.5 Hybrid retrieval and local embedding machinery

- **Anthropic Contextual Retrieval** ([Sep 2024](https://www.anthropic.com/news/contextual-retrieval)): contextual embeddings −35% top-20 retrieval failures; **+ BM25 −49%**; + reranker −67%. The strongest single citation for *hybrid beats either alone*. (Also: "if your KB < 200K tokens, just put it all in the prompt" — i.e., below that size, skip indexing; an RLM just decomposes it.)
- **Blended RAG** (IBM, [arXiv:2404.07220](https://arxiv.org/abs/2404.07220)): dense+sparse hybrid sets retrieval SOTA. **Late chunking** (Jina, [arXiv:2409.04701](https://arxiv.org/abs/2409.04701)): embed-then-chunk preserves context — needs a long-context embedder; skip for now.
- **BM25S** ([arXiv:2407.03618](https://arxiv.org/abs/2407.03618), [github](https://github.com/xhluca/bm25s)): pure-Python BM25, memory-mapped indices (2M docs: 0.49 GB RAM mmap), 51 MB install. The obvious REPL helper implementation.
- **BEIR** ([arXiv:2104.08663](https://arxiv.org/abs/2104.08663), NeurIPS'21): BM25 remains a robust zero-shot baseline; dense retrievers often *underperform* it out-of-distribution.
- **Local embedder shortlist** (all runnable in the same llama.cpp/Ollama stack; MTEB numbers from cards):
  - **EmbeddingGemma-300M** ([Google, Sep 2025](https://developers.googleblog.com/en/introducing-embeddinggemma/)): best open multilingual <500M on MTEB at release; **<200 MB RAM quantized**; day-one llama.cpp/Ollama support. Default for the weakest hardware.
  - **Qwen3-Embedding-0.6B** ([Jun 2025](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)): 64.33 MTEB-multilingual / 70.70 MTEB-Eng-v2, 32K ctx, instruction-aware, code-retrieval trained (8B sibling is MTEB-multilingual #1, 70.58). Official GGUF availability **[UNVERIFIED]**; llama.cpp supports the architecture.
  - **jina-embeddings-v4** ([Jun 2025](https://huggingface.co/jinaai/jina-embeddings-v4)): 3.8B multimodal, **official text-only GGUFs** (retrieval/code/text-matching; Q4_K_M ≈ 1.8 GB), dedicated code adapter; Jina's llama.cpp benchmarks: ~3,700 tok/s on an L4 GPU for the 3B model, 16,500 tok/s for the 570M v3. CPU throughput for 0.5B-class embedders is plausibly a few hundred tok/s **[UNVERIFIED extrapolation]** — overnight indexing is the right frame.
  - **nomic-embed-text-v1.5** (137M, fully open, official GGUF) and **BGE-M3** ([arXiv:2402.03216](https://arxiv.org/abs/2402.03216), dense+sparse+multi-vector, LightRAG's recommended local embedder) as fallbacks.

---

## 4. Agentic long-term memory systems (P-B)

Landscape at a glance (details below; "4B-viable" = could a quantized 4B-class local model operate the memory loop, per evidence):

| System | Mechanism (1 line) | Storage/infra | 4B-viable? | Verdict for rlm_local |
|---|---|---|---|---|
| MemGPT / Letta ([arXiv:2310.08560](https://arxiv.org/abs/2310.08560), Oct 2023) | OS-style memory tiers, self-editing via function calls | PostgreSQL + pgvector | **No** — paper itself: "significantly degraded with GPT-3.5… best with GPT-4" | Steal the tiering idea only |
| Mem0 ([arXiv:2504.19413](https://arxiv.org/abs/2504.19413), Apr 2025; v3 Apr 2026) | LLM extracts facts → ADD-only store; hybrid retrieval | **Qdrant-on-disk + SQLite**, pip, Ollama-supported | **Yes** (bounded extraction prompts; new single-pass pipeline needs no tool calling) | **Closest fit** — the shape to copy, not necessarily the dependency |
| Zep / Graphiti ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956), Jan 2025) | Temporal knowledge graph, bi-temporal facts | **Neo4j/FalkorDB server required** | **No** — own README warns small models fail its structured-output extraction | Dead end; steal `valid_from`/`superseded_by` columns |
| A-MEM ([arXiv:2502.12110](https://arxiv.org/abs/2502.12110), Feb 2025) | Zettelkasten notes: keywords/tags/context + links + evolution | ChromaDB embedded, Ollama-supported | **Yes — proven**: evaluated on Qwen2.5-1.5B/3B and Llama-3.2-1B/3B, still beats baselines | **Best conceptual template** for a small-model memory loop |
| MemoryBank ([arXiv:2305.10250](https://arxiv.org/abs/2305.10250), May 2023) | Full history + daily summaries + Ebbinghaus forgetting | Vector store (repo now **404** [verified Jul 2026]) | Demonstrated with ChatGLM-6B | Not adoptable as code; forgetting curve is free arithmetic |
| ReadAgent ([arXiv:2402.09727](https://arxiv.org/abs/2402.09727), Feb 2024) | Paginate → gist memories → lookup originals | None (gists in context) | Untested on small models | Already the RLM idiom (`map_query` summarize + `grep` lookup); formalize it |
| memoRAG ([arXiv:2409.05591](https://arxiv.org/abs/2409.05591), Sep 2024) | 7B "memory model" compresses corpus into KV cache; clues steer retrieval | **16–24 GB GPU**, faiss | **No** | Dead end; steal query-expansion-by-clues idiom |
| HippoRAG 1/2 ([arXiv:2405.14831](https://arxiv.org/abs/2405.14831), [arXiv:2502.14802](https://arxiv.org/abs/2502.14802)) | OpenIE triples → schema-less KG; Personalized PageRank retrieval | Local parquet by default; reference config is **Llama-3.3-70B + 7B embedder** | Unproven/risky (OpenIE at 4B) | Phase-3 option: SQLite triples + numpy PPR |

### 4.1 Key evidence points

- **Mem0 on LOCOMO** ([Table 2](https://arxiv.org/html/2504.19413v1)): J-score **66.88** (Mem0g 68.44) vs best RAG config 60.97, Zep 65.99, A-Mem 48.38, full-context 72.90 — at **p95 latency 1.44s vs 17.1s** and **1,764 vs 26,031 retrieved tokens**. April 2026 vendor numbers (LoCoMo 92.5, LongMemEval 94.4) are managed-platform claims the README itself disclaims for OSS — treat as marketing. Mem0's **April 2026 algorithm change** is the small-model-relevant one: single-pass ADD-only extraction (no tool-call loop) + multi-signal retrieval (semantic + BM25 + entity) + temporal reasoning ([README](https://raw.githubusercontent.com/mem0ai/mem0/main/README.md)).
- **A-MEM on LoCoMo** ([Table 1](https://ar5iv.labs.arxiv.org/html/2502.12110)): multi-hop F1 **45.85 vs MemGPT 25.52** with GPT-4o-mini, at ~1,200–2,520 tokens/answer vs ~16,900 for full-context baselines. And the **only direct small-model evidence in the literature**: with Llama-3.2-3B driving the memory loop, A-MEM multi-hop F1 26.38 vs MemGPT 2.68; with Qwen2.5-1.5B, 24.32 vs 4.21. Cross-paper conflict: Mem0's re-run scored A-Mem at 48.38 vs Mem0 66.88 — different metrics/protocols; treat both as directional.
- **Zep on LongMemEval** ([blog](https://blog.getzep.com/state-of-the-art-agent-memory/)): 71.2% vs 60.2% full-context (+18.5% relative), median latency 2.58s vs 28.9s — but the pipeline used gpt-4o-mini internally and their own README warns against small models for its extraction.
- **HippoRAG**: +20% over prior RAG SOTA on multi-hop QA; single-step retrieval ≈ IRCoT at 10–30× cheaper; HippoRAG 2 adds +7 pts avg on associative tasks. PPR is pure numpy; the heavy part is LLM OpenIE indexing at scale.

### 4.2 What this means architecturally

The small-model-viable memory loop has exactly three LLM-touching operations, all of which are **bounded sub-call jobs**, not root-model duties:

1. **Note construction at write time** (A-MEM/Mem0): `chunk → {summary, keywords, tags}` — a prompt-template job with proven 1.5–3B viability.
2. **Hybrid retrieval at read time**: embedding cosine + BM25 (SQLite FTS5) + recency/strength scoring — pure code, no LLM.
3. **Consolidation/dedup**: by *code policy* (similarity threshold, "new supersedes similar old"), not LLM tool-call decisions — the lesson of Mem0's 2026 redesign and Graphiti's small-model warning is the same: **keep LLM judgment out of the memory-management loop wherever arithmetic will do.**

Everything else — forgetting (Ebbinghaus decay over `(last_access, access_count)`), temporal validity (`valid_from`/`superseded_by` columns), a pinned core-memory variable persisted to disk (MemGPT's core block, minus JSON function calling) — is SQLite schema and a few dozen lines of Python.

---

## 5. Codebase-scale digestion (P-C)

### 5.1 How production coding agents handle repo context — the industry pivot

| System | Approach | Key details |
|---|---|---|
| **Aider** ([repo-map docs](https://aider.chat/docs/repomap.html), [tree-sitter post, Oct 2023](https://aider.chat/2023/10/22/repomap.html)) | tree-sitter symbol extraction + graph ranking (PageRank-style) → repo map sized to a token budget (default 1K) | Pure CPU; the highest value/cost pattern in the literature |
| **Claude Code** ([best practices](https://www.anthropic.com/engineering/claude-code-best-practices)) | **grep/glob exploration + subagents with separate contexts + CLAUDE.md**; no embedding index | The HN thread "Claude Code/Cursor is using grep?" ([Jul 2025](https://news.ycombinator.com/item?id=44529677)) marks the industry's pivot back to lexical exploration |
| **Cursor** ([indexing post, Jan 2026](https://cursor.com/blog/secure-codebase-indexing)) | Merkle-tree sync + syntactic chunking + embeddings cached by content hash | 50K-file workspace metadata ≈ 3.2 MB; P99 time-to-first-query 4.03 h → 21 s; reports +12.5% accuracy from semantic search |
| **GitHub Copilot / Blackbird** ([VS Code docs](https://code.visualstudio.com/docs/copilot/workspace-context), [Blackbird post, Feb 2023](https://github.blog/2023-02-06-the-technology-behind-githubs-new-code-search)) | Hybrid: semantic index + grep + usages (find references); Blackbird = Rust ngram index, git-blob dedup + delta indexing | 15.5B docs/115 TB indexed in ~18 h; index ≈ ¼ of source size |
| **Sourcegraph Cody** ([2023 post](https://sourcegraph.com/blog/how-cody-understands-your-codebase)) | **Dropped embeddings for adapted BM25** + IDE context | Reasons: third-party embedding of code, index maintenance, vector DB cost at >100K repos — lexical won at enterprise scale |
| Windsurf | "Codebase awareness engine" | Internals undocumented **[UNVERIFIED]** |

The pattern is unmistakable: **lexical + structural first; embeddings optional and incremental; the model explores rather than ingests.** That is precisely the RLM stance.

### 5.2 Static structure extraction — the cheap backbone

- **tree-sitter** ([docs](https://tree-sitter.github.io/tree-sitter/)): incremental, error-tolerant parsing, official Python bindings, ~25 maintained grammars. Used by Aider, Cody-autocomplete, and the 2026 local-first tools below. Parse-on-keystroke speed per file.
- **LocAgent's Python-AST graph** ([arXiv:2503.09089](https://arxiv.org/abs/2503.09089), Mar 2025) — the most instructive design for rlm_local: nodes {directory, file, class, function}, edges {contain, import, invoke, inherit}, built with **stdlib `ast` in seconds per codebase**. On SWE-bench Lite localization, a fine-tuned **Qwen2.5-Coder-7B** reaches file Acc@5 88.3% (GPT-4o-class) at $0.05/example; its BM25-over-entities search is the load-bearing component (removing it: 88.3 → 69.0).
- **Convergent 2026 local-first exemplars** — two independent Show-HN tools landed on the same architecture: **SrcLight** ([Feb 2026](https://news.ycombinator.com/item?id=47141625)) and **Vexp** ([Feb 2026](https://news.ycombinator.com/item?id=47113273)): tree-sitter/AST + **single SQLite per repo** containing symbol tables, FTS5 indexes (symbols, trigram source search, stemmed docstrings), relationship graph (callers/callees/imports/inheritance), content-hash incremental indexing, and "context capsules" (pivot files full, neighbors as signature skeletons — 70–90% token reduction). This is the design to copy almost verbatim.

### 5.3 Code retrieval models — lexical vs dense

- **CoIR benchmark** ([arXiv:2407.02883](https://arxiv.org/abs/2407.02883), ACL 2025): dense models roughly double BM25 on NL→code retrieval (BM25 NDCG@10 29.79 vs E5-base 50.90, Voyage-Code-002 56.26) — but BM25 is a non-trivial floor, and agentic localization rarely needs pure semantic search (LocAgent ablation; Sourcegraph; Claude Code).
- **Local code embedders**: **jina-code-embeddings 0.5B/1.5B** ([Sep 2025](https://jina.ai/news/jina-code-embeddings-sota-code-retrieval-at-0-5b-and-1-5b/)) — Qwen2.5-Coder backbones, GGUF quants, 0.5B beats Qwen3-Embedding-0.6B by 5 points on code benchmarks; **Qwen3-Embedding** ([arXiv:2506.05176](https://arxiv.org/abs/2506.05176)) doubles as a code retriever.
- **Synthesis:** hybrid (BM25 + symbol index + optional dense) is the sweet spot; SrcLight's RRF fusion and Vexp's BM25+TF-IDF+centrality are concrete recipes. Dense is the most expensive piece — make it optional.

### 5.4 Repo-level agents — evidence that simple pipelines work at 7B scale

- **Agentless** ([arXiv:2407.01489](https://arxiv.org/abs/2407.01489), Jul 2024): retrieval→localize→repair→validate, no agent loop; 32% SWE-bench Lite at $0.70, beating all open agent frameworks of its day.
- **SWE-Fixer** ([arXiv:2501.05040](https://arxiv.org/abs/2501.05040), Jan 2025): BM25 coarse retrieval + fine-tuned 7B models, two model calls per instance → 22–30% SWE-bench — BM25-first pipelines work with 7B-class open models.
- **mini-SWE-agent** ([github](https://github.com/SWE-agent/mini-swe-agent), Jul 2025): ~100 lines, bash-only, 65% on SWE-bench (model attribution **[UNVERIFIED]**).
- **SWE-Gym** ([arXiv:2412.21139](https://arxiv.org/abs/2412.21139), ICML 2025): fine-tuned open-weight agents +19% absolute — training helps; base sizes **[UNVERIFIED]**.
- **Precomputed digestion**: DeepWiki ([Cognition, May 2025](https://cognition.ai/blog/deepwiki); open clone [deepwiki-open](https://github.com/AsyncFuncAI/deepwiki-open)) and RepoAgent ([arXiv:2402.16667](https://arxiv.org/abs/2402.16667)) — LLM-generated hierarchical repo wikis/docs; useful for "what does this subsystem do," costs LLM-hours, cache by content hash.

### 5.5 Feasibility on a CPU laptop (100K files)

- tree-sitter parse + symbol/edge extraction + FTS5/BM25 + import graph + PageRank: **yes, easily** — tens of minutes to ~2 h (estimate extrapolated from tree-sitter/Aider/LocAgent claims, not a measured figure).
- Python-AST graph (Python repos): seconds-to-minutes per SWE-bench-scale repo (LocAgent) → ~1 h at 100K files plausible.
- Dense embedding of a full repo: **marginal on CPU-only** (~100M tokens of code = many hours-days); **yes on a weak GPU overnight**. Mitigate: embed symbol-level chunks only, 0.5B model at 128–256d (Matryoshka), content-hash caching so re-embedding is incremental.
- Module-summary trees with a 7–8B local LLM: feasible overnight at **module level** (hundreds of summaries), not file level.

---

## 6. Synthesis for rlm_local

### 6.1 The unified mental model

> **Memory = `ContextStore` + indexes. Retrieval = REPL helper functions. Decomposition remains the engine.**

Nothing in this research changes the RLM architecture; it populates it. Every retrieval/memory technique worth having reduces to (a) precomputed structures on disk (SQLite tables, FTS5 indexes, optional embedding blobs), and (b) thin Python helpers the root model calls from code. LLM judgment stays in bounded sub-calls (note construction, summarization) — never in memory-management or retrieval-control loops, where small models are weakest and arithmetic suffices. This preserves the design spec's core invariants: the root context stays task-agnostic (P2), every LM call stays locally in-distribution (P1), and helper idioms normalize trajectories across tasks (P4/R3.3).

### 6.2 Turn-0 routing table (encode in the decomposition prologue)

Retrieval timing is a trained skill (Search-R1) — a prompted 4B will not improvise it. Put this table's content in the system prompt's "How to work" section:

| Query type | First move | Escalation | Evidence anchor |
|---|---|---|---|
| Exact needle / identifier | `grep` / `bm25` | answer directly, **skip sub-calls** | Self-Route; Anthropic exact-match |
| Semantic needle (paraphrase) | `embed_search` | hybrid BM25+embed, rank-fused | NoLiMa; Anthropic hybrid −49% |
| Multi-hop | retrieve candidates, then decompose hop-by-hop | retrieval helper per hop inside the RLM loop | IRCoT +15; LOFT; BrowseComp-Plus |
| Aggregation / counting / set ops | **skip retrieval entirely** → chunk → `llm_query_batched` → Python reduce | — | OOLONG; LOFT-SQL; RLM paper |
| Global sensemaking ("main themes") | map-reduce summaries over chunks | (phase 3) MiniRAG-style simplified graph | GraphRAG vs vanilla RAG; MiniRAG SLM table |
| Temporal | metadata/date filters + programmatic sort | time-aware query expansion | LongMemEval |
| Code question | `repo.outline` / `repo.grep` / `repo.symbols` | `repo.traverse` + targeted reads; `repo.search` hybrid | LocAgent; Claude Code; Sourcegraph |

### 6.3 Proposed `memory` helper (P-B) — Mem0-shaped, SQLite-backed

State lives in the existing SQLite store pointed at a **persistent path** (cross-session) instead of the per-task temp dir. New dependencies: one small embedder served by the existing model server (or `sentence-transformers` on CPU). SQLite FTS5 is stdlib.

```python
memory.add(text, tags=None)              # sub-tier note construction (summary/keywords) → embed → insert; dedupe by similarity threshold
memory.search(query, k=5)                # hybrid: embedding cosine ⊕ FTS5 BM25 ⊕ recency/strength decay → k short strings
memory.note(chunk)                       # explicit A-MEM-style gisting of a text into a stored note
memory.forget(query=None, older_than=None)  # explicit delete + Ebbinghaus sweep (pure arithmetic)
memory.write_core(text)                  # pinned core-memory var persisted to disk (MemGPT core block, no tool calling)
```

Deliberate omissions: no graph DB (Zep), no LLM-decided UPDATE/DELETE (pre-2026 Mem0), no evolution pass on writes (A-MEM's expensive step — make it optional), no dedicated memory model (memoRAG). Conflict resolution = "new fact supersedes similar old fact" via a `superseded_by` column (Zep's one cheap idea).

### 6.4 Proposed `repo` helper family (P-C) — ranked by value ÷ cost

1. **`repo.outline(path)`** — tree-sitter skeleton (signatures + defining lines), Aider-style. Highest value/cost ratio in the literature; pure CPU.
2. **`repo.grep(pattern)`** + SQLite FTS5 trigram index — proven sufficient for Claude-Code-class agents.
3. **`repo.symbols(name)`** — qualified-name dictionary + BM25-over-symbols (LocAgent's load-bearing component); near-free on top of #1.
4. **`repo.map(budget_tokens)`** — import/reference graph + PageRank, sized to budget; the natural "always-on digest" injectable into REPL metadata.
5. **`repo.callers(fn)` / `repo.traverse(ids, hops, relations)`** — LocAgent-style BFS with fold/preview/full detail levels (tree-formatted output — LLM graph reasoning is format-sensitive).
6. **`repo.search(query)`** — hybrid FTS5 BM25 ⊕ optional dense (jina-code-0.5B GGUF), RRF fusion; dense optional.
7. **`repo.summary(dir)`** — hierarchical LLM summaries (DeepWiki pattern), bottom-up, content-hash cached, generated overnight by the sub-tier.
8. **`repo.reindex(changed)`** — content-hash incremental updates (SHA-256 per file + per-dir rollups). Mandatory plumbing, near-zero cost.

Persistence: **one SQLite file per repo** (SrcLight/Vexp convergent design): `files(hash, mtime)`, `symbols(qname, kind, signature, span)`, `edges(contain/import/invoke/inherit)`, FTS5 indexes, optional embedding blobs.

### 6.5 Explicit skip list

- **GraphRAG community reports** — 610K tokens/query, rebuild-on-insert, collapses with SLMs (MiniRAG table). Map-reduce summaries instead.
- **Zep/Graphiti** — graph DB server + structured-output extraction their README flags as small-model-hostile.
- **memoRAG** — 7B KV-compression memory model, 16–24 GB GPU.
- **Full MemGPT/Letta runtime** — Postgres + a function-calling loop the paper shows degrading below GPT-4 class.
- **Heavy embedders** (NV-Embed 7B, jina-v4 3.8B as default) — 300M–600M class suffices.
- **Reranker by default** — real gains (Anthropic 49%→67% failure reduction) but +1–2 s/query and another resident model; only if needle quality is the measured bottleneck.
- **Contextual Retrieval as default** — one LLM call per chunk at index time; consider only for small, high-value, repeatedly-queried corpora.
- **Late chunking** — needs a resident long-context embedder; marginal here.

### 6.6 Build order (maps to the spec's roadmap)

| Phase | Deliverable | Effort |
|---|---|---|
| R1 | `bm25` helper (BM25S or FTS5) over ContextStore + turn-0 routing table in `prompts.py` | days |
| R2 | `memory` module (§6.3) with persistent SQLite path + hybrid search + decay | 1–2 weeks |
| R3 | `repo` indexer: tree-sitter outline/symbols/edges → per-repo SQLite; `repo.grep/outline/symbols/map` helpers | 2–3 weeks |
| R4 | Optional `embed_search` (EmbeddingGemma/Qwen3-Emb GGUF) + RRF fusion, applied to memory and repo | 1 week |
| R5 | Overnight pipelines: `repo.summary` module trees; incremental reindex | 1 week |
| R6 (optional, evals must demand it) | HippoRAG-lite: triples table + numpy PPR for associative multi-hop | 2+ weeks |

### 6.7 Open questions to measure (don't trust, verify)

1. Does `bm25` materially beat `grep`+`chunk` on your actual corpora? (BEIR says lexical is a strong floor; measure the delta.)
2. Sub-tier (4B) note-construction quality for `memory.add` — A-MEM's 1.5–3B evidence is encouraging but on different models; run the conformance suite on note construction before enabling writes.
3. Semantic-needle rate in real usage — if rare, defer `embed_search` entirely (R4 is the most deferrable item).
4. CPU embedding throughput on the target machine (all published numbers are GPU); this decides R4/R5 feasibility.
5. Whether `repo.summary` summaries stay in-distribution for the root model (measure root-context trajectory similarity with/without, per the spec's §10.3 method).

---

## References (grouped; all URLs verified during research unless flagged)

**Long-context vs retrieval:** [LOFT](https://arxiv.org/abs/2406.13121) · [Self-Route](https://arxiv.org/abs/2407.16833) · [NVIDIA LC-vs-RAG](https://arxiv.org/abs/2501.01880) · [BrowseComp-Plus](https://arxiv.org/abs/2508.06600) · [RULER](https://arxiv.org/abs/2404.06654) · [NoLiMa](https://arxiv.org/abs/2502.05167) · [LongMemEval](https://arxiv.org/abs/2410.10813) · [OOLONG](https://arxiv.org/abs/2511.02817) · [FreshStack](https://arxiv.org/abs/2504.13128) · [Seven Failure Points](https://arxiv.org/abs/2401.05856) · FanOutQA ([site only](https://fanoutqa.com/), arXiv ID [UNVERIFIED])

**Graph/agentic retrieval:** [GraphRAG](https://arxiv.org/abs/2404.16130) · [LightRAG](https://arxiv.org/abs/2410.05779) + [repo](https://github.com/HKUDS/LightRAG) · [MiniRAG](https://arxiv.org/abs/2501.06713) · [nano-graphrag](https://github.com/gusye1234/nano-graphrag) · [IRCoT](https://arxiv.org/abs/2212.10509) · [FLARE](https://arxiv.org/abs/2305.06983) · [Self-RAG](https://arxiv.org/abs/2310.11511) · [Search-R1](https://arxiv.org/abs/2503.09516) · [Agentic RAG survey](https://arxiv.org/abs/2501.09136) · [OpenAI Deep Research](https://openai.com/index/introducing-deep-research/)

**Hybrid & local machinery:** [Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) · [Blended RAG](https://arxiv.org/abs/2404.07220) · [Late chunking](https://arxiv.org/abs/2409.04701) · [BEIR](https://arxiv.org/abs/2104.08663) · [BM25S](https://github.com/xhluca/bm25s) · [EmbeddingGemma](https://developers.googleblog.com/en/introducing-embeddinggemma/) · [Qwen3-Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [jina-embeddings-v4](https://huggingface.co/jinaai/jina-embeddings-v4) + [GGUF benchmarks](https://jina.ai/news/optimizing-ggufs-for-decoder-only-embedding-models/) · [BGE-M3](https://arxiv.org/abs/2402.03216) · [nomic-embed](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)

**Memory systems:** [MemGPT](https://arxiv.org/abs/2310.08560) · [Letta docs](https://docs.letta.com/overview) · [Mem0](https://arxiv.org/abs/2504.19413) + [README](https://raw.githubusercontent.com/mem0ai/mem0/main/README.md) · [Zep/Graphiti](https://arxiv.org/abs/2501.13956) + [README](https://raw.githubusercontent.com/getzep/graphiti/main/README.md) + [Zep blog](https://blog.getzep.com/state-of-the-art-agent-memory/) · [A-MEM](https://arxiv.org/abs/2502.12110) + [repo](https://github.com/WujiangXu/A-mem-sys) · [MemoryBank](https://arxiv.org/abs/2305.10250) (repo 404) · [ReadAgent](https://arxiv.org/abs/2402.09727) · [memoRAG](https://arxiv.org/abs/2409.05591) · [HippoRAG](https://arxiv.org/abs/2405.14831) + [HippoRAG 2](https://arxiv.org/abs/2502.14802) · [Memory survey](https://arxiv.org/abs/2404.13501)

**Codebase digestion:** [Aider repo-map](https://aider.chat/docs/repomap.html) · [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) · [Cursor indexing](https://cursor.com/blog/secure-codebase-indexing) · [Blackbird](https://github.blog/2023-02-06-the-technology-behind-githubs-new-code-search) · [Sourcegraph Cody](https://sourcegraph.com/blog/how-cody-understands-your-codebase) · [tree-sitter](https://tree-sitter.github.io/tree-sitter/) · [LocAgent](https://arxiv.org/abs/2503.09089) · [SrcLight](https://news.ycombinator.com/item?id=47141625) · [Vexp](https://news.ycombinator.com/item?id=47113273) · [CoIR](https://arxiv.org/abs/2407.02883) · [jina-code-embeddings](https://jina.ai/news/jina-code-embeddings-sota-code-retrieval-at-0-5b-and-1-5b/) · [Agentless](https://arxiv.org/abs/2407.01489) · [SWE-Fixer](https://arxiv.org/abs/2501.05040) · [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) · [SWE-Gym](https://arxiv.org/abs/2412.21139) · [DeepWiki](https://cognition.ai/blog/deepwiki) · [RepoAgent](https://arxiv.org/abs/2402.16667)

**RLM anchors:** [RLM paper](https://arxiv.org/abs/2512.24601) · [harness post](https://alexzhang13.github.io/blog/2026/harness/) · [RLM post](https://alexzhang13.github.io/blog/2025/rlm/) · local design spec `docs/20260721-1034-rlm-harness-small-models-design.md`
