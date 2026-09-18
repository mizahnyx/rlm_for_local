# The two-stage cell budget, landed — and the two defects its sandbox tests caught

**Created:** 2026-09-18 04:51
**Status:** point-in-time record. RO16's *second* half (the two configurable time limits) is
implemented, tested and proved non-vacuous. The *first* half (bytes read as the unit, with
a watchdog) is still unbuilt, as the decision record says.
**Supersedes nothing.** `docs/20260917-2042-decisions-recorded-budget-and-ledger.md` holds
the design; `docs/20260917-2059-blocked-two-stage-budget-and-a-gate.md` holds the failed
first attempt; `docs/20260917-2246-handoff-state-and-next-step.md` §8/§9 held the code and
the test plan this lands.

## What was built

| file | change |
|---|---|
| `src/rlm_local/config.py` | `cell_timeout_hard: float = 1200.0` beside `cell_timeout` |
| `src/rlm_local/templates.py` | `CELL_HARD_TIMEOUT_ERROR`, `CELL_EXTENDED_WARNING` |
| `src/rlm_local/repl.py` | `REPLResult.hard_timeout`; `REPLSandbox(cell_timeout_hard=…)`; `_cell_activity` (reset per cell, `+= 1` per harness request); `_extension_reporter`; the soft/hard deadlines and the gate in `execute()`; `_wait_for_message`; `_on_timeout(cell_id, *, extended)`; `_announce_extension` |
| `src/rlm_local/root_loop.py` | passes `cell_timeout_hard`; injects `_extension_reporter`; `warning_sink` (constructor kwarg); `_report_cell_extension`; `_log_cell_timeout(..., hard=)` writing `limit=`, `budget=` and `activity=` |
| `src/rlm_local/__init__.py` | `completion(..., warning_sink=None)` |
| `src/rlm_local/cli.py` | `--cell-timeout-hard` / `RLM_CELL_TIMEOUT_HARD`; `ask_overrides` carries it; `_cmd_ask` wires `warning_sink` to stderr |

Behaviour: the **soft** limit (`cell_timeout`, 60 s on `tiny`/`laptop`, 120 s on
`workstation`) is reached first. If the cell has asked the harness for *anything* — a corpus
helper, a sub-call, a search — it is demonstrably working and is granted the **hard** limit
(`cell_timeout_hard`, 1200 s), which is announced on the operator's warning sink and
recorded as a `cell_extended` event. A cell that asked for nothing is stuck and is stopped
at the soft limit. `cell_timeout_hard == cell_timeout` switches the second stage off, which
is the behaviour of every release before this one.

## Defect 1: the extension branch could never fire

The verbatim snippet in `docs/20260917-2246-handoff-state-and-next-step.md` §8 put the gate
at the **top** of the wait loop:

```python
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if (not extended and hard > soft and self._cell_activity > 0):
                        extended = True; deadline = hard_deadline; continue
                    msg = None
                else:
                    msg = _recv_msg(self._worker_sock, timeout=remaining)

                if msg is None:
                    return self._on_timeout(cell_id, extended=extended)
```

A real wait does not end at the top of the loop. It ends **inside `_recv_msg`**, whose
`timeout=remaining` expires at the deadline and returns `None` — so the code goes straight
to `_on_timeout` and the extension branch is never evaluated. The branch is only reachable
if a message arrives *exactly* at or after the deadline and the loop comes back around,
which in practice does not happen.

This is precisely what the sandbox-level test strategy in the handoff
(`docs/20260917-2246-handoff-state-and-next-step.md` §2) was chosen to catch,
and it did: `test_a_working_cell_is_granted_the_hard_limit_and_finishes` failed with
`Error: cell exceeded the 0.3s time limit.` while the trace showed the parent had served
the helper (`parent handled corpus_count cell=1`, `parent activity now 1`) — the gate's
input was there and the branch still did not run.

The fix moves the decision to where the wait actually ends, so there is exactly **one**
decision site rather than two that can drift apart.

## Defect 2: the clock cannot say why a wait ended

`_recv_msg` returns `None` for two different facts: the deadline passed with nothing to
read, and the worker closed the socket. Only the first may buy an extension. The first fix
distinguished them with `msg is None and time.monotonic() >= deadline`, which is wrong
often enough to matter: a socket timeout can fire a fraction of a millisecond early
(Windows rounds the select timeout), so the clock said "not yet at the deadline" and the
cell was stopped as stuck. Measured on this host: **1 failure in 4 runs** of the same test,
then 6 consecutive green runs after the change below.

`_wait_for_message` now asks the question directly instead of inferring it:

```python
        readable = select.select([self._worker_sock], [], [], remaining)[0]
        if not readable:
            return None, True          # the window was spent
        return _recv_msg(...), False   # a message, or EOF: not a spent window
```

