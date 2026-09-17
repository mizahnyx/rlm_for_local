# A model timeout cost the run and left no trace of why

**Created:** 2026-09-16 23:30
**Status:** closed — a transport failure now costs a turn, never the run
**Supersedes nothing.** Diagnosed from the abnormal second run in
`docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md`.

## What happened

The second run of the relevance-signal measurement produced four quality events
and **no `end` event**, so its outcome was reported as unknown rather than as a
result. The diagnosis, from the artefacts rather than from theory:

```
=== run q2 exit=2  end 21:51:45 ===
live-ask-q2.txt: 1 line — "Error: The read operation timed out"
live-ask-q2.jsonl: 49 events, no `end` record written by the loop
```

Exit code 2 is the CLI's error path. The exception — a **read timeout while waiting
for the router** — propagated out of `RootLoop.run()`, so a 30-minute run returned
no answer, and its trajectory ended mid-flight. The reason survived only on the
stderr of a file nobody reads. Three consequences, in ascending order of
seriousness:

1. A run was lost that had done 30 minutes of work.
2. The failure was invisible in the trajectory, so the summary said "unknown" and
   the honest reading was indistinguishable from absent data.
3. **Every comparison measured on this host is vulnerable to it.** The 15 GiB box,
   ~3 tok/s decode and the router's multi-instance habits make read timeouts a live
   risk, and a run that dies of one is not a smaller sample — it is a missing one,
   silently.

## The fix

The turn's model call is guarded. A raise is counted (`RootLoop.model_errors`),
logged as a `model_error` guardrail event with the exception type, and the turn loop
**breaks into forced finalization** rather than unwinding — and the forced call is
already guarded, so a model that is failing outright yields
`FINALIZATION_FAILED`. Every run now ends in an answer string and an `end` record,
whatever the model does.

Two tests, both new: a run whose first turn raises returns an answer, records
`model_errors == 1`, writes an `end` event, and carries the exception type in its
trajectory; and a model that raises on *every* call still produces a terminal
answer and an `end` event rather than an exception and an orphaned trajectory. One
mutation entry observed red (the guard re-raising, which is exactly the defect).

`StubBackend` gained the ability to script an exception, because a transport failure
is the only honest way to test survival of one.

## What this does not fix

The timeout itself. A slow router is a host condition (OD5), and this change makes
the harness *survive* it rather than prevent it — a degraded run is now visible, not
healthy. If runs begin to show `model_error` events regularly, that is evidence
about the host, and the response is the router's instance count and model residency
rather than more harness code.
