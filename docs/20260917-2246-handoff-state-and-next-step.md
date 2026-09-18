# Handoff: state of the corpus harness, and the next step (the hard cell limit)

**Created:** 2026-09-17 22:46
**Status:** a resume document, written because the context that did this work is being
rolled. It records what is landed, what is half-done and where, the two traps that cost
time, the verification state *including the debt*, and the exact next action.
**Supersedes nothing.** It summarises and points; the dated records remain the history.

> **Update, 2026-09-18 05:10 — the next action in §2 is done, and §5's measurement is half
> done. Read this before acting on anything below.**
>
> - **§2 (re-land the hard cell limit): landed** as `d30265e` — `cell_timeout_hard`, the
>   progress gate, the `cell_extended` event and the operator warning, `--cell-timeout-hard`
>   / `RLM_CELL_TIMEOUT_HARD`. 5 sandbox-level tests, 3 integration/CLI tests. Fast suite
>   **1326 passed / 0 failed**; mutation table **184 guards / 0 problems**; doc lint 50
>   documents clean.
> - **Two defects were found in §8's verbatim code, by exactly the test strategy §2
>   prescribed**: the extension branch sat at the top of the wait loop, where a real wait
>   never returns, so it *could never fire*; and telling "the window closed" from "the
>   worker closed the socket" by comparing the clock was wrong about one run in four, and
>   is now decided by `select`. Details, with the deviations from §8 that were deliberate:
>   `docs/20260918-0451-two-stage-cell-budget-landed.md`.
> - **§5 step 3, partly run**: the probe at the real limits on `lunacode`
>   (`two_limits_supported=True`) shows the working cell extended at 60 s and finishing at
>   75.00 s with **0 timeouts**, and the stuck control stopped at 60.03 s with
>   `limit=soft activity=0`. **Still open**: a third case that outlives 1 200 s (the hard
>   limit firing for real, ~22 minutes, not yet added to the probe), and any *model* run
>   producing an extension — these cells are scripted.
>   `docs/20260918-0510-probe-two-limits-at-the-real-limits.md`.
> - Everything else in this document — §3's two traps, §6's standing rules, §7's map —
>   still holds, and §7's map now also includes the two records above.

---

## 1. Where the code is

`main` at **`ba529fa`**, clean tree locally and on `lunacode`
(`~/Misc/rlm_for_local`, one `git pull` to sync — it is already there).

Recent commits, newest first, each with its own tests:

| commit | what it did |
|---|---|
| `ba529fa` | **A timed-out cell was charged two turns.** The timeout branch advanced the turn counter inside the block loop; the turn loop's own advance then fired too. With `max_turns=8` every timeout stole two turns (Run A: 3 timeouts → 5 real turns, reported as 8). Fixed by setting the nudge in the block loop and sending/advancing once after it, beside the syntax branch. 2 tests, 2 mutations. |
| `963c1d5` | The budget probe runs at the **real** limits (60 s soft / 1200 s hard) and records on its page whether the build even has a second limit (`two_limits_supported`). |
| `2deaad5`, `303e724`, `5331237` | Probe fixes: truncate its trajectory per run (the logger appends), install its wrappers once (they were stacking so one case logged into another), and do not assume a field from reverted code. |
| `76c221e` | **AGENTS.md rule 5** extended with the owner's rule: when something unexpected happens, *produce the traces and let the owner reach the verdict*; reproducers live in `scripts/`, traces stay where the corpus is. |

## 2. The next action: re-land the hard cell limit (RO16, second half)

The design is decided (owner, 2026-09-17) and recorded in
`docs/20260917-2042-decisions-recorded-budget-and-ledger.md`: **two limits per cell,
both configurable** — a *soft* one (60 s on tiny/laptop, 120 s on workstation) that
**signals** and lets a working cell continue, and a *hard* one (1200 s = 20 min) that
stops it. **No run-level ceiling** — the owner's call. The extension is granted only on
**demonstrable progress**, and it is announced.

