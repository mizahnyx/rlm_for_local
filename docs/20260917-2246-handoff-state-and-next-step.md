# Handoff: state of the corpus harness, and the next step (the hard cell limit)

**Created:** 2026-09-17 22:46
**Status:** a resume document, written because the context that did this work is being
rolled. It records what is landed, what is half-done and where, the two traps that cost
time, the verification state *including the debt*, and the exact next action.
**Supersedes nothing.** It summarises and points; the dated records remain the history.

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
- Doc lint: **48 documents clean**; linter self-test 6/6.
- New mutation entries were run **individually** and both went red.
- **Debt, stated plainly: the full mutation table has not been re-run since `ba529fa`.**
  The last full run was 176 guards / 0 problems before this commit. Running
  `python scripts/check_guard_nonvacuity.py` (~25 min) is the first verification task of
  the next session, before anything new lands.
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
