# What a corpus call costs, and why seconds and engine-steps are both the wrong unit

**Created:** 2026-09-17 15:30
**Status:** point-in-time. Measured on the real index on `lunacode`, warm and cold,
with a live run's evidence alongside.
**Supersedes nothing.** Answers the owner's finding of 2026-09-17 (*"60 seconds timeout
for `corpus_count` is really too little … a methodology must be devised"*) and their
hunch (*"measure the cost in operations, not in fixed time"*) — the hunch is right in
direction and needs one correction the measurements forced.

## 1. The live run: the budget killed searches, not counts

Run A (`live-ask-band2`, 2026-09-17): the graded question, `Qwen3.5-4B-Abliterated`,
`--profile laptop --max-turns 8`, complete index, defaults (so `cell_timeout = 60 s`).

| fact | value |
|---|---|
| outcome | forced finalization, 1 563.9 s (~26 min) |
| **`cell_timeout` events** | **3 — and all three on `corpus_search`** |
| `syntax_retry` / `syntax_giveup` | 0 / 0 |
| addresses served | 17, all band `none` or `weak` — none answering |
| answer's citations | none at all (`served_not_cited=17`) |
| audit | `complete` (the first run with the served-address instrumentation) |

Two things follow immediately.

**The verb that costs the budget is the search, not the count.** On this corpus a
search reads the eight hit snippets off an external drive; that is the cost, and under
any load it crosses 60 s. The owner's finding was right that a harness limit was being
reported as a model failure; the helper it lands on is `corpus_search`.

**The nudge worked.** Both timeouts produced a message to the model ("that cell was
stopped after 60s"), so the fix from
`docs/20260917-1200-two-failures-that-were-not-the-models.md` is exercised live.

**And the run found a bug of mine.** It reported `turns=9/8`. The explicit turn counter
of the syntax-retry change sits at `max_turns` after a natural exhaustion and one lower
after an early break, so the forced path's `turn + 1` was wrong in one case and right
by accident in the other. `turns_used` is now counted where a turn is *entered*
(`turns_started = max(turns_started, display_turn)`, idempotent under a retry that
re-runs the same turn). Two tests and one mutation entry; the instrument, not the model
— which is exactly what a live run is for.

## 2. What the count aggregations actually cost

`corpus_count` runs four aggregations over the path index (4 972 609 rows). Timed
separately on the real index, with SQLite's progress handler counting engine steps:

| aggregation | cold | warm | engine steps | bytes read (warm) |
|---|---|---|---|---|
| `COUNT(*)` | **231.46 s** | **0.69 s** | ~0M | 5.3 MB |
| `COUNT(*) WHERE kind='file'` | 42.33 s | — | 19.2M | — |
| `SUM(size) WHERE kind='file'` | 29.21 s | **42.43 s** | 23.5M | **3 429 MB** |
| `GROUP BY kind` | 4.35 s | — | 54.7M | — |
| `COUNT(*) FROM text_chunks` (29M rows) | **637.62 s** | — | **~0M** | — |

Three findings, and the third is the one that changes the design.

1. **The byte total is the expensive half**, as predicted: `SUM(size)` touches every
   row's page — **3.43 GB of reads** — while `COUNT(*)` walks an index. "How many
   files?" is cheap; "how many bytes?" is not.
2. **Cache state dominates by more than two orders of magnitude**: the same `COUNT(*)`
   is 231 s cold and 0.69 s warm. Any budget denominated in *seconds* is therefore
   partly a measurement of whether the page cache happened to hold the table.
3. **An engine-step budget would not have bounded the worst queries at all.** The
   231 s `COUNT(*)` and the 637 s `COUNT(*)` on the chunk table both reported **~0M
   engine steps**: the time was spent waiting for pages. The progress handler fires
   between VM instructions, and a query that is waiting on I/O is not executing any.
   So the operation budget is the wrong instrument for precisely the verbs that hurt —
   it would guard the cheap ones and sleep through the expensive ones.

## 3. What a search costs

| operation | seconds | bytes read |
|---|---|---|
| `corpus_search k=8` (warm) | 14.43 s | 10.4 MB |
| `corpus_search k=4` (warm) | 0.02 s | 0.0 MB |
| `corpus_search` (earlier probe, cold-ish) | 43.14 s | — |

Again the same shape: the first touch of the FTS pages and the eight snippets costs
everything, and a repeat against the same pages costs nothing. `k=4` halves the snippet
reads, and the numbers here cannot separate "k=4 is cheaper" from "the pages were
already resident" — that needs a cold-vs-cold comparison, which the earlier probe
(43 s) and this one (14 s) suggest is the dominant variable.

## 4. The unit that survives contact with the data

**Storage bytes read.** It is a property of the request given the cache state: 3.43 GB
for the byte total, 10 MB for a search, 0 MB for a repeat. It is host-independent in the
sense that matters — the same call asks for the same bytes — and it makes the failure
message actionable ("this would read 3.4 GB; the published snapshot answers the same
question from one row").

Two consequences, both honest:

- **A bytes budget cannot be enforced by the progress handler.** For a pure-I/O query
  there are no steps to count, and the first measurement run proves it. The mechanism
  is a **watchdog thread** that polls the process's own I/O counters (`/proc/self/io`,
  `read_bytes`) and calls SQLite's `interrupt()` when the budget is exceeded — with the
  wall clock kept only as the outer guard for something genuinely stuck.
- **Bytes read is a cache-state measurement, which folds this into the ledger.**
  `docs/20260917-1510-cache-freshness-ledger-design.md` answers "is this cache current,
  and what would make it current?". A stale or absent cache is *exactly* what inflates
  bytes read: the 231 s cold count is the same query over an un-warmed cache. So the
  two owner answers of 2026-09-17 — route archive members through the extraction cache
  and mark what needs mining; report whether a cached count is stale — and the budget's
  unit are one design, not three.

## 5. What is still unmeasured

- **The sampled estimate of archive-member-shaped chunks** was still running when this
  was written; the exact count is unobtainable by scanning (a full `LIKE '%!%'` over
  29M rows was **aborted at 80M engine steps after 732 s**, which is itself the
  strongest argument yet that the read path must not go looking by scan).
- **A cold-vs-cold search at k=8 against k=4.** The numbers above are confounded by
  page-cache state, and they are the input to "should the default read fewer hits".
- Whether the 3.43 GB `SUM(size)` can be avoided entirely by maintaining a running
  total on write (the path index is the only writer, so a counter added at write time
  would make the byte total free) — a small design question for the ledger, not a
  measurement.