It was implemented once and **reverted** (`docs/20260917-2059-blocked-two-stage-budget-and-a-gate.md`)
because its tests could not see the helper the cell called. The turn-counter defect in
§1 is what had to land first, since the extension's tests assert turn counts.

**The exact pieces, with the shape each takes:**

| file | change |
|---|---|
| `src/rlm_local/config.py` | `cell_timeout_hard: float = 1200.0` beside `cell_timeout` |
| `src/rlm_local/templates.py` | `CELL_HARD_TIMEOUT_ERROR` (the message when the hard limit fires) and `CELL_EXTENDED_WARNING` (the operator line). Both must be *emitted* or `tests/test_templates.py::test_no_dead_templates` fails. |
| `src/rlm_local/repl.py` | `REPLResult.hard_timeout: bool`; `REPLSandbox(cell_timeout_hard=…)`; `self._cell_activity = 0` reset per cell and `+= 1` in `_handle_request`; in `execute()` a soft deadline and a hard deadline, and at the soft deadline extend **iff** `not extended and hard > soft and self._cell_activity > 0`, calling `self._announce_extension(elapsed)` (which calls the injected `self._extension_reporter`); `_on_timeout(cell_id, *, extended, elapsed)` choosing the hard or soft message and setting `hard_timeout` |
| `src/rlm_local/root_loop.py` | pass `cell_timeout_hard`; set `self._repl._extension_reporter = self._report_cell_extension`; `self._warning_sink` (constructor kwarg); `_report_cell_extension(elapsed)` logging the `cell_extended` event and calling the sink with `CELL_EXTENDED_WARNING`; `_log_cell_timeout(..., hard=)` writing `limit=soft|hard` and the matching budget; the block-loop call passes `hard=repl_result.hard_timeout` |
| `src/rlm_local/__init__.py` | `completion(..., warning_sink=None)` → `RootLoop` |
| `src/rlm_local/cli.py` | `--cell-timeout-hard` + `RLM_CELL_TIMEOUT_HARD`; `ask_overrides` carries it; `_cmd_ask` passes `warning_sink=lambda m: print(m, file=sys.stderr)` |

**The test strategy that must be used** (this is the part that failed last time): the
**primary proof is at the sandbox level**, driving `REPLSandbox` with a stub worker
socket and **no process startup** — activity > 0 and soft deadline passed → extension
announced once and the hard deadline applied; activity == 0 → `_on_timeout` with the
soft message; hard deadline reached → `hard_timeout=True` and the hard message;
`hard == soft` → no extension. One loop-level integration test asserts only that the
`cell_extended` event appears. **Do not** measure the extension through a root-loop test
with a short soft limit: the first cell's window then contains worker startup, which is
how the earlier attempt read `corpus_calls=0` while the real probe on `lunacode` shows
the helper served at ~0 ms.

**Mutation entries to add:** the extension gate ignoring `_cell_activity`; the hard
branch not stopping (`not extended` removed); `ask_overrides` dropping the hard limit;
`_log_cell_timeout` reporting `soft` for both.

## 3. Two traps that cost time — do not rediscover them

1. **A timed-out cell leaves the worker still running it.** The parent abandons the
   cell, but the worker is inside the sleep, so the *next* cell queues behind it and
   dies on the same budget — and that second timeout is what triggers the sandbox's
   worker restart. **One timeout therefore normally costs two cells**, which is why
   `tests/test_root_loop_integration.py::TestATimedOutCellCostsOneTurn` uses three turns
   with two sleeping cells to reach a clean third.
2. **A short soft limit measures process startup, not the cell.** Anything under a few
   seconds tests the worker's start. Use seconds, and prefer sandbox-level tests.

## 4. Verification state, including the debt

