# The search-budget decision: give the latency room instead of reshaping the search

**Date:** 2026-09-23 (owner's call, 2026-09-24 in conversation). **Commit:** the one that carries
this file. Related: `docs/20260923-1500-the-search-latency-decision-brief.md` (the options),
`docs/20260923-1400-ro5-the-load-gate-measured.md` (the measurements).

## The decision

The owner's words: *"Gate 5, resize budgets for search. Given the old hardware, and the sheer scope
of this task, raising budgets seems like the best option here."*

That is a decision **against** the three options the brief put first — affordable terms, a
rare-term prefilter, a cheaper ranking — all of which change *what a search returns* to make it
affordable. The latency is accepted and given room instead, on hardware where waiting is cheaper
than a killed cell. Those three options stay **untried, deliberately**; they are not rejected on
evidence, and the brief's own smallest experiment (rare-term expressions from `fts5vocab`, if that
virtual table works on this index) remains unrun.

## Why the numbers point that way

Recorded, not re-measured here:

- a question on this corpus costs **10–65 minutes** (the brief, from the live runs);
- search p95 **≥ 60 s** over the queries those runs actually issued, with **seven of fifteen not
  finishing inside 60 s**;
- the hard cell limit was **3 600 s = 60 min**, i.e. **below the slowest recorded question** — the
  only limit that actually stops anything could not honour the decision to wait;
- the soft limit was **60 s**, crossed by half the searches: a signal that a routine operation
  always trips is a signal that is always on.

## What changed

| | before | after |
|---|---|---|
| `cell_timeout` (soft) | 60 / 60 / 120 s | **180 / 300 / 600 s** (tiny / laptop / workstation) |
| `cell_timeout_hard` | 3 600 s | **5 400 s** |

The CLI help (`--cell-timeout`, `--cell-timeout-hard`) and the living docs that state these numbers
(`AGENTS.md` §2, `docs/operator-guide.md`, `docs/extensibility-guide.md`, `docs/rlm-local-manual.md`)
were updated with them. The manual had been **stale at 1 200 s** since the previous raise, which is
the failure mode a default with no test produces: it is fixed here, and the profile table in it is
now covered by the test below.

## What did not change

Nothing about search itself: not the expression, the ranking, the hit count, the bands, or what a
hit means. The brief's options 1–3 are untouched, and so is the `fts5vocab` lead.

## Verified

- `tests/test_cell_budget_default.py` — five tests, written **before** the change (four went red
  first): the hard limit is 5 400 s on every profile; it covers the slowest recorded question
  (3 900 s); each soft limit is above the recorded 60 s p95 and below the hard one; the two limits
  stay distinct; and the CLI help states the default in force.
- Two mutations, each run alone and red: the hard limit dropped back below the slowest recorded
  question, and the soft limit dropped back under the recorded p95.
- Doc lint clean.

## Unverified — and this one is an inference, not a measurement

**The 10–65 minutes is a per-*question* figure, and it is being used here to size a per-*cell*
limit.** A question spans many cells, so the per-question figure is an upper bound for any single
cell rather than the quantity the limit actually governs — using it errs upward, which is the safe
direction for a limit whose purpose is to stop stuck work, but it is an inference. Nobody has
measured the worst *single cell* on this corpus. If a cell ever dies at 5 400 s while running a
helper that was working, that measurement becomes the one to make.

Also unmeasured: whether raising the budgets changes how often the escape hatches fire (a longer
cell can burn more turns before the citation guard sees a submission). That is a live-run question,
and no live run happens while the box is away.
