# A helper call cannot be bounded by the cell budget

**Date:** 2026-09-26. **Status:** observed twice in one afternoon, with the second instance
controlled; it **corrects an attribution** made in
`docs/20260926-2200-descriptions-reached-the-model-and-changed-nothing.md`, and it names a fix
direction that is not implemented. Discovered while running the set-level version of that
experiment.

## What was observed

Twice today, a question run stopped writing trajectory events for 15–22 minutes while the model
server sat idle. The signature was identical both times:

| | instance 1 (13:51, `RLM_DERIVED_PASS=1` arm) | instance 2 (17:30, `RLM_DERIVED_PASS=0` arm) |
|---|---|---|
| time since the last trajectory event | 22 min | 17 min, and still going |
| probe process state | `D`, `wchan=folio_wait_bit_common` | `D`, `wchan=folio_wait_bit_common` |
| model server CPU over a 20 s sample | **0 ticks** — nothing generating | **0 ticks** |
| model request in flight | none (no connection to the router) | none |
| other processes holding the index | one stray diagnostic of mine | **none** — `fuser` shows only the probe |
| index disk read rate while blocked | not measured at the time | **109 MiB / 15 s**, and the probe's own `read_bytes` grew **481 MiB in 20 s** |

In instance 2 nothing else on the box was reading anything, and the probe's own read counter was
climbing. So the parent process is **inside its own call, reading the index**, for tens of
minutes.

## The correction

`docs/20260926-2200-…` read instance 1 as *I/O contention with my own concurrent index work*,
because a stray diagnostic of mine happened to be reading the same 38 GB index in those minutes.
Instance 2 has no such competitor and shows the same signature at the same rate, so that
attribution was **not established** and is withdrawn here. The mechanism that fits both is the
one below; the earlier record's substantive result (the pass served three descriptions; the
answer was byte-identical) is untouched, because it rests on the trajectories, not on the stall.

## Why the budget does not fire, and why that is the interesting part

The harness has two cell limits, and they are enforced **by the parent** (RO16). A corpus helper
call is serviced by the parent too: the worker asks, the parent runs `handle_search` and answers.
While the parent is inside that call it cannot evaluate the budget it is supposed to enforce, so
a search that takes 17 minutes produces **no `cell_timeout`, no `cell_extended`, and no answer** —
the run simply stops moving, and the model never learns that anything happened.

That is exactly the failure `AGENTS.md` §2 names: a harness limit must not read like a model
failure, and here it reads like nothing at all. It also bounds what the Gate 5 decision
(`docs/20260923-2300-the-search-budget-decision-and-the-cell-limits-raised.md`) can achieve:
raising the *cell* budgets from 3 600 s to 5 400 s accepts the latency of a search, but it does
not bound one, because the blocking call is not on the cell's path.

## The fix direction (not implemented, not measured)

A helper call needs a bound of its own, evaluated where the blocking happens: either

- a SQLite **progress handler** with a deadline, which is the mechanism already used to heartbeat
  the mining lock from inside a long count (`docs/20260922-…`, RO22) — it can abort a scan at a
  chosen wall-clock limit and return `CORPUS_SEARCH_TOO_SLOW`-style text; or
- running the search in a **thread or subprocess with a timeout**, which needs care: the honest
  failure is "the search did not finish", never a partial hit list presented as a result.

Neither is written. What the two instances establish is that the failure exists, is reachable
from an ordinary question, and is silent — which is enough to make the fix worth its own item
rather than another note. The measurement that would size the bound is the distribution of
helper-call durations on this index, which nobody has: today's two instances are 15–22 minutes,
and the same configuration also produced questions that finished in 532–958 s.

## Unverified

- Whether the two instances are the same *query shape*. The queries themselves are corpus-derived
  and were not compared across instances; what is compared is the process signature and the read
  rate. A broad query (common terms, many FTS candidates, `bm25` ordering over them) is a
  plausible mechanism and is **inferred**, not measured.
- Whether the search that blocks eventually returns. Both instances were eventually abandoned
  (the first arm was killed; the second is still running as this is written), so neither has an
  observed outcome.