- Fast suite at `ba529fa`: **1317 passed / 8 skipped / 12 deselected / 0 failed** (8:17).
- Doc lint: **49 documents clean**; linter self-test 6/6.
- Mutation table: **178 guards / 0 problems**, re-run in full after the counter fix, so
  the debt this section previously carried is cleared. Log kept at
  `.tmp_verify/table.txt` in the checkout (scratch, gitignored).
- Traces: `~/rlm-derived/traces/` on `lunacode`, **22 pages**, index at `index.md`.
  The two probe pages (`…-probe-budget-real-slow-with-helper.md`,
  `…-probe-budget-real-stuck-no-helper.md`) show the real magnitudes.

## 5. Step 3 after that: the probe at real limits, three cases

`scripts/probe_cell_budget.py --out-dir ~/rlm-derived`, then
`rlm trace render ~/rlm-derived --out-dir ~/rlm-derived/traces …`.
Cases: **slow with helper** (sleeps 75 s → must extend and finish), **stuck without
helper** (the control → stopped at 60 s), and a third to add — **a cell that outlives
1200 s** (the hard limit must fire) — which costs ~22 minutes of wall clock, so run it
deliberately and once. Its page must show `two_limits_supported=True`; if it shows
`False`, the code being probed is not the build that has the feature.

## 6. The owner's standing rules (they are in `AGENTS.md`, and they are load-bearing)

- **Traces before verdicts**: on anything unexpected, instrument it, render the
  trajectory, point at the file, and stop short of concluding. `AGENTS.md` §1.5.
- **Read-only corpus, three layers**, the mount being the only real boundary; derived
  state never inside the corpus (`AGENTS.md` §1.8), and corpus-derived data never leaves
  the machine that holds it (`AGENTS.md` §1.9) — aggregates travel, identifiers and
  quotes do not.
- **Do not state what you have not verified** (`AGENTS.md` §1.5); a check that cannot
  see the truth says `unknown` (`AGENTS.md` §1.8 corollary).
- **Test first, and prove each guard non-vacuous** in `scripts/check_guard_nonvacuity.py`
  (`AGENTS.md` §1.1 and §1.2); never edit source while that table runs (`AGENTS.md` §3).
- **The owner's other direction, still open by design**: mnemonic addresses are
  **per chat session**, the harness sees mnemonics and the owner sees true addresses,
  the delivered answer has addresses substituted inline with the model's raw output kept
  in the trajectory, and **structured hits land before the aliases**. The alias half
  stays *data-gated*: Run A's forced answer cited no address at all, so the size of the
  prize is still unmeasured (`docs/20260917-1215-mnemonic-addresses-design.md`,
  `docs/20260917-1330-decision-brief-mnemonics-budgets-and-proofs.md`).

## 7. The map, for orientation after the roll

| where | what |
|---|---|
| `docs/20260917-1040-corpus-a-read-that-scanned-every-chunk.md` | `corpus_read` scanned 29M rows; 43 s → 3.86 s measured fix |
| `docs/20260917-1530-what-a-corpus-call-costs.md` | the cost unit: bytes read, not seconds or engine steps |
| `docs/20260917-1510-cache-freshness-ledger-design.md` | the ledger; one table, remedies typed `queue`/`command` |
| `docs/20260917-2042-decisions-recorded-budget-and-ledger.md` | the two-limit budget, and the fingerprint decision |
| `docs/20260917-2059-blocked-two-stage-budget-and-a-gate.md` | why the first attempt was reverted |
| `docs/20260917-1330-decision-brief-mnemonics-budgets-and-proofs.md`, `…-1400-two-decisions-explained-from-the-ground-up.md` | the mnemonic briefing and the ground-up explanations |
| `scripts/probe_cell_budget.py` | the cell-budget probe (real limits; `two_limits_supported` on the page) |
| `scripts/check_guard_nonvacuity.py` | the mutation table; run it first |

---

## 8. Appendix: the reverted implementation, verbatim

