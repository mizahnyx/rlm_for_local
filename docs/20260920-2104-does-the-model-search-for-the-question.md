# Does the model search for the question? (13 runs, measured)

**Created:** 2026-09-20 21:04
**Status:** point-in-time measurement. It exists to decide between two candidate causes of the
same symptom — weak evidence — and it does not decide *for* either: it splits them, with
numbers, and orders the fixes.
**Supersedes nothing.** It refines `docs/20260919-2215-the-second-question-set.md`, whose
"retrieval is the binding constraint" reading was already corrected by the owner's finding that
one run "totally drifted away from the original question".

## The method

For every recorded question run (13: six from the first set, six from the second, one
single re-run), take the question's content words using the harness's **own** definition
(`content_terms` — the same one the band labels use), then measure, for every corpus call the
model made, how many of those words appear in the query it actually sent
(`term_coverage`). A call sharing **no** question word is *off-question*.

Then cross-tabulate the **bands** of the addresses those calls served. That is the
discriminator:

| the model asked | it was served |
|---|---|
| **on-question** (shares ≥1 question word) | **strong=36, weak=44, unbanded=1** |
| **off-question** (shares none) | **none=40** |

Two facts fall straight out of that table, and neither is a story about one bad run:

1. **An off-question query never produced a usable hit.** All 40 addresses it returned were
   banded `none` — "the passage matches your words, not your question". So a drifting model
   cannot be rescued by better recall: there is nothing for recall to find when the question
   is not in the query.
2. **An on-question query always produced banded evidence** — and roughly half of it is
   `weak` (44 against 36 `strong`, and *zero* `partial`, the middle band never firing on these
   questions). `weak` is the band the citation guard refuses to treat as an answer, so even a
   model that asks perfectly still ends up with evidence it may not cite on about half its
   hits.

## Per run, by index

`on`/`off` are the counts of calls; the bands are the addresses those calls served.

| run | question words | calls | on | off | bands, on-question | bands, off-question |
|---|---|---|---|---|---|---|
| set 1 #1 | 9 | 3 | 1 | 2 | weak=8 | none=16 |
| set 1 #2 | 4 | 1 | 0 | 1 | — | — (coverage only, no search) |
| set 1 #3 | 7 | 2 | 1 | 1 | weak=8 | — |
| set 1 #4 | 3 | 2 | 1 | 1 | strong=8 | — |
| set 1 #5 | 3 | 2 | 1 | 1 | — | — |
| set 1 #6 | 3 | 2 | 1 | 1 | strong=8 | — |
| set 2 #1 | 9 | 4 | **0** | **4** | — | **none=24** |
| set 2 #2 | 4 | 4 | 3 | 1 | weak=20 | — |
| set 2 #3 | 7 | 4 | 2 | 2 | weak=8 | — |
| set 2 #4 | 3 | 2 | 1 | 1 | strong=8 | — |
| set 2 #5 | 3 | 1 | 1 | 0 | — | — (a path search, no bands) |
| set 2 #6 | 3 | 2 | 2 | 0 | strong=8, unbanded=1 | — |
| after repair #1 | 3 | 3 | 1 | 2 | strong=4 | — |

The catastrophic run is now explained without any appeal to the index: **set 2 #1 made four
calls and not one of them shared a content word with its question**, and the 24 addresses it
was served were all `none`. That is the same question whose *single* re-run, when it did
search the question's words, cited an exact answering address (`strong=4`).

## What this orders, and what it does not settle

- **Query construction first, and it is free.** It is the difference between `none` (nothing
  to cite, ever) and `strong`/`weak` (evidence, some of it citable). Four of thirteen runs had
  no on-question search at all; every run that had one got banded hits. Fixing this is a prompt
  change, not a purchase.
- **Retrieval quality (`OD2`) is not exonerated, and is now measurable rather than assumed.**
  44 of 80 on-question addresses were `weak`, and `partial` never fired at all. If a prompt
  that makes the model ask the question still leaves half its evidence `weak`, embeddings have
  a number to beat.
- **What it does not settle**: whether the `weak` half is the corpus's fault or the questions'.
  A question with nine content words spread across a document yields passages carrying some of
  them; the band calls that `weak`, and the guard refuses it. That is the harness being
  honest about thin evidence, not necessarily a recall failure — and the pages behind those
  addresses are what would say which.

## Reproducing it

`scripts/` is not the right home for this one — it reads trajectories rather than the corpus, so
any operator can run it anywhere the traces are. It reuses `content_terms` and `term_coverage`
deliberately: a measurement that invented its own notion of "content word" could disagree with
the band labels sitting next to it in the same table. Aggregates only, printed by index rather
than by question id, because a query the model sent is corpus-derived text.
