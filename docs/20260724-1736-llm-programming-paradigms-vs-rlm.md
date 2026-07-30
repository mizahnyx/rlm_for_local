# One Research Program, Four Terms: DSPy, PEEK, GEPA, and RLM — Comparison Research Report

**Date:** 2026-07-24
**Status:** Research synthesis
**Companion documents:** harness design spec (`20260721-1034`), memory/retrieval report (`20260722-2118-rlm-memory-retrieval-research.md`), capability-extension report (`20260722-2118-rlm-capability-extension-research.md`), memory-wiki spec (`20260723-1324`), and the **rlm-kernel spec** (`20260724-1736-rlm-kernel-evolvable-spec.md`) that this report motivates.
**Method:** four parallel deep-research threads (DSPy deep dive; GEPA & text-level optimization; PEEK identification + paradigm survey; minimal-kernel precedents from LISP/Smalltalk/Nock/Forth/Unix). Primary sources where reachable; unverified items flagged **[UNVERIFIED]**.

---

## 1. Executive summary

The four terms are not four separate things to compare against the RLM approach — they are **one coherent research program**, largely from Omar Khattab's orbit (Stanford/Databricks/MIT), and it has already *merged* with the RLM line:

- **DSPy** = the *programming model* (typed signatures + composable modules + optimizers that compile prompts/demos/weights offline).
- **RLM** = the *inference architecture* (recursive decomposition in a code REPL; context offloaded; the harness carries the inductive bias).
- **GEPA** = the *optimizer* (reflective text evolution; beats GRPO with up to 35× fewer rollouts; now the flagship DSPy optimizer).
- **PEEK** = the *memory artifact* (a bounded, prompt-resident "context map" maintained programmatically — evaluated on top of RLM).

