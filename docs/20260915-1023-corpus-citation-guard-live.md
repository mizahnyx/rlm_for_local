# The citation guard, live: one run, one cited answer, and what it cost

**Created:** 2026-09-15 10:23
**Status:** closed — the guard does what it was added for; the sample is n=1 and the
cost is real
**Supersedes nothing.** Follows `docs/20260915-0655-corpus-citation-compliance-measured.md`
(the prompt-only phase, 0 cited answers in 3 runs) and
`docs/20260914-2135-corpus-live-runs-and-the-unsearched-nudge.md`.

## The change this measures

The uncited-answer refusal landed as `169b20e`: a corpus answer containing neither
an address nor the word `coverage` is refused once per nudge of its own budget,
on both submission channels, with `NUDGE_CORPUS_UNCITED` carrying the escape
hatch ("say the corpus does not contain it and quote the coverage"). The question
this run answers is the one the previous record left open: **does the guard
produce cited answers, or merely more turns?**

## Scale

- Model: `Qwen3.5-4B-Abliterated` (`laptop` profile, root and sub), `rlm ask
  --profile laptop --max-turns 8`, router at `127.0.0.1:9010`.
- Corpus: `/srv/corpus` with the path index plus a text index at **~42%
  coverage** (1,225,427 of 2,882,822 text files) — the same evening's niced
  `index_text` window was still running, so this run competed with it.
- Same question, same script (`live-ask.sh`) as the three prompt-only runs, so
  the comparison is like for like.

## What was measured

| Run | Turns | Elapsed | Forced | Helper calls | `corpus_uncited` refusals | Answer | Citations | Recorded |
|---|---|---|---|---|---|---|---|---|
| 5 (prompt only) | 3 | 599 s | no | search ×1 | 0 | 207 chars | 0 | `answers_with_address=False` |
| 6 (prompt only) | 7 | 1385 s | no | search ×1, read ×5 | 0 | 180 chars | 0 | `answers_with_address=False` |
| 7 (prompt only) | 7 | 1117 s | no | search ×1, read ×5 | 0 | 197 chars | 0 | `answers_with_address=False` |
| **8 (guard live)** | **8** | **1574 s** | **yes** | search ×1, read ×5 | **2** | **520 chars** | **2** | **`answers_with_address=True`**, with a `Citations:` line |

**The guard fired twice** — the model submitted uncited answers, was told twice to
cite or state coverage, spent its whole 8-turn budget doing that, and then the
forced-finalization answer came back **with a `Citations:` line naming an
address**. So the requirement is satisfiable at this model scale when the harness
insists; it was not satisfiable by asking politely.

**The citation is grounded, and that was checked rather than assumed.** Comparing
the addresses in the run's own cell output with the addresses in its answer, as
sets: 1 distinct address printed, 1 distinct address cited, intersection 1, cited
but never printed **0**. The model cited something it had actually read and
invented nothing. (The comparison is a set operation over the trajectory; no
answer text was read to make it.)

## What this does not prove

- **n = 1.** One run, one question, one model. The prompt-only phase was also
  measured on one model, and its 0-for-3 is the reason the guard exists; the
  guard's 1-for-1 is one data point, not a rate.
- **The cited answer came from forced finalization.** The model was refused twice,
  used every turn, and complied only when the forced-finalization prompt asked it
  once more. A cited answer that arrives only at the end of the budget is a
  success for provenance and a failure for efficiency, and both halves belong in
  the record.
- **Cost.** 1574 s and the full 8 turns, against 599–1385 s and 3–7 turns in the
  prompt-only runs. One run cannot separate that from the index window competing
  for the machine, but the direction is not in the guard's favour.
- **Whether a large model needs the guard at all** — untested, as before.
- **Whether the escape hatch absorbs an unanswerable question gracefully** — the
  one behaviour that matters most for a 42%-indexed corpus, and this question had
  an answer, so the hatch was never exercised live.
- The content of the answer: whether what it says about the corpus is *true* is
  the owner's evaluation, on the laptop, not something this document can assert.

## State of the corpus index when this was written

The 12-hour `index_text` window (started 2026-09-14 22:40) reached **1,225,427 of
2,882,822 text files — 42.5%** at 09:56, at the measured ~60 items/s, and stops on
its own budget shortly. The remaining ~1.66M is roughly **7–8 more hours of
windows**. Until then every search carries its coverage, and "no matches" remains
a statement about 42% of the corpus rather than about the corpus.
