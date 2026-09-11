# Router Model Assessment — lunacode `llama-server` router

**Date:** 2026-09-11 00:50
**Router:** `https://lunacode:9010/v1` — llama.cpp router, `role=router`,
`max_instances=4`, `models_autoload=true`, 13 models configured
**Host:** lunacode — 15 GiB RAM / 11 GiB swap, 4 inference threads
**Harness:** this repository, `tiny` profile, via
`scripts/assess_router_models.py`
**Baseline:** `Qwen3.5-4B-Abliterated` — **90/100, SUITABLE**, P4 = 15/15
(voluntary `answer['ready']`), measured earlier the same day.

**Result:** six other models pass the battery — five at **100/100**; see §3.

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

## 3. Stage 2 — suitability battery (complete)

Ran the `--quick` battery (P1 + P4 + P6, 50-point scale reported /100) against the
ten screen survivors, one at a time with a router restart between models. Total
wall clock **22 247 s (6.2 h)** including the one retry.

| Model | Score | Verdict | P1 | P4 | P6 | Time |
|---|---|---|---|---|---|---|
| **Qwen3-4B-2507** | **100** | **SUITABLE** | 20/20 | 15/15 | 15/15 | 1 430 s |
| **Nanbeige4.2-3B-Heretic** | **100** | **SUITABLE** | 20/20 | 15/15 | 15/15 | 4 325 s |
| **MiniCPM5-2B** | **100** | **SUITABLE** | 20/20 | 15/15 | 15/15 | 818 s |
| **Qwen3-VL-4B** | **100** | **SUITABLE** | 20/20 | 15/15 | 15/15 | 2 330 s |
| **Qwen3-VL-4B-Abliterated** | **100** | **SUITABLE** | 20/20 | 15/15 | 15/15 | 3 198 s |
| **Qwen3.5-4B-HauhauCS** | **90** | **SUITABLE** | 20/20 | 15/15 | 10/15 | 2 253 s |
| **Qwen3.5-4B-Q38-Heretic** | **90** | **SUITABLE** | 20/20 | 15/15 | 10/15 | 3 086 s |
| Huihui-0.8B-Abliterated | 70 | MARGINAL | 20/20 | 0/15 | 15/15 | 882 s |
| Qwen3.5-2B-Instruct | 60 | MARGINAL | 20/20 | 0/15 | 10/15 | 1 784 s |
| Qwen3.5-0.8B-Unsloth | 50 | MARGINAL | 20/20 | 0/15 | 5/15 | 1 196 s |
| *(baseline)* Qwen3.5-4B-Abliterated | 90 | SUITABLE | 20/20 | 15/15 | 10/15 | 1 848 s |

**Answer to the question asked:** besides `Qwen3.5-4B-Abliterated`, **six models
pass the suitability battery**:

- **five with a perfect 100/100** — `Qwen3-4B-2507`, `Nanbeige4.2-3B-Heretic`,
  `MiniCPM5-2B`, `Qwen3-VL-4B`, `Qwen3-VL-4B-Abliterated`, i.e. *above* the
  current production model on this scale;
- **two at 90/100** — `Qwen3.5-4B-HauhauCS` and `Qwen3.5-4B-Q38-Heretic`, equal
  to the baseline. Both lose the same 5 points on P6's third needle.

Three models are MARGINAL (70 / 60 / 50), all of them 0.8B–2B, and all three fail
for the same reason: P4 = 0.

**Notable:** `MiniCPM5-2B` reaches 100/100 in 818 s — a third of the baseline's
runtime — so on this hardware it is the best capability-per-second on the router.

### 3.1 P1 is saturated — P4 is the discriminator

Every one of the ten models scored **20/20 on P1** ("emits a valid ```repl
block"), including both 0.8B models. On this router P1 separates nothing: the
protocol *format* is easy. The entire spread between SUITABLE and MARGINAL comes
from two probes:

- **P4 — voluntary `answer['ready']` submission (0 or 15).** Seven models submit on
  their own; three never do and are carried by forced finalization. `MiniCPM5-2B`
  and `Huihui-0.8B-Abliterated` illustrate the gap exactly: same P1, same
  six-model class, opposite P4 outcome.
- **P6 — needle retrieval (0–15).** The two 90/100 models lose the same third
  needle; `Huihui-0.8B-Abliterated` retrieves *all three* needles despite failing
  P4 outright — the sub-call path works fine on a 0.8B model when the loop
  reaches it.

Two consequences worth acting on:

- **The R25.6 voluntary-submission few-shot generalised.** Seven of ten models now
  submit on their own and five hit 100/100, where the recorded pre-R25.6 baseline
  was 0/15 on P4 with forced finalization carrying every run. The fixed few-shot
  (a short probe then an immediate `answer['ready']`) is not
  Qwen3.5-Abliterated-specific.
- **`Qwen3.5-2B-Instruct`'s P4 evidence is contradictory and deserves a look:**
  the probe reports `answer['ready'] = True found in model output` *and*
  `No final answer detected` + forced finalization. The model wrote the
  submission text somewhere the REPL did not execute it as a submission (a
  malformed or outside-the-fence block). That is a harness-diagnostic gap, not a
  model verdict — P4 scores 0 on a disagreement it does not explain.

### 3.2 Models deliberately not battery-tested

`Qwen2.5-VL-3B` and `LFM2.5-2.6B-Heretic` failed the stage-1 screen and were
skipped: a ~1.5 h battery each would only re-confirm that they cannot emit the
protocol in a single turn. Re-run them with `--only` if you want the explicit
FAIL on record.

### 3.3 One model needed a retry, and why

`Qwen3.5-4B-Q38-Heretic` first recorded a `ConnectTimeout` after burning its full
900 s client timeout. The router log shows the model itself is fine
(`state: ready`, 4.3B params, `n_ctx` 262 144, serving at ~7 tok/s with prefix
caching at `f_keep = 0.97`). The failure is a **host interaction, not a model
property**: `--before-each` restarts the router, the first request then triggers
a load of a model whose preset asks for a 262 144-token context on a 15 GiB
machine, and the router is unresponsive to new connections while that allocation
happens. The re-run completed normally at 90/100.

Mitigations now in the tool: `wait_for_router` requires three consecutive answers
before starting a model, and a model whose run dies on an `httpx.TransportError`
is retried once after 20 s instead of being recorded as a failure. A transient
there costs an hour, so the retry is worth its worst case.

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

- **`Qwen3.5-4B-Q38-Heretic`** is being re-measured after its first attempt died
  on a `ConnectTimeout` (the router's restart/load window, not the model). The
  sweep now waits for three consecutive router answers and retries a model once
  after a transport error, so a second occurrence should not cost an hour.
- **`Qwen3.5-2B-Instruct`'s contradictory P4 evidence** (submission text present,
  no submission executed) should be turned into a real diagnostic: P4 currently
  scores 0 without saying *why* the two signals disagree.
- **P1 is saturated on this router** — every model, down to 0.8B, scores 20/20.
  If the battery is used for ranking rather than pass/fail, the weight belongs on
  P4 and P6, not P1.
- **`--screen` is not a gate.** It passes 0.8B models; its only justified use is
  dropping models that cannot emit the protocol at all.
- **Parallel assessment is not safe here.** The router can hold several
  instances, but this host cannot — see §4. If a bigger box ever serves the
  router, raise `max_instances` and parallelise then.
