# The two-stage cell budget, measured live at the real limits

**Created:** 2026-09-18 05:10
**Status:** point-in-time measurement. Closes the "never observed live" caveat that
`docs/20260918-0451-two-stage-cell-budget-landed.md` ended on: the extension had been
proved only through stub workers and stub backends.
**Supersedes nothing.** The design is in `docs/20260917-2042-decisions-recorded-budget-and-ledger.md`;
the code and its tests are in `docs/20260918-0451-two-stage-cell-budget-landed.md`.

## What was run, and where

`scripts/probe_cell_budget.py` on `lunacode`, against the real read-only corpus
(`/srv/corpus`, index `~/rlm-derived/corpus.sqlite`), at the **real** limits —
`soft=60s`, `hard=1200s`, the `tiny` profile — on the commit that landed the feature
(`d30265e`). No model is involved: the probe scripts the cells and the parent's own
telemetry is the evidence. Python 3.14.7 on that host.

The probe's two cases are the boundary that matters, not the extremes: a cell that runs
**past** the soft limit and well inside the hard one, with and without a helper call. The
hard limit itself (a cell outliving 1 200 s) is *not* in this measurement — see "Still
open" below.

## The result

**Case 1 — a working cell (`corpus_coverage()` then `sleep 75`):**

```
probe_env  requested soft=60s hard=1200s two_limits_supported=True cell_sleeps=yes
cell_extended  turn=1  elapsed=60s soft=60s hard=1200s last_helper=corpus_coverage
probe_cell cell=1 first_line='corpus_coverage()' elapsed=75.00s timed_out=False
           hard=False corpus_calls=1 activity=1 stdout=0 stderr=0
```

**Case 2 — the control, a cell that asked for nothing (`sleep 75`):**

```
probe_cell cell=1 first_line='import time; time.sleep(75)' elapsed=60.03s timed_out=True
           hard=False corpus_calls=0 activity=0 stdout=0 stderr=42
cell_timeout  turn=1  block=1 limit=soft budget=60s activity=0 last_helper=none
              corpus_calls=0 cell_timeouts=1
```

Both cases in one run of the probe: `real-slow-with-helper` → 2 cells, 1 corpus call,
**0 timeouts, 1 warning**; `real-stuck-no-helper` → 2 cells, 0 corpus calls, **1 timeout,
0 warnings**.

## What this establishes, and what it does not

Established, on the real host at the real magnitudes:

1. **The gate opens on demonstrable progress and closes without it.** The same cell body,
   the same limits, the same bridge: with a helper call the cell is extended
   (`cell_extended`, `last_helper=corpus_coverage`) and **runs to completion in 75.00 s
   with `timed_out=False`**; without one it is stopped at `elapsed=60.03s` with
   `limit=soft … activity=0`. Before this change both were killed at 60 s and both looked
   like a timeout.
2. **The extension is announced once, and on the right turn.** One `cell_extended` at
   turn 1, with the elapsed time, both limits and the verb in flight — and exactly one
   operator warning for the run (`warnings: 1`), none for the control.
3. **The event says which limit fired and why.** `limit=soft|hard`, `budget=`, `activity=`
   and `last_helper=` are all present in the live trajectory, which is what makes the
   distinction readable after the fact rather than inferred.
4. **`two_limits_supported=True`** on the page: the build being probed is the one with the
   feature. The probe records this because its first version compared two configurations
   that were identical in the tree and said nothing about it.

Not established:

- **The hard limit has never fired against a real cell.** These cases are stopped by
  choice well inside 1 200 s. The case that outlives the ceiling costs ~22 minutes of wall
  clock and has not been run; until it is, "a working cell that spends 20 minutes is
  stopped, and told so" rests on the unit tests alone.
- **No *model* run has produced an extension.** The cell here is scripted. Whether a real
  model's cell — which asks for helpers, waits on a 231 s cold count, and may ask several
  times — triggers the same path in the same way is the live-run question, and it is the
  next one worth asking.
- One incidental observation from the control, consistent with the known trap that a
  timed-out cell leaves the worker running: its second cell took 15.01 s rather than
  ~0 s, because the worker was still finishing the abandoned sleep. It is recorded in
  `probe_cell` and is why the counter bounds failure *events* rather than distinct cells.

## Where to read it

The trajectories and their rendered pages live where the corpus is, never in this
repository (`AGENTS.md` §1.9):

- `~/rlm-derived/probe-budget-real-slow-with-helper.jsonl`
- `~/rlm-derived/probe-budget-real-stuck-no-helper.jsonl`
- `~/rlm-derived/traces/` — 22 pages and an index; these two runs' pages are
  `20260918-0726-probe-budget-real-slow-with-helper.md` and
  `20260918-0727-probe-budget-real-stuck-no-helper.md`.

The pages contain corpus text and the trajectories contain served addresses, so both are
0600 in a 0700 directory. Only the instrumentation lines above — timings, limits, counts,
and the name of the helper verb — are reproduced here, because those are aggregates.

`rlm trace summary` on each trajectory (counts only, safe to quote anywhere):

```
probe-budget-real-slow-with-helper.jsonl: turns=2/2 forced=True elapsed=75.1s answers=1
  cited_answering=0 … refusals_uncited=1 refusals_weak=0 searches=0 bands=none audit=complete
probe-budget-real-stuck-no-helper.jsonl: turns=2/2 forced=True elapsed=75.1s answers=1
  cited_answering=0 … refusals_uncited=0 refusals_weak=0 searches=0 bands=none audit=partial
```

Two things in those lines are **not** about the budget, and are named here so they are not
read as such: both runs are `forced=True` because the probe's scripted cell calls
`answer['ready']` without a `Citations:` line, and the first case therefore eats one
`corpus_uncited` refusal (its cell *did* call a helper, so the citation guard applies) —
the extension still did its job, the cell ran its full 75 s. And the stuck case's audit is
`partial` for the reason the viewer was built to state: that run served no
`corpus_served` events, so an empty served set is *unknown*, not empty.
