# Router Model Assessment — lunacode `llama-server` router

**Date:** 2026-09-11 00:50
**Router:** `https://lunacode:9010/v1` — llama.cpp router, `role=router`,
`max_instances=4`, `models_autoload=true`, 13 models configured
**Host:** lunacode — 15 GiB RAM / 11 GiB swap, 4 inference threads
**Harness:** this repository, `tiny` profile, via
`scripts/assess_router_models.py`
**Baseline:** `Qwen3.5-4B-Abliterated` — **90/100, SUITABLE**, P4 = 15/15
(voluntary `answer['ready']`), measured earlier the same day.

---

## 1. Why this document has two stages

The full `rlm check` battery is ~7 completions per model. Measured on this host,
a single completion runs at **6.6 tok/s prompt processing and 3.0 tok/s decode**
(single resident model, no swap pressure — see §4), so one `--quick` battery is
roughly **1.5 hours per model**. Twelve models would need ~18 hours of the
laptop's CPU.

So the assessment runs in two stages:

1. **Protocol screen** (done, §2) — one turn per model, using the *real* system
   prompt and prologue, checking only whether the model emits a ```repl block.
   ~2–6 minutes per model including the model load. This separates "can operate
   the protocol at all" from "cannot", which is the cheapest available
   discriminator.
2. **Suitability battery** (running, §3) — the project's own `--quick` battery
   (P1 + P4 + P6), on the models that passed the screen.

The screen is **necessary, not sufficient**: it is deliberately weak (a 0.8B
model passes it), and the battery is what actually measures multi-turn
behaviour, error recovery and voluntary submission.

## 2. Stage 1 — protocol screen (complete)

Method: real system prompt (`prompts.build_system_prompt` with the `tiny`
profile's `prompt_vars`), the metadata message, the decomposition prologue, then
**one** `chat()` call with `max_tokens=400`, `temperature=0`. Pass = a ```repl
fence appears in the reply.

| Model | Screen | Reply | Time |
|---|---|---|---|
| Qwen3-VL-4B-Abliterated | **PASS** | 497 chars | 247 s |
| Qwen3-VL-4B | **PASS** | 576 chars | 237 s |
| Qwen3.5-4B-Q38-Heretic | **PASS** | 571 chars | 199 s |
| Qwen3-4B-2507 | **PASS** | 553 chars | 190 s |
| Nanbeige4.2-3B-Heretic | **PASS** | 722 chars | 347 s |
| Qwen3.5-0.8B-Unsloth | **PASS** | 1 644 chars | 102 s |
| Qwen3.5-2B-Instruct | **PASS** | 525 chars | 101 s |
| Qwen3.5-4B-HauhauCS | **PASS** | 707 chars | 93 s |
| Huihui-0.8B-Abliterated | **PASS** | 245 chars | 87 s |
| MiniCPM5-2B | **PASS** | 369 chars | 77 s |
| Qwen2.5-VL-3B | **FAIL** | 285 chars — prose only ("I will start by printing a small sample…"), no fence | 126 s |
| LFM2.5-2.6B-Heretic | **FAIL** | 96 chars — emitted `<\|tool_call_start\|>[llm_query(…` instead of a fenced block | 68 s |

Raw records: `logs/router-model-assessment.jsonl`,
`logs/router-model-screen-retry.jsonl` (both gitignored).

Two observations worth keeping:

- **The screen is weak.** Even 0.8B models pass it, so it is a rejection filter,
  not a ranking. Nothing here should be read as "likely to pass the battery".
- **`LFM2.5-2.6B-Heretic` fails in the same way the project already recorded**
  for its LFM predecessors: it imitates the *shape* of a tool call rather than
  emitting the harness's fenced Python. That is a model-family property, not a
  prompt-wording accident, and it is consistent with the FAIL recorded for
  `LFM2.5-VL-1.6B` and `LFM2.5-8B-A1B-Uncensored`.

## 3. Stage 2 — suitability battery (in progress)

Running the `--quick` battery (P1 + P4 + P6, 50-point scale reported /100) in the
background, in this order, one model at a time with a router restart between
models so only one model instance is resident (see §4):

