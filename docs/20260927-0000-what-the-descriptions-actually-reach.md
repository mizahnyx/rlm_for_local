# What the descriptions actually reach

**Date:** 2026-09-26. **Status:** the set-level measurement, and it changes the picture the
single-question record drew. Follows `docs/20260926-2200-descriptions-reached-the-model-and-changed-nothing.md`
(the one-question live A/B) and `docs/20260926-2300-a-helper-call-cannot-be-bounded-by-the-cell-budget.md`
(the search pathology that stopped the set-level sweep).

## What was measured, and how

Three things, in this order:

1. **The set-level sweep's pass-off arm**: all six questions of the drafted set, run on lunacode
   with `RLM_DERIVED_PASS=0`. Three questions completed; the fourth was abandoned after 45
   minutes of repeated `cell_timeout`/`cell_extended` (the pathology `docs/20260926-2300-…`
   records), and the pass-on arm of the sweep was never started.
2. **A replay of the model's own queries**, which is what carries the result below. For every
   question, every `corpus_search` query the model actually issued (taken from the pass-off
   arm's trajectory, or from the recorded run when the arm has none) was run twice against the
   live index at `k=8`: the **ordinary** search — what the model is served with the pass off —
   and the pass's own `derived_only` search. Every cache-origin hit was then read back and its
   band computed against that question's content words. Nine queries, six questions.
3. **One targeted live A/B** (question 6, in `docs/20260926-2200-…`) and question 1's pass-on
   arm, which was stopped mid-run after its searches came back **identical** to its pass-off
   arm's (same query digest, same three addresses, same bands) with **zero** addresses added by
   the pass.

## The result: two questions of six can see a description at all

| question | queries the model issued | descriptions served by **ordinary ranking** | descriptions the **pass** adds |
|---|---|---|---|
| 1 | 2 | 1 per query, both `strong` | **0** |
| 2 | 2 | 0 | 0 |
| 3 | 2 | 0 | 0 |
| 4 | 1 | 0 | 0 |
| 5 | 1 | 0 | 0 |
| 6 | 1 | 0 | **3** (`strong` ×1, `partial` ×2) |

**Four of the six questions cannot see a description at all** — no cached description contains
the words the model actually searched with. Of the two that can:

- **Question 1 does not need the pass.** Its description ranks **first** in the ordinary search
  (`origins by rank: cache, file, file`), so the pass-off arm was already shown it, the pass's
  own search finds the same description and **discards it as a duplicate**, and the two arms
  serve byte-identical results at every `k` tried (3, 5, 8). The pass is inert here by
  construction.
- **Question 6 is the one case where the pass is the only route**, and the live A/B there
  (`docs/20260926-2200-…`) produced a **byte-identical answer** with and without those three
  descriptions, because they carried `weak`/`none` bands against that question while the file
  hits carried `partial`.

## Why this corrects the earlier picture

Two claims from earlier records are narrower than they read:

- **"Descriptions are indexed but invisible to search"** (`docs/20260925-0500-…`) was measured
  at `k=64` with queries built for the test, and it is true of *some* queries. Measured today:
  for a question whose words match a description, that description ranks **#1** — ahead of every
  file chunk — and is served with the pass disabled. Invisibility is a property of the query,
  not of the index.
- **The pass's dedupe is load-bearing and can swallow the whole contribution.** A description is
  stored under its document's own display address, so `handle_search` drops any derived hit whose
  address is already among the file hits. For question 1 the pass searches, finds the
  description, and appends nothing (`derived addresses already among the file hits: 1`,
  appended: `0`). An experiment that only counted the pass's *matches* and not its *appends*
  would have reported a description added that the model never saw — which is what this
  session's first replay did before the dedupe was checked.

## What it means for the Gate 3 decision

The budget (`docs/20260924-0100-gate-3-the-description-spend-decision.md`, 12–70 h for the value
set) buys coverage for a **minority of questions**: 2 of 6 here — and one of those two is served
by ranking anyway, so the pass changed nothing for it. In the single case where the pass was the
only route, the answer was **byte-identical** with and without the descriptions.

So the open question is not "are descriptions useful" in general. It is whether paying 12–70
hours is worth a change that no measurement in this session has yet detected. The experiment that
would find one is narrower than another sweep: **a question whose pass-only descriptions come back
`strong`/`partial` and whose answer then changes.** Question 6 is the only pass-only case measured
so far, and it changed nothing.

## Cost, measured in passing

The pass doubles the searches a helper call performs: every `corpus_search` runs the ordinary
search and then a second `derived_only` one. For question 6's query the ordinary search returned
in under a second and the derived-only search took **105.6 s**; for question 1's both were
instant. The cost is therefore not a constant overhead but a per-query latency that can be a
minute and a half on this index — which the run pays whether or not `k` has room for the result.

## What this does not establish

- **The set-level live sweep was not completed.** The pass-off arm finished three questions; the
  pass-on arm was never run for the set. The fire rate above is a **replay of the model's
  queries**, not a second live arm: it measures what the harness would serve, not what the model
  then does with it. Only question 6 has a live A/B, and only it can speak to answers.
- **Six questions from one drafted set** is the population. `2 of 6` is a property of *these*
  descriptions and *these* questions, not a rate for the corpus.
- **The bands are the harness's own labels** (`covers n/m` against the question's content words),
  not a judgement of relevance. What `strong` means to a reader is still the thing
  `docs/20260917-0915-…` left to the owner.
- **`n=1` per arm** wherever an arm exists, on a model known to be unstable across router cache
  states (`AGENTS.md` §4).
