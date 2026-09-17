# The relevance signal: one voluntary submission, and a measurement gap

**Created:** 2026-09-16 20:15
**Status:** closed — suggestive, not established; the gap is named and cheap to close
**Supersedes nothing.** Tests the prediction in
`docs/20260916-1710-corpus-match-quality-signal.md`.

## What was run

Two 8-turn runs of the same unanswerable question on the commit that labels every
hit with how much of the question it covers (`f616077`), against a baseline of four
previous 8-turn runs that were **all** forced.

| run | turns | forced | elapsed | turns using a helper | addresses printed | citations | answer | refusals |
|---|---|---|---|---|---|---|---|---|
| sig1 | 8 | **yes** | 2 477 s | 8 | 0 | 0 | prose, states coverage | `corpus_uncited` ×1, `corpus_last_turn` ×1 |
| sig2 | 8 | **no** | 1 719 s | 7 | 0 | 0 | prose, states coverage | `corpus_last_turn` ×1 |
| *attempts 1–4 (before)* | 5, 8, 8, 8 | **yes, all** | 1 474–2 247 s | 6–12 | 0–5 | 0–1 | — | 0–2 |

## What this does and does not show

**New:** sig2 **submitted voluntarily**. Across the four previous 8-turn runs of
this question the model never once concluded on its own — every answer arrived by
forced finalization. This is the first voluntary submission on an unanswerable
question in this project's records.

**Not established:** that the label caused it. One run in two did it; the other
behaved exactly as before and was answered by forced finalization. This project has
already recorded that model behaviour is unstable across router cache states, so a
single voluntary submission is a *hypothesis* about the signal, not a measurement
of it.

**And the measurement I wanted has a gap.** I tried to read the distribution of
`strong/partial/weak/none` labels the runs saw, by grepping the cells' printed
output — and got nothing, in both runs, because **the model printed no search
results at all** (0 addresses printed, 0 snippets). The labels were served to it
inside its tool results; they were not echoed into the transcript, so the log does
not contain them. Inferring "the model saw `weak`" from "the label exists" is
exactly the kind of unverified claim this project does not make.

Two incidental facts worth keeping: sig1 recorded a `corpus_uncited` refusal (the
guard fired once), and both answers *state coverage* — the absence arm in
substance, as in every recent run.

## The cheap fix that makes the next run decisive

Log what each search **served**, from the parent (which already sees every helper
response): one `corpus_search_quality` guardrail event per call carrying the
labels' distribution — `weak=3 partial=1` — and the question's content-word count.
Then "did the model see a weak match?" is a recorded fact rather than an inference,
and the next two runs can attribute the voluntary submission to the signal or to
variance. Ten lines, one test, one mutation.

That is the next step, and it is deliberately the only one: the sequence after it
is unchanged — the escape arm's harness-side evidence, then RO7.
