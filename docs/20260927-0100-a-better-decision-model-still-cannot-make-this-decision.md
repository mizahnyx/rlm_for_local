# A better decision model still cannot make this decision

**Date:** 2026-09-26. **Status:** Gate 6 phase B1, run a second time against a newer and better
checkpoint — **failed again**, with one configuration closer than the first. Compares with
`docs/20260925-0300-laya-phase-b1-the-shipped-checkpoint-cannot-make-this-decision.md`, and
measures against the criteria pre-registered in `docs/20260924-0200-gate-6-the-laya-controller-probe-design.md` §5.

## What ran

The same 14 synthetic cards, the same probe, the same scorer, zero-shot, on lunacode's CPU:

- **`fastino/GLiNER2.5-Decide`**, released 2026-09-24: 340M parameters, DeBERTa-v3-large
  encoder, Apache 2.0, run through `gliner2` 2.0.0 in the isolated `~/laya-eval` venv (never
  the harness venv). Two protocols, because the first one was not the whole story:
  - **three heads in one call** — the shape a head-to-head with Laya must use, since Laya's
    protocol is one call per state with all questions riding together;
  - **one head per call** (`--gliner-split-heads`).
- The adapter, its mapping and its tests are `src/rlm_local/decisions.py` (`GLiNERDecideClient`,
  `gliner_schema_from`, `gliner_answers_from`); the ablation below is an ad-hoc script, and the
  two headline numbers come from the repository's own `scripts/probe_laya_decisions.py`.

## The result

| | Laya (B1, 2026-09-25) | GLiNER, three heads | GLiNER, one head per call |
|---|---|---|---|
| **accuracy (12 scored)** | 3/12 = **0.25** | 5/12 = **0.4167** | 7/12 = **0.5833** |
| always-`expand` baseline on this set | 5/12 = 0.4167 | 5/12 = 0.4167 | 5/12 = 0.4167 |
| `drop` ever chosen | no (0 of 12) | no (0 of 12) | yes (2 of 12, 1 correct) |
| mean confidence, correct / wrong | 0.030 / 0.058 | 0.361 / 0.391 | 0.514 / 0.580 |
| median decision time | 3.34 s | 2.835 s | 4.712 s |
| errored cases | 0 | 0 | 0 |

Confusion, one head per call: `expand` → expand 5 (all five right); `summarise` → expand 2;
`one_line` → expand 2, one_line 1; `drop` → expand 1, drop 1.

So the newer model is **twice the Laya checkpoint's accuracy (7/12 against 3/12)** and it is the
first of the two to name `drop` at all. It is also still **10 of 12 answers `expand`**, it never
once chooses `summarise` correctly — the action that saves the context this controller exists to
save — and its confidence is *inverted*: higher, in every configuration, when the answer is
wrong.

## What the ablations say about the *instrument*

Four variants over the same cards, all measured with the probe's own scorer in one ad-hoc script
(so they are comparable with each other; the two headline numbers above come from the probe):

| variant | accuracy | reading |
|---|---|---|
| described labels + our prompt (three heads) | 5/12 | the shipped protocol |
| **bare label names** + prompt | **1/12** | the criterion sentences are load-bearing: without them the model is far below chance |
| described labels, **no prompt** | 6/12 | one case better than with our prompt — at n=12 that is not a finding |
| bare labels, no prompt | 1/12 | the same collapse |

A fifth variant — the action head alone, scored through `parse_response` — reported 14 errors and
measured nothing. That was my instrument's fault, not the model's: `parse_response` requires all
three typed answers, so a one-head reply is a malformed *result object*. Scoring the action label
directly is what produced 7/12, and it is the measurement the split-heads option now reproduces
through the probe (4.712 s is the cost of the three calls, not of one).

## Against the pre-registered criteria

`docs/20260924-0200-…` §5: B1 fails if "accuracy is near chance on obvious cards, or confidence
is uninformative (… confident and wrong at the same rate as confident and right)". Both hold:

- **Confidence is uninformative.** Mean confidence is higher when wrong in all three
  configurations measured (0.391 vs 0.361, 0.580 vs 0.514, and Laya's 0.058 vs 0.030). A
  threshold on it would select against correct answers.
- **Accuracy is at or near the majority class.** The three-head protocol scores exactly the
  always-`expand` baseline (5/12). The split protocol clears it by two cards (7/12) while still
  answering `expand` 10 times out of 12 — a controller that expands almost everything is the
  no-controller baseline with extra steps.

And the caveat that document wrote in advance applies unchanged, which is why this is not a
verdict on decision models: **the checkpoint is trained for operational routing** (customer
intent, tickets, severity, spam) and our cards are technical document fragments. What is measured
is "this checkpoint, with this mapping, on these cards".

One further pre-registered criterion is worth recording even though it is not what was being
tested here: the Phase A bar in `docs/20260924-0200-gate-6-the-laya-controller-probe-design.md` §5
was "more than a few hundred milliseconds" per decision. A single-head call is ~1.9 s and a
three-head decision ~2.8 s (4.7 s split) on this box, so both
models are an order of magnitude past that bar — it was already true of Laya at 3.34 s. The
model card's sub-200 ms claim is not what this box measures; that difference is hardware, batching
and head count, and it is stated rather than smoothed.

## What it does not establish

- **Nothing about a head trained on our own cards**, which is the fallback the design names: it
  needs the signal that `docs/20260924-0200-gate-6-the-laya-controller-probe-design.md` §3 says
  does not exist yet. This measurement does not change that.
- **Nothing about other checkpoints.** `GLiNER2.5-Decide-1B` (59.6% on Fastino's own suite) and
  `GLiNER2.5-multi-Decide` are untried; on Fastino's benchmark the 1B is *below* the 340M, so
  bigger is not obviously better here.
- **Nothing at scale.** Twelve scored cards, one box, one run each; the two unscored cards stay
  unscored by design.
- **`relevant` and `importance` are not scored.** The probe scores the action, which is the
  decision that spends context. Whether the other two heads are any good is unmeasured.

## A packaging defect worth knowing

`gliner2` 2.0.0 cannot run inference without `peft`: its inference engine imports its trainer,
which imports `peft`, which the package does not declare as a dependency. `uv pip install gliner2`
succeeds and then the first model load raises `ModuleNotFoundError: No module named 'peft'`. What
worked: `uv pip install gliner2 peft` (which also brought `accelerate`), into the isolated eval
venv. Model load was 12.6 s warm and 199 s cold on first download.
