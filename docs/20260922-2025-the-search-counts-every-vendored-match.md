# The search counts every vendored match, and that is a candidate for the 20-minute call

**2026-09-22.** `docs/20260922-2010-correction-the-20-minute-search.md` recorded that a cell in
the first prose-derived question ran the full hard limit — 1 200 s — inside a single
`corpus_search`. This is a code audit of that path, done while the set was still running, and
it names one candidate mechanism. **It is inferred, not measured**, and the measurement that
would confirm it is written down here rather than skipped.

## The audit

`TextIndex.search` does two things per call:

```python
rows = self._conn.execute(sql, (*params, max(1, int(k)))).fetchall()   # bm25 + LIMIT k
...
if not include_vendored:
    row = self._conn.execute(
        "SELECT COUNT(*) FROM text_fts JOIN text_chunks c"
        " ON c.id = text_fts.rowid WHERE text_fts MATCH ? AND c.vendored = 1",
        (expression,),
    ).fetchone()
```

The first is bounded and cheap: `ORDER BY bm25(text_fts) LIMIT 8` reads only what it returns.
The second is **unbounded in the size of the match set** — it counts every vendored row the
expression matches, joined to `text_chunks`, and it exists to print one line: `[N further
matches hidden by the vendored filter; search again with include_vendored=True]`.

Two properties make this the obvious suspect:

1. **Its cost scales with how common the question's words are**, not with `k`. A prose question
   drawn from real text uses ordinary words; ordinary words match large fractions of a 29M-chunk
   index. The count then walks the whole posting list to produce a number nobody needs to act on.
2. **This project has already measured that shape.** `COUNT(*) FROM text_chunks` took **972 s**
   where a search returning rows took **0.2 s** (`docs/20260915-2305-…`). That is why coverage
   became a *published snapshot*: CL6 was "a search counted the index it was searching". **This
   is CL6 again, with a different subject** — the vendored count was left on the search path,
   and it is more exposed than the coverage count ever was, because it runs once per modelled
   search (eight times in question 1) and its cost varies with the question's wording.

## What is measured and what is not

**Measured**: the cell's `last_helper=corpus_search`, `limit=hard budget=1200s activity=5
block=1`; eight searches served in that question; three further cells extended to 84 s, 461 s
and 357 s, each with `last_helper=corpus_search` or `corpus_find`.

**Not measured**: that the vendored count is where those minutes went. The audit shows it is the
only unbounded work on that path; it does not show it is the expensive part. Anything from a
cold page cache to a pathological FTS expression would also be consistent with the traces so
far.

**The measurement that settles it** (to run when the box is idle, so it neither contends with a
probe nor distorts a set's timings):

```bash
# The same query shape the search runs, timed on a term from a real question, and again with
# the count clause removed. The difference is the answer.
EXPLAIN QUERY PLAN SELECT COUNT(*) FROM text_fts JOIN text_chunks c
  ON c.id = text_fts.rowid WHERE text_fts MATCH ? AND c.vendored = 1;
# then, on the live index, with the read counters sampled before and after (as RO21 did).
```

## Why this is not being fixed now

Changing search behaviour mid-set would make questions 1 and 2 incomparable with 3–6 — the
harness would have changed underneath the measurement. The finding is worth more as a
*reason* the set's wall times are what they are, and the fix belongs after the set, with the
owner's call on which of these it takes:

- **bound the count** — count up to a cap and say `at least N` (honest, and the line is
  informational anyway);
- **drop it and say unknown** — the `AGENTS.md` §1.8 corollary: a number that cannot be
  afforded on the request path must be reported as unknown rather than computed there;
- **make it opt-in** — compute it only when an operator asks for it, the same way coverage
  moved behind `rlm corpus counters --refresh`.

The third is the one I would pick: it is the shape the project already chose once for exactly
this defect, and it keeps the information available without charging the model for it.
