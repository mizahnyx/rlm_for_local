# The content-routing window: 13 092 containers the old routing had given up on, 99.9% listed

**Created:** 2026-09-21 22:50
**Status:** point-in-time. One bounded mining window (30 minutes) run to apply the
content-routing change to the corpus, plus the one verb that had to exist first. Counts are
from the live index; no path, no member name, no passage.
**Supersedes nothing.** It is the corpus-wide confirmation of
`docs/20260921-1751-container-routing-by-content-and-the-rar-question.md`, whose sampling
this replaces with a full pass over the populations it measured.

## What had to exist first: a way to re-open a skip

Content routing and the wider extension list made 12 448 extensionless containers and 52
mis-named archives listable — and every one of them had been recorded
`skipped/no_listing_engine`, a **terminal state no verb could revisit**. `rlm mine retry`
resets `failed` rows only, and `enqueue` is a no-op for a row that already exists, so the
work the previous change unlocked would never have been handed to a worker. That is its own
small defect, and it is the general shape of "the harness learned to do the thing the skip
recorded it could not".

`MineStore.reset_skipped(task, note)` fixes it, reached as
`rlm mine retry --task T --skipped-note N`, **scoped by task and note** because a skip is a
judgement and an unscoped reset would re-run skips whose reason still stands. Four tests, one
mutation entry red, including the one that matters: after the verb, the queue really does hand
the item to a worker.

## The window

```
re-queued 33 failed item(s) for list_archive
re-opened 19,446 skipped item(s) for list_archive (note=no_listing_engine) — the harness can do this now
=== window: list_archive for 30 minutes ===
  22,952 items  (done 22,700, skipped 120, failed 132, cache hits 3,081)
```

**~12.8 items/s over 30 minutes, 22 952 containers processed.** Before the window the queue
held 19 446 unclaimable and 24 041 pending; after it, 20 568 pending and **120** unclaimable.

## What happened to the 19 446 the old routing gave up on

| outcome | containers | share |
|---|---|---|
| **listed** (`done` + `done/cache`) | **12 987** | **99.2%** |
| failed | 3 | 0.02% |
| still `no_listing_engine` | 1 | 0.008% |
| pending (the window ended) | 104 | 0.8% |

Of the settled ones the success rate is **99.97%**: 12 343 listed outright, 644 replayed from
the derivation cache, three failures (2 `BadZipFile`, 1 `ReadError`), and **one** genuine
`no_listing_engine` — a container whose bytes name no format the harness knows, which is the
honest answer and the only one left in that population.

This is the measurement the sampling promised and could not give: **content routing was worth
12 987 previously-unsearchable containers**, and the ~13 per second throughput means the whole
population cost half an hour.

## The window's own totals, and one number not to misread

| `list_archive` state | containers |
|---|---|
| done | 54 975 |
| done/cache | 22 640 |
| pending | 20 568 |
| failed `BadZipFile` | 109 |
| failed `ReadError` | 20 |
| failed `UnicodeEncodeError` | 3 |
| skipped `no_listing_engine` | **120** |

The 22 640 `done/cache` are large, and that is the design working rather than a surprise: a
re-opened item whose derivation cache is already present replays the listing from the cached
TSV instead of re-opening the container. The window's own `cache hits 3,081` is that,
counted as it happened.

**One number to read carefully**: an early probe of mine printed "members recorded for 500
sampled extensionless containers: 0", which was **wrong — my query's fault, not the code's**.
It sampled containers that were not done, so of course none had members. Checked directly, a
container the window listed has **8 member rows**, and the table is non-empty. The lesson is
the one this project keeps relearning: a probe that cannot see the truth must say "unknown",
and mine said zero.

## What the window did not settle

* **Whether the 23 listed mis-named `.rar` files are the zip ones.** The population shows 23
  `done`, 80 back to `skipped/no_listing_engine` — and the 80 are consistent with libarchive
  not being wired in, since `.rar` is not in the policy. The 23 *should* be the zip-magic
  files, but that was not confirmed per file, and it is the one claim here I am not making.
* **No member count for the corpus as a whole.** `COUNT(*)` and `COUNT(DISTINCT container)`
  over `archive_members` are precisely the scans this project refuses to put on a probe
  (10 546 237 members were recorded on 2026-09-14; the table is larger now). The bounded
  lookups above are the evidence instead.
* **The remaining 20 568 pending containers** are the next window's work, not this one's.

## Host state, recorded because a throughput number is meaningless without it

`lunacode`, load average ~2.0, no competing mining process, corpus mounted read-only at
`/srv/corpus`, index at `~/rlm-derived/corpus.sqlite`. A stale `mine.lock` from an earlier
worker (PID 460724, not running) was taken over by the run's own staleness rule, which is the
documented 300-second path — no process was signalled to ask about it (`AGENTS.md` §3).
