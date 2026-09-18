# The sampler spent 8 minutes asking SQLite for two numbers

**Created:** 2026-09-18 09:22
**Status:** point-in-time measurement and fix. Corrects a claim made earlier the same day,
in `docs/20260918-0750-sampling-passages-and-probing-with-real-questions.md` and in the
commit that landed the tool: *"a draw is a handful of indexed lookups however large the
index is."* The probes were that cheap. The draw was not.
**Supersedes nothing** — the tool's design is unchanged; one query was replaced.

## What happened

The first live run of `rlm corpus sample --n 5 --seed 1234` against the complete index
(29 015 791 chunks) returned five passages correctly — in **8m17.9s**. Nothing failed; the
command simply looked hung for eight minutes, which for an interactive tool is the same
thing as failing.

The unit tests were green throughout: a fixture index has a handful of rows, where every
possible query is instant. This is the second time in two days that a *tool* passed its
tests and behaved wrongly at corpus scale, and the reason is structural — 29M rows is not
a big version of a 6-row fixture, it is a different regime.

## The measurement, not the guess

`SELECT MIN(id), MAX(id) FROM text_chunks` — the statement that established the sampling
range:

```
seconds=272.505
plan: SCAN text_chunks USING COVERING INDEX text_chunks_source
```

A full scan of a covering index over all 29 015 791 rows. It was 8m17s of an 8m17s draw.

The two things I would have blamed first were both innocent:

| query | plan | measured |
|---|---|---|
| `SELECT id … WHERE id >= ? ORDER BY id LIMIT 1` (the probe) | `SEARCH text_chunks USING INTEGER PRIMARY KEY (rowid>?)` | **0.000 s**, walked past 0 rows |
| the same with `AND vendored = 0 AND origin = 'file'` | `SEARCH … USING INTEGER PRIMARY KEY (rowid>?)` | **0.000 s**, walked past 0 rows |

`MIN(id), MAX(id)` *together* is what SQLite declined to answer from the btree ends —
with a covering index available it scanned that index instead. `first[0]` and `last[0]`
of two single-ended seeks cost nothing.

## The fix, and its measurement

`ORDER BY id LIMIT 1` and `ORDER BY id DESC LIMIT 1`, each answered from a btree end
because `id` is the rowid:

| | before | after |
|---|---|---|
| wall clock, `--n 5 --seed 1234`, live index | **8m17.9s** | **10.8s** |
| blocks / bytes / address tokens | 5 / 6162 / 5 | 5 / 6162 / 5 |

The identical output is itself evidence: the same seed drew the same five passages
through a different query, which is what "reproducible" is supposed to mean.

## The guard, and why it is a query-shape test

A unit test cannot hold a 272-second cost — on a fixture index the two forms are
indistinguishable in time, and `EXPLAIN QUERY PLAN` on a tiny table reports a scan for
*both*, because scanning the table in rowid order *is* the natural order at that size. So
the guard holds the shape of the query the draw issues, via SQLite's trace callback: no
statement may contain `MIN(` or `MAX(`, and the bounds must be read with
`ORDER BY id DESC`. The mutation entry that restores the old query is red
(`TestRandomPassages::test_the_draw_never_asks_sqlite_for_both_extremes_at_once`).

The comment in the code carries the number (272.5 s) and the plan text, because the next
person to "simplify" two seeks into one `MIN/MAX` will see nothing wrong with it.

## Verification

- Fast suite: **1348 passed / 8 skipped / 12 deselected / 0 failed**.
- Mutation table: **192 guards / 0 problems** (one new entry, red as required).
- Live: the sampler on the complete index, 5 passages in **10.8s**, every passage carrying
  an address (`grep -c '#L'` = 5 for 5 blocks), read-only through the mount.

## What this says about the rest of the corpus work

Two generalisations worth carrying forward, both already in the project's rules and both
easier to believe with a number attached:

1. **A tool that is correct in the small may be unusable in the large**, and only a live
   run at scale says which. `rlm corpus sample` had 7 green unit tests and 5 green CLI
   tests while taking eight minutes to do its job.
2. **"Count a big table" is not the only expensive shape.** The recorded traps were
   `COUNT(*)` (972 s) and `SUM(size)` (42 s); this one is `MIN(x), MAX(x)` — a query that
   returns two integers, cannot be distinguished from a cheap one by reading it, and took
   272 s. Anything over `text_chunks` now gets its plan read before it ships.
