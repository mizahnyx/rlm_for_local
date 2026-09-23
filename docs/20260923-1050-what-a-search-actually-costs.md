# What a search actually costs

**2026-09-23.** `docs/20260922-2025-the-search-counts-every-vendored-match.md` named the
vendored count as the candidate for a cell that spent the full 1 200 s hard limit inside
`corpus_search`. This measures the search path on the queries that live runs actually issued —
taken from the `corpus_served` events, so they are the real ones — and the audit turns out to be
half right. The count is real and now bounded; a second cost the audit did not name is larger.

## The measurement

Nine distinct queries, timed in three parts: the total match count, the vendored count the
harness runs, and `search(k=8)` as the model experiences it.

| words | matches | total count | vendored count | `search(k=8)` |
|---|---|---|---|---|
| 2 | 22 490 | 67.3 s | 0.1 s | 15.0 s |
| 2 | 1 136 | 7.6 s | 0.0 s | 3.2 s |
| **1** | **704 801** | 371.1 s | **202.0 s** | **89.6 s** |
| 1 | 58 997 | 65.4 s | 0.1 s | 11.9 s |
| 1 | 254 292 | 417.2 s | 32.1 s | 78.7 s |
| 1 | 89 692 | 136.6 s | 0.8 s | 4.6 s |
| 1 | 273 632 | 251.1 s | 7.6 s | 16.7 s |
| 1 | 2 035 | 3.0 s | 0.0 s | 0.5 s |
| 2 | 84 995 | 171.1 s | 3.4 s | 12.9 s |

## What it says

**1. The model searches short phrases, never the question.** All nine queries are one or two
words. That matters because it is the only reason the harness's own expression builder is not a
disaster: handed a *whole question*, `_match_expression` ANDs its content words — nine of them
here — and that expression **matches nothing and scans the FTS index for 35 s**
(`SCAN text_fts VIRTUAL TABLE INDEX 0:M1`, 0 hits). A first version of this measurement did
exactly that and reported zeros for every question, which is how the fact surfaced.

**2. Both halves of the harness's search path scale with the match set, and the ranking is the
larger.** `search(k=8)` ranged **0.5 s to 89.6 s**; the vendored count ranged **0.0 s to
202 s**. An `ORDER BY bm25(text_fts) LIMIT 8` still has to score *every* matching row, so a
common word costs a minute before the model sees a hit — and RO21's warm/cold ratio (0.02 s
against 101.24 s) says the cold case is worse than anything measured here.

**3. The vendored count's cost is wildly uneven at similar sizes** (67 647 matches → 0.8 s;
99 194 → 32.1 s; 197 851 → 202 s). That is page-cache behaviour, and it is the reason bounding
it by *work* rather than by *rows* is the right fix.

## The fix, and its guard

The count now reads `SELECT COUNT(*) FROM (SELECT 1 … LIMIT cap+1)`, so it costs the same
whatever the question's words are, and `hidden_vendored_at_least` distinguishes "exactly the
cap" from "more" — reported through one helper (`vendored_hidden_note`) so the two call sites
cannot drift into one saying `412` and the other `at least 200`.

**The guard is trace-based, and the mutation table is why.** The Python clamp (`hidden =
VENDORED_COUNT_CAP`) makes the *number* look capped even when the query counts every row, so the
first version of the test passed with the bound removed — the table called it VACUOUS. When the
cost is invisible in the answer, assert on what was executed: the test now requires the counting
statement to carry a `LIMIT`, the same shape the container-member guard uses for the same
reason. 4 tests, 1 mutation entry red.

## Still open

- **The ranking cost (up to 89.6 s warm) is untouched**, and it is the bigger half. Scoring
  704 801 matches to return 8 cannot be made cheap by a Python change; it needs a ranking
  decision — refuse or advise very common terms, require two content words, or prefilter before
  bm25 — and that changes what a search returns, so it is the owner's call.
- **The expression-variant measurement is not done**: AND-of-content-words against an OR of the
  same words, comparing the bands each serves. It was written and not run, because the
  query-cost measurement came first and answers a prior question (what the harness pays at all).
- Caveat on every number above: my own counting queries read the same b-trees first, so the
  `search(k=8)` figures are **warm-cache** figures. The real cost is higher.
