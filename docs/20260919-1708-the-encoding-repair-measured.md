# The encoding repair, run on the live corpus and measured

**Created:** 2026-09-19 17:08
**Status:** point-in-time measurement. RO19's fix and its repair pass are landed
(`b76ada9`, `c473dff`); this is what happened when the repair was run against the real
index and what changed afterwards.
**Supersedes nothing.** The finding is the owner's
(`docs/20260919-1318-owner-findings-first-question-set.md` §1); the fix's design is in the
commit that landed it, and one earlier number in this session is corrected below.

## The repair, as it ran

```
rlm corpus reindex-encodings --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite

considered=46735 re-indexed=46725 already-current=3 empty=0 failed=7
failures by cause: UnicodeEncodeError=7
real 33m17s
```

Three things in that output are worth stating plainly:

- **46 725 sources were re-indexed**, which is 99.98% of the population — the 46 735
  files the sniffer recorded as cp1252 or latin-1 (46 107 + 628).
- **7 failed, all `UnicodeEncodeError`**: a path whose *name* is not UTF-8 cannot be
  stored as SQLite TEXT. That is RO20, recorded separately, and it is why the pass had
  to be made fault-tolerant first — its first live run died after six seconds on the
  first of them.
- **33m17s, not the ~13 minutes the help text predicted** (≈23 files/s rather than 60).
  The estimate was mine and it was 2.5× optimistic; the correction is in the operator
  guide, because the next person to run this needs the real number.

The absolute `candidates=46,735` is itself a cross-check: two independent paths — the
classification table's encoding counts, and this query — agree exactly.

## What changed, measured

**Whole-index token counts, exact `COUNT(*)` over the FTS postings**, before and after
(the only before/after pair that is comparable, since the old rows are gone):

| word | chunks before | chunks after | change |
|---|---|---|---|
| `también` | 14 201 | 18 234 | +28% |
| `información` | 23 785 | 36 914 | +55% |
| `día` | 21 179 | 25 299 | +19% |
| `canción` | 1 752 | 2 467 | +41% |
| `comité` | 166 | 203 | +22% |
| `público` | 2 784 | 4 936 | +77% |
| `después` | 9 537 | 25 285 | +165% |

These are *chunks*, not files, and the before counts were never zero: most accented words
were already findable in the UTF-8 majority. What the repair added is the whole legacy
minority.

**Per-file, exact presence.** For a deterministic 17-file sample of the population, take
an accented word out of the file itself and ask whether an indexed chunk of *that source*
matches it:

```
candidates=46,735 sampled=17 word_present=17 encoding_recorded=17
no_accented_word=3 unreadable=0
```

17 of 17 — and each of those sources now records the codec it was decoded with, which is
what makes the read path return the same text the search matched.

## A number I reported earlier in this session that does not mean what I said

Before the repair I ran the same per-file check and reported **0 of 17**. That number is
real but it answers a different question: the check asked whether the file appeared in the
**top 50 hits**, and it was written before I had any idea how many chunks a common Spanish
word matches. After the repair the same check said 2 of 17 — not because only two files
were fixed, but because `información` now matches 36 914 chunks, so a given file is far
below rank 50. The check was a *ranking* test standing in for a *presence* test, and the
honest evidence for the repair is the exact presence result above plus the token counts,
not the 0 → 2 pair. It is recorded here rather than quietly dropped, because "0 of 17
before, 17 of 17 after" would have been a much better story and it would have been wrong.

## What is still open

- **RO20 — 7 sources (of the 98 non-UTF-8 paths) remain unindexed and unsearchable.** The
  repair reported them instead of dying on them; fixing them needs a display form SQLite
  accepts that still resolves to the same raw bytes.
- **3 sources were already current**, which needs no action but is unexplained: if a
  source is both a text file and extracted from a container, its derived chunks would
  already carry UTF-8, and that is the likely reason — unverified.
- **No model run has used the repaired index yet.** The next question set is what would
  show whether question 1 of the first set now gets an answer instead of a coverage
  report; the harness-side evidence (unit tests + these measurements) says the tokens are
  there, but a question answered from them has not been observed.
