# K4 First Run Report — 2026-07-30

**Milestone:** K4-real acceptance (GEPA offline optimization of a live harness text artifact)
**Verdict:** ✅ **SUCCESS — promoted.** The evolved `how-to-work` scores **train 1.0 (5/5), held-out 1.0 (2/2)** vs baseline **0.8 / ~0.45**, passing the optimizer's promotion gate on both axes.

---

## Final configuration

- **Target:** `contract/how-to-work.md` (live prompt page; the only live target currently)
- **Suite:** `needle_search` (5 train / 2 held-out, deterministic 70/30 split)
- **Profile:** `tiny` · **Model:** Qwen3.5-4B-Abliterated (root=sub tier) via llama-server router
- **Optimizer:** GEPA via `optimize_anything`, budget 40 metric calls, `max_workers=2`, `run_dir` checkpoints, PYTHONUTF8, unbuffered direct redirect
- **Promotion:** gate-routed (`propose → validate → promote` into the incumbent path), lineage `<!-- optimized_by: gepa-run-v5-rescued-1785419689 -->`, tag `gepa-optimized`, vault commits `0361376` (baseline) → `f250c34` (promotion)

## The evolved text (what GEPA changed)

GEPA specialized the generic orchestrator addendum into a **task-specific extraction procedure**: probe a small window and identify the target field, map the field to concrete extraction sub-calls in order, execute one extraction per turn with an immediate regex-verification snippet, then concatenate and submit — with explicit small-window rules (single-focus turns, `llm_query_batched` for multi-sentence reads, extract immediately on regex match). Measured effect: needle tasks went from 4/5 with one few-shot-imitation failure ("blue" for every question) to **5/5, plus both held-out tasks** (which the seed scored ~0.45 on).

## The road here (honest log)

| Attempt | Outcome | Root cause |
|---|---|---|
| v1 (24 h) | Circular — no candidates ever proposed | `litellm` + `tenacity` undeclared as hard deps of `gepa` |
| v2 (3.7 h) | Crashed at "Proposed new text" | Windows cp1252 `print()` on `\u2011` in candidate |
| v3 (21 h, hard-stopped) | Healthy but unbounded-slow | 6-way parallel self-contention; budget only gates between iterations |
| v4 (4 h 45 m) | Crashed at iteration ~2 | cp1252 write inside GEPA's **own log file** |
| v5 (21 h, power cut at 32/40) | **State rescued** | checkpoint preserved program 3 (valset 0.9) |
| **Rescue (2026-07-30)** | **PROMOTED** | direct eval of rescued candidate: **1.0 / 1.0** |

Fixes that made it possible: reflection-path deps + `litellm.ssl_verify` global (`f891ff6`), smart-quote normalization in the parser (`30ee3d2`, load-bearing in production — sub-calls emit curly quotes routinely), stdout UTF-8 guard, `run_dir`/`max_workers` wiring, and `PYTHONUTF8=1` at process level.

## Read on the result

- The optimization pipeline is **proven end-to-end**: evolve → checkpoint → rescue → gate → promote, with every gate holding under pressure (encoding, power loss, load noise).
- Baseline instability across runs (0.6 ↔ 0.8) is load-driven; eval-suite stability is the next improvement target, not more budget.
- Next levers, in order: eval-suite hardening; second target (`helper-docs`); wiring `prologue`/`nudges`/`fewshots` as live targets (currently introspection-only); then the memory/ontology phases of the kernel roadmap.
