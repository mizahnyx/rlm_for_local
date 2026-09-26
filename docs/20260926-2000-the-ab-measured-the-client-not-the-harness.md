# The A/B measured the client, not the harness

**Date:** 2026-09-26. **Status:** first attempt **void — no mechanism result**; the client
timeout is fixed, the pair is re-running. Continues
`docs/20260926-1800-a-cap-bound-document-does-not-finish-inside-its-timeout.md` (which queued
this A/B) and `docs/20260925-0600-the-description-pilot-ran-and-did-not-test-the-thing.md`
(why the A/B replaced a diff against a recorded baseline).

## What the experiment does

One question, twice, one environment variable apart: `RLM_DERIVED_PASS=0` (arm `off`) then
`=1` (arm `on`), same index, same model, same `--max-turns`, minutes apart, one model
resident. The count that decides the mechanism result is **`derived_hits_shown`** — how many
hits carrying a cached description's text reached the model — read out of the trajectory, not
out of the answer. Secondary counts: citations, refusals (uncited / weak), helper calls,
`cell_timeout`, and wall clock per question.

Why this shape: the pilot (`docs/20260925-0600-…`) served **0** cache-origin hits, so it never
put a description in front of the model and could say nothing about usefulness; and the
baseline it was supposed to diff against was never found, so there was nothing to compare its
plumbing result *to*. Two arms run now, one flag apart, need no baseline file — and the `off`
arm is the baseline.

## What it produced: arm `off` measured the client

| | arm `off` (`RLM_DERIVED_PASS=0`) |
|---|---|
| wall clock | **902 s** |
| turns | 1 of 8 (`turns=?`, no `end` event) |
| helpers served | **0** — no search was reached |
| citations / refusals / `cell_timeout` | 0 / 0 / 0 |
| `derived_hits_shown` | **0** |
| outcome | `error=ReadTimeout: The read operation timed out`, one `guardrail` event (`model_error`) at turn 1 |
| arm `on` | started, then stopped by me: the same call would have failed the same way |

902 s is not a model that thought for 15 minutes. It is `HTTPModelBackend`'s default **300 s
read timeout plus its two silent retries** — the retries happen inside the HTTP call, so only
the final failure is recorded. The probe built its backend with `timeout=300.0` while
`rlm summarise` had already been given a timeout *derived from the measured rates* on
2026-09-23; the probe was simply never brought along.

A question's first turn on this box is a large prompt processed at ~5.4 tok/s plus a cell
decoded at ~1.8 tok/s, so one call is minutes by construction. **The run measured the HTTP
client.** No arm reached a search, so `derived_hits_shown` is 0 for a reason that has nothing
to do with descriptions, and this attempt is void as an experiment rather than negative.

## What changed

- `question_probe.DEFAULT_REQUEST_TIMEOUT = 1800.0`, with the measurement in the comment and
  the number in the probe's header (`request_timeout=`), so a run states its own bound.
- `scripts/run_question_probe.py --timeout SECONDS`, passed through to
  `HTTPModelBackend` — a knob that would have made the first attempt a 30-second edit.
- A guard for it: a mutation entry that puts the 300 s default back goes red on a test that
  asserts the argument is *passed through*, because a parsed argument the backend never sees
  is a knob that does nothing.
- `docs/operator-guide.md` states it in the probe section.

## What this does and does not establish

**Establishes:** the probe's default request timeout was too small for a corpus question on
this hardware, by measurement (902 s = 3 × 300 s), and a run built on it reports a
`ReadTimeout` that reads like a model failure. It also establishes that the A/B's *plumbing*
works: both arms launched, wrote a trajectory, and the `off` arm's failure was recorded on its
own line rather than taking the run with it.

**Does not establish:** anything about descriptions. Whether a description that reaches the
model changes a citation, a refusal or the time a question takes is still untested. The
`derived_hits_shown` count remains the decisive number, and it has never yet been non-zero in
a live run.

## Still running

The pair re-runs with the fixed timeout (`--timeout 1800`, `--max-turns 8`), same question,
same two arms. The first attempt's trajectories are kept in
`~/rlm-derived/ab-derived-pass/attempt1-timeout/` beside the corpus; the second attempt writes
`off/` and `on/`. The record of the result — counts only, with the same table — follows in a
new document rather than an edit to this one.

## Unverified

- Whether 1 800 s is enough for every turn of this question. It is derived from the measured
  rates and the observed failure, not from a completed run.
- Whether the `on` arm reaches a search at all: the question was chosen for measured overlap
  with a cached description, but "the words occur in a description" and "the model's query
  finds it" are two different claims, and only the run can distinguish them.
