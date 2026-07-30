# RLM × Capability Extension — Research Report

**Subtitle:** What other harnesses call "tool calling," re-founded on code-as-interface — the evidence, the scaling architecture, and the security bill
**Date:** 2026-07-22
**Status:** Research synthesis
**Companion to:** `20260721-1034-rlm-harness-small-models-design.md`, `20260722-2118-rlm-memory-retrieval-research.md`, and the `rlm_local` implementation
**Method:** three parallel deep-research threads (code-as-action evidence; large tool-library scaling; small-model reliability + agent security). Every key claim carries a URL; unverified items flagged **[UNVERIFIED]**.

---

## 1. Executive summary

Your intuition — that the RLM pattern is a more LLM-natural capability-extension mechanism than JSON tool calling — is **supported by the evidence, with one important asterisk**.

The support: CodeAct measured up to **+20.7pp success with ~30% fewer turns** over JSON actions (GPT-4 class); smolagents' GAIA reproduction scored **55.15% with code actions vs 33% after switching the same setup to JSON**; Anthropic now ships **Programmatic Tool Calling** and reports tool-definition loads of 150K→2K tokens (**98.7% reduction**) via code-execution-with-MCP; Cloudflare's Code Mode argues LLMs are simply better at *writing code that calls tools* than at emitting tool-call tokens ("LLMs have seen a lot of code; they have not seen a lot of tool calls"). The 2025–2026 industry convergence is unambiguous: tools presented as **code APIs, discovered progressively, executed in a sandbox, with bulk results kept in the execution environment**.

The asterisk: the code-advantage concentrates in capable models. On 2024-era 7B models, CodeAct and JSON both scored ≈0–5%, and JSON sometimes won (Mistral-7B: 0.0% code vs 3.7% JSON). Two things changed since: modern 4B–12B generalists (Qwen3-4B-Instruct-2507 class, self-reported BFCL-v3 61.9) are dramatically stronger coders, and the RLM paper itself shows a post-trained **Qwen3-8B operating a recursive code REPL successfully (+28.3% avg over base)**. No published head-to-head exists for quantized 1B–12B local models — so the harness should be built format-tolerant and measured on its own tasks (§6.5).

The defensible claim isn't "code beats JSON." It is what the evidence supports overwhelmingly:

> **Execution-environment-mediated context management beats everything-through-the-model** — and a small local model, with the smallest context of all, needs it most.

For scaling to hundreds of tools, the architecture is settled: **never put the library in context; put a search tool in context** (Anthropic Tool Search: 85% context cut with accuracy *up*, Opus 4 49%→74%), index tools locally (SQLite + embeddings — trivially cheap at 50K tools), show 3–5 tools at a time, and let the LLM **grow the library itself** by persisting its own working functions (LATM/Voyager pattern) behind a review gate. The security bill for all this is real and itemized in §5 — prompt injection via tool outputs is the top threat, and the mitigations are architectural, not prompt-level.

---

## 2. The case for code-as-interface (and its limits)

### 2.1 Lineage

