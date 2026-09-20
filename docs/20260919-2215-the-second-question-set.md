# The second question set: the encoding fix holds, and it is not what limits the harness

**Created:** 2026-09-19 22:15
**Status:** point-in-time measurement. The owner's six questions were re-run against the
repaired index with the *same* limits as their first run (soft 90 s / hard 1 200 s, six
turns), so each question has a before and an after.
**Supersedes nothing.** It qualifies its own predecessor:
`docs/20260919-1952-the-encoding-fix-at-the-model-level.md` reported a single re-run of
question 1 that cited an exact answering address, and this is what happens when the same
thing is done six times.

## The comparison

`cited_answering` is the count of cited addresses that were **served and whose match-quality
band answers the question** — the only column here that says "the answer used evidence that
fits". `bands` is what the searches served.

| question | first helper turn | searches | bands served | cited_answering | uncited refusals | forced | wall |
|---|---|---|---|---|---|---|---|
| 1 | 4 → 4 | 1 → **0** | none → none | 0 → 0 | 0 → 1 | F → T | 326 → 852 s |
| 2 | 4 → 4 | 1 → 1 | strong=8 → strong=8 | **0 → 1** | 2 → 1 | T → T | 1 206 → 1 285 s |
| 3 | 3 → 3 | 1 → 1 | strong=8 → strong=8 | 2 → 2 | 2 → 1 | T → T | 893 → 1 321 s |
| 4 | 3 → 3 | 3 → 3 | none=16, weak=8 → none=24 | 0 → 0 | 0 → 0 | T → **F** | 1 938 → 1 493 s |
| 5 | **6 → 4** | 0 → **2** | none → **weak=20** | 0 → 0 | 0 → 0 | T → T | 727 → 1 300 s |
| 6 | 3 → 3 | 1 → 1 | weak=8 → weak=8 | 0 → 0 | 0 → 0 | T → T | 2 618 → 1 489 s |

Totals: **7 707 s → 7 739 s** (2h08m → 2h09m, unchanged). Questions that produced at least
one *answering* citation: **2 of 6 before, 2 of 6 after** — question 2 gained one, question 3
held its two, and question 1 lost nothing it had (it never had one) but changed strategy.

## What the fix did, and what it did not

**Did**: question 2 gained an exact answering citation (`cited_answering=0 → 1`), and
question 5's searches went from serving *nothing with a band* to `weak=20` — the words are
now in the index. In the earlier single re-run of question 1, the one that actually searched,
the accented word was found and cited exactly. The index-side measurement is unambiguous:
46 725 sources re-indexed, token counts up everywhere, a 17-file sample all findable.

**Did not**: move the set as a whole. Two of six questions produced an answering citation
before the repair and two do after. The honest reason is visible in the columns above, and
none of them is the encoding:

1. **Strategy variance dominates a single comparison.** Question 1 ran three times across
   this work: it searched (`corpus_coverage` + `corpus_search`) and reported coverage; it
   searched and cited exactly; and in this set it called **`corpus_find` only** — the *path*
   search — and never searched the words at all (`searches=0`, `bands=none`). Three runs,
   three strategies, same question, same model, temperature 0. One question, two runs, is
   not a measurement, and the predecessor record's n=1 caveat is now demonstrated rather
   than asserted.
2. **Retrieval, not the budget or the encoding, is the limiter.** Four of six questions were
   served only `none` or `weak` bands: the index had nothing that answered them, and the
   harness cannot cite what it was not given. Question 4 is the extreme — 24 addresses
   served, **all** of them banded `none`, and three searches used to find them.
3. **The orientation cost is stable and untouched**: first helper turn 4, 4, 3, 3, 4, 3 —
   two to three turns of a six-turn budget spent before the corpus is asked anything. It is
   now on every summary line, so the next set measures it without a script.
4. **Five of six answers still came from forced finalization**, which bypasses the citation
   guard by design. Question 4 is the exception: it submitted voluntarily, uncited, naming
   its coverage — the escape hatch's second live use.

## Operational notes worth keeping

- Question 5's first helper call moved from turn 6 to turn 4, the only orientation change in
  the set.
- Question 1's run shows three mechanisms behaving as designed in one trajectory: a cell
  that did not compile was retried without costing a turn (`syntax_retry`, so `turn_start=7`
  against `turns_used=6`), one cell passed the 90 s soft limit with activity and was
  **extended** rather than killed, and an uncited answer was refused once.
- The set ran with the code as of `1603c59`, so its stdout lines lack `first_helper_turn`;
  the pages were rendered afterwards with `443e848` and carry it.

## Where to read it

`~/rlm-derived/traces/questions-set2/index.md` — six pages, one per question, each with the
transcript, the citation audit and the passage behind every cited or served address. The
first set's pages are in `~/rlm-derived/traces/questions/`, and the single after-repair run
in `~/rlm-derived/traces/questions-after-repair/`.

## What this says to do next

The two levers the columns point at, in order:

1. **Retrieval quality** — `OD2` (embeddings) was recorded as "becomes load-bearing for
   corpus retrieval" the moment real questions showed weak recall. Four of six questions
   served nothing but `none`/`weak`. That is now the measured bottleneck, and it is an owner
   call.
2. **The orientation cost** — two to three turns per run, stable across both sets, and
   fixable at the prompt (make the first move a corpus call) rather than at the budget. It is
   cheap to try on **one** question now that the metric is rendered.
