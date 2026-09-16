# A search cannot count the index it is searching

**Created:** 2026-09-15 23:05
**Status:** closed — the defect is fixed and covered; three follow-ups are named
**Supersedes nothing.** Follows `docs/20260915-0655-corpus-citation-compliance-measured.md`
and `docs/20260915-1023-corpus-citation-guard-live.md`; it is the reason the
escape-hatch test in that sequence has still not happened.

## The run that produced no answer

An unanswerable question — *"What does the corpus say about the Zxqvarn Protocol
for orbital tether maintenance, and who ratified it?"* — was put to the corpus to
exercise the citation guard's escape hatch. It ran for **7 250 s (two hours)**,
used 5 of its 8 turns, and ended with `forced=True` and the final answer
`(No answer produced — forced finalization failed)`. No `corpus_uncited` refusal
ever fired, because the model never submitted anything: five cells, **four of them
failing**, then the error budget, then a forced-finalization call that also failed.

## Mechanism, measured rather than guessed

The four failing stderr messages contain **no exception tokens at all** — no
`NameError`, no `Traceback` — so they are harness messages, not model mistakes.
Fingerprinted against this checkout's string literals:

| stderr | length | identity |
|---|---|---|
| ×2 | 42 | the cell-timeout message (`Error: cell exceeded the 120.0s time limit.`) — the `laptop` profile's `cell_timeout` is exactly 120 s |
| ×2 | 162 | `Error: the REPL worker did not respond and was restarted.` |

So each failing cell was: the model calls a corpus helper → the **parent** takes
too long to answer → the 120 s cell limit fires → **the worker is killed and
restarted**. That is a timeout, and the parent was doing the model's bidding.

Per-query timing on the live index (25 025 095 chunks, 12+ GiB, while a mining
window ran), each with a hard 45 s cap:

| query | time |
|---|---|
| `TextIndex.search` (FTS retrieval) | **0.2 s** |
| `COUNT(*) FROM text_chunks` | **972 s** |
| `COUNT(*), COUNT(DISTINCT source), SUM(…) FROM text_chunks` (`stats()`) | aborted at 45 s |
| `COUNT(DISTINCT source) FROM text_chunks` | aborted at 45 s |
| `COUNT(*) FROM classification WHERE kind='text'` | aborted at 45 s |
| `COUNT(*) FROM mine_queue WHERE task='index_text' AND state='done'` | aborted at 45 s |
| `COUNT(*) FROM mine_queue WHERE note='needs_ocr'` (517 rows) | 25.9 s |

The asymmetry is the whole finding: **retrieval is fast and counting is not**, by
four orders of magnitude on the same code path. `corpus_search` and
`corpus_coverage` both called `TextIndex.coverage()`, which runs those counts; the
model called them seven times between search and coverage.

**A correction worth recording.** My first plan was to swap
`COUNT(DISTINCT source) FROM text_chunks` for the queue's `index_text done` count,
on the assumption that the claim index made it a seek. It is not: it aborted at
45 s too. Measuring the pieces before implementing is the only reason that
assumption is not in the code.

## The fix: publish, don't count

The invariant, now written where the code enforces it: **the search path never
counts a big table.**

- `TextIndex.publish_coverage()` stores a *computed* coverage dict in the index's
  `meta` table, with the time it was computed.
- `TextIndex.published_coverage()` reads it. A search and `corpus_coverage()` use
  it, and when nothing has been published they say **unknown** — never zero,
  because "0 sources indexed" is a confident wrong answer about how much of the
  corpus was searched, which this project ranks below saying nothing.
- An old snapshot says so: `coverage_note` appends `(snapshot N min ago)` once a
  snapshot is more than a minute old. Staleness is the price of the design, and it
  is charged out loud.
- `run_queue` publishes at the end of every mining window — the process that is
  already hours long pays the ~16 minutes once, for the index that window produced.
- `rlm corpus counters` shows the snapshot; `rlm corpus counters --refresh` is the
  deliberate, slow way to compute a new one when the index is idle.

The numbers are always *computed*, never *accumulated*, which is the deliberate
choice: an accumulated counter can drift away from the index it describes, and a
wrong number is worse than an old one.

## Verification

- 6 new tests on the snapshot (round-trip, absent-is-None-not-zero, a corrupt
  snapshot reading as None, survival across a reopen, the age suffix, and
  back-compat when there is no timestamp).
- 4 new bridge tests, including the guard that matters: a search that finds
  nothing **executes no counting query** against `text_chunks`, `classification`
  or `mine_queue`, proven with `sqlite3`'s trace callback and a documented
  exclusion for the FTS `MATCH` count (which is bounded by the match set).
- 1 new miner test (a finished window publishes), 3 new CLI tests for the new verb.
- 3 new mutation entries, each observed red: the search falling back to
  `coverage()`, the snapshot becoming unreadable, and the window not publishing.
- Full fast suite, mutation table and doc lint re-run for the commit.

## What is still not good enough

- **The refresh is still slow** (~16 minutes): that is the trade, but it means the
  numbers are as fresh as the last window or the last deliberate refresh.
- **`mine status` still counts**, so it is an operator-only command that can take
  minutes on the live index. It is not on a cell's path, so it is not fixed here.
- **Two candidate follow-ups, recorded rather than done**: run the index in WAL
  mode so a long read cannot block the writer and vice versa, and keep the search
  path's connection read-only (`ensure()` takes a write lock on every search today,
  which is a second, independent reason a search can stall while mining).
- **The forced-finalization failure is silent.** The run's `end` event carries no
  error field, so a two-hour run ends with the string `(No answer produced — forced
  finalization failed)` and no logged reason. That is a "cannot see the truth" gap
  in our own code, and it should record the exception it swallowed.
- **The escape hatch is still unverified**, because this defect stopped the run
  before the model ever submitted. The sequence resumes there: re-run the
  unanswerable question against the fixed search path.

## Corroborating state

The mining chain behaved exactly as designed while all of this happened: window 2
ended on its 12-hour budget at 23:02 with `rc=0`, 2 468 954 of 2 882 822 text files
indexed (85.6%), and the supervisor started window 3 for the remaining ~413 868
files (~1.9 h at the measured ~60 items/s).