**Why this is here.** The hard limit was written once, tested, found not to fire in the
tests, and reverted **before it was committed** — so none of it is in `git log`. It
lives in this appendix and nowhere else. The owner directed this handoff to be kept
current precisely so the next context does not re-derive (or re-break) it. Apply these
pieces, then the tests in §2, then the mutations in §9.

`src/rlm_local/config.py` — beside `cell_timeout`:

```python
    # Two, and both configurable: the soft limit *signals* that a time-consuming
    # operation has started and lets the cell continue, the hard limit stops it
    # (owner, 2026-09-17 — old hardware makes some legitimate operations slow).
    cell_timeout: float = 60.0
    cell_timeout_hard: float = 1200.0
```

`src/rlm_local/templates.py` — beside `CELL_TIMEOUT_ERROR` (both **must** be emitted or
`test_no_dead_templates` fails):

```python
CELL_HARD_TIMEOUT_ERROR = (
    "Error: cell exceeded the hard time limit of {timeout}s and was stopped."
)
CELL_EXTENDED_WARNING = (
    "[rlm] a cell has been running for {elapsed:.0f}s (soft limit {soft:g}s) and is "
    "doing work — {helper} — so it is allowed up to {hard:g}s. This is a "
    "time-consuming operation on this host, not a hang."
)
```

`src/rlm_local/repl.py` — four pieces. **(i)** the result field:

```python
    hard_timeout: bool = False
    """Which limit stopped it: the *soft* one (a cell that was doing nothing) or the
    *hard* one (a cell that was working and needed longer than the ceiling)."""
```

**(ii)** the constructor and the per-cell state (`_cell_activity` is the gate):

```python
    def __init__(self, cell_timeout: float = 60.0, cell_timeout_hard: float = 1200.0,
                 stdout_cap: int = 256 * 1024,
                 restart_after_consecutive_timeouts: int = 2) -> None:
        self._cell_timeout = cell_timeout
        self._cell_timeout_hard = cell_timeout_hard
        #: How many times the cell in flight asked the harness for something. The
        #: second limit is granted only on *demonstrable progress*.
        self._cell_activity = 0
        #: Called with the elapsed seconds when a cell is granted its hard limit.
        self._extension_reporter: Any = None


    def _handle_request(self, msg_type: str, msg: dict) -> None:
        # Any request from the cell is activity: it is what distinguishes a slow cell
        # from a stuck one when the soft time limit is reached.
        self._cell_activity += 1
```

**(iii)** the wait loop in `execute()` — this replaces `deadline = time.monotonic() +
self._cell_timeout` and the `if msg is None: return self._on_timeout(cell_id)`:

```python
            started = time.monotonic()
            soft_deadline = started + self._cell_timeout
            hard_deadline = started + self._cell_timeout_hard
            extended = False
            self._cell_activity = 0
            deadline = soft_deadline

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if (not extended
                            and self._cell_timeout_hard > self._cell_timeout
                            and self._cell_activity > 0):
                        extended = True
                        deadline = hard_deadline
                        self._announce_extension(time.monotonic() - started)
                        continue
                    msg = None
                else:
                    msg = _recv_msg(self._worker_sock, timeout=remaining)

                if msg is None:
                    return self._on_timeout(cell_id, extended=extended,
                                            elapsed=time.monotonic() - started)
```

**(iv)** the timeout and the announcement:

```python
    def _on_timeout(self, cell_id: int, *, extended: bool = False,
                    elapsed: float = 0.0) -> REPLResult:
        self._consecutive_timeouts += 1
        message = (CELL_HARD_TIMEOUT_ERROR.format(timeout=f"{self._cell_timeout_hard:g}")
                   if extended else
                   CELL_TIMEOUT_ERROR.format(timeout=f"{self._cell_timeout:g}"))
        # …then the existing restart logic, with timed_out=True, hard_timeout=extended
        # on every REPLResult it returns.

    def _announce_extension(self, elapsed: float) -> None:
        report = self._extension_reporter
        if report is None:
            return
        try:
            report(elapsed)
        except Exception:  # pragma: no cover - telemetry must never break a cell
            pass
```

