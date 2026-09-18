# Blocked: the two-stage cell budget, and one gate it exposed

**Created:** 2026-09-17 20:59
**Status:** point-in-time. The implementation of RO16's two-stage budget was **written,
tested, found not to work as claimed, and reverted** — nothing of it is in the tree.
The design stands (`docs/20260917-2042-decisions-recorded-budget-and-ledger.md`); this
records what blocked it, so the next attempt starts from facts instead of from my
memory of the afternoon.
**Supersedes nothing.**

## What was built, and what it did

The change was small and mechanical: `cell_timeout_hard` as a second configurable
limit (default 1 200 s), a `_cell_activity` counter incremented whenever the cell asks
the harness for anything, an extension branch in `REPLSandbox.execute()` that grants
the hard limit when the soft limit is reached *and* activity is non-zero, a
`cell_extended` guardrail event, an operator warning through a `warning_sink` the CLI
wires to stderr, and `--cell-timeout-hard` / `RLM_CELL_TIMEOUT_HARD`.

Four tests were written for it: a slow cell extends and finishes; a stuck cell is
stopped at the soft limit; the hard limit stops even a working cell; the CLI flag
reaches the override dict.

## The blocker, stated exactly

**The two-stage tests observe no helper activity at all.** The failing assertion was
the same in two tests:

```
assert 'limit=hard' in 'block=1 limit=soft budget=6s last_helper=none corpus_calls=0 cell_timeouts=1'
```

`corpus_calls=0` and `last_helper=none` say the harness never served the helper the
cell called — so the extension gate correctly saw a stuck cell and stopped it. But the
*single-stage* test, with `cell_timeout == cell_timeout_hard` and the identical cell
body (`corpus_coverage()` then a sleep), **passes and reports
`last_helper=corpus_coverage`**. The same cell, the same bridge, the same host: the
helper is visible when the extension branch cannot fire, and invisible when it can.

Two hypotheses, neither confirmed:

1. **Worker startup is inside the measurement.** The first cell of a run pays the
   worker's own start. If that exceeds the soft limit, the cell is stopped before its
   code runs — which is exactly what `corpus_calls=0` looks like. Refuted by the
   single-stage test observing the helper under a *0.5 s* limit, but that test's
   timeout may be firing on a later cell; I did not establish which.
2. **The activity counter's lifecycle is wrong.** `_cell_activity` is reset at the top
   of `execute()` and incremented in `_handle_request`. If a helper request is served
   on a path that does not reach `_handle_request` — a stale-cell request, for
   instance, which is answered by `_answer_stale_request` — activity stays zero while
   the model believes it called a helper.

**The next diagnostic is small and specific:** log `_cell_activity` and the served
verb on every cell boundary for a two-stage run, and print which turn the timeout
belongs to. Whichever hypothesis holds, the fix is a few lines; the point of writing
this down is that the *symptom* (a helper invisible to a gate that reads helper
requests) is the kind of thing this project has met before — a check whose subject is
not where it thinks it is.

## Why it was reverted rather than landed

The project's first rule is that a change lands with the test that fails without it, and
its second is that a guard is proved non-vacuous. A two-stage budget whose extension
branch has never fired in a test satisfies neither, and a timeout mechanism that is
subtly wrong is worse than the one-stage mechanism it replaces — it would silently turn
"stuck" into "allowed to run for twenty minutes". The tree is back at `75916e4` with
`tests/test_root_loop_integration.py` green (64 passed), and the design is intact in the
decision record.

## The gate this ran into, and it is the owner's

Implementing the *staleness* half of the ledger (RO15) requires answering a question the
owner's earlier decision does not settle by itself. The decision was: **caveat a search
when staleness is known, refuse when it is unknown.** Applied literally to the index
that exists today, that is a problem, because **no cache has a fingerprint yet**:

- every cache is therefore "unknown", and
- "refuse when unknown" would **refuse every search on the current index** until a
  fingerprint is published — turning an upgrade into an outage for as long as the owner
  has not run `rlm corpus counters --refresh` or a mining window.

Three ways out, and this is the call:

| option | what happens on the existing index | the trade |
|---|---|---|
| **Migrate a baseline fingerprint** | the first read after the upgrade records the index's own completeness marker (`complete`, source count, chunk count) as the baseline, so freshness is *known* from then on | nothing breaks; but the first fingerprint is "as of the upgrade", not "as of the build", so a stale-at-upgrade index reads as current until its inputs change |
| **Treat pre-fingerprint indexes as unknown, and refuse** | searches refuse with "how current this index is cannot be established; run `rlm corpus counters --refresh`" | maximally honest and briefly unusable — the owner runs one command to restore service |
| **Grandfather them as current** | a cache with no fingerprint is assumed current until its inputs visibly change | nothing breaks and nothing is refused; but it is the confident-default this project ranks below silence, and it makes the first fingerprint meaningless |

My recommendation is the second: it is the only one that keeps the rule the owner chose
("unknown → refuse") true, the fix is one operator command, and the message tells the
operator exactly which command. But it *is* an outage-by-design for one command's
duration, which is the owner's to accept or refuse.
