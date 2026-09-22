# The closing phase, measured live

**2026-09-22.** The RO22 fix was verified against the real corpus rather than only in tests.
This is the live half of `docs/20260922-0855-the-window-that-counted-instead-of-finishing.md`
and it changes one number in it: the closing phase is not free, it is **97 seconds**.

## The run

`mine run --task list_archive --for 25m --no-coverage-scan`, window 1 of up to 8, launched
09:16:28 with 13 131 pending. A 30-second trace (`window-phase-timing.txt`, beside the
corpus) recorded the last log line and its mtime throughout, so the phases are measured and
not inferred from where the process happened to be when someone looked.

| moment | evidence |
|---|---|
| 09:16:28 | window 1 started, 13 131 pending |
| 09:41:28 | budget fires (`stopped: budget after 1,502.0s`) after 6 392 items |
| 09:41:32 | `stopped:`, the totals and `by task:` are in the log |
| 09:43:05 | `window 1 exited rc=0 … pending now: 6739` — **closing phase 97 s** |
| 09:43:05 | window 2 started immediately |

The item loop honoured its budget to the second (1 502.0 s against 1 500 s, the overrun
being the item in flight), which is what a budget checked between items should do.

## What the closing phase cost, and what it consists of

97 seconds, not 76 minutes. Of that, ~90 s is `format_status(store.status())`'s
`GROUP BY task, state` over `mine_queue` (~3M rows for all tasks) — the operator path CL6
recorded as still counting, and the largest remaining item in the closing phase. It is two
orders of magnitude below the coverage scan it replaced and needs no change now; it is
recorded here so the next person does not have to measure it again.

The status block printed, verbatim from the log:

```
archive members recorded: unknown (counting them scans the whole table; publish one with
`rlm corpus counters --refresh`)
```

That is the `AGENTS.md` §1.8 corollary doing its job: the number nobody has measured says
**unknown**
and names the command that would measure it, instead of spending an hour producing it on a
window's closing path.

## The queue as this ran

- `list_archive`: **done 92 000** (85 047 before the chain), pending 6 178 at 09:46,
  skipped 121, failed 136.
- `index_text`: done 2 881 603, skipped 1 219, **no failures** — RO20's fix holding.
- `extract_text`: done 5 354, failed 3, skipped 517.
- Throughput in this window: ~4.3 items/s (6 392 in 1 502 s), against ~5/s in the earlier
  content-routing window; the remainder skews heavier and cache-misses more (3 033 cache
  hits of 6 392).

## Two probes of my own that were wrong, corrected here

Both are the same mistake in different clothes — a probe reporting a number it could not
actually see (`AGENTS.md` §1.8's corollary), and both were caught by cross-checking against a
recorded measurement rather than by reading the code.

1. **Relative paths.** A `.rar` probe ran `bsdtar` on the recorded paths as stored and
   classified every failure as "other": the paths are relative to the corpus root, so
   libarchive was answering "no such file" and the probe read its own mistake as a format
   verdict — 0 of 25 listed, reported as a finding. Resolved against the root, the same 25
   files give **23 of 25**, exactly what the earlier probe had recorded.
2. **An extraction check that could not succeed.** Asking whether libarchive can extract a
   RAR member, the probe ran `bsdtar -xf archive -C dir member` and then looked for a file
   *it had named itself*, while the member lands at its own path — so it reported **0 of 3
   extracted**. Measured on what actually lands: **6 of 6**, with real byte totals.

## Still open

- The final window's `counters --refresh` publishes coverage and the member count once;
  until it runs, both are `unknown` by design.
- `status()`'s `GROUP BY` (~90 s here) is unchanged, as is `find_members`' scan.
- The `.rar` and `.7z` decision material this run produced is in
  `docs/20260922-0946-rar-and-7z-what-is-measured.md`.
