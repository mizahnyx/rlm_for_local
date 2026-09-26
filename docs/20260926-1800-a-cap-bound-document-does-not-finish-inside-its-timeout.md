# A cap-bound document does not finish inside its derived timeout

**Date:** 2026-09-26. **Status:** observed state plus one labelled inference; the measurement that
would settle it is still running. Context: `docs/20260925-0200-…` (the derived timeout),
`docs/20260923-2100-…` (the cold cost it was derived from).

## What is observed

Three records in `~/rlm-derived/summaries-sources.jsonl`, and the second document still in flight:

| document | input | outcome |
|---|---|---|
| 1 | 746 chars | **done in 62 s** — 68 estimated output tokens, compression 0.366, groundedness 0.565, no cap hit, 0.42 s unexplained |
| 2 | **32 722 chars** (the 32 KiB cap) | started, and still running long past its call timeout |

## The inference, and why it is only an inference

`HTTPModelBackend` retries inside the HTTP call, so **an attempt that times out is retried silently**
— only the *final* failure writes a metrics row. Three records therefore mean: document 1 wrote
start+end, document 2 wrote start, and document 2 has now spent longer than the 2 061 s the derived
bound allows. The only consistent reading is that it is in its **second or third attempt**.

I have not yet seen whether it ends in a description or an error, so "the cap-bound document does not
finish inside its timeout" is currently an inference from the record count, not a measured verdict.

## The arithmetic that made it marginal

A 32 KiB document is ~8 200 tokens of prompt. At the measured fresh-prompt throughput of 5.4–8.3
tok/s that is **22–31 minutes**, against the 2 061 s (34 minute) bound derived from the *same*
throughput figures. It was marginal by construction, and the retry window multiplies the worst case
to roughly **1.7 hours before the run admits failure**. Slow, and wasteful: the model works, the
client gives up, and the work is thrown away.

## The decision this exposes — an owner call about bounds

- **Lower the input cap.** 16 KiB is ~11–15 minutes at the measured rates, comfortably inside the
  timeout, and still several times more than a 400-token description needs. The cost is that the
  largest documents are described from their opening rather than in full.
- **Raise the timeout** and accept that the biggest documents take up to an hour each, with the
  retry window still multiplying a genuine failure.

A third, smaller thing worth fixing whichever way that goes: **the input cap is not part of the
derivation key.** `summary<=v1,400tok` names the output bound and the engine but not the cap, so
changing the cap would leave descriptions of *truncated* documents looking valid and never
re-described. That is a cache-correctness hole rather than a performance one, and it is cheap to
close by putting the cap in the params string.

## Still running

The A/B is queued behind the describer, as designed: the same question twice, `RLM_DERIVED_PASS=0`
then `=1`, with `derived_hits_shown` the count that decides whether a mechanism result exists. If the
second document ends in failure, the A/B still runs — against the descriptions that did get written,
which the record will state plainly rather than quietly.
