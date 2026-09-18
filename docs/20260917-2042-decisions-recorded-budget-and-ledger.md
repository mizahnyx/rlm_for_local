# Decisions recorded: the cell budget in two stages, and three ledger calls

**Created:** 2026-09-17 20:42
**Status:** point-in-time record of the owner's answers to
`docs/20260917-2008-before-you-answer-budgets-and-the-ledger.md`. Two of the four are
ready to build; the budget answer carries a caveat that needs one more scope decision
before it is coded.
**Supersedes nothing.**

## The four answers

| question | owner's answer |
|---|---|
| what a cell's budget counts | **bytes read, with the clock as the outer guard** — plus a two-stage time limit: *"We know we are in old hardware, so, some operations on your table are necessarily time consuming. The hard limit in time can be upped to 20 minutes if the initial 120 seconds are consumed, and then a warning should be sent to the user about starting a time consuming operation."* |
| where the ledger is read | **all three eventually**; the CLI first, since it is the smallest honest step and the others can render the same computation |
| a search over a stale index | **caveat when staleness is known, refuse when it is unknown** — the same distinction the mount probe makes between `ro=1` and `ro=?` |
| the mining queue as action list | **one table with remedies typed `queue` or `command`** — one source of truth for work, without pretending a queue item can do what only a re-walk can |

## The budget, as the owner's caveat makes it

Today: one limit, `cell_timeout` seconds (60 on `tiny`/`laptop`, 120 on `workstation`),
and a cell that exceeds it is killed. The owner's answer makes it **two stages**, and
the reason is sound rather than merely permissive: the hardware is old, so a
*legitimate* corpus operation can exceed any short limit, and killing it wastes the
work and teaches the model nothing.

**Stage 1 — the soft limit, which signals.** The cell runs for `cell_timeout` (default
120 s, configurable per invocation and per profile). When it is reached, the harness
does **not** kill a cell that is doing real work: it **announces** that a
time-consuming operation has started — an operator-visible line naming the cell, the
elapsed time and the helper in flight, plus a `cell_extended` guardrail event with the
same facts — and lets it continue.

**Stage 2 — the hard limit, which stops it.** `cell_timeout_hard` (default 1 200 s =
20 minutes, configurable) is the ceiling: reaching it kills the cell, with the
`cell_timeout` event naming the hard limit as what fired.

**The gate, settled by the owner (2026-09-17): extension requires demonstrable
progress.** A cell that has read bytes, or has a helper call in flight, is *slow work*
and gets the second stage. A cell that has consumed nothing and is waiting — a `sleep`,
a wedged mount read — is *stuck work* and is killed at the soft limit, because the
second stage exists for slow work and not for stuck work. And the announcement is not
optional: silence would make a 20-minute cell look like a hang, which is the confusion
this project keeps paying for.

**Both limits are configurable**, and there is **no run-level ceiling** — the owner's
call, recorded here so it is not re-litigated: two limits per cell, the first
signalling and the second hard, each settable by flag, environment or profile. A run
that hits the hard limit eight times can take hours; that is the operator's business,
and the events make it visible.

## What follows, in order

1. **Structured hits** (decided earlier, still unbuilt): the observed `'S'` failure, and
   independent of the alias work.
2. **The two-stage budget with bytes accounting and the extension warning** — the only
   change here that directly addresses the three live `cell_timeout` events, all of
   which landed on `corpus_search`.
3. **RO14's read path**: archive members read through the extraction cache; a miss
   reports "this container needs mining" instead of scanning.
4. **RO15's fingerprints and the CLI report** (`rlm corpus freshness`), then the trace
   index section and the console page.
5. **The staleness caveat** in search results, and the refusal path when freshness is
   unknown.

Each lands with its tests, its mutation entries and its living-document updates, as
every other change here has. The mnemonic *alias* half stays gated on a run that
actually cites an address — Run A's forced answer cited none, so the prize remains
unmeasured rather than small.
