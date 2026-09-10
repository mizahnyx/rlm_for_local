# Memanto vs rlm_for_local — "Behaviour as Content" Comparison

**Date:** 2026-09-03 21:07
**Question addressed:** starting from this repository's "behaviour as content" premise, how do its current and projected capabilities compare with Memanto (moorcheh-ai/memanto)?
**Companion documents:** `20260903-2107-rlm-for-local-full-analysis.md`, `20260903-2107-agent-memory-landscape-report.md` (full research landscape with [F]/[I] markers and URLs).

---

## 1. The two premises

**rlm_for_local (behaviour as content):** the harness is evolvable like a Smalltalk image — prompts, few-shots, executable helper code, and memory are markdown pages in a git-versioned vault ("markdown is truth"), with a derived FTS5 index, a quarantine→validate→promote gate for model-authored content, and an offline GEPA loop that rewrites behavior pages against verifiable evals with held-out regression protection. Behavior changes are content changes: versioned, diffable, gated, measurable.

**Memanto (memory as a managed service):** a companion "Memory Agent" that manages the memories of your other agents — what to keep, what conflicts, what expires, who needs to know. A FastAPI service + CLI storing typed memory cards (13 types) in Moorcheh's closed-source Information-Theoretic Search engine (local Docker container or cloud); governance is runtime curation: LLM extraction, conflict resolution (supersede/retain/annotate → CLI: keep_old/keep_new/keep_both/remove_both/manual), policy-driven expiry, daily summaries, session-start briefing. Markdown is the *export/sync* surface (Open Knowledge Format: "diffable, committable, greppable").

Sources: github.com/moorcheh-ai/memanto · arXiv:2604.22085 · docs.memanto.ai · independent audits (carsteneu/ai-memory-comparison `evidence/memanto.md`; AlexisOlson/somnigraph `research/sources/memanto.md`).

## 2. Mirror-image architecture

| Axis | rlm_for_local | Memanto (v0.2.19) |
|---|---|---|
| What is content | 8 page kinds incl. **executable helpers (code)** | 13 typed memory cards incl. behavior-shaping rows (`instruction`, `learning`, `error`, `preference`) |
| Truth | Markdown IS truth; index derived; git = temporal dimension | Engine namespace is truth; markdown is export/sync (OKF) |
| Write path | Gate: propose → quarantine → validate → **human promote** | **Zero-ingestion** (no gate); confidence 0.0–1.0 + 6-value provenance + conflict flags ex post |
| Read path | FTS5 BM25, ≤400-char cards (small-model LID invariant), fully open, 100K pages proven (117 ms p95) | Closed ITS engine, sub-90 ms [M], "no reranker/no fusion/no feedback loop — ranking not in the repo" (somnigraph) |
| Contradictions | demote → deprecated/superseded (git keeps history); no runtime detection | Runtime detection; supersede/retain/annotate; one LLM prompt over session files + human-in-the-loop (v0.2.15 fixed a wrong-memory-deletion bug) |
| Forgetting | `decay_score` = e^(−t/S), S=86400·(1+ln(1+access_count)) — **never called, inputs unpersisted** | Policy YAML per agent: retention-by-type table (context 7d … artifact 180d), provenance/confidence rules, presets, dry-run, purge off by default, `[EXPIRED]`-labeled recall + `restore`, `expired_at` + rule name recorded |
| Injection | Build-time assembly: system prompt + helpers compiled into REPL namespace per completion; core-memory in metadata | Runtime briefing: `agent bootstrap` snapshot, SessionStart hooks/skills (`connect claude-code` writes `CLAUDE.md` + skill), `memory.md` sync, MemantoClaw host-side proxy |
| Learning loop | **Outer, offline, outcome-driven**: GEPA vs regex-verified evals; promote iff train win ∧ held-out non-regression | **Inner, online, experience-driven**: daily cycles; no mechanism improves its own curation against any benchmark |
| Model regime | 1–8B local, CPU; harness compensates | Benchmarked with Claude Sonnet 4 / Gemini 3 (+4.8pp from the reader model alone) |
| Openness | Fully open incl. retrieval | MIT orchestration; closed engine (local container binary in both modes; air-gappable with Ollama) |
| Maturity | Solo, 31 commits, one week, v0.1.0 | ~1,857 stars in ~5.5 months, ~30 releases, bounty program as public roadmap |

