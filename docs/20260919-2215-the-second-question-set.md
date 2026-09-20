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

## The owner's reading of the pages (added 2026-09-20 00:20)

Four findings, quoted as given:

1. *"Question 1 that was answered correctly on isolation once, became unanswered in the last
   run due to the model stumbling with the `corpus_search` non existing `limit` parameter
   twice and trying to loop over the characters of the string returned by `corpus_find`."*
   Both halves are in the trajectory: question 1 logged **exactly two `stderr` guardrails**,
   which is what a call with a keyword the wrapper does not take produces
   (`_harness_corpus_search(query, k=8, …)` raises Python's own `TypeError`), and its only
   helper call was `corpus_find` — so no word search happened at all. **Two harness defects,
   both mine to fix**: the wrappers raise a traceback where a teaching message belongs, and
   `corpus_find` returns a string where its sibling `corpus_search` returns a list, which is
   the same shape bug that record already documents for search.
2. *"Question 2 is right, demonstrating correct reasoning and proper citation, despite the
   `corpus_search` parameter stumbling."*
3. *"Question 3 has the right answer, and it demonstrated dealing with Python syntax errors
   with extra turns until getting the expression right."* The trajectory shows the mechanism
   working as designed: `syntax_retry`, which costs neither a turn nor the error budget
   (hence `turn_start` exceeding `turns_used`).
4. *"In Question 4, the model totally drifted away from the original question, searching for
   unrelated words in the corpus, thus getting no valid answer."*

**Finding 4 corrects a reading in this record.** The section above says retrieval quality is
the binding constraint (four of six questions served only `none`/`weak` bands). For question 4
the cause is upstream of the index: *the model chose the wrong words*. Both are true — a wrong
query produces a weak band — but they are different problems with different fixes, and only
the owner could see it, because it required reading what the model asked for rather than what
the search returned. So `OD2` (embeddings) is a live decision, not yet a verdict, and the model's
query construction deserves its own measure before anything is built for recall.

The two harness defects finding 1 names — the `corpus_search` keyword that raises a traceback
instead of teaching, and `corpus_find` returning a string where its sibling returns a list —
are queued as the next fixes. Both are cheap, both are testable without a model, and both
cost turns in every run that hits them.

## What this says to do next

The two levers the columns point at, in order:

1. **Retrieval quality** — `OD2` (embeddings) was recorded as "becomes load-bearing for
   corpus retrieval" the moment real questions showed weak recall. Four of six questions
   served nothing but `none`/`weak`. That is now the measured bottleneck, and it is an owner
   call.
2. **The orientation cost** — two to three turns per run, stable across both sets, and
   fixable at the prompt (make the first move a corpus call) rather than at the budget. It is
   cheap to try on **one** question now that the metric is rendered.
