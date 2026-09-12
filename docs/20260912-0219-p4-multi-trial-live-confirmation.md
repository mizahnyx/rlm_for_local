# P4 multi-trial, live: three trials, three different failures

**Date:** 2026-09-12 02:19
**Confirms:** `20260911-1518-p4-multi-trial.md` on a real model, which that
document listed as its first open item ("not re-measured live").
**Status:** confirmed, and the result is stronger evidence for the change than
the change's own rationale was.

## 1. The run

Full quick battery — not P4 alone — so the record is an operator-facing verdict
rather than a composite assembled by hand. Same methodology as every other run in
this thread: `--profile tiny`, `default` weights, router restarted before the
model via `--before-each`.

```bash
uv run python scripts/assess_router_models.py \
    --only Qwen3.5-2B-Instruct \
    --out logs/router-model-battery-recheck2.jsonl \
    --before-each "ssh lunacode systemctl --user restart llama-router.service"
```

## 2. Result

| Probe | Single-shot runs (2 recorded) | This run, 3 trials |
|---|---|---|
| P1 | 20/20 both times | 20/20 |
| **P4** | 0/15 both times | **0.0/15 — `['none', 'none', 'none']`** |
| P6 | 10/15 both times | 10/15 |
| Score | 46.7 (predicted and measured) | **46.7** |
| Verdict | NOT SUITABLE | **NOT SUITABLE** |
| Elapsed | 1778.8 s / 1779 s | 2177 s |

The verdict is unchanged, which is the point: the single-shot 46.7 was **not** one
unlucky sample. Three independent trials on three different questions all failed
to submit, so the model's P4 is 0/3 and the composite arithmetic is the same as
before — `(10 + 0 + 20 × 10/15) / 50 = 46.7`.

Two things this also settles by accident: **P1 and P6 were identical in all three
runs**, so the instability found on 2026-09-11 is specific to P4's submission
behaviour, not to protocol emission or needle retrieval. The multi-trial sampling
is aimed at the one probe that needed it.

Wall time grew 1779 s → 2177 s (+398 s), less than "3× P4" would suggest: P1's
three protocol runs dominate the battery alongside P4's.

## 3. The interesting part: the three trials failed three different ways

```
[P4] Trial 1: FAIL — no submission (forced finalization)
[P4] Trial 1: DIAGNOSTIC [submission_text_is_quoted_or_commented]: submission line
     sits inside a string in an executed block (turn 12), not as a statement — the
     interpreter never runs it as code, so `answer['ready']` is still False when the
     cell ends ... The model is describing the submission instead of performing it.
[P4] Trial 2: FAIL — no submission (forced finalization)
[P4] Trial 2: answer['ready'] = True never appeared in the output.
[P4] Trial 3: FAIL — no submission (forced finalization)
[P4] Trial 3: DIAGNOSTIC [submission_not_reached_at_runtime]: submission line is a
     real statement in an executed block (turn 12) that produced no traceback and no
     submission — it was not reached at runtime. The harness cannot tell which of
     these it was: a conditional or loop body that did not run, `answer` rebound to
     something that is not a dict, or an earlier block in the same turn that ended
     the turn first.
[P4] Summary: 0/3 trials submitted voluntarily — no majority voluntary
```

| Trial | What the model did | Diagnostic |
|---|---|---|
| 1 | Wrote the submission line **inside a string** in an executed block | `submission_text_is_quoted_or_commented` |
| 2 | Never emitted `answer['ready']` at all | none — correctly, there is no contradiction to explain |
| 3 | Emitted it as a **real statement** in a clean block that did not submit | `submission_not_reached_at_runtime` |

Three consequences, each of which validates a decision made earlier the same day:

1. **The text/code lexer was not speculative.** It was added at 13:59 on the
   strength of this model writing literal code into `answer['content']` in its own
   P6 evidence; four hours later it fired on the same model for the reason it was
   written. Without it, trial 1's line would have been reported as
   `submission_not_reached_at_runtime` — the right class, the wrong explanation.
   The caveat in `20260911-1359-p4-live-confirmation.md` §4 ("would name that
   mechanism if it applied; it was not observed applying") is now closed by
   observation.
2. **Per-trial diagnostics were the right call.** Trial 1 and trial 3 failed by
   *different* mechanisms; collapsing diagnostics to the first would have reported
   the string case and hidden the not-reached case entirely.
3. **Multi-trial sampling was the right call, for a better reason than expected.**
   The justification was "one sample of a flaky behaviour decided a band". The
   live result shows the behaviour is not merely flaky but a *repertoire*: the
   single-shot probe would have reported one of these three explanations as *the*
   reason the model cannot submit.

## 4. The instability question, narrowed

The 2026-09-11 investigation saw this model submit voluntarily three times in a
row on a **warm** router (prompts already cached), and never on a cold one. This
run is cold, and trial 1 is the *same query* as those warm runs — so for trial 1
the cache-state explanation survives: cold, it wrote the line inside a string and
did not submit; warm, it submitted. Trials 2 and 3 are new questions whose warm
behaviour has not been measured, so no claim is made about them.

What the multi-trial probe now gives an operator is the decision-relevant fact
regardless of mechanism: **0 of 3 trials submitted voluntarily**, so this model
does not drive the harness, and the three explanations are on the record rather
than one of them.

## 5. Open

- **Trial 3's mechanism is genuinely unresolved**, and the diagnostic says so
  instead of guessing. Distinguishing "did not run" from "ran but did not take
  effect" needs the cell's runtime state, which the trajectory does not carry.
- **The cache-state hypothesis is still a hypothesis.** It fits every observation
  so far (cold ⇒ no submission, warm ⇒ submission on the identical query), and it
  has not been isolated. A controlled test — same query, warm vs cold, several
  repetitions each — is the way to settle it, and it is the natural next step if
  P4 verdicts are going to be trusted for marginal models.
- **P4's `--quick` cost** is now ~400 s more per model on this host; `RLM_CHECK_P4_TRIALS=1`
  remains the documented way to buy that back.
- Unchanged from the earlier reports: P3's scoring weaknesses, `--screen` is not a
  gate, `Qwen2.5-VL-3B` and `LFM2.5-2.6B-Heretic` remain un-battery-tested, and
  the host's `max_instances=4` remains an OOM risk.
