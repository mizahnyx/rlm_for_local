# The fabrication did not recur, and the guard is insurance rather than a cure

**Created:** 2026-09-16 15:55
**Status:** closed — the check is negative-but-useful; the guard's live arm is still
unfired
**Supersedes nothing.** Verifies `docs/20260916-1540-corpus-citation-must-be-served.md`
against the configuration that produced the defect.

## What was run

Two runs of the same unanswerable question at the budget that fabricated a citation
earlier the same day (`--max-turns 4`, `--profile laptop`, complete index), now on
the served-citation guard.

| run | turns | forced | elapsed | addresses printed | citations | refusals | answer |
|---|---|---|---|---|---|---|---|
| 4c | 4 | yes | 529.8 s | 0 | **0** | `corpus_uncited` ×1 | prose, names coverage |
| 4d | 4 | yes | 529.1 s | 0 | **0** | `corpus_uncited` ×1 | prose, names coverage |
| *(4a, before the guard)* | 4 | yes | 669 s | 0 | **1 (unserved)** | 0 | prose + a `Citations:` line for an address it was never given |

## What this establishes, and what it does not

**Established:** the fabrication did not recur. Neither run emitted a citation at
all, so no unserved address reached an answer, and the one refusal each run
recorded was the *no address and no coverage* arm (`unserved=0`), not the
fabrication arm. Both answers take the absence route in prose and name coverage.

**Not established, and worth stating plainly:** the guard's fabrication arm has
**never fired in a live run**. It is covered by four loop tests and two mutation
entries, and it is the only thing standing between a plausible-looking invented
address and a delivered answer — but "not reproduced in two runs" is not "the model
stopped doing it". Across the six short-budget runs measured today, **one**
fabricated. That is a low rate with n too small to call a rate, and it is exactly
the kind of behaviour this project has already recorded as unstable across router
cache states.

So the honest framing is: the guard did not *fix* the model; it made the failure
**undeliverable** when it happens. That is the right shape for a harness whose
whole citation apparatus exists to keep a confident wrong answer from looking
checkable.

**One incidental good sign for the escape arm:** after being refused once for
citing nothing and naming nothing, both runs produced answers that *do* state
coverage. The absence arm is reachable in substance; what is still missing is a
voluntary submission that takes it (both answers arrived by forced finalization).

## What is next, unchanged

The standing order from `docs/20260916-1405-corpus-convergence-permission-was-not-enough.md`
and the roadmap: **the relevance signal** — a weak match and a strong one still
look identical to the model, which is why a short run cannot tell "these are not an
answer" — then the escape arm's harness-side evidence, then RO7 batch synthesis.
