# The typed controller is abandoned

**Date:** 2026-09-26. **Status:** owner's call, recorded. Closes Gate 6 for now. Evidence:
`docs/20260925-0300-laya-phase-b1-the-shipped-checkpoint-cannot-make-this-decision.md` (Laya, 3/12),
`docs/20260927-0100-a-better-decision-model-still-cannot-make-this-decision.md` (GLiNER2.5-Decide,
5/12 in one call and 7/12 one head per call). Design and pre-registered criteria:
`docs/20260924-0200-gate-6-the-laya-controller-probe-design.md`.

## The call

Owner, 2026-09-26: *"Lets for now abandon the typed controller idea."*

## What that means in the code

- **Nothing is wired into a run.** No admission engine is constructed by the harness, no card is
  triaged, and the current behaviour stands: what a search serves is what the model is given.
  `make_admission_engine` was built for a controller that now has no caller.
- **The client, the probe and the 14 cases stay.** They are tested code, the probe is a
  reproducible instrument, and deleting them would delete the only measurement of this idea. They
  stay unshipped: `src/rlm_local/decisions.py`, `scripts/probe_laya_decisions.py`,
  `scripts/laya_decision_cases.json`.
- **No further checkpoint sweeps are planned.** The reason is in the numbers: the second model was
  twice as accurate as the first and still scored at the majority class in its head-to-head
  configuration, never chose `summarise` correctly, and put *more* confidence on its wrong answers
  than its right ones. A third checkpoint is a lottery ticket against a criterion that already
  failed twice.

## What stays true, and is worth keeping

- **The label descriptions are the signal.** Bare label names scored 1/12; the same cards with the
  criterion sentences scored 5/12. Any later attempt at this idea — by any model — should carry the
  sentences.
- **The sign of "can a small local model make this call" is negative on this hardware, zero-shot.**
  That is now measured twice, on two architectures, with one scorer.
- **The distance to usable is not small.** Best configuration 7/12 = 0.583 on a four-way decision,
  with 10 of 12 answers being `expand`; an admission controller that expands nearly everything is
  the no-controller baseline with extra latency (2.8–4.7 s a card against a Phase A bar of a few
  hundred milliseconds).

## What this does not close

- **The problem the controller was for.** The harness still has no way to decide what to spend
  context on when a search serves more than the model can use — the thread `docs/20260923-1700-…`
  (the value set) and `docs/20260927-0000-…` (what descriptions actually reach) are the same
  problem approached from the retrieval side. If it bites, `docs/20260924-0200-…` §7 is where to
  restart, and its fallback — a head trained on our own cards — still needs the signal that the
  probe design's §3 says does not exist.
- **The Connectome comparison** (`docs/20260925-0400-…`): a *generative* cheap model as the
  read-time admission policy is a different design that this measurement does not touch, because
  it was not tried.
