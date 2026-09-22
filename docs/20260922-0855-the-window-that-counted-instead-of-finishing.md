# The window that counted instead of finishing

**2026-09-22.** A container-listing chain on `lunacode` looked stopped for over an hour.
It was not stopped. It had finished 7 437 items, hit its budget, and then spent the next
~51 minutes counting the index to publish a coverage snapshot, followed by a second
big-table count for a status line. This records the traces, the mechanism, the two fixes
and the one measured constraint that shaped them.

This is a correction of `docs/20260921-2340-mining-windows-partial-and-where-it-stopped.md`,
which recorded the same stall honestly but ranked three candidate causes — an SSH session
ending, the window budget, the lock — and **the leading candidate was wrong**. That
document stands as written; this one supplies what the traces added.

## What was observed

The chain ran `mine run … --task list_archive --for 25m` in a loop, up to six windows,
writing `~/rlm-derived/window-chain.log`. At the point of investigation the log held 13
lines; the last was the 7 437-item progress line, with **no** trailing "pending now:"
line that the wrapper prints after a window exits. Probes over ~15 minutes, none of which
touched the corpus:

| observation | value |
|---|---|
| `mine_queue`, `list_archive` | pending 13 131, done 85 047, skipped 121, failed 136 — unmoved |
| worker process | alive; `state=D`, `wchan=folio_wait_bit_common`, RSS 52 MB, **no child process** |
| its open files | log (at EOF), a zero-size node, and **three on the index** — **no corpus file** |
| I/O | `read_bytes` advancing ~0.2–2.3 MB/s in ~53 read syscalls/s of ~4 KB; `write_bytes` **frozen at 0** after 08:32 |
| lock file | mtime **07:41:13**, i.e. already 48 minutes stale at 08:29; **absent** by 08:42 |
| window arithmetic | window 1 began 07:16; 07:41 is exactly +25 minutes |

## The mechanism

Each step is tied to a trace above, not to a reading of the code.

1. **The item loop ended correctly.** The last heartbeat of the per-item lock is 07:41:13,
   exactly the 25-minute budget — so the budget worked, for items.
2. **The closing coverage scan ran for ~51 minutes.** `run_queue` calls
   `publish_coverage_snapshot(conn)` unconditionally at its end, and that path runs
   `TextIndex.coverage()` — the CL6 query (`COUNT(*)`, `COUNT(DISTINCT source)`, `SUM(…)`
   over 29M chunks). Its own docstring says ~16 minutes on the real index; here it was
   ~51 minutes cold, from 07:41 to 08:32.
3. **The lock was already released when the next count began.** `release_lock` *unlinks*
   in the CLI's `finally` immediately after `run_queue` returns — a predicate I could
   check — so the lock file being **gone** while the process lived proves `run_queue` had
   returned. The final write at 08:32:29 is the snapshot's own.
4. **A second big-table count followed, with no lock held.** The closing report prints
   `format_status(store.status())`, and `status()` called `MineStore.member_count()`:
   `SELECT COUNT(*) FROM archive_members` — read-only, which is exactly why `write_bytes`
   sat at zero while `read_bytes` kept climbing. This phase began at 08:32 and was still
   running when the worker was stopped.
5. **Neither phase is bounded by the budget.** The budget, deadline, pause and `max_items`
   checks all sit at the top of the item loop, so anything `run_queue` does after the loop
   runs past every one of them. That is by construction, not by accident.

## Why it looked like a dead chain

Two independent tells conspired, and both are worth knowing:

- Python's stdout redirected to a file is **block-buffered**. The progress lines appear
  only because they pass `flush=True`; the `stopped: budget …` line, the per-task totals
  and the status block were written into a 4 KB buffer and were invisible while the process
  lived. A finished window that has not yet flushed looks exactly like a window that never
  finished.
- The wrapper prints "pending now:" only after the worker exits, so the log had no line
  that could have distinguished "still counting" from "died".

A **torn log plus an unmoving queue** is the signature of both a hung worker and a worker
counting in silence. Distinguishing them needed `/proc`, not the log.

## What changed

`RO22` in the roadmap. Five changes, each with a test that fails without it:

1. **A chained window can decline the scan.** `run_queue(coverage_scan=False)` and
   `mine run --no-coverage-scan`. A chain now pays the ~16-minute (warm) scan **once**, at
   the end, via `rlm corpus counters --refresh`, instead of once per window — which is what
   turned 44 minutes of remaining work into a projected five hours of counting.
2. **No status line counts 30M rows.** `status()` quotes a *published* member count
   (`meta['archive_members_published']`, written by the deliberate path) and otherwise says
   **unknown** and names the remedy. This closes the item CL6 left open — "`mine status`
   still counts (operator-only path)" — which is the trap that held this window open.
3. **The closing scan heartbeats.** `_pulse` installs `sqlite3.set_progress_handler` around
   the expensive scan and refreshes the lock's mtime from *inside* the statement. The lock
   heartbeats per item, so the long non-item phase was precisely the window in which a live
   worker looked dead and the next worker could take its lock.
4. **The report runs inside the lock.** The CLI's closing prints moved into the `try` whose
   `finally` releases the lock, so a window cannot look free while it is still reporting.
5. **A measured constraint, not a preference.** A **bare `SELECT COUNT(*)` steps no VM
   instructions at all** — SQLite answers it from the b-tree — so a progress-handler
   heartbeat is structurally blind to it. Measured: 0 pulses for `COUNT(*)` against 600 for
   `COUNT(x)`/`SUM(x)`/`COUNT(DISTINCT x)` over 200 000 rows
   (`scripts/probe_count_forms.py`). `MEMBER_COUNT_SQL` therefore counts a **column**:
   `COUNT(member)` is exactly the row count (`member` is `NOT NULL`) and reads the same
   covering index, but it steps the VM, so the heartbeat can cover it.

## Verification

- 7 new guards in `tests/rlm_kernel/test_mine.py` and `tests/test_cli_mine.py`, and 7 new
  mutation entries in `scripts/check_guard_nonvacuity.py`, all red as required.
- **The mutation table caught a vacuous test of my own.** My "the pulse must not outlive the
  statement it guards" assertion could not see a leaked handler, because the 30-second rate
  limit swallowed every later call — the handler was still installed and the test passed
  anyway. Fixed by running that one assertion with `min_seconds=0.0`. Third time this
  project has shipped a vacuous test; second time the table caught it.
- Fast suite and doc lint: see the commit for the numbers.

## What is still true, and what is not proved

- **The remaining 13 131 containers were not listed** when this document was written. The
  chain was stopped deliberately at the cheapest possible point — the read-only report
  phase, with nothing in flight and every finished item already committed.
- **The coverage scan is not cheaper.** It is still ~16 minutes warm and ~51 cold; it is
  now paid once per chain rather than once per window. Making it genuinely cheap
  (maintained counters, WAL/`mmap_size`, or a warm pass first) stays open and is a
  candidate, not a plan.
- **The concurrency hazard was not observed, only inferred.** No second worker was
  started, so nothing here is evidence that a takeover would have corrupted anything. What
  is verified is the code path and the missing lock file; what is inferred is the takeover.
- **`find_members` still scans `archive_members`** (`member LIKE '%…%'`). That is the
  "archives are directories" name search off the window path, and it is left open.

## Next

Re-run the listing chain with `--no-coverage-scan`, publish coverage once when the queue
empties, and then put the `.rar` decision to the owner with the `bsdtar` evidence from
`docs/20260921-2340-mining-windows-partial-and-where-it-stopped.md`.