The mirror: rlm_kernel's weakest layer (episodic memory — decay unwired, no runtime conflict surface, no briefing) is Memanto's entire product; Memanto's weakest layer (procedural memory — `instruction`/`learning` rows are inert text with no executable interface, verification, or evolution) is rlm_kernel's core.

## 3. Evaluation cultures (parallel epistemics)

Memanto reports SOTA — 89.8% LongMemEval / 87.1% LoCoMo (arXiv:2604.22085) — while its own eval repo states "current agentic memory benchmarks are deeply flawed" and **now shows Hindsight ahead on both** (91.4% / 89.61%) while site copy still claims the lead. Its ablation's largest single gain was recall expansion k=10→40 (+20.4pp) — retrieval breadth + reader model, not the ITS moat. rlm_for_local never touched those benchmarks; it built regex-verifiable task evals with held-out splits and a conformance trail whose flagship incident is a *vacuous guard test*, concluding "the evaluator's text shapes scores more than the model does." Same epistemic instinct; different measurement targets (memory QA vs task completion).

## 4. Projected functionality and convergence

**Memanto stated future** (paper §V-E + releases): automated type assignment (rule-based decision tree); multi-agent shared memory with access control ("under active development"); thousands-of-agents scale evaluation; benchmark protocols stressing conflict resolution; continued security hardening (v0.2.17–19 fixed a CWE-290 loopback-trust bypass, DNS rebinding, secrets-in-errors; MCP server still authenticates no inbound clients); OKF v0.2 conformance + universal migration adapters (bounty #1609).

**rlm_for_local stated future** (kernel spec + closeout docs): K3b PEEK-style orientation caches; K5 remote vault (LAN "rlm-wiki" daemon, 500K pages / 1M CAS / media pipelines); bindings pattern (`call_api` → host-side `KernelBridge.handle_api_call`); wiring all vault templates as live behavior; second GEPA target (`helper-docs`); vector tier behind the `SearchBackend` seam; QLoRA distillation from verified trajectories.

**Convergence map:**
- K5 networked vault ↔ Memanto multi-agent shared memory (both → shared, access-controlled memory estate).
- Bindings pattern + rlm_web bridge ↔ MemantoClaw proxy (both → harness-side memory injection substrate).
- Memanto OKF export ↔ rlm_kernel native format (Memanto's escape hatch is this repo's premise; an OKF bundle is one parser from being a vault).
- `SearchBackend` seam ↔ Moorcheh ITS (the seam exists precisely to admit a semantic engine).
- Memanto's proposed future metric — "estate quality over time: contradiction rate, staleness, precision at month six" — is outcome-governed measurement; adopting it would turn its inner loop into an outer loop (what this repo already does to its own behavior pages via GEPA).

**Research placement** (see landscape report): the Khattab-cluster trajectory RLM → PEEK → GEPA is exactly this repo's composition (K3b = PEEK); ACE (arXiv:2510.04618) is the closest published analog, minus the gate; MemEvolve (arXiv:2512.18746) is "GEPA for memory systems"; Letta's Context Repositories (Feb 2026, git-versioned memory files) show the service school converging on "markdown is truth". Per the landscape gap analysis: no published system validates writes against measurable outcomes before promotion, and none combines eval-gated offline prompt evolution with an episodic store under controlled comparison — the gate + GEPA pairing is this repo's distinctive composition.

## 5. Verdict

Memanto treats a slice of behavior (instructions, preferences, learnings) as content, but only rlm_for_local treats behavior as *governed, executable, evolvable* content. Memanto is broader, faster-moving, better integrated (14 connect targets + ~10 framework plugins + MCP/SDK), backed by a proprietary retrieval engine whose moat its own ablation understates. rlm_for_local is narrower, fully open down to the ranking function, radically model-poor by design, and process-rigorous to a degree Memanto doesn't attempt. They are the inner and outer loops of the same future agent; each roadmap moves toward the other. (Decision recorded 2026-09-03: **no integration with Memanto**; next step is remediation of this repo's findings — see `20260903-2107-remediation-plan.md`.)