Code as Policies ([arXiv:2209.07753](https://arxiv.org/abs/2209.07753), 2022) → ProgPrompt ([arXiv:2209.11302](https://arxiv.org/abs/2209.11302)) → VisProg ([arXiv:2211.11559](https://arxiv.org/abs/2211.11559)) / ViperGPT ([arXiv:2303.08128](https://arxiv.org/abs/2303.08128)) → Chameleon ([arXiv:2304.09842](https://arxiv.org/abs/2304.09842), +11.4pp ScienceQA) / PAL → **CodeAct** ([arXiv:2402.01030](https://arxiv.org/abs/2402.01030), ICML 2024) and TaskWeaver ([arXiv:2311.17541](https://arxiv.org/abs/2311.17541)) → DynaSaur ([arXiv:2411.01747](https://arxiv.org/abs/2411.01747), COLM 2025) → smolagents ([Jan 2025](https://huggingface.co/blog/smolagents)) → **Cloudflare Code Mode** ([Sep 2025](https://blog.cloudflare.com/code-mode/)) → **Anthropic Code-Execution-with-MCP / Programmatic Tool Calling** ([Nov 2025](https://www.anthropic.com/engineering/code-execution-with-mcp), [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)). The RLM is the recursive, context-centric generalization of this lineage.

### 2.2 CodeAct — the anchor numbers, including the caveat

From the [full paper](https://arxiv.org/html/2402.01030v4) (M3ToolEval, 82 multi-tool multi-turn tasks):

- gpt-4-1106-preview: **74.4% code vs 52.4% JSON vs 53.7% text**, with 5.5 vs 7.6 turns. gpt-3.5: 51.2% vs 26.8% (JSON). Overall: up to +20% success, up to −30% actions; CodeAct better for 12 of 17 models.
- **The caveat that matters for us:** gains concentrate in frontier models. 2024-era open 7B models scored near-zero in *every* format (CodeLlama-7B 4.9% code; Mistral-7B 0.0% code vs 3.7% JSON; best open model lemur-70B 13.4% vs 74.4% GPT-4). Format does not rescue a weak model.
- CodeActInstruct (7,139 trajectories, deliberately keeping error→self-repair episodes) fine-tunes: CodeActAgent-Mistral matched 70B-class open models; the Llama-2 variant showed **no** improvement — benefit is backbone-dependent. Lesson: modern code-strong backbones (Qwen coder lineage) are the right substrate.
- RLM-specific cross-check from the RLM paper ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601)): GPT-5-RLM beat "CodeAct with sub-calls" by a **median 130%** — naively adding sub-LLM calls to a code loop is *not* the same as the recursive context-offloaded design. Your architecture is the point, not just code-as-actions.

### 2.3 The 2025–2026 industry convergence

- **smolagents / Open Deep Research** ([Feb 2025](https://huggingface.co/blog/open-deep-research)): CodeAgent-based GAIA reproduction 55.15% (then-SoTA open framework); **same setup switched to JSON actions degraded to 33%** — a 22pp swing. Their security posture is candid: custom AST-level LocalPythonExecutor with import allowlists, and "**no local python sandbox can ever be completely secure**."
- **Anthropic, Code execution with MCP** ([Nov 2025](https://www.anthropic.com/engineering/code-execution-with-mcp)): tools as a `./servers/` filesystem of TypeScript APIs read on demand = **150,000 → 2,000 tokens (98.7%)**; intermediate results stay in the execution environment; agents persist reusable functions + SKILL.md. Follow-up **Programmatic Tool Calling** ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)): **−37% avg tokens** on complex research tasks; accuracy 46.5%→51.2% on their internal agentic benchmark; best for 3+ dependent calls, "less beneficial for simple single-tool invocations."
- **Cloudflare Code Mode** ([Sep 2025](https://blog.cloudflare.com/code-mode/)): MCP schemas → TypeScript API; model writes code against it in V8 isolates; **sandbox has no network, only RPC bindings to tools; API keys live in the supervisor, never in generated code**. Qualitative results; rapid ecosystem uptake (mcp-use, local Deno re-implementations).
- **Progressive disclosure economics**: Anthropic Tool Search ([docs](https://docs.claude.com/en/docs/agents-and-tools/tool-use/tool-search-tool)): 58 tools/5 servers ≈ 55K tokens → ~8.7K total (**85% cut**), up to 10,000 deferred tools, accuracy **up** (Opus 4 49%→74%, Opus 4.5 79.5%→88.1%) — selection accuracy degrades beyond ~30–50 tools in context; keep 3–5 hot tools loaded.

### 2.4 The contrarian evidence (steal from it too)

- **Speakeasy Dynamic Toolsets** ([Nov 2025](https://www.speakeasy.com/blog/how-we-reduced-token-usage-by-100x-dynamic-toolsets-v2)): *within* the JSON paradigm — `search_tools`/`describe_tools`/`execute_tool` with lazy schemas (schemas are 60–80% of tool tokens) → ~96% input-token reduction, 100% task success with Sonnet 4.5 on 40–400 tools. The context-economics win is separable from code-vs-JSON.
- **Zechner, "You don't need MCP"** ([Nov 2025](https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/)): Playwright MCP = 21 tools/13.7K tokens vs equivalent **CLI + README = 225 tokens**, loaded on demand. Amp's [How to Build an Agent](https://ampcode.com/how-to-build-an-agent): the effective core is "an LLM, a loop, and enough tokens."
- **xLAM / APIGen-MT** ([arXiv:2409.03215](https://arxiv.org/abs/2409.03215), [arXiv:2504.03601](https://arxiv.org/abs/2504.03601)): fine-tuned 1B–8B **JSON** function-calling models beat GPT-4o on BFCL/τ-bench — small-model JSON weakness is a *training* problem, not intrinsic to JSON. (But see §4: specialists are format-fragile through serving stacks.)

### 2.5 Synthesis for 4B–8B local models

Reliability expectation, honestly: **unproven either way, skewing toward code for modern small models** — because (a) modern 4B–12B generalists are far stronger coders than 2024 7Bs, (b) code composes calls in one turn (fewer sequential decisions to fail — the compounding-reliability math from the design spec §3), (c) REPL variables keep bulky outputs out of the small context, and (d) RLM-Qwen3-8B proves an 8B can run the recursive loop after training. But DynaSaur's general-32B-beats-coder-32B result says reasoning, not code specialization, is the bottleneck — and a 4B that can't parse *any* format reliably fails regardless. **Build format-tolerant (code REPL primary; a `tool_call(name, **kwargs)` shim is one line) and measure on your own tasks.**

---

## 3. Scaling to hundreds of tools

### 3.1 The problem and the settled answer

Tool definitions flood context ("agents connected to thousands of tools… hundreds of thousands of tokens before reading a request" — Anthropic). Selection accuracy degrades beyond ~30–50 tools in context (Anthropic docs); a synthetic stress test (RAG-MCP, [arXiv:2505.03275](https://arxiv.org/abs/2505.03275)) found sharp degradation beyond ~100 tools, with retrieve-then-call at 43.13% vs 13.62% all-in-prompt selection accuracy (directional; small eval **[flag]**). The settled answer across Anthropic, Cloudflare, Speakeasy, DynaSaur, and the academic retrieval line: **a search tool in context, not the library.**

### 3.2 Tool retrieval — the real bottleneck (budget effort accordingly)

- **ToolBench** ([arXiv:2307.16789](https://arxiv.org/abs/2307.16789), ICLR 2024): 16,464 real APIs; ToolLLaMA never sees the library — a trained dense retriever proposes candidates. Retriever NDCG@1: BM25 ≈ 18.5, Ada ≈ 57.5, trained retriever 78.0. Oracle-vs-retrieved pass rates show **retrieval quality, not the LLM, is the primary bottleneck at scale** (~2× gap). DFSDT (branching search) vs ReAct: 63.8% vs 35.3% avg pass rate.
- **AnyTool** ([arXiv:2402.04253](https://arxiv.org/abs/2402.04253)): hierarchical category→API retrieval + self-reflection, +35.4% over ToolLLM.
- **Gorilla** ([arXiv:2305.15334](https://arxiv.org/abs/2305.15334)): retriever quality swings accuracy enormously (−29% with weak retriever, −52% with BM25); retriever-aware training makes models robust to retrieved-doc noise.
- **ToolRet** ([arXiv:2503.01763](https://arxiv.org/abs/2503.01763), ACL 2025): 43k-tool corpus; **general-purpose IR models perform poorly on tool retrieval** — index tool docs with their quirks in mind (name, synthetic example queries, functionality fields — Multi-Field, [arXiv:2602.05366](https://arxiv.org/abs/2602.05366), Feb 2026; TDWA name-weighting, ScaleMCP, [arXiv:2505.06416](https://arxiv.org/abs/2505.06416)).
- **FitText** ([arXiv:2605.02411](https://arxiv.org/abs/2605.02411), May 2026): agent writes pseudo-descriptions and refines against retrieval feedback — +26.7 pts pass rate over static query retrieval. Let the model re-query the tool index mid-task.
- **Graph RAG-Tool Fusion** ([arXiv:2502.07223](https://arxiv.org/abs/2502.07223)): +71.7% mAP@10 when tools must be *chained* (dependency edges matter for composition).
- **Scale is a non-issue**: 50K tools × 384-dim float32 ≈ 75 MB; brute-force cosine is single-digit ms on CPU. sqlite-vec / FAISS / numpy all fine. The hard part is retrieval *quality*, not speed.

### 3.3 What accuracy to expect (benchmarks with hard numbers)

- **ToolHop** ([arXiv:2501.02506](https://arxiv.org/abs/2501.02506)): multi-hop tool use — best model GPT-4o **49.04%**. Composition is the hard part.
- **Tool Decathlon** ([arXiv:2510.25726](https://arxiv.org/abs/2510.25726), ICLR 2026): 604 MCP tools, 32 real apps, ~20-turn tasks — Claude-4.5-Sonnet **38.6%**, best open-weights 20.1%.
- **τ-bench** ([arXiv:2406.12045](https://arxiv.org/abs/2406.12045)): gpt-4o <50% task success; pass^8 <25% — reliability *across retries* collapses.
- Practical reading for 4B–8B local: single-tool calls with retrieval narrowed to ≤5 candidates: **60–80%** per call (TinyAgent 1.1B–7B + ToolRAG matched GPT-4-Turbo on their suite, [EMNLP 2024 demo](https://aclanthology.org/2024.emnlp-demo.9/); ToolACE-8B fine-tune hit BFCL SOTA, [arXiv:2409.00920](https://arxiv.org/abs/2409.00920)). Multi-hop chains: **≤30–50% end-to-end zero-shot** — compensate with retries, DFSDT-style branching, and persisted helper macros that collapse common chains into single calls (LATM economics, §3.5).

### 3.4 Tool documentation formats — what weak models parse best

- **BFCL v4 Format Sensitivity** ([blog](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html), Jul 2025; 39 models × 26 configs): JSON-schema docs give the highest accuracy across nearly all models (blog internally inconsistent on Python-vs-XML ordering **[flag]**); **Python ≈ JSON ≫ XML for return/call format, "particularly for smaller models"**; fine-tuned specialists (CoALM-70B, watt-tool-70B) collapse to near-0 under format shifts — overfitting to one serialization.
- **Anthropic, Writing effective tools** ([2025](https://www.anthropic.com/engineering/writing-tools-for-agents)): namespace tools (`asana_search` vs `jira_search`); unambiguous param names; concise-by-default outputs (206→72 tokens); resolve UUIDs to semantic names; consolidate CRUD into workflow-level tools ("schedule_event, not list_users+list_events+create_event"); **Tool Use Examples (few-shot call samples): 72%→90% on complex parameter handling** — the single strongest doc-format result.
- Edge-scale evidence: TinyAgent (ToolRAG + curated docs), Octopus v2 (2B on-device, [arXiv:2404.01744](https://arxiv.org/abs/2404.01744)), Less-is-More ([arXiv:2411.15399](https://arxiv.org/abs/2411.15399)): fewer tools in prompt → higher success, −70% execution time.
- Gap: no clean public ablation of doc-format × accuracy at 4B–8B quantized **[flagged gap]** — the above is extrapolation.

### 3.5 LLM-authored persisted tools — the compounding library

- **LATM** ([arXiv:2305.17126](https://arxiv.org/abs/2305.17126)): strong model makes a reusable Python tool *once*; cheap model reuses it at strong-model accuracy — a "functional cache." The canonical economics for a local harness: expensive authoring (root model, verified), cheap reuse (any tier).
- **Voyager** ([arXiv:2305.16291](https://arxiv.org/abs/2305.16291)): the persistent skill-library archetype — code skills stored with embedding-indexed descriptions, retrieved and composed; skills transfer zero-shot to new worlds.
- **CREATOR** ([arXiv:2305.14318](https://arxiv.org/abs/2305.14318)): disentangles tool-*writing* from tool-*using*; up to +15.3% on MATH-class tasks **[exact table unverified]**.
- **ToolMaker** ([ACL 2025](https://aclanthology.org/2025.acl-long.1266/)): converts papers/repos into callable tools with closed-loop self-testing — 80% of 15 complex scientific tasks.
- **Alita** ([arXiv:2505.20286](https://arxiv.org/abs/2505.20286)): minimal predefinition, agent generates task-specific MCPs at runtime — 75.15% pass@1 on GAIA validation.
- **DynaSaur** ([arXiv:2411.01747](https://arxiv.org/abs/2411.01747)): the in-loop version — new functions defined at runtime accumulate in a library, retrieved by docstring-embedding search; +61% relative from good initial tools, +7% more from accumulation; **61.91% of no-codegen failures were insufficient tooling**. Generated actions are low-complexity (cyclomatic ~3) — within small-model reach.
- Anthropic endorses the pattern operationally: agents persist code as reusable functions + SKILL.md metadata — *with* sandboxing caveats (§5).

### 3.6 MCP ecosystem, mid-2026

Registry: **>20,000 server records** on the official registry (name+version keyed; unique servers fewer **[flag]**). The 2026-07 protocol revision adds a stateless core, Tasks extension, server UIs, enterprise auth. For local stacks: **LM Studio is an MCP host since 0.3.17** (Jun 2025) with per-tool confirmation ([blog](https://lmstudio.ai/blog/lmstudio-v0.3.17)); **llama.cpp** has tool-call parsing via jinja templates for ~any model plus experimental built-in agent tools with explicit untrusted-environment warnings ([server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)); **Ollama** has no official MCP client found through Jul 2026 **[inference from absence]**. Gateways (e.g. [docker/mcp-gateway](https://github.com/docker/mcp-gateway)) front containerized servers with per-profile tool allowlists and secret isolation — the standard enterprise pattern, directly applicable as rlm_local's MCP bridge.

---

## 4. Reliability engineering for 4B–8B models (delta over the design spec)

The design spec §5.6 already ships rescue parsing, templated retry nudges, error budgets, and forced finalization (validated by the Forge playbook and field reports). The capability-extension research adds:

1. **Prefer generalist instruct models for the root** — Qwen3-4B-Instruct-2507 self-reports BFCL-v3 **61.9** (vs 53.0 GPT-4.1-nano) with large agentic gains over its predecessor (TAU1-Retail 48.7 vs 24.3) ([card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)). Format specialists (ToolACE, watt-tool) use custom `[func(...)]` syntax + bespoke system prompts — they collapse when the serving stack mangles their template (the March 2026 eval: 15–20% vs 85–97% for generalists). If you adopt a specialist (e.g. Hammer2.1, Qwen2.5-Coder-based, hermes parser), run its *exact* prompt/template/parser and re-validate through your stack.
2. **Escalation cascades** — after 2–3 failed attempts on a step, escalate *that step's* sub-call to a bigger model (14B/32B local or API) instead of burning iteration budget (RouteLLM, [arXiv:2406.18665](https://arxiv.org/abs/2406.18665); Minions protocol, [Ollama blog](https://ollama.com/blog)). NVIDIA's position paper argues SLMs are the right default for agentic sub-tasks with heterogeneous routing as the natural pattern ([arXiv:2506.02153](https://arxiv.org/abs/2506.02153)).
3. **Deterministic validators per tool** — schema/type/range checks whose *error text* becomes the retry context; plus post-hoc outcome checks on side effects (GoEx post-facto validation pattern, Gorilla ecosystem).
4. **Self-consistency on final answers only** — n=3–5 samples, majority vote ([arXiv:2203.11171](https://arxiv.org/abs/2203.11171)); affordable at 4B for a single synthesis call.
5. **Grammar-constrain the envelope, not the values** — refines the spec's loose-GBNF rule: constrain call structure (name + arg keys + types), leave string values free.
6. **Pin a per-model regression eval** — a fixed task suite per model×quant×template upgrade (`bfcl-eval` pip package + 20–50 harness-specific tasks). Model/template/quant interaction is the #1 silent breakage source.

---

## 5. Security engineering for code-executing agents

The threat model is not hypothetical, and it applies with *more* force to a harness that executes model-written code with tool functions.

### 5.1 Documented attacks (2024–2026)

- **The lethal trifecta** ([Willison, Jun 2025](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)): private-data access + untrusted content + external communication = trivial exfiltration. "Once an LLM agent has ingested untrusted input, it must be constrained so that it is impossible for that input to trigger any consequential actions." Guardrail detection rates are a failing grade; only architectural avoidance works.
- **MCP tool poisoning** ([Invariant Labs, Mar 2025](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)): malicious instructions hidden in tool *descriptions* (invisible to users, visible to models) → SSH key exfiltration; **rug pulls** (server changes description post-approval); **cross-server shadowing** (a malicious server reprograms behavior toward a trusted server without being invoked). Mitigations: pin/hash tool descriptions, show full args, cross-server dataflow controls.
- **Toxic flows** ([GitHub MCP, May 2025](https://invariantlabs.ai/blog/mcp-github-vulnerability)): malicious issue in a public repo → agent pulls private data into a public PR. Worked against Claude 4 Opus; "Always Allow" confirmation makes it invisible. Note from the [WhatsApp exploit](https://invariantlabs.ai/blog/whatsapp-mcp-exploited): "sandboxing the MCP server is not a relevant mitigation, as the attack solely relies on the agent's instruction-following" — **sandboxing is necessary, not sufficient**.
- **InjecAgent** ([arXiv:2403.02691](https://arxiv.org/abs/2403.02691)): indirect prompt injection via tool results succeeds 24% on ReAct GPT-4 — small models will be worse.
- **CVEs worth knowing**: [CVE-2025-53109](https://nvd.nist.gov/vuln/detail/CVE-2025-53109) — official MCP Filesystem server **symlink escape** from jailed dirs (directly relevant to `rlm_local`'s `open` jail: path-prefix checks are insufficient without symlink resolution); [CVE-2025-6514](https://nvd.nist.gov/vuln/detail/CVE-2025-6514) — mcp-remote command injection; [CVE-2025-32711](https://nvd.nist.gov/vuln/detail/CVE-2025-32711) — EchoLeak zero-click M365 Copilot exfiltration **[details partially verified]**.
- **Defenses literature**: AgentDojo benchmark ([arXiv:2406.13352](https://arxiv.org/abs/2406.13352)); **CaMeL** ([arXiv:2503.18813](https://arxiv.org/abs/2503.18813)) — control/data flow extracted from the *trusted* query so untrusted data can't influence program flow, 77% AgentDojo success with provable security vs 84% undefended; Design Patterns for Securing LLM Agents ([arXiv:2506.08837](https://arxiv.org/abs/2506.08837)).

### 5.2 Sandboxing, ranked for cross-platform local use

- **Cross-platform baseline (what rlm_local has + must extend):** subprocess + resource limits (POSIX rlimits; **Windows Job Objects** for memory/process-count + kill-on-close) + scrubbed environment. The only truly portable tier.
- **Linux:** bubblewrap — `--unshare-net`, ro-bind `/usr`, rw-bind jail, seccomp, `--die-with-parent`. Best effort/security ratio. (gVisor is stronger but heavier; nsjail similar tier.)
- **macOS:** `sandbox-exec` profiles — functional but long-deprecated by Apple **[verify on target version]**.
- **Windows:** Windows Sandbox is Pro/Enterprise-only and single-instance — poor fit for automated per-cell use; run the REPL inside WSL2/Docker with bwrap for untrusted workloads.
- **Container tier:** Docker with `--network none`, read-only rootfs, tmpfs jail, non-root, `--cap-drop ALL` — works on all three OSes via Docker Desktop/OrbStack.
- **In-process "sandboxes":** RestrictedPython's own docs say it is **not a sandbox**. smolagents' AST executor is a good *complement* (import allowlist, op caps), never a boundary.
- **The strongest pattern to copy — Cloudflare bindings:** REPL code has **no network and no secrets**; the only outside access is RPC to host-side tool functions that hold credentials. "The AI cannot possibly write code that leaks any keys."

### 5.3 Prioritized security checklist for rlm_local

Existing: subprocess isolation, restricted builtins, `open` jailed to task temp dir, per-cell timeout, stdout caps. Additions, prioritized (effort: S <1 day, M 1–3 days, L >3 days):

1. **[S] Scrub the subprocess environment** — minimal allowlist env; never inherit API keys. Credentials live in host-side tool functions, injected at call time (bindings pattern). Highest value-per-line on this list.
2. **[S] Resolve symlinks in the jail** — `os.path.realpath` + verify resolved path is under the jail root (CVE-2025-53109 lesson).
3. **[S] Import allowlist + hard blocks** — block `ctypes`, `subprocess`, `os.system`, `socket`, `pty`; curate `shutil`; control dynamic `__import__`. One in-REPL subprocess call voids every builtin restriction.
4. **[M] Hard resource limits** — RLIMIT_AS/CPU/NPROC/FSIZE on POSIX; Job Objects on Windows (kill the whole tree on timeout).
5. **[M] Default-deny network in the REPL** — network only via explicit tool functions enforcing a host allowlist + private/loopback/link-local blocking (MCP spec SSRF guidance, [security best practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices)); bubblewrap/sandbox-exec/container escalation per OS.
6. **[S–M] Taint marking** — wrap tool outputs/retrieved text in explicit delimiters with a standing system-prompt order ("data, not instructions"); log warnings when untrusted text appears in generated cells. Partial mitigation; CaMeL-style separation is the principled version [L].
7. **[M] Consequential-action gates** — human-in-the-loop confirm for irreversible/exfil-capable calls, displaying **full untruncated arguments**; never blanket "always allow" for exfil-capable tools.
8. **[S] Immutable audit log** — every cell, tool call (name+args), output digest. Nearly free; required for post-incident analysis. (`logger.py` is most of the way there.)
9. **[M] Tool-description integrity** — hash/pin tool schemas at load, re-verify per session; treat descriptions as untrusted input; namespace tools to prevent shadowing.
10. **[M] Secret-leak detection on outputs** — scan stdout/file writes for known secret shapes before they leave the harness.
11. **[L] Promotion pipeline for persisted model-written tools** — quarantine → review gate → content-hash pin → versioned library; persisted code runs under the same sandbox forever. Treat model-authored code as a **supply-chain artifact**; otherwise `save_tool` is a self-service rug pull.
12. **[L] Container tier** for untrusted corpora/tools.

---

## 6. Synthesis: the capability architecture for rlm_local

### 6.1 Design principle

> **Never put the library in context; put a search tool in context. Tools are plain Python functions. Bulk results live in the REPL, not the prompt. The library grows itself — behind a gate.**

The root model ships with exactly two tool-related built-ins (plus the existing REPL primitives). Everything else is discovered just-in-time:

```python
search_tools(query, detail="desc")   # hybrid (BM25 ⊕ embedding) over the tool index
                                     # detail: "name" → "desc" (one line) → "full" (signature + docstring + 1 example)
get_tool_doc(name)                   # full doc for one tool, on demand
tools.<namespace>.<fn>(...)          # resolved on demand after discovery — plain Python call
save_tool(name, code, doc)           # persist a working function into the index (QUARANTINED, §5.3 #11)
```

### 6.2 The tool index

- One local SQLite file (same convergent design as the memory/repo stores): per tool — name, one-liner, full doc (JSON-schema-ish signature + docstring + **1–2 concrete call examples**), synthetic example queries, namespace/server, content hash.
- Multi-field indexing (name and synthetic queries embedded separately from doc body — Multi-Field/TDWA evidence); hybrid BM25 ⊕ embedding; ≤50K tools is trivial on CPU.
- MCP bridge (later phase): import external servers *into the index*, not into context, behind per-server allowlists (Docker MCP Gateway pattern).

### 6.3 Documentation standard (lint all tools against it)

- Namespaced semantic names (`gdrive_get_document`), unambiguous params (`user_id`), workflow-level consolidation over raw CRUD.
- JSON-schema-ish signatures (BFCL: JSON docs win), Python-style *calls*, **never XML anywhere in the loop**.
- One concrete call example per tool (Tool Use Examples: 72%→90%); a one-sentence "when to use" line written for a new hire; concise default outputs with a verbose option.
- **Prompt budget**: tool documentation visible to the root model ≤ 1–2K tokens total; 3–5 full docs in flight at once, never more (Anthropic: selection degrades beyond ~30–50 tools in context; small models degrade far earlier).

### 6.4 The self-growing library (LATM/Voyager/DynaSaur economics)

When the root model writes a multi-step workflow that *executed successfully*, `save_tool` offers persistence: auto-generate the one-liner + example from the trace, store in a `user_*` namespace, **quarantined until reviewed**, hash-pinned on promotion. DynaSaur's generated actions average cyclomatic complexity ~3 — small models can author them; LATM's economics say authoring cost is paid once, reuse is nearly free. This is also where the memory report connects: persisted tools and memory notes share the same SQLite + hybrid-search infrastructure.

### 6.5 Build order

| Phase | Deliverable | Effort | Evidence anchor |
|---|---|---|---|
| C1 | Tool registry + `search_tools`/`get_tool_doc` with detail levels; 3–5 hot tools always loaded | weekend-scale | Anthropic Tool Search 85% cut + accuracy up; Speakeasy 96% |
| C2 | Canonical doc template + backfill docs/examples for existing helpers (`memory.*`, `repo.*` become tools in the same index) | days | Tool Use Examples 72→90; BFCL format sensitivity |
| C3 | `save_tool` persistence with quarantine + review gate | days | LATM; Voyager; DynaSaur +7% |
| C4 | Security hardening §5.3 #1–#10 (before any network-capable tool) | 1–2 weeks | §5 evidence |
| C5 | MCP bridge: external servers into the index, per-server allowlists | 1–2 weeks | Gateway pattern |
| C6 (optional) | Fine-tune a 4–8B root on ToolACE/xLAM-style data with irrelevant-tool augmentation if selection accuracy plateaus | weeks | xLAM/ToolACE 8B ≈ GPT-4-class selection |

### 6.6 Realistic expectations (4B–8B quantized, local)

| Capability | Expectation | Basis |
|---|---|---|
| Single tool call, ≤5 candidates after retrieval | 60–80% per call | TinyAgent; ToolACE-8B; STE |
| Multi-hop chain (3+ dependent calls) | ≤30–50% end-to-end zero-shot | GPT-4o is 49% on ToolHop; Claude-4.5 38.6% on Tool Decathlon |
| Tool *selection* with index | near-frontier after C1/C2 | Anthropic 49%→74% with tool search |
| Authored-helper reuse | high once gated | LATM functional cache |
| Bottleneck | **retrieval quality, then recovery** — not raw call syntax | ToolBench oracle gap ~2×; Gorilla −29…−52% |

### 6.7 Honest bottom line

- The evidence supports your thesis: code-as-interface + progressive disclosure + environment-mediated results is the 2025–2026 convergence, and it favors small models *most* (smallest contexts, most to gain from keeping bulk out of them).
- But format is not a moat: fine-tuned JSON specialists match frontier models on selection, and Speakeasy shows the context win is achievable inside JSON-FC. The durable advantage of your design is the RLM part — context offloading + programmatic decomposition + sub-calls — not code-vs-JSON per se (the RLM paper's 130%-median over "CodeAct+sub-calls" is the receipt).
- The security checklist is **not optional**. The moment tools can touch network/filesystem/secrets, the lethal-trifecta math applies; items C4 must land before C5.

---

## References (grouped; verified during research unless flagged)

**Code-as-action:** [CodeAct](https://arxiv.org/abs/2402.01030) + [full text](https://arxiv.org/html/2402.01030v4) · [Code as Policies](https://arxiv.org/abs/2209.07753) · [ViperGPT](https://arxiv.org/abs/2303.08128) · [VisProg](https://arxiv.org/abs/2211.11559) · [Chameleon](https://arxiv.org/abs/2304.09842) · [TaskWeaver](https://arxiv.org/abs/2311.17541) · [DynaSaur](https://arxiv.org/abs/2411.01747) · [smolagents](https://huggingface.co/blog/smolagents) + [Open Deep Research](https://huggingface.co/blog/open-deep-research) + [secure execution docs](https://huggingface.co/docs/smolagents/en/tutorials/secure_code_execution) · [Anthropic code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) · [Anthropic advanced tool use (PTC, Tool Search)](https://www.anthropic.com/engineering/advanced-tool-use) + [Tool Search docs](https://docs.claude.com/en/docs/agents-and-tools/tool-use/tool-search-tool) · [Cloudflare Code Mode](https://blog.cloudflare.com/code-mode/) · [Speakeasy Dynamic Toolsets](https://www.speakeasy.com/blog/how-we-reduced-token-usage-by-100x-dynamic-toolsets-v2) · [Zechner on MCP](https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/) · [Amp, How to Build an Agent](https://ampcode.com/how-to-build-an-agent)

**Large tool libraries:** [ToolBench/ToolLLM](https://arxiv.org/abs/2307.16789) · [AnyTool](https://arxiv.org/abs/2402.04253) · [Gorilla](https://arxiv.org/abs/2305.15334) + [OpenFunctions v2](https://gorilla.cs.berkeley.edu/blogs/7_open_functions_v2.html) · [API-Bank](https://arxiv.org/abs/2304.08244) · [ToolAlpaca](https://arxiv.org/abs/2306.05301) · [ToolSandbox](https://arxiv.org/abs/2408.04682) · [ToolHop](https://arxiv.org/abs/2501.02506) · [Tool Decathlon](https://arxiv.org/abs/2510.25726) · [τ-bench](https://arxiv.org/abs/2406.12045) · [MCP-Bench](https://arxiv.org/abs/2508.20453) · [RAG-MCP](https://arxiv.org/abs/2505.03275) · [ProTIP](https://arxiv.org/abs/2312.10332) · [ToolRet](https://arxiv.org/abs/2503.01763) · [Multi-Field retrieval](https://arxiv.org/abs/2602.05366) · [FitText](https://arxiv.org/abs/2605.02411) · [ScaleMCP](https://arxiv.org/abs/2505.06416) · [Graph RAG-Tool Fusion](https://arxiv.org/abs/2502.07223) · [docker/mcp-gateway](https://github.com/docker/mcp-gateway)

**LLM-authored tools:** [CREATOR](https://arxiv.org/abs/2305.14318) · [LATM](https://arxiv.org/abs/2305.17126) · [ToolMaker](https://aclanthology.org/2025.acl-long.1266/) · [Alita](https://arxiv.org/abs/2505.20286) · [Voyager](https://arxiv.org/abs/2305.16291)

**Doc formats & small-model reliability:** [BFCL v4 format sensitivity](https://gorilla.cs.berkeley.edu/blogs/17_bfcl_v4_prompt_variation.html) · [BFCL leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) (live table JS-rendered [UNVERIFIED]) · [Anthropic writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents) · [TinyAgent](https://aclanthology.org/2024.emnlp-demo.9/) · [Octopus v2](https://arxiv.org/abs/2404.01744) · [Less-is-More](https://arxiv.org/abs/2411.15399) · [xLAM](https://arxiv.org/abs/2409.03215) · [APIGen-MT/xLAM-2](https://arxiv.org/abs/2504.03601) · [ToolACE](https://arxiv.org/abs/2409.00920) + [card](https://huggingface.co/Team-ACE/ToolACE-8B) · [Hammer2.1](https://huggingface.co/MadeAgents/Hammer2.1-7b) · [watt-tool-8B](https://huggingface.co/watt-ai/watt-tool-8B) · [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) · [NVIDIA SLM position paper](https://arxiv.org/abs/2506.02153) · [RouteLLM](https://arxiv.org/abs/2406.18665) · [self-consistency](https://arxiv.org/abs/2203.11171) · [Plan-and-Solve](https://arxiv.org/abs/2305.04091) · [LM Studio MCP](https://lmstudio.ai/blog/lmstudio-v0.3.17) · [llama.cpp server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md)

**Security:** [Lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) · [MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks) · [WhatsApp MCP exploit](https://invariantlabs.ai/blog/whatsapp-mcp-exploited) · [GitHub MCP toxic flow](https://invariantlabs.ai/blog/mcp-github-vulnerability) · [InjecAgent](https://arxiv.org/abs/2403.02691) · [AgentDojo](https://arxiv.org/abs/2406.13352) · [CaMeL](https://arxiv.org/abs/2503.18813) · [Agent security design patterns](https://arxiv.org/abs/2506.08837) · [MCP security best practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices) · [OWASP LLM Top 10 2025](https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/) · [CVE-2025-53109](https://nvd.nist.gov/vuln/detail/CVE-2025-53109) · [CVE-2025-6514](https://nvd.nist.gov/vuln/detail/CVE-2025-6514) · [CVE-2025-32711](https://nvd.nist.gov/vuln/detail/CVE-2025-32711) · [bubblewrap](https://raw.githubusercontent.com/containers/bubblewrap/master/README.md) · [gVisor platforms](https://gvisor.dev/docs/architecture_guide/platforms/) · [RestrictedPython](https://restrictedpython.readthedocs.io/en/latest/) · [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-overview)

**RLM anchors:** [RLM paper](https://arxiv.org/abs/2512.24601) · [harness post](https://alexzhang13.github.io/blog/2026/harness/) · design spec + memory/retrieval report in this repo's `docs/`
