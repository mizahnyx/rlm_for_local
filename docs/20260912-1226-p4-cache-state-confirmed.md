# The router's cache decides this model's P4 — a controlled result

**Date:** 2026-09-12 12:26
**Closes:** roadmap VD1 and sequence item 3 — "does a warm router really make the
flaky model submit? Fits every observation, never isolated".
**Status:** confirmed on a controlled comparison. The answer is yes, and it changes
how a P4 verdict should be read.

## 1. The comparison

The 2026-09-11 investigation had the right instinct and the wrong design: it
compared runs that differed in more than cache state. This one varies only that.
Same model (`Qwen3.5-2B-Instruct`), same query (P4's trial 1, the ocean question),
same server, `temperature=0.0`, same `--profile tiny` — and within each round the
**first** run populates the router's prefix cache while the **second** run reuses
it. Two rounds, each starting from a router restart.

```
$ python scripts/p4_cache_state_probe.py --model Qwen3.5-2B-Instruct \
      --restart-cmd "ssh lunacode systemctl --user restart llama-router.service" \
      --rounds 2 --runs-per-round 2 --out logs/p4-cache-state.jsonl

round  position  outcome
    1  first     none
    1  warm      voluntary
    2  first     none
    2  warm      voluntary

first-of-round: 0/2 voluntary
warm-in-round:  2/2 voluntary
```

Four runs, four consistent results, ~350 s each. On a freshly restarted router the
model never submits; on the immediately following run of the *identical* prompt it
always does. Nothing else differs.

## 2. What this means for a P4 verdict

- **P4 measures the cold case, and that is the operator-relevant one.** Each of
  P4's three trials sends a *different* question, so each is a first run of a fresh
  prompt — i.e. every trial is a cold-prompt measurement. A user bringing a new
  task to the harness is in exactly that position, so the verdict answers the
  question that matters. It is not an artefact of unlucky sampling.
- **A warm-run number is a different number.** Repeating the same task on a warm
  router gave voluntary submission every time. So "this model cannot submit" is a
  statement about first contact with a prompt, and any sweep should record the
  router state alongside the score — which is why the sweep's per-model records
  carry `weights` and `p4_diagnostic`, and why this document exists rather than a
  footnote.
- **It is not universal.** Five models scored a full 15/15 on P4 in the
  2026-09-11 sweep, cold. The instability is a property of *this* model's
  behaviour under this serving stack, not of the probe; a probe that returned the
  same answer regardless of the model would be the broken one.

## 3. What is *not* established

- **The mechanism inside the serving stack is not isolated.** The comparison
  varies cache state and nothing else, which is enough to establish that cache
  state is the operative difference — but "prefix reuse changes the numerics" is
  the plausible explanation, not a measured one. The router runs a quantized KV
  cache with flash attention and slot selection by LCP similarity; the router log
  shows the warm run reusing a slot at `f_sim_best = 0.948`. Confirming the causal
  path would need work inside llama.cpp, which is outside this project.
- **Only one model, one query, two rounds.** `n=4` is enough for a 4/4 split
  between conditions and not enough to put a confidence interval on anything. The
  claim made here is the one the data supports: in this configuration, cold runs
  did not submit and warm runs did, every time.
- **The other two P4 questions were not tested warm.** The probe used trial 1's
  query so the result is comparable with the recorded single-shot runs; whether
  trials 2 and 3 behave the same way when warmed is unmeasured.

## 4. Guards and artifacts

- `scripts/p4_cache_state_probe.py` — the experiment, reusable for any model:
  `--restart-cmd` supplies the cold condition, `--rounds`/`--runs-per-round` the
  sample, `--query-index` picks which P4 question to use. It classifies each run
  with the same `_run_p4_trial` the battery uses, so its outcomes are directly
  comparable with a recorded sweep.
- `logs/p4-cache-state.jsonl` — the four records (gitignored; reproduced in full
  in §1).

## 5. Open

- **The battery could record the cache state it measured under.** It currently
  records the weight profile, the sampling and the P4 diagnostic, but not "this
  was a cold-prompt measurement". Adding one field would make the warm/cold
  distinction visible in every sweep record instead of only in this document.
- **The other router-host items are unchanged**: `max_instances=4` on a 15 GiB box,
  and the two models never battery-tested after failing the screen.