A closed socket is *not* a spent window — there is nothing slow about a worker that went
away, and crediting it with progress would hand a dead worker's cell twenty minutes of a
budget nothing is using. That asymmetry is a test of its own
(`test_a_worker_that_went_away_is_not_credited_with_slow_work`) and a mutation entry.

## Deviations from the handoff's appendix, each deliberate

1. **`_on_timeout` loses the `elapsed` parameter.** The appendix carried it but never used
   it; the elapsed seconds already reach the operator line and the `cell_extended` event
   through `_announce_extension`. An unused parameter is a claim about a need that is not
   there.
2. **A late message re-arms the window of the stage the cell is in**, not always the soft
   one. The appendix left the two `deadline = time.monotonic() + self._cell_timeout`
   re-arms untouched; on an already-extended cell that would stop it at the soft deadline
   and *report the hard limit as what fired* — a false statement about a budget that had
   seconds left to run.
3. **The nudge names the budget that fired.** A cell that was granted 1200 s and spent it is
   not told "60s".
4. **`activity=` joined the `cell_timeout` event.** The blocked record's own next diagnostic
   was "log `_cell_activity` and the served verb per cell"; `limit=soft activity=0` is now
   the whole diagnosis in one line, and the gate's input is on the page rather than in the
   code that produced the verdict.
5. **The soft timeout message keeps its original rendering.** The appendix spelled it
   `f"{self._cell_timeout:g}"`, which turns `1.0` into `1` — and two pre-existing tests
   (`TestCellTimeoutCorrelation`) assert the message the model reads, so the first full
   suite run after the change failed on `'…the 1.0s time limit.'` against `'…the 1s time
   limit.'`. The right answer was to leave the wording alone: this change is about *which*
   limit fired, not about re-spelling a number three recorded trajectories quote. The hard
   message is new text and uses `:g`, matching the event's `budget=`, since there is nothing
   to preserve.

## Verification

- **Fast suite: 1326 passed / 8 skipped / 12 deselected / 0 failed** (8:25), up from
  1317 passed at the handoff — 9 new tests.
- **Mutation table: 184 guards / 0 problems**, run in full after the change. The first
  full run reported exactly one problem and it was a *stale target*, not a vacuous guard:
  R7's "the REPL timeout error goes back to an inline f-string" pointed at the
  `message = CELL_TIMEOUT_ERROR.format(...)` line this change moved, so the table said
  `target not found` rather than skipping it (the behaviour its own header promises). The
  entry was re-derived against the new two-branch message, proved red on its own, and the
  full table re-run green.
- **Doc lint: 50 documents clean**, linter self-test 6/6.
- 5 sandbox-level tests (`tests/test_repl.py::TestTheTwoStageCellBudget`) drive
  `REPLSandbox.execute()` through a stub worker socket and a thread — **no process startup
  is inside any measurement**, which is what trap 2 of the handoff warned about and the
  reason the first attempt's tests read `corpus_calls=0`.
- 3 integration/CLI tests in `tests/test_root_loop_integration.py`: a hard timeout recorded
  with `limit=hard`, a stuck cell recorded with `limit=soft activity=0`, the two CLI flags
  and their environment variables, and one loop-level test that the reporter reaches
  `_report_cell_extension`, logs one `cell_extended` on the right turn, and hands the
  rendered `CELL_EXTENDED_WARNING` to the injected sink.
- 6 mutation entries added plus 2 pre-existing ones re-derived, all **red as required**: the
  extension without progress, the extension branch that can never fire, a single limit that
  still extends, the hard budget reported as the soft one, the hard budget not configurable,
  a closed socket read as a spent window, the turn-counter entry (its target moved when the
  nudge gained the conditional budget), and the R7 inline-string entry above.
- The existing `TestTheCellBudgetIsVisibleAndConfigurable` test was **changed** rather than
  left alone: with a second limit, its cell (`corpus_coverage()` then `sleep`) is no longer
  stopped at the soft limit, so its assertions now describe the hard path — which is also
  what the "hard budget is reported as the soft one" mutation needs in order to be
  non-vacuous. Two other pre-existing tests were **left untouched and passing**, and their
  failure in the first suite run is what caught the wording change recorded above.

## What is still unverified

- **No live model run has produced a `cell_extended` event.** Everything above is
  unit-level and loop-level with a stub backend; the live behaviour — a real corpus call on
  `lunacode` crossing 60 s and being allowed to continue — is unobserved.
- **The hard limit has never fired against a real cell.** Step 3 of the handoff's plan
  (`docs/20260917-2246-handoff-state-and-next-step.md` §5) is exactly this
  measurement: three probe cases at the real limits, including a cell that outlives 1200 s
  (~22 minutes of wall clock). Its page must show `two_limits_supported=True`; if it shows
  `False`, the build being probed is not this one.
- The gate reads *harness requests*, not bytes: a cell that reads 3.4 GB without calling a
  helper is invisible to it. That is the unbuilt half of RO16.
