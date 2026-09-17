# Two failures that were not the model's

**Created:** 2026-09-17 12:00
**Status:** point-in-time. Both defects are fixed, tested and mutation-proved.
**Supersedes nothing.** Answers two of the six findings in the owner's review of
2026-09-17, and quotes them so the fix can be checked against the complaint.

## Finding: "Sometimes the model fails to produce valid Python, those steps must be repeated without penalty until valid Python is generated."

Before this, a cell that did not compile cost the run **twice**: it consumed one of
the fifteen turns, and it incremented the consecutive-error streak that forces
finalization after three. A small model's worst habit — malformed syntax — was
therefore charged as if it had reasoned badly, when in fact the cell never ran:
nothing was attempted, so nothing was learned, and the run's budget shrank for a
failure that carries no information about the question.

What happens now:

- The worker catches `SyntaxError` **before** the generic handler and reports the
  cell as `syntax_error=True` on the result, alongside a message from
  `templates.py` (`WORKER_CELL_SYNTAX_ERROR`). The parent keys off the flag, not
  the wording, so rewording the message cannot break the behaviour.
- `RootLoop` treats it as a formatting failure: it appends `NUDGE_SYNTAX_ERROR`
  (which names the interpreter's complaint and says this did not count), asks for
  the cell again, and **does not advance the turn counter**. The loop became an
  explicit `while` for this: every other exit from a turn advances the counter
  exactly once, and this one deliberately stands still.
- The error budget is untouched. Only *not compiling* is free — a cell that ran and
  raised still counts, which the tests pin so the exemption cannot widen.
- The retry is bounded by a new `max_syntax_retries` (default 5), and running out is
  recorded as its own `syntax_giveup` event: "asked again and it broke again" and
  "gave up asking" are different facts about a model.

Measured in the fixtures: a broken cell followed by a good one ends the run with
**`turns_used=1`** out of three available, one `syntax_retry` event, and the model
told why; a model that never writes valid Python stops after exactly the retry
budget.

## Finding: "60 seconds timeout for `corpus_count` is really too little, that parameter has to be configured somehow … Also a methodology must be devised probably to diagnose when a harness failure is product of a too low value on `corpus_count` timeout."

Both halves are now real.

**Configurable.** `cell_timeout` was profile-only (60 s on `tiny`/`laptop`, 120 s on
`workstation`) with no flag. `rlm ask` now takes `--cell-timeout SECONDS`, and
`RLM_CELL_TIMEOUT` sets the same value from the environment, so a loaded host can be
worked around per invocation instead of by editing `config.py`. The overrides are
built by a new `ask_overrides()` so the plumbing is testable without a model server —
a knob that silently fails to reach the run is worse than no knob.

**Diagnosable.** A timeout used to be invisible: the only trace was stderr prose that
`parse_stderr` then reported as if the code had raised, which is exactly the
confusion the finding names. Now:

- the sandbox marks the result `timed_out=True`;
- `RootLoop` writes a **`cell_timeout`** guardrail event carrying
  `budget=60s last_helper=corpus_count corpus_calls=N cell_timeouts=N`;
- the model is told it hit a *budget* (`NUDGE_CELL_TIMEOUT`), and that the answer is
  to ask for less in one cell — bound the work, split it, use a cheaper helper — or
  to say what it was asking for instead of retrying blindly;
- `RootLoop.cell_timeouts` counts them.

The methodology that makes this a diagnosis rather than a counter is in `docs/operator-guide.md` §3, "Was it the budget?":

1. `grep -c '"guardrail": "cell_timeout"' "$LOG"` — how many cells died on the budget.
2. Read `last_helper=` on those events. **A cluster on one verb is a harness
   problem**; a scatter across verbs, or `last_helper=none`, points at the model.
3. Compare the named budget with what that verb costs on this host (for
   `corpus_count` on the path index: counting 4,972,609 entries — and the same verb
   on the text index is the CL6 case, ~16 minutes, which no cell budget can hold).
4. **A timeout is not evidence about the answer.** A run whose cells died on the
   budget says nothing about whether the model could have answered; raise
   `--cell-timeout` and re-run before judging it, and treat a *persistent* cluster as
   a helper to fix rather than a knob to raise — which is what RO11 turned out to be
   (one passage read scanned 29M rows for >150 s).

## What is not verified

- The syntax retry is proved in fixtures. **No live run has yet produced a
  `syntax_retry` event**, so the cost of the fix — extra model calls inside one turn,
  and more tokens spent on a model that keeps failing — is measured nowhere. The
  instrument (`syntax_retry` / `syntax_giveup` counts per run) is there to measure it
  on the next live run.
- The timeout diagnosis is proved with a 0.5 s budget and a sleeping cell. Whether
  `corpus_count` actually exceeds 60 s on the owner's host under load is *inferred*
  from the owner's report plus the size of the index; it has not been re-measured
  here, and the honest answer for that verb is one `cell_timeout` event away.
- `--cell-timeout` is on `rlm ask` only, matching `--max-turns`. `chat` still has
  neither, which is a gap for corpus chat sessions.
