# The prompt experiment: one question, and the obstacle that moved

**Created:** 2026-09-20 22:15
**Status:** point-in-time measurement. Two prompt instructions were added
(`aa1dc55`) and tried on **one** question, chosen because it is the one whose failures are
documented from three angles: it produced a coverage-report answer before the encoding
repair, a `corpus_find`-only run with no citation after it, and an exact answering citation
in its single re-run.
**Supersedes nothing.** It tests the two levers
`docs/20260920-2104-does-the-model-search-for-the-question.md` ordered: ask the corpus early,
and ask it the question's own words.

## The four runs of this question, side by side

| run | prompt | index | first helper turn | calls | on-question | bands served | cited_answering | answer | wall |
|---|---|---|---|---|---|---|---|---|---|
| set 1 | before | pre-repair | 4 | 2 | 1 | none | 0 | 155 chars, names coverage | 326 s |
| set 2 | old | repaired | 4 | 1 (`find`) | 0 | none | 0 | 673 chars, uncited refusal | 852 s |
| single re-run | old | repaired | 4 | 3 | 1 | strong=4 | **1 (exact)** | 419 chars | 1 577 s |
| **this run** | **new** | repaired | **2** | 1 | **1** | **strong=4** | **1 (exact)** | **857 chars** | 1 753 s |

## What moved

1. **The orientation instruction worked, and it is measurable.** First helper turn **4 → 2**
   — the model reached the corpus two turns earlier, which is the largest single-run change in
   orientation recorded so far (the metric has been 3–4 across thirteen runs). That is two
   more turns of a six-turn budget spent on the question.
2. **The search was on-question and returned only `strong` hits** — four of them, no
   off-question calls at all, against a run of the same question that made four calls and
   shared not one word with the question.
3. **The answer cited an exact answering address** and is the longest of the four runs.
4. **The `limit=` class of failure disappeared.** Counting exception *types* over the three
   repaired-index runs of this question:

   | run | cells with stderr | types |
   |---|---|---|
   | set 2 (old prompt) | 3 | `SyntaxError=1`, **`TypeError=2`** |
   | single re-run (old prompt) | 1 | **`TypeError=1`** |
   | **this run (new prompt)** | 4 | **`IndexError=3`, `ValueError=1`** |

   The two `TypeError`s in set 2 are the owner's finding, confirmed numerically: the two calls
   with `limit=`. With the prompt stating the parameter names and the helper answering a
   mistyped one, **no `TypeError` occurred at all**.

## What is *not* established, and one thing that got worse

**A new error class appeared where the old one vanished**: `IndexError=3` and `ValueError=1`
in this run, against none before. The shape is familiar from every other fix in this project —
remove one obstacle and the model walks into the next one — but the cause is **not** established
here, and it will not be guessed at. `IndexError` is what indexing an empty list gives, and this
run is the first where the model searched early and had four strong hits to work with; both of
those are consistent with several different mistakes, and the transcript is the only thing that
tells them apart.

The page is rendered for that reading: `~/rlm-derived/traces/questions-prompt-a/index.md`.

Everything else is n = 1. One question, one run, one model, one host — a promising result about
*instructions*, not a measurement of effect size, and this project has already recorded that
this model's behaviour varies between runs of the same prompt.

## What it cost, and what it orders

1 753 s (≈29 minutes) for one question, the longest of the four. The next step is either the
whole set with the new prompt (~2–3 hours, and the only way to see whether the orientation gain
holds across questions and whether the new `IndexError` class is general or particular), or
reading this page first to find out what the `IndexError`s are — which is cheap and would say
whether the next set is worth running before or after another fix.