`src/rlm_local/root_loop.py` — the sandbox construction gains
`cell_timeout_hard=cfg.cell_timeout_hard` and
`self._repl._extension_reporter = self._report_cell_extension`; `__init__` gains
`warning_sink: Any = None` stored as `self._warning_sink`; and:

```python
    def _report_cell_extension(self, elapsed: float) -> None:
        helper = self._last_corpus_helper or "no corpus helper in flight"
        if self._logger:
            self._logger.log_guardrail(
                getattr(self, "_current_turn", 0), "cell_extended",
                f"elapsed={elapsed:.0f}s soft={self._config.cell_timeout:g}s "
                f"hard={self._config.cell_timeout_hard:g}s last_helper="
                f"{self._last_corpus_helper or 'none'}",
            )
        sink = self._warning_sink
        if sink is None:
            return
        try:
            sink(CELL_EXTENDED_WARNING.format(
                elapsed=elapsed, soft=self._config.cell_timeout,
                hard=self._config.cell_timeout_hard, helper=helper,
            ))
        except Exception:  # pragma: no cover - a warning must never break a run
            pass
```

`_log_cell_timeout(self, turn, block, hard: bool = False)` writes
`limit={"hard" if hard else "soft"}` and the matching budget
(`cell_timeout_hard` when hard, else `cell_timeout`); its call site in the block loop
passes `hard=repl_result.hard_timeout` (that call site is now *after* the block loop,
see §1 — the nudge is set there and sent once).

`src/rlm_local/__init__.py`: `completion(..., warning_sink: Any = None)` →
`RootLoop(..., warning_sink=warning_sink)`.

`src/rlm_local/cli.py`: `--cell-timeout-hard` (`type=float`,
`default=os.environ.get("RLM_CELL_TIMEOUT_HARD")`) beside `--cell-timeout`;
`ask_overrides` gains `if getattr(args, "cell_timeout_hard", None) is not None:
overrides["cell_timeout_hard"] = float(args.cell_timeout_hard)`; and `_cmd_ask` passes
`warning_sink=lambda message: print(message, file=sys.stderr)`.

## 9. Appendix: the tests and mutations for the hard limit

**Sandbox-level tests (the primary proof — drive `REPLSandbox` with a stub worker
socket; no process startup, no root loop):**

1. activity > 0 and the soft deadline passes → `_announce_extension` called once with
   the elapsed seconds, the hard deadline then applies, and the cell completes.
2. activity == 0 → `_on_timeout` with `hard_timeout=False` and the **soft** message.
3. the hard deadline is reached with activity > 0 → `hard_timeout=True` and the
   **hard** message.
4. `cell_timeout_hard == cell_timeout` → no extension at all (today's behaviour kept).

**Loop-level integration (one):** a run whose cell is extended logs one `cell_extended`
guardrail event and one warning through the injected sink. Do **not** try to drive the
extension with a short soft limit through the root loop — see §3, trap 2.

**Mutation entries (old → new, for `scripts/check_guard_nonvacuity.py`):**

| label | target `old` → `new` | its test |
|---|---|---|
| the extension is granted without any progress | `and self._cell_activity > 0):` → `):` | sandbox test 2 |
| the hard limit stops being a limit | `if (not extended` → `if (extended` | sandbox test 3 |
| a single limit stops meaning no extension | `and self._cell_timeout_hard > self._cell_timeout` → `and True` | sandbox test 4 |
| the hard budget is reported as the soft one | `f"limit={'hard' if hard else 'soft'}"` → `f"limit={'soft'}"` | the integration test |
| the cell budget stops being configurable | `overrides["cell_timeout_hard"] = float(args.cell_timeout_hard)` → `pass` | the CLI-flag test |