| # | Model | State |
|---|---|---|
| 1 | Qwen3.5-4B-HauhauCS | running |
| 2 | Qwen3.5-4B-Q38-Heretic | queued |
| 3 | Qwen3-4B-2507 | queued |
| 4 | Nanbeige4.2-3B-Heretic | queued |
| 5 | Qwen3.5-2B-Instruct | queued |
| 6 | MiniCPM5-2B | queued |
| 7 | Qwen3-VL-4B | queued |
| 8 | Qwen3-VL-4B-Abliterated | queued |
| 9 | Qwen3.5-0.8B-Unsloth | queued |
| 10 | Huihui-0.8B-Abliterated | queued |

**Skipped deliberately:** `Qwen2.5-VL-3B` and `LFM2.5-2.6B-Heretic` — they cannot
emit the protocol in a single turn, so a ~1.5 h battery each would only
re-confirm a rejection. Re-run them with `--only` if you want the explicit FAIL
on record.

Results append to `logs/router-model-battery.jsonl`, one JSON object per model,
so the sweep is resumable: read the completed model IDs and re-launch with
`--skip` for those. Expected wall clock: roughly 12–16 hours for the ten models.

```bash
# collect the verdicts as they land
type logs\router-model-battery.jsonl

# resume after an interruption (skip what is already recorded)
uv run python scripts/assess_router_models.py --endpoint https://lunacode:9010/v1 \
    --out logs/router-model-battery.jsonl --skip Qwen3.5-4B-HauhauCS
```

## 4. Host tuning note (why the sweep restarts the router between models)

The first sweep attempt ran on the shared router config with `max_instances=4`.
The router keeps every model it has served resident, so within an hour three 4B
instances were live on a 15 GiB machine:

```
Mem:  15Gi total, 12Gi used, 263Mi free      Swap: 11Gi total, 6.8Gi used
load average 4.26      llama-server: 2h11m CPU in 39 min wall clock
```

Inference collapsed to **6.6 tok/s prompt / 3.0 tok/s decode** — an
order of magnitude slower than a single-instance run should be — and the risk of
an OOM killing the user's router was real.

Restarting the router before each model keeps exactly one instance resident
(measured afterwards: 2.6 GiB free, 6.7 GiB available, one instance at 10 GB
RSS). Note the harness tool does **not** permanently change the host's
`max_instances`: the workaround is a `--before-each` command supplied at
invocation, so the operator's configuration is untouched.

**Correction to an earlier reading:** clearing the resident models restored
memory but *not* speed — single-instance throughput is the same 6.6/3.0 tok/s.
So the laptop is simply slow at these models with 4 threads, and the long runtime
is not a thrashing artefact. The restart is still worth keeping: it removes the
OOM risk and about 5 GiB of RAM pressure.

## 5. How to reproduce

```bash
export RLM_ENDPOINT="https://lunacode:9010/v1"

# stage 1 — cheap protocol screen over everything the router advertises
uv run python scripts/assess_router_models.py --screen

# stage 2 — the real battery, one model at a time, router restarted between runs
uv run python scripts/assess_router_models.py \
    --only Qwen3.5-4B-HauhauCS Qwen3-4B-2507 \
    --before-each "ssh lunacode systemctl --user restart llama-router.service"
```

Both stages use the same `RLM_ENDPOINT` / `--endpoint` plumbing added to the CLI
(see the operator guide §2.3), so a single export configures `rlm ask`,
`rlm chat`, `rlm check` and this script.

## 6. Follow-ups

- **Stage 2 verdicts** land in `logs/router-model-battery.jsonl`; the table in §3
  should be replaced with real scores once the run finishes.
- **`--screen` is not a gate.** If it is ever used to decide what *not* to
  assess, remember it passes 0.8B models; its only justified use is dropping
  models that cannot emit the protocol at all.
- **Parallel assessment is not safe here.** The router can hold several
  instances, but this host cannot — see §4. If a bigger box ever serves the
  router, raise `max_instances` and parallelise then.
