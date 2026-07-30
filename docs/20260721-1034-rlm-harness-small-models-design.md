# Design Document: A Recursive Language Model (RLM) Harness for Small Local Models

**Date:** 2026-07-21
**Status:** Proposed
**Audience:** Engineers implementing the harness
**Working name:** `rlm-local`

---

## 1. Purpose and scope

This document specifies an actionable design for a general-purpose **Recursive Language Model (RLM) harness** that runs on **small, locally-hosted open-weight models** (roughly 1B–12B parameters, quantized) on machines **without a powerful GPU** — CPU-only laptops, Apple Silicon, or a single consumer GPU with 6–12 GB VRAM.

The design applies the harness principles from Zhang & Khattab's work on RLMs:

- The [July 2026 harness post](https://alexzhang13.github.io/blog/2026/harness/) — harnesses as *compositional generalizers*: a good harness keeps every individual LM call **locally in-distribution (LID)** via **context offloading** and **programmatic sub-agent calling**, inducing **equivalence classes over tasks** so that learning (and behavior) generalizes across lengths and domains.
- The [October 2025 RLM post](https://alexzhang13.github.io/blog/2025/rlm/) and the reference implementations ([rlm-minimal](https://github.com/alexzhang13/rlm-minimal), [rlms package](https://github.com/alexzhang13/rlm)) — the concrete REPL-based instantiation.

The reference results were obtained with frontier API models and an RL-trained 30B MoE. Neither assumption holds here. The central design problem this document solves is:

> **How do we preserve the architectural properties that make RLMs generalize (LID observations, task-agnostic root context, programmatic decomposition) when the underlying model is small, slow, untrained for this protocol, and running on constrained hardware?**

In scope: inference-time harness architecture, prompts, guardrails, resource management, evaluation, and a trajectory-logging format that leaves the door open to later distillation/fine-tuning.
Out of scope: RL training infrastructure (see §11 for why, and for the pragmatic fine-tuning path).

---

## 2. Design principles (from the source material) and their implications

Each principle below is stated as in the source, then translated into a hard design requirement. These requirements are the acceptance criteria for the architecture in §4–§8.

### P1 — Locally in-distribution (LID) observations
*A good harness shapes each call to the underlying Transformer so that every observation is locally in-distribution: each individual LM call handles a prompt that is in-distribution with respect to its training data, even when the overall task is out-of-distribution (OOD).*

- **R1.1** The root model must never see the raw user context. It sees only: a fixed system prompt, context *metadata* (type + size), its own code, and *truncated* REPL output.
- **R1.2** Every sub-call must receive a single, bounded, well-formed prompt (no history, no harness boilerplate), sized to what the sub-model handles comfortably — for small models this is **much smaller** than the reference's ~100K chars (see §6).
- **R1.3** Root history must stay short and uniform in format. Long REPL output pollutes history the same way raw context does; per-block truncation is aggressive (2–8K chars, vs. 20K in the reference).
- **R1.4** Small models degrade fastest on weird inputs. Every prompt the harness generates must be *boring*: plain prose + simple fenced code, matching the model's chat template exactly, no exotic markup.

### P2 — Context offloading
*Input-specific context is passed as a symbolic variable so the root LM never directly sees it; different problems then appear similar at the first step.*

- **R2.1** The context lives only in the REPL (as `context`) and, for large inputs, primarily **on disk** (§5.7) — not in RAM-resident message history and never in the root prompt.
- **R2.2** The root model's first-turn view is *task-agnostic*: query + context metadata. This is what makes two different tasks look isomorphic to the root model (the equivalence-class property).

### P3 — Programmatic sub-agent calling
*Sub-agents and tools are functions in a code REPL; intermediate results are stored in REPL variables and passed between calls without the root LM ever seeing them.*

- **R3.1** Sub-LLM calls are ordinary Python functions (`llm_query`, `llm_query_batched`) in the REPL. No JSON tool-calling protocol anywhere in the design — this follows the RLM authors' explicit bet against JSON tool calling, and it is doubly right for small models, whose weakest skill is exactly rigid JSON schemas (§9).
- **R3.2** Tool/sub-call outputs land in REPL variables. The root model pulls information into its own context only via deliberate, small `print()`s.
- **R3.3** Because small models write worse code than frontier models, the REPL pre-loads a thin **standard library of canonical operations** (`peek`, `grep`, `chunk`, `map_query`, `show_vars`). This is *not* a retreat to hard-coded workflows: they are thin Python helpers the model may ignore. Their real function is **trajectory normalization** — nudging different tasks into the same code idioms, which is exactly the equivalence-class mechanism of P4, and it reduces the amount of novel code a 4B model must emit correctly.

### P4 — Equivalence classes over tasks
*A good harness induces an equivalence relation ∼H over task states: structurally similar tasks produce near-identical trajectories in the root context, enabling compositional generalization across lengths and domains.*

- **R4.1** All harness-generated text (metadata line, turn counters, error messages, nudges) must be **templated and stable** — identical wording for identical situations across tasks. Free-form harness prose breaks the token-level similarity the principle depends on.
- **R4.2** Trajectory logging (§10.3) must record the root context verbatim per turn so we can *measure* trajectory similarity across tasks (edit distance, 3-gram containment/Jaccard — the proxy metrics from the paper's appendix) and verify the harness is actually inducing equivalence classes.
- **R4.3** The decomposition nudge (P5) and the helper library (R3.3) exist precisely to make strategies converge across tasks without RL.

### P5 — The decomposition nudge is mandatory (not optional)
*Untrained RLMs often discover a non-generalizable shortcut: offload the whole problem to one sub-call. A "nudge to decompose" (a condensed orchestrator addendum) fixes this and yields eval lift exceeding train lift. RL is only the sample-efficient way to instill this behavior; prompting is the zero-training way.*

- **R5.1** The orchestrator addendum is **always on** in the system prompt (adapted to small numbers, §5.5).
- **R5.2** Turn 0 is structurally forced to be *probe + plan*: the harness rejects (with a templated nudge) any turn-0 attempt to finalize, and the turn-0 user prompt requires an explicit decomposition statement before code. This reproduces, by construction, the behavior the trained RLM learns.
- **R5.3** Anti-shortcut guard: a single sub-call whose input is >60% of the whole context is flagged in the REPL result with a templated reminder to decompose. (Heuristic; tunable.)

### P6 — Depth-1 recursion is the sweet spot
*The reference work used recursive depth 1 (root RLM + plain-LM sub-calls) and found it sufficient; deeper recursion is easy but rarely needed.*

- **R6.1** `max_depth = 1` by default. On slow local hardware every extra depth multiplies latency; depth-2 exists behind a config flag for experimentation only.

### P7 — The harness is a drop-in `completion()` replacement
*An RLM call presents as a single text→text call; all recursion is internal.*

- **R7.1** Public API: `rlm_local.completion(query, context) -> str`, plus a streaming/events variant for UIs. All budgets, retries, and degradation are internal.

---

## 3. Constraints: what small local models change

| Reality on constrained hardware | Consequence for the design |
|---|---|
| Decode ~4–15 tok/s (7–8B Q4 on a laptop CPU), ~20–50 tok/s (3–4B); prompt eval somewhat faster | Minimize *root* turns and root-history size; push bulk reading into sub-calls; prefer batched sub-calls over sequential turns; exploit prefix caching aggressively |
| RAM 16–32 GB total; model weights 2–9 GB; KV cache grows with context (e.g. ~4 GB per 32K ctx for an 8B, FP16 KV) | Small effective context windows per model call (8–32K tokens); quantized KV cache; context offloaded to disk; one model server, not many |
| Only one (or zero) discrete GPU; likely one model resident at a time | Two-tier routing (§5.2) must fit in one server process or use fast model swap; sub-call parallelism is server slots (1–4), not 16 |
| Small models: weaker instruction following, code errors, format drift, "narration" instead of action (~25% unforced) | Guardrail layer (§5.6): rescue parsing, templated retry nudges, error budgets, forced finalization; temperature 0 for code |
| No RL training pipeline (needs 8×H100-class nodes; the paper's RLM training costed 1.5–3× base-model RL) | Instill decomposition by prompt + structure (R5); log trajectories for optional QLoRA distillation later (§11) |
| Chat-template/parser fragility across serving stacks (tool specialists scored 15–20% when their format didn't survive the stack) | The harness emits **plain text with ```repl fences** — no reliance on server-side tool parsers; validate the full stack end-to-end with the exact server build (§10.1) |

**Reliability math that drives the whole design:** at 95% per-step reliability, an 8-step loop completes ~66% of the time. The harness therefore keeps plan horizons short, makes every step verifiable programmatically where possible, and treats the root model as an orchestrator that writes *little, simple* code — never a solver that writes long programs.

---

## 4. Architecture overview

```
user ──► completion(query, context)
            │
            ▼
   ┌────────────────────────────────────────────────────────┐
   │                     RootLoop                           │
   │  messages = [system(+addendum), metadata, prologue]    │
   │  for turn in 1..max_turns:                             │
   │     text  = RootModel.chat(messages)                   │
   │     block = Parser.extract(text)        # §5.6         │
   │     result = REPL.execute(block)        # §5.3         │
   │     if result.final_answer: return it                  │
   │     messages += [assistant text, templated REPL out]   │
   │  return ForcedFinalizer(messages)       # §5.5         │
   └────────────────────────────────────────────────────────┘
            │  OpenAI-compatible HTTP          │ exec in subprocess
            ▼                                   ▼
   ┌──────────────────────┐          ┌─────────────────────────────────┐
   │   ModelServer pool   │          │           REPL sandbox          │
   │  (llama-server /     │          │  globals: llm_query,            │
   │   Ollama / MLX)      │◄─────────┤           llm_query_batched,    │
   │  root tier + sub tier│  socket  │           helpers, SHOW_VARS    │
   └──────────────────────┘          │  locals:  context (lazy),       │
                                     │           answer, user vars     │
                                     │  store:   ContextStore (disk)   │
                                     └─────────────────────────────────┘
            │
            ▼
   TrajectoryLogger (JSONL per completion: every root message,
   every sub-call prompt/response, every REPL result, budgets used)
```

Specified below (§5.1–§5.7): `ModelServer` (+ two-tier routing), `REPL`, `SubcallManager`, `RootLoop` (with prompts), `Parser`, and `ContextStore`. `TrajectoryLogger` is a thin observer over these modules; its record format is driven by the logging requirements in R4.2 and the metrics in §10.3.

---

## 5. Module specifications

### 5.1 `ModelServer` — inference backend layer

**Interface**
```python
class ModelBackend(Protocol):
    def chat(self, messages: list[dict], *, tier: str,
             max_tokens: int, temperature: float = 0.0,
             response_schema: dict | None = None) -> str: ...
```

- **Transport:** OpenAI-compatible HTTP only (`/v1/chat/completions`). Supported servers: `llama-server` (reference target), Ollama, LM Studio, `mlx_lm.server`. No native SDK lock-in; every candidate server is validated against the conformance suite in §10.1.
- **Tiers:** `"root"` and `"sub"` map to (possibly different) models/ports. Default single-machine deployment: **one server, one model, both tiers**; upgrading the sub-tier to a smaller/faster model is a config change, not a code change.
- **Server configuration (llama.cpp reference):**
  - `--ctx-size` per profile (§6); `--flash-attn`; `--cache-type-k q8_0 --cache-type-v q8_0` (halves KV memory);
  - `--cache-ram` (host-memory prompt caching) **on** — the root loop re-queries with a long shared prefix every turn;
  - threads = physical cores; `keep_alive` pre-warm at startup; server-side `max_tokens` cap as backstop.
- **Prefix-cache discipline (critical for latency):** the system prompt is byte-identical across calls and tasks; history is append-only; harness messages contain **no timestamps, no per-turn randomness**; the only per-turn novelty is appended at the tail. (Field reports confirm a single changing header silently kills cache hits.)
- **`response_schema` (optional):** when the server supports JSON-schema-constrained decoding (llama.cpp GBNF, Ollama `format`, vLLM xgrammar), sub-calls may request structured output (§5.3). Grammars must be *loose* — force the skeleton, leave content free; over-tight constraints measurably degrade small-model output ("constraint tax"). Never use grammars on the root model's free-form ```repl output — rescue parsing is cheaper and safer there.

### 5.2 Two-tier model routing

| Tier | Role | Model guidance (July 2026 evidence) | Why |
|---|---|---|---|
| `sub` | Bulk extraction/summarization/labeling over chunks — the hot path | **Qwen3.5-4B / Qwen3-4B-Instruct-2507 class, Q4_K_M** (~3.4 GB; best measured tool/structured reliability per GB; 262K native ctx on the 2507 card). Fallback: Phi-4-mini (2.5 GB) on very weak CPUs. | Runs hundreds of times per task; must be cheap and obedient |
| `root` | Planning, decomposition, code synthesis, aggregation | Same 4B class by default; upgrade to **Qwen3-8B Q4_K_M** (~5 GB) or Qwen2.5-Coder-7B when RAM allows | Root code quality is the top failure source; a coder-tuned root helps |
| `triage` (optional) | yes/no and classification sub-calls | Llama-3.2-3B / Qwen3-1.7B | Only when the sub-tier is the bottleneck |

Avoid for any tier: reasoning-distill models (R1-Distill class — they burn turns "thinking about whether to call the tool"), sub-Q4_K_M quants (tool/format reliability collapses before chat quality does), and any model not validated through *your* serving stack (§10.1).

Single-model deployments are first-class: tier routing collapses to one endpoint.

### 5.3 `REPL` — sandboxed execution environment

- **Isolation:** subprocess-isolated Python worker (not in-process `exec`), communicating with the harness over a localhost socket (length-prefixed JSON, as in the reference). Rationale on weak hardware: per-cell wall-clock timeouts must be *enforceable* (killable worker), and a small model emitting `while True:` must not hang the harness. Docker isolation is a config option for untrusted inputs; default is subprocess + restricted builtins (`input/eval/exec/compile/globals/locals` blocked, `open` restricted to the task temp dir).
- **State:** persists across turns within one `completion()`. Scaffold names (`context`, `answer`, helpers, `llm_query*`) are restored after every cell so model code can't brick the environment.
- **Namespace injected at start:**
  - `context` — the user context (str or list[str]); for large inputs a lazy `Context` object backed by `ContextStore` (§5.7) with `str/list` ergonomics.
  - `answer` — dict `{"content": "", "ready": False}`; setting `ready=True` signals termination **from inside code** (the mechanism of the current `rlms` package — strictly more robust than regex-parsing `FINAL()` from prose, which weak models misfire).
  - `llm_query(prompt, *, schema=None) -> str` — one-shot sub-call; no history, no system prompt. Errors return as `"Error: ..."` strings (never raised), so the root model can self-correct next turn.
  - `llm_query_batched(prompts, *, schema=None) -> list[str]` — bounded fan-out (§5.4), order-preserving, per-item errors as strings.
  - Helpers (R3.3): `peek(n=2000)`, `grep(pattern, max_hits=50)`, `chunk(size=None, by=None)`, `map_query(prompts_or_chunks, template, batch=True)`, `show_vars()`. Each is ≤15 lines of obvious Python, documented in the system prompt with one-line examples.
  - `rlm_query` exists only when `max_depth > 1`; otherwise it aliases `llm_query` (reference behavior).
- **Cell limits (new vs. reference, which had none locally):** per-cell wall-clock timeout (default 60 s), per-cell stdout/stderr capture cap (256 KB), memory guard via subprocess rlimits where the OS allows.
- **Result envelope** returned to `RootLoop`: `{stdout, stderr, final_answer, warnings}`. `warnings` carries templated harness messages, e.g. the P5 anti-shortcut flag and sub-call budget notices.

### 5.4 `SubcallManager` — the economics of the hot path

Owns concurrency, budgets, and prompt hygiene for `llm_query*`:

- **Concurrency:** `max_concurrent_subcalls` default **2** (server slots permitting); `llama-server --parallel 2` or sequential otherwise. Batched calls are still *semantically* parallel — the root model is told to prefer one `llm_query_batched` over a Python loop of `llm_query` (fewer root turns burned).
- **Per-prompt sizing:** `sub_prompt_char_budget` (default **16K chars**, profile-tunable 8–32K) enforced as a *warning*, not a hard error: prompts above budget succeed but append a templated notice to the result ("this sub-call exceeded the recommended size; expect degraded quality"). Hard-failing teaches nothing on turn N-1 of a 15-turn budget.
- **Total budget:** `max_subcalls` (default 60) and `max_subcall_chars` (default 4M chars) per completion; on exhaustion, `llm_query` returns a templated exhaustion string and the root is nudged to finalize with what it has.
- **Schema support:** when `schema=` is passed and the server supports constrained decoding, attach it; otherwise fall back to appending `"Respond with JSON matching: ..."` to the prompt and repair-parsing the result (§5.6).
- **Caching:** identical sub-call prompts within a completion are memoized (small models repeat themselves under retry).

### 5.5 `RootLoop` and prompts — the heart of the design

**Message layout (byte-stable prefix first):**

1. `system` — `SYSTEM_PROMPT` (below) with config-injected numbers.
2. `user` — metadata: `"Your context is a {type} of {n} total characters. A sub-LLM call handles roughly {sub_budget} characters well. You have {max_turns} turns."` Prefixed with `"Answer the following: {query}\n\n"` — the **query is in the prompt; the context is not** (P2).
3. `user` — **decomposition prologue** (R5.2; the `user_prologue` mechanism of the reference, mandatory here).
4. Per turn: `user` — `"Turn {i}/{max_turns}:"` (+ turn-0 safeguard: *"You have not inspected the context yet. Probe it first; do not finalize."*), `assistant` — raw model text, `user` — templated result message: `"REPL output:"` (or `"REPL output (block k):"`), each block's stdout+stderr truncated to `repl_output_char_cap` (default **4K chars**, tail-preserving for errors).

**SYSTEM_PROMPT** (design sketch — final wording lives in `prompts.py`, numbers injected):

```
You are a Recursive Language Model: you answer a query whose (potentially huge)
context lives in a Python REPL, not in this conversation. You act turn by turn
until you submit an answer.

REPL contract
- Write code in ```repl fences. The REPL persists across turns.
- Available: `context` (the data), `answer` (dict: set answer["content"] and
  answer["ready"]=True to submit), `llm_query(prompt)`, `llm_query_batched(list_of_prompts)`,
  and helpers: peek(n), grep(pattern), chunk(size), map_query(items, template), show_vars().
- Only print() output is shown back to you, truncated to {repl_cap} characters.
  Never print large slices of context — pass slices to llm_query instead.
- llm_query sees ONLY the prompt you give it (no REPL, no history). Give it a
  self-contained chunk and ask for a short, specific answer. It handles about
  {sub_budget} characters well.

How to work (follow this order)
1. PROBE: print small samples and counts to learn the context's structure.
2. PLAN: in prose, state how the task decomposes — what each turn computes and
   which sub-calls it issues — before writing more code.
3. EXECUTE one small step per turn; print a tiny verification sample after each.
4. SUBMIT only after you have printed your candidate answer. If turns run low,
   submit your best inference rather than nothing.

Rules for a small context window (yours)
- Your own window is small: delegate reading, summarizing, classifying, and
  extracting to llm_query; keep only small results and decisions yourself.
- Prefer one llm_query_batched over a loop of llm_query calls.
- If a keyword/regex search answers it directly, skip sub-calls.
- Chunk big work: {example_chunking_idiom}.

[2–3 short worked few-shot transcripts in the exact harness format:
 needle-search (grep → peek → FINAL), aggregation (chunk → map_query →
 combine in Python → print → submit), and a recovery-from-stderr example.]
```

Notes on the prompt design:
- **Few-shots are load-bearing.** Small models need the format demonstrated, not described. The deprecated `RLM_SYSTEM_PROMPT_OLD` (5 embedded examples) outperforms the terse modern prompt for this regime; we carry 2–3 compact ones, in the *exact* message format of live turns (P4: few-shots are part of the equivalence-class normalization).
- The orchestrator addendum's *content* (probe → plan → one step per turn → verify → submit; fat prompts, few batches; Python-filter first; stage coarse→fine passes) is folded into "How to work" with small-model numbers substituted.
- All capacity claims (`{sub_budget}`, `{repl_cap}`, `{max_turns}`) are **config-injected** — the reference hardcoded "500K chars", which would be a lie here and would actively mislead the root model into oversized sub-calls.

**Turn accounting and termination:**
- `max_turns` default **15** (guardrail evidence: cap agentic loops at 15–25; each local turn is expensive).
- Termination: `answer["ready"]=True` observed in a REPL result → return `answer["content"]`.
- Exhaustion: one forced finalization call — `"Based on what you have, provide your best final answer now."` — and its text is the response. Never return empty.
- Hard limits raise structured results, not crashes: `TimeoutResult/BudgetResult` carry `partial_answer`.

### 5.6 `Parser` and the guardrail layer

Ordered pipeline per root response (each rule is cheap; the pipeline never asks the model to re-do work the harness can do mechanically):

1. **Extract:** all ```repl fenced blocks (DOTALL regex). Execute sequentially.
2. **Rescue parse** (before spending a retry — worth +8–12pp end-to-end in the field data): accept ```python / ``` / unclosed-final-fence; strip narration around a single obvious block; if the model *narrated* code without fences ("I would run chunk(context)..."), no extraction — go to 3.
3. **Templated retry nudge:** if no block and no finalization, append a *specific* user message naming the defect (`"No ```repl block found. Emit exactly one ```repl block with your next step."`) — targeted nudges beat blind retries (+6–9pp). Max 2 consecutive nudges, then count as an error.
4. **stderr self-correction:** Python exceptions are returned as REPL stderr text; small models fix their own code well when shown the traceback. `max_consecutive_errors` default **3**, then forced finalization.
5. **Finalization repair:** if the model sets `answer["ready"]` with empty content, one nudge; if it prose-answers "FINAL: ..." outside code, accept it via a *strict* line-anchored pattern as a courtesy path (small models will do this; accepting it is cheaper than punishing it).
6. **Sub-call output repair:** `schema=` responses parse-then-repair (strip fences, balance braces) before returning to REPL code as a string.

### 5.7 `ContextStore` — disk-backed context

- Inputs above `context_spill_threshold` (default 1M chars) are ingested to a per-task SQLite/file store in the task temp dir; `context` in the REPL becomes a lazy handle (`len`, slicing, iteration, `.lines()`) backed by the store. RAM stays flat regardless of context size; this is what makes "10M-token context on a 16 GB laptop" a real claim rather than a slogan.
- `grep` is implemented over the store (streaming, capped hits); `chunk` returns views, not copies, where possible.
- REPL `open` is jailed to the task dir, so intermediate artifacts (extracted JSON, buffers) can be persisted by model code and re-read across turns without touching the root context.

---

## 6. Configuration and hardware profiles

All numbers in one `config.toml`; three shipping profiles (benchmark on the target machine — decode speed is memory-bandwidth-bound and varies 5× across "CPU-only" machines):

| Setting | `tiny` (8 GB RAM, CPU-only) | `laptop` (16–32 GB RAM / M-series) | `workstation` (32+ GB RAM, 6–12 GB GPU) |
|---|---|---|---|
| root model | Phi-4-mini Q4_K_M (2.5 GB) | Qwen3.5-4B / Qwen3-4B-2507 Q4_K_M | Qwen3-8B Q4_K_M |
| sub model | = root | = root | Qwen3-4B-2507 Q4_K_M |
| server ctx (root) | 8K tokens | 16K tokens | 32K tokens |
| server ctx (sub) | 8K | 16K | 32K |
| `sub_prompt_char_budget` | 8K chars | 16K chars | 24–32K chars |
| `repl_output_char_cap` | 2K chars | 4K chars | 8K chars |
| `max_turns` | 12 | 15 | 20 |
| `max_concurrent_subcalls` | 1 | 2 | 4 |
| `max_subcalls` / `max_subcall_chars` | 30 / 1M | 60 / 4M | 100 / 12M |
| `max_depth` | 1 | 1 | 1 |
| cell timeout | 60 s | 60 s | 120 s |

KV-cache sanity check (why ctx values are modest): an 8B-class model at 32K ctx costs ~4 GB KV at FP16 (~2 GB at q8_0) — fine on `workstation`, fatal on `tiny`. Rule: model file + KV cache + OS headroom ≤ RAM; pick the quant 1–2 GB under the budget.

**Worked latency example (`laptop`, 4B Q4_K_M, ~25 tok/s decode, prompt-eval cached):** a 10-turn root loop with 24 sub-calls of ~2K-token prompts ≈ 10 × (cached-prefix eval + ~300 tok decode) + 24 × (~2K tok eval + ~150 tok decode) ≈ 4–8 minutes. This sets user expectations: the harness trades **wall-clock for capability** — tasks that were impossible for the base model become slow-but-possible. Interactive UX should stream turn events.

---

## 7. Protocol summary (for implementers)

- **Root → model:** plain chat messages; no tool-calling mode, no grammars on root; temperature 0; `max_tokens` 1500–3000/turn.
- **Model → harness:** prose + ```repl blocks, or (courtesy) strict `FINAL:` line.
- **Harness → REPL:** `{code}` → `{stdout, stderr, final_answer, warnings}`.
- **REPL → sub-tier:** `{prompt, schema?}` → `{text}`; batched variant arrays both sides.
- **Termination:** env-signaled (`answer.ready`) > courtesy FINAL > forced finalizer.
- **Every string the harness emits** lives in `templates.py`, versioned, and frozen within a release (R4.1).

---

## 8. Failure modes and mitigations

| Observed/expected failure | Mitigation |
|---|---|
| Model narrates instead of emitting code (~25% unforced on small models) | Rescue parse → templated nudge (2×) → error count; few-shot transcripts demonstrate the format |
| Whole-context single sub-call shortcut (non-generalizing strategy) | Turn-0 plan requirement + >60%-context sub-call warning (R5.3) |
| Root history bloat → OOD root context (context rot inside the harness) | 4K per-block truncation; `show_vars` lists names not values; sub-call results stay in REPL variables; optional compaction: summarize history into a REPL var at 85% of root ctx (reference mechanism, off by default) |
| Broken/malformed Python from root | stderr fed back verbatim; 3-error budget; forced finalizer |
| Runaway cell (`while True`, huge alloc) | subprocess kill on cell timeout; rlimits; stdout cap |
| Sub-call quality collapse on oversized prompts | size warnings; sub-tier with large native ctx; staging idiom (coarse filter → fine extract) taught in prompt |
| Prompt-cache misses → 5–10× latency | byte-stable prefix, append-only history, no timestamps; `--cache-ram`; cache-hit rate exported as a metric |
| Model/server chat-template drift | conformance suite (§10.1) runs per model×server before any eval; pin server versions in lockfile |
| Silent wrong answers on aggregation | prompt instructs "compute aggregates in Python, not in prose"; verification few-shot shows print-check of candidate answer |

---

## 9. Why not [alternative]

- **JSON tool-calling protocol (Hermes/OpenAI tools):** small models' weakest surface; parser support varies per server build; the RLM thesis (and our P3) is code-as-interface. We keep constrained decoding only where it's cheap and optional (sub-call `schema=`).
- **Retrieval-first (BM25/RAG) front-end:** reasonable for pure needle tasks, but the target workloads (aggregation, semantic mapping, multi-hop association over 100K+ docs) are exactly where the RLM paper shows retrieval loops losing to programmatic decomposition. A `grep` helper gives 80% of BM25's value for these corpora with zero index cost. A retriever can be added later *as a REPL helper*, not as a root-context flood.
- **Deep recursion (depth ≥ 2):** latency multiplies on one server; the reference found depth 1 sufficient at far larger scales. Flag exists; off by default.
- **Fine-tune-first:** reversed — make the prompted harness reliable, log trajectories, *then* distill (§11). Training on a broken harness's logs teaches broken strategies.

---

## 10. Verification and evaluation plan

### 10.1 Conformance suite (run per model × server build before anything else)
~30 scripted probes: emits a ```repl block on request; recovers from a planted stderr; sets `answer` correctly; respects size claims; handles `schema=` sub-calls; no narration on forced-code turns. A model×server pair failing >20% is rejected up front (the field data shows leaderboard winners failing 80%+ through mismatched stacks — test the stack, not the leaderboard).

### 10.2 Capability benchmarks
- **Length generalization:** needle/aggregation suites at 4×–32× the root context window (MRCR- and OOLONG-style tasks synthesized over public datasets), evaluated per profile. Success = accuracy at 8×+ length vs. base-model-on-full-context baseline (which should be near zero or impossible).
- **Domain transfer:** train nothing; instead verify *strategy consistency*: same decomposition idiom chosen on 3 domains sharing latent structure (the paper's spam/Jeopardy, essays/math analogues, built small).
- **Cost metrics:** wall-clock/query, root tokens/turn, sub-calls/query, cache-hit rate, peak RSS.

### 10.3 Equivalence-class measurement (P4 verification)
Log full root trajectories; for task pairs sharing latent structure, compute nearest-trajectory similarity under the paper's appendix metrics (token-Levenshtein, 3-gram containment, Jaccard, weighted Jaccard, length ratio). Target: RLM root trajectories measurably closer across same-structure tasks than a base-model baseline's — the local, training-free echo of Figure 8 in the source post.

### 10.4 Ablations
Nudge on/off; helper library on/off; `laptop` root 4B vs 8B; truncation 2K/4K/8K. Each ablation is one config flag — the harness must be built so all of §5's numbers are knobs.

---

## 11. Path to training (out of scope, designed-for)

Full RL (prime-rl style) is infeasible on target hardware — the paper's own RLM training needed 8×H100 nodes and ran 1.5–3× slower than base-model RL. The pragmatic ladder:

1. **Now:** prompted harness (this document).
2. **Cheap win:** rejection-sample distillation — run the harness, keep trajectories that reach verified-correct answers, QLoRA fine-tune the root model on root-context slices (Unsloth-class tooling runs 4–8B LoRA on consumer GPUs/Macs). This is precisely "training on short tasks" in the paper's sense — and their central result says length/domain generalization should *transfer* from short-task training if the harness holds up its end (equivalence classes). That prediction is testable cheaply with §10.3's metrics before and after distillation.
3. **Trajectory format compatibility:** `TrajectoryLogger` emits JSONL with per-turn root messages — convertible to verifiers/prime-rl format later without harness changes.

---

## 12. Implementation roadmap

| Phase | Deliverable | Acceptance |
|---|---|---|
| P0 — Skeleton (week 1–2) | `ModelBackend` (llama-server), subprocess REPL, RootLoop with prompts/templates, `ContextStore` (RAM-only), JSONL logger | `completion()` answers a 4×-ctx needle task end-to-end on `tiny` profile |
| P1 — Guardrails (week 2–3) | Parser rescue pipeline, nudge templates, budgets, cell timeouts, forced finalizer, conformance suite | Conformance ≥80% on chosen model×server; no unbounded runs in fuzzing |
| P2 — Scale-out (week 3–5) | Disk-backed ContextStore, batched sub-calls + memoization, cache-hit metrics, two-tier routing, profile configs | `laptop` profile passes 8×-length aggregation suite; peak RSS flat vs. context size |
| P3 — Evaluation (week 5–6) | Benchmark harness, trajectory-similarity metrics, ablation flags | §10.2–10.4 numbers reported on 2 profiles |
| P4 — Optional distillation | rejection-sampling exporter → QLoRA recipe | post-LoRA §10.2 scores ≥ prompted baseline, generalization per §10.3 |

---

## 13. References

- Zhang & Khattab, [Language model harnesses are compositional generalizers](https://alexzhang13.github.io/blog/2026/harness/), Jul 2026 — LID, equivalence classes, length/domain generalization, decomposition nudge, training-cost analysis, trajectory-distance metrics.
- Zhang & Khattab, [Recursive Language Models](https://alexzhang13.github.io/blog/2025/rlm/), Oct 2025 (+ [arXiv:2512.24601](https://arxiv.org/abs/2512.24601)) — REPL instantiation, emergent strategies (peek/grep/partition-map/summarize), OOLONG & BrowseComp-Plus results, depth-1 design.
- Reference code: [rlm-minimal](https://github.com/alexzhang13/rlm-minimal), [rlms package](https://github.com/alexzhang13/rlm) — system prompts, orchestrator addendum, defaults (max_iterations 30, 20K-char truncation, batch concurrency 16/4), answer-dict mechanism, `user_prologue`.
- Small-model evidence (secondary, 2026): [local tool-calling eval](https://www.jdhodges.com/blog/local-llms-on-tool-calling-2026-pt1-local-lm/) (Qwen3.5-4B 97.5% @ 3.4 GB), [tool-calling model roundup](https://www.promptquorum.com/power-local-llm/best-local-models-tool-calling-2026), [agent guardrails playbook](https://dev.to/monuminu/llm-agent-guardrails-the-engineering-playbook-for-taking-an-8b-local-model-from-53-to-99-on-18c) (53%→86.5% via rescue parsing/retry nudges/step enforcement/compaction; max_iterations 15–25), [small-model reliability field report](https://github.com/elsung/small-model-tool-reliability) (GBNF fixes narration 25%→0%; loose-grammar rule), [Qwen3-4B-Instruct-2507 card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507), [llama.cpp GBNF](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/grammars/README.md), [llama.cpp `--cache-ram`](https://github.com/ggml-org/llama.cpp/discussions/20574), [KV-cache guide](https://insiderllm.com/guides/kv-cache-optimization-guide/), [CPU benchmarks](https://markaicode.com/benchmarks/tool-cpu-benchmark/), [cross-hardware tok/s](https://github.com/geerlingguy/ai-benchmarks), [Unsloth](https://github.com/unslothai/unsloth).
