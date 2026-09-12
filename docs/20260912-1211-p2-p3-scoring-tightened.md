# P2 and P3 scoring tightened — the deferred weaknesses are gone

**Date:** 2026-09-12 12:11
**Enacts:** roadmap items 4 and 5 (`docs/20260912-1155-roadmap.md`), ledger
entries BS1, BS2, BS3.
**Status:** enacted; three new mutations red as required (54 in the table).

*Timestamp note:* this document was first written as `20260912-1240-…` from an
unchecked reading of the clock. The naming convention makes the prefix the
creation time, so it has to be true; renamed to 12:11, the time verified when the
error was noticed, and the error is recorded here rather than quietly fixed — a
prefix an auditor cannot trust is worse than no prefix.

Two of the battery's probes were documented, deferred weaknesses — pinned by
tests that asserted the *bad* behaviour so a change would be deliberate. This is
that deliberate change, made on the owner's decision, and it moves score
semantics for any full-battery run recorded from now on.

## 1. P3 measured absence of failure, not recovery

The old scoring had two defects (F15), both now fixed:

| | Before | After |
|---|---|---|
| A model that executed **no cell at all** | 15/15 | **0/15** — "emitting nothing is not recovering" |
| Cells ran, none raised | 15/15 | 15/15 — *nothing to recover from*, stated as such |
| A cell raised, a **later** cell ran clean | 15/15 only if a balanced `grep(` call appeared *anywhere* | **15/15** — the error streak ended |
| A cell raised, a balanced `grep(` call appeared **before** it | 15/15 (credited recovery never performed) | **0/15** |

The behavioural change is the second and fourth rows: recovery is now "a cell
after the failure ran without a traceback". That needs no regex, and it is the
behaviour rather than a proxy for it. A valid helper call after the failure is
still reported as supporting evidence — it signals a *deliberate* correction,
which is not the same thing as getting past the error.

Bounded by turn order, conservatively: a message whose turn is missing or
non-integer cannot be shown to come after the failure, so it does not count.

## 2. P2 counted an unterminated call as a valid one

`grep('never closed` extracts as the empty string (the lexer cannot delimit it)
and `_balanced_parens("")` is `True`, so it cost the model nothing. P2 now
requires a call it can delimit; one such call costs 5 of 15. `_balanced_parens`
still returns `True` for the empty string — it is a predicate about parens, and
emptiness is the caller's business.

## 3. What this means for recorded numbers

- **Quick batteries (P1+P4+P6) are unaffected.** Every model verdict in
  `docs/20260912-1155-roadmap.md` §3 stands as recorded.
- **Full-battery composites are not comparable across this change.** P3 was worth
  15 points to a model that produced no code at all, and P2 could not penalise an
  undelimited call; a pre-2026-09-12 full-battery score and a post-change one are
  measuring different things. The recorded full-battery runs on the record —
  `rlm check` results in the 2026-07/08 conformance documents — stay as they are,
  on their own semantics, and are labelled by date like every other record.
- The next full-battery run on any model is the first that can be compared with
  another full-battery run.

## 4. Guards

| Mutation | Guard it proves |
|---|---|
| `BS1 a model that never executes a cell gets full credit again` | The no-cells branch. |
| `BS2 the recovery scan ignores turn order again` | That recovery is a *later* cell. |
| `BS2 a cell at the failing turn counts as coming after it` | Strict turn ordering (`>` not `>=`). |
| `BS3 an unterminated helper call is scored valid again` | That a delimited call is required. |

Tests: `TestProbeP2NowRejectsUnterminatedCalls` (an unterminated call scores 10
and fails; a balanced call still scores 15), `TestProbeP3` (no cells → 0; all
cells clean → 15 with the reason stated; a clean cell after the failure → 15; a
failure with no following cell → 0; the pre-error grep no longer recovers), and
`TestTurnOrdering` (six cases, including missing and non-integer turns).
`RecoveringStub`, `ErrorOnlyStub` and `UnterminatedCallStub` make each case a real
trajectory through the root loop rather than a synthetic log.

## 5. Open

- **No live re-measurement.** The new P2/P3 semantics are proven against driven
  trajectories and stub models, not against a router model: a full battery is
  ~9 completions, i.e. an hour or more on this host, and no model on the router
  needs a full-battery verdict right now. The next full battery run is the first
  live evidence.
- **P3 still cannot distinguish "wrote correct code" from "wrote code that
  happened to work".** That is a property of a 15-point probe, not a defect
  introduced here; it is bounded by P2, which now scores the calls themselves.