The school itself has operationalized the merger: **DSPy shipped `dspy.RLM` in Jan 2026**, and RLM-GEPA (optimizing an RLM harness's skill text offline) is a published, working pattern (+7.2pp on AppWorld). The school's own synthesis, in print: **architecture decides what is learnable; language-space optimization decides how cheaply it is learned** — the harness post shows ~10× eval-lift-per-train-lift from architecture; GEPA shows ~35× rollout efficiency over RL from language-space search.

For our broader question — is there a **minimal, unified, LISP/Smalltalk-like paradigm** for embedding LLMs in programming? — the answer from the landscape is *yes, and it is converging independently from four directions*: (1) the LLM call as an ordinary typed function in a host language (Instructor/Mirascope/DSPy/RLM), (2) all persistent state as bounded, curated **text artifacts** maintained by programs (PEEK, Skills, GEPA-evolved prompts — not vector DBs), (3) optimization as a compiler pass over text parameters (GEPA's `optimize_anything`), (4) a standardized tool/protocol pipe (MCP under the Linux Foundation). The classic-kernel analysis (LISP metacircular eval, Smalltalk image, Forth dictionary, Nock frozen core, Unix pipes) maps onto the RLM design almost term-for-term (§7) — and adds the one correction our era requires: **all growth passes through an evaluator gate**.

The deliverable shape this motivates (detailed in the kernel spec): **three primitives — REPL eval, advanced search, a human-readable indexed wiki — with everything else (ontology, capabilities, prompts, few-shots, memory) as content grown in-system through a verification gate.**

---

## 2. PEEK — identified

**PEEK: "Context Map as an Orientation Cache for Long-Context LLM Agents"** ([arXiv:2605.19932](https://arxiv.org/abs/2605.19932), May 2026; [blog](https://zhuohangu.github.io/blog-post-peek/); [repo](https://github.com/zhuohangu/peek)) — confidence ~90%.

- **What it is:** a small, constant-sized **context map** living in the agent's prompt that caches *orientation knowledge about a recurring external context* (what the corpus contains, how it's organized, which entities/schemas proved useful). Explicitly **a cache, not a summary, not RAG**. Maintained by a "programmable cache policy": a **Distiller** (extracts transferable knowledge from execution trajectories — no ground-truth labels needed), a **Cartographer** (turns it into structured ADD/DELETE/REPLACE edits with stable IDs), and an **Evictor** (fixed token budget).
- **Who:** Zhuohan Gu, Qizheng Zhang, **Omar Khattab**, Samuel Madden (MIT). Main experiments run **PEEK on top of RLM(GPT-5-mini)**; the blog thanks RLM's first author. Beats ACE (the prior prompt-learning framework) by 6.3–34.0% at 1.4–5.8× lower cost. Corroboration: an X post surfaces all four terms in one sentence — *"DSPy, RLM, PEEK, GEPA is context engineering, you can compose, measure, eval and improve"* **[source unverifiable — X not scrapable]**.
- **Why it matters to us:** PEEK is the missing **"active external-context state"** quadrant — and its future-work section describes "collections of caches that agents interact with through programs" — i.e., our wiki memory, sharded. Its bounded-budget policy (Distill→Cartograph→Evict) is a proven alternative to unbounded memory growth (the "image rot" risk in §8).
- **Caveats:** `pip install peek-ai` 404s on PyPI as of 2026-07-24 **[UNVERIFIED packaging]**; only validated on frontier-scale models — small-model behavior unproven. Runner-up candidates for "PEEK" (a KV-cache scheduler, a robotics paper) don't fit the discourse; also possible the user heard the RLM blog's own verb ("peek at the context") — the system's name is surely a nod to it.

---

## 3. The lineage, as one program

| Date | Artifact | Layer it owns |
|---|---|---|
| 2020 | ColBERT ([arXiv:2004.12832](https://arxiv.org/abs/2004.12832)) | retrieval composition |
| Dec 2022 | DSP ([arXiv:2212.14024](https://arxiv.org/abs/2212.14024)) | LM+RM pipelines, bootstrapped demos |
| Oct 2023 | DSPy ([arXiv:2310.03714](https://arxiv.org/abs/2310.03714), ICLR'24) | programs-as-text-transformation-graphs; compilers |
| Dec 2023 | DSPy Assertions ([arXiv:2312.13382](https://arxiv.org/abs/2312.13382)) | validators + self-refinement (→ Refine/BestOfN) |
| Jun 2024 | MIPROv2 ([arXiv:2406.11695](https://arxiv.org/abs/2406.11695)) | joint instruction+demo optimization |
| Jul 2025 | **GEPA** ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457), ICLR'26 Oral) | reflective text evolution |
| Aug 2025 | DSPy 3.0 + Arbor ([arXiv:2508.04660](https://arxiv.org/abs/2508.04660)) | GRPO on programs; CodeAct; BetterTogether |
| Dec 2025 | **RLM paper** ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601)) | recursive inference; context offloading |
| **Jan 2026** | **`dspy.RLM` ships** ([release](https://github.com/stanfordnlp/dspy/releases)) | the harness becomes a module |
| May 2026 | **PEEK** ([arXiv:2605.19932](https://arxiv.org/abs/2605.19932)) | orientation-cache memory on RLM |
| Jul 2026 | harness post ([blog](https://alexzhang13.github.io/blog/2026/harness/)) | harnesses as compositional generalizers |
| Jul 2026 | `predict-rlm` on PyPI ([repo](https://github.com/Trampoline-AI/predict-rlm)) | RLM + DSPy signatures + RLM-GEPA, productized |

DSPy's own docs timeline lists RLM as part of the DSPy lineage. Current versions (verified 2026-07-24): DSPy 3.2.1 stable / 3.3.0b1 (ReActV2, typed BaseLM, SandboxSerializable); GEPA 0.1.4; dspy.RLM experimental.

---

## 4. RLM vs DSPy — the systematic comparison

The key realization: **they are different layers, already composed upstream.** The honest question for us is not "which" but "what to import."

| Axis | DSPy | RLM (paper + `rlm_local`) |
|---|---|---|
| What it is | Programming model: signatures + modules + offline compilers | Inference architecture: root LM in a code REPL, runtime decomposition |
| When "learning" happens | Compile-time (offline search over instructions/demos/weights) | Run-time (per-instance decomposition); RL at train-time in the paper |
| Control flow | Fixed DAG by the programmer; bounded loops inside nodes | Open-ended REPL; the model decides decomposition per instance |
| Task interface | Typed signature (Pydantic fields; docstring *is* the instructions) | Plain text in, answer dict out — minimal format tax on weak models |
| Context economics | Context in the prompt (adapters render it) | Context **offloaded**; root sees metadata + truncated prints (LID) |
| Generalization mechanism | Instructions generalize across inputs of one program (demos overfit more) | Equivalence classes over trajectories → 8–32× length + cross-domain transfer |
| What's optimized | Named predictors' text surface and weights | Nothing by default (prompted); RL in the paper (8×H100 territory) |
| Failure modes | Needs metric+examples; baseline saturation starves the optimizer; compile cost | Compounding per-step reliability; shortcut strategies; code errors |
| Cost shape | Expensive compile, cheap serve | No compile, expensive serve (many calls/query) |
| Small-model posture | Teacher-compile documented (strong proposes, small executes); TwoStepAdapter for format-weak models | Architectural accommodation: LID offloading, boring prompts, helpers, rescue parsing |

**Is an RLM expressible as a dspy.Module?** It exists: `dspy.RLM(signature, max_iters, max_llm_calls, tools, sub_lm, interpreter_factory)` in a Deno/Pyodide sandbox, with `SUBMIT` validated against the signature. It buys typed outputs, optimizer compatibility, tracing, save/load. It does **not** fit our regime: sandbox has no filesystem/network (vs our ContextStore/grep), "~500K-char llm_query" claims are frontier-model fiction at 4B, no per-call size warnings, no two-tier routing beyond `sub_lm`, Deno dependency, experimental API churn. **Verdict: port DSPy's mechanisms into `rlm_local`; don't adopt `dspy.RLM` wholesale** (though it's a cheap controlled experiment via a custom `interpreter_factory`).

**"DSPy optimizing an agent harness's prompts" already has receipts:** dspy.RLM docs state GEPA/MIPROv2 "improves the loop's behavior, not just the task instructions"; **RLM-GEPA on AppWorld**: PredictRLM(GPT-5.5-low) 0.839→**0.911 SGC** (+7.2pp), transfer to held-out split; GEPA's TerminalBench adapter optimizes the Terminus agent's system prompt; Google ADK rewrites root-agent instructions; Instacart MAMuT jointly optimizes multi-agent trajectories (84.7% vs 77.1% localized). One caveat from the AppWorld data: artifacts optimized through a *weak proxy* executor lost −4.2pp when run on a *stronger* model — **optimize against the model you deploy** (directly relevant to our two-tier setup).

---

## 5. The paradigm landscape: convergence to a minimal kernel

### 5.1 What died or sank into infrastructure

- **LMQL** ("SQL for LLMs") — unmaintained (last release Oct 2023); constrained decoding sank into the serving layer (xgrammar/llguidance/outlines-core are commodity llama.cpp/vLLM features). **TypeChat** similarly absorbed by native structured outputs. *Lesson: any paradigm that duplicates what the decoder enforces gets eaten by the server.*
- **Framework sprawl persists by star count** (LangChain 142k★, LlamaIndex 51k★, Haystack 3.0 days old) but the intellectual gravity is elsewhere: the "LLM call as plain function" libraries (Instructor, Mirascope "the anti-framework," Marvin, BAML), the minimalists (smolagents ~2 primitives; Karpathy's microgpt, Feb 2026, ~200 lines), and the Khattab cluster.

### 5.2 The four converging lines

1. **LLM call = ordinary typed function in a host language.** Instructor/Mirascope/Marvin/BAML/DSPy-Modules/smolagents/RLM all reject framework graphs; composition = function composition. RLM adds the LISP-like property: **self-application** (recursion — the callee is the same kind of value as the caller).
2. **Persistent state = bounded, curated text artifacts maintained by programs.** PEEK's context map (fixed budget, Distiller/Cartographer/Evictor), Agent Skills (SKILL.md, three-level progressive disclosure, open standard since Dec 2025), CLAUDE.md culture, GEPA-evolved prompts. *Diffable, versionable documents — not vector DBs, not hidden KV state.*
3. **Optimization = compiler pass over text parameters.** GEPA's `optimize_anything` ("if you can measure it, you can optimize it") treats prompts, code, agent architectures, and configs as one kind of thing, with adapters that optimize *other people's frameworks from outside* — a meta-layer, not a framework.
4. **Standardized tool pipe.** MCP donated to the Agentic AI Foundation (Linux Foundation; Anthropic/Block/OpenAI co-founders) — the Unix "pipe" of the stack.

### 5.3 The emerging kernel shape (synthesis)

If text is the universal medium, the minimal kernel has ~4 primitives:

- **Value:** the signature-typed *text→text function* (DSPy Signature / Instructor model / RLM call) — one kind of thing, like the s-expression.
- **Combinator:** host-language application, including **recursion** (the RLM REPL) — the eval/apply analogue.
- **Environment/symbol table:** bounded text artifacts under programmatic maintenance (PEEK map, skills, wiki memory).
- **Macro/compiler:** reflective text optimization (GEPA) — programs that rewrite programs-as-text.
- **The single reduction rule:** *text × context → text*, with chunking, retrieval, recursion, and memory built **in-system by the model itself** — verbatim the RLM design principle.

---

## 6. GEPA and text-level optimization — local economics

**Mechanism** ([paper](https://arxiv.org/abs/2507.19457), [repo](https://github.com/gepa-ai/gepa), [docs](https://gepa-ai.github.io/gepa/)): select a candidate prompt-set from the **Pareto frontier** (per-instance score vectors preserve diverse specialists) → execute on a minibatch capturing full traces + evaluator feedback (**"actionable side information"** — the text analogue of a gradient) → a **reflection LM** diagnoses failures in natural language and writes an improved prompt → accept mutants that beat parents on the minibatch → Pareto update; system-aware merge recombines specialists. Budget denominated in **metric calls**.

**Numbers:** beats GRPO by 6% avg / up to 20% with **up to 35× fewer rollouts** (v2 abstract; v1 said 10% avg — revised down); beats MIPROv2 by >10% (+12% AIME-2025); 100–500 metric calls vs 5,000–25,000+ for GRPO. Adoption mid-2026: flagship `dspy.GEPA`; Databricks (90× cheaper serving), Dropbox (gemma-3-12b malformed JSON 40%→<3%), Nubank, Microsoft MAI, Google ADK, OpenAI/HF Cookbooks. TextGrad (published in Nature, Mar 2025) lost head-to-head to GEPA; MIPROv2/SIMBA are strictly dominated for our use; APE/OPRO/PromptBreeder/EvoPrompt are ancestors.

**Local feasibility (the part that matters to us):**
- Requirements: task LM behind any OpenAI-compatible endpoint (llama-server qualifies), reflection LM via the same protocol, ≥3 examples (20–100 is the sweet spot), a metric returning **score + textual feedback**, trajectories as text (our `TrajectoryLogger` JSONL is the right raw material).
- **Proven local runs:** Llama-3-8B student + Qwen2.5-Coder-7B reflector → 62.8%→80.3%; GEPA-Lite with a ~4B reflector; Liquid-LFM-1.2B student 45%→70% in 5 mutations. Reflection quality measurably matters (GPT-4o-mini→4.1-mini raised F1 41.4→45.4 in one study), so use the strongest local tier (8B root) as reflector while the 4B sub-tier is the student.
- **Wall-clock on our boxes:** sub-call-tier artifact (retry-nudge wording, map_query templates): metric call ≈ 10–20 s; 300–500 calls + ~20–50 reflection calls ≈ **one overnight run**. Root-tier artifact (system prompt/prologue; metric call = full harness run ≈ 4–8 min): 150 calls ≈ 10–20 h; 500 ≈ a weekend. Every candidate is text — runs checkpoint/resume naturally.
- **Saturation trap:** a too-strong student yields all-perfect minibatches → no failure signal → no mutations. Keep eval tasks in the 30–70% success band (our 4B fails plenty — abundant signal).
- **Governance ("prompt CI" is standard practice):** version in git, golden-suite eval on every change, **held-out gate before promotion**, instant rollback, lineage archive (DGM lesson: their agent faked test logs and deleted detection markers — keep the evaluator itself human-gated). GEPA-optimized prompts that reward-hack *verbalize the hack in the text* — sanitizable, unlike RL.

---

## 7. The classics, mapped (LISP / Smalltalk / Nock / Forth / Unix)

| Classic kernel element | System | LLM-REPL-wiki analog |
|---|---|---|
| 7 primitives + lambda | LISP (McCarthy 1960) | REPL builtins: `llm_query`, `llm_query_batched`, `peek/grep/chunk/map_query`, `answer` |
| `eval` written in itself | LISP | The REPL executing model-written code that orchestrates sub-calls; system prompt = self-description in the system's own medium |
| Homoiconicity (code=data) | LISP | Text universality: prompts, pages, tool docs, data — one medium the model can write |
| Macros (user-level language growth) | LISP | Persisted helpers (`save_tool`), SKILL.md-style procedure pages |
| Objects + messages; "messaging is the big idea" | Smalltalk | Wiki page + wikilink/search hit; capabilities addressed by name, found by search, invoked via REPL |
| The **image** (persistent live state) | Smalltalk | The git-versioned wiki vault — knowledge *and* capabilities as one artifact |
| Total reflection / system browser | Smalltalk | Hybrid search over the vault's own pages (backlinks = senders; tool search = implementors) |
| Class definition at runtime | Smalltalk | Authoring a definition/tool page → reindex → vocabulary grows without code change |
| Nock (~40-line frozen spec) | Urbit | The frozen REPL contract + message templates + helper signatures + page schema |
| Event log as pure function of state | Urbit | Git history + append-only trajectory/audit logs → replayable |
| Jets (verified fast paths) | Urbit | Host-side optimized helpers validated against REPL-visible semantics (`grep` over ContextStore) |
| Dictionary of words | Forth | SQLite tool index; user words ≡ builtin words (`save_tool` is `:`) |
| IMMEDIATE / metacompilation | Forth | Tools that author tools; skills that write skills |
| Byte streams + pipes | Unix | REPL variables carrying bulk between calls; only deliberate `print()`s cross into the model |

Two warnings from history: **Urbit** shows a frozen core must freeze *late* (after the protocol proves itself — and keep a jets-like escape hatch), and minimalism alone doesn't compound an ecosystem; **Smalltalk's image** shows the one thing to improve on — the image wasn't human-readable, and our wiki corrects that (human-readability is what makes the review *gate* possible).

## 8. What compounds in self-growing systems

The meta-pattern from the modern evidence: **(persistent artifact) × (retrieval mechanism) × (verification gate)** — remove any leg and it stalls.

| Pattern | Verdict | Evidence |
|---|---|---|
| Skill libraries + embedding retrieval (Voyager pattern) | **Compounds** | zero-shot transfer; DynaSaur +7% from accumulation; Anthropic industrialized it (Skills) |
| Tool persistence behind a review gate (LATM pattern) | **Compounds** | functional-cache economics; Tool Search 85% context cut with accuracy up |
| Prompt/program evolution **with automated eval gates** | **Compounds** | AlphaEvolve production results (0.7% of Google's fleet); OpenEvolve reproductions (local-model support); DGM 20→50% SWE-bench with ablations |
| Progressive disclosure / search-over-library | **Settled** | Anthropic/Cloudflare/Speakeasy/Skills |
| Capability-as-content (SKILL.md, MCP resources) | **Compounding now** | Skills open standard; MCP at Linux Foundation |
| Full self-modifying agents (ADAS-style) | **Partially** | needs archive + benchmarks (DGM ablations); frontier-models-only |
| Agent-grown ontology without curation | **Stalled/mixed** | extraction works (Zep/Graphiti), vocabulary generation doesn't; consolidation unsolved |
| Ungated self-extension | **Failed** | MCP tool poisoning, rug pulls, toxic flows |
| Self-improvement without an external evaluator | **Stalled** | every compounding system has a ground-truth gate |

---

## 9. Design principles for a minimal evolvable LLM kernel

Distilled from the classics + the compounding evidence (each with its risk):

- **P1 — One universal medium: text.** All state, code, knowledge, procedures, ontology are human-readable text in one store. *Risk:* retrieval becomes the bottleneck → schema'd frontmatter so structure is machine-checkable while bodies stay prose.
- **P2 — One eval: the REPL.** A single locus of execution; everything else is data. *Risk:* the evaluator is the whole attack surface — sandboxing and guardrails are kernel features, not niceties.
- **P3 — The image is a human-readable, versioned wiki.** Persistent live state as markdown + git; indexes derived and rebuildable. *Risk:* image rot → decay arithmetic, `superseded_by`, PEEK-style bounded budgets, periodic compaction.
- **P4 — Vocabulary grows by definition pages, not code changes.** Extending ontology or capabilities = authoring a page. *Risk:* definition-quality drift → the gate applies to pages, not just code.
- **P5 — Every capability is content retrievable by search.** Never the library in context; a search tool in context. Introspection = search over the system's own pages. *Risk:* retrieval quality is the primary bottleneck → multi-field indexing, hybrid search, per-tool examples.
- **P6 — The system describes itself to itself.** The kernel ships its self-description as searchable pages (REPL contract, helper docs, conventions). *Risk:* self-description ≠ self-understanding — the mirror must face an evaluator.
- **P7 — Frozen kernel, liquid above.** Freeze the REPL contract, message templates, helper signatures, page schemas; everything else is upgradeable content. *Risk:* freeze too early (Urbit's lesson) → keep the frozen set smaller than feels safe, and version it.
- **P8 — All growth passes through an evaluator gate.** Quarantine → automated validation → review → promotion. The LLM-era correction to the classics. *Risk:* reward hacking (gate the evaluator itself), gate throughput (auto-validation carries weight, humans sample), over-gating kills liveliness (quarantined code stays usable-in-sandbox before promotion).

---

## 10. What this means for rlm_local (import list, ranked by value ÷ effort)

1. **GEPA offline optimization of the harness's textual surface** (prologue, "How to work" block, nudge/warning templates, few-shots) via `gepa.optimize_anything` or a `dspy.Module` wrapper — zero-rewrite path exists. Our templated harness warnings are *already* GEPA-style actionable feedback; return them as `feedback` alongside the score. Budget: 150–300 metric calls (demonstrated working) = a weekend on `laptop`. Keep eval tasks in the 30–70% success band; held-out gate before promotion.
2. **Strong-reflection / local-student split:** student = 4B sub-tier (runs constantly), reflection = 8B root-tier (called ~12–36 times/run) — asymmetry we already have. Fully local; no frontier API needed.
3. **Typed sub-calls (the predict-rlm pattern):** centralize parse-repair (DSPy `parse_value` style: json_repair → literal_eval → raw, ~30 lines replaces scattered repair code); a **TwoStepAdapter equivalent** for the 4B sub-tier (free-form answer → tiny extractor pass into schema) — cheaper and more robust than GBNF on values.
4. **Validators as Refine:** unify the rescue-parse/nudge/error-budget pipeline with the metric/feedback vocabulary — same strings serve runtime retries and compile-time reflection. BestOfN on the final answer only.
5. **Bootstrap few-shot selection:** run the trainset, keep verified-correct trajectories, auto-select the 2–3 canonical transcripts (BootstrapFewShot mechanism) — doubles as the rejection-sampling pool for the QLoRA path (BetterTogether sequencing: prompt-optimize first, distill second; DSPy's own RL tutorial admits prompt optimizers are "typically better on cost/quality" for small LMs).
6. **PEEK-style bounded orientation cache** for recurring contexts: a fixed-token "context map" page per corpus, maintained by a Distill→Cartograph→Evict policy — our wiki note layer with a budget. Adopt the *policy*, not the package (PyPI 404; frontier-only validation).
7. **Skip:** dspy.RLM wholesale (sandbox too weak for our regime), Arbor/GRPO (4 GPUs, 18 h for +4.4pp), MIPROv2/SIMBA at root-rollout budgets (dominated by GEPA), ADAS/DGM free-form self-rewriting (frontier-only, safety surface).

**The honest open problem** (flagged by every thread): the entire DSPy/RLM/PEEK/GEPA cluster was demonstrated on frontier-class models; nothing in this research proves small local models can hold the root-LM planning role at the same level. Our own guardrail-heavy harness is the accommodation; the convergence of *small-model-robust* practice today is: decoding-layer constraints (xgrammar via llama.cpp), endpoint-agnostic typed-function libraries, and offline optimization — the optimizer/memory layers are the research frontier, which is exactly where this project sits.

---

## 11. References (anchors; full URL sets in the thread logs)

- **The program:** [DSP](https://arxiv.org/abs/2212.14024) · [DSPy](https://arxiv.org/abs/2310.03714) + [docs](https://dspy.ai/) + [releases](https://github.com/stanfordnlp/dspy/releases) · [GEPA](https://arxiv.org/abs/2507.19457) + [repo](https://github.com/gepa-ai/gepa) + [showcase](https://gepa-ai.github.io/gepa/guides/use-cases/) · [PEEK](https://arxiv.org/abs/2605.19932) + [blog](https://zhuohangu.github.io/blog-post-peek/) · [RLM](https://arxiv.org/abs/2512.24601) + [posts](https://alexzhang13.github.io/blog/2026/harness/) · [dspy.RLM deep-dive](https://dspy.ai/diving-deeper/rlm/index.md) · [predict-rlm](https://github.com/Trampoline-AI/predict-rlm)
- **Optimization lineage:** [APE](https://arxiv.org/abs/2211.01910) · [ProTeGi](https://arxiv.org/abs/2305.03495) · [OPRO](https://arxiv.org/abs/2309.03409) · [PromptBreeder](https://arxiv.org/abs/2309.16797) · [TextGrad](https://arxiv.org/abs/2406.07496) · [MIPROv2](https://arxiv.org/abs/2406.11695) · [SIMBA](https://dspy.ai/api/optimizers/SIMBA/) · [Arbor](https://arxiv.org/abs/2508.04660)
- **Self-growing systems:** [AlphaEvolve](https://deepmind.google/discover/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) · [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) · [ADAS](https://arxiv.org/abs/2408.08435) · [Darwin-Gödel Machine](https://arxiv.org/abs/2505.22954) · [Voyager](https://arxiv.org/abs/2305.16291) · [LATM](https://arxiv.org/abs/2305.17126) · [DynaSaur](https://arxiv.org/abs/2411.01747) · [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) · [self-evolving agents survey](https://arxiv.org/abs/2507.21046)
- **Paradigms:** [LMQL (dead)](https://pypi.org/project/lmql/) · [Guidance](https://pypi.org/project/guidance/) · [Outlines](https://pypi.org/project/outlines/) · [Instructor](https://github.com/567-labs/instructor) · [Mirascope](https://pypi.org/project/mirascope/) · [smolagents](https://huggingface.co/blog/smolagents) · [microgpt](https://karpathy.github.io/2026/02/12/microgpt/)
- **Classics:** [McCarthy 1960](http://www-formal.stanford.edu/jmc/recursive.pdf) · [Roots of Lisp](http://www.paulgraham.com/rootsoflisp.html) · [Smalltalk](https://en.wikipedia.org/wiki/Smalltalk) · [Nock 4K spec](https://raw.githubusercontent.com/urbit/urbit/master/doc/spec/nock/4.txt) · [Urbit](https://en.wikipedia.org/wiki/Urbit) · [Forth](https://en.wikipedia.org/wiki/Forth_(programming_language)) · [Unix pipes](https://en.wikipedia.org/wiki/Pipeline_(Unix))
