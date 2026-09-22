# Mining windows for the container queue: what completed, and where it stopped

**Created:** 2026-09-21 23:40
**Status:** point-in-time and **partial**. The first window is a completed measurement; the
chained windows that were meant to finish the queue stopped mid-run, and this records that
plainly rather than leaving the impression of a finished job.
**Supersedes nothing.** It continues `docs/20260921-2250-the-content-routing-window.md`.

## What the owner asked for

Mining windows until every container is listed, then the `.rar` call.

## What completed and is measured

The **first window** (30 minutes, `list_archive`) ran to completion and is recorded in
`docs/20260921-2250-the-content-routing-window.md`: **22 952 containers processed, 22 700
done**; of the 19 446 the old routing had given up on, **12 987 now list (99.2%, 99.97% of
those that settled)**, with **one** honest `no_listing_engine` left and 120 in the whole
queue. That measurement stands.

The **second run** was a chain of up to six 25-minute windows that stops when nothing is
pending. Two windows' worth of work is visible in its log — a **7 437-item window** (done
7 432, skipped 1, failed 4, 4 629 cache hits) — but the chain then **stopped advancing**: its
log's last line is cut off mid-write, the "pending now" line after the window never appeared,
and repeated checks over more than 50 minutes showed the identical final line. Queue pending
went 20 568 → 15 455 → 13 131 and did not move again.

**What that means, stated without a mechanism I have not proved:** the chain did not finish
the queue, and I did not diagnose why. The candidates, in the order the evidence ranks them,
are an SSH session end taking the parent shell (the detached chain was launched through one),
the window's own 25-minute budget interacting with the wrapper, or a lock the next window
could not take. The `list_archive` queue is **left with roughly 13 000 pending containers**.

**This is the same class of failure the project already has a rule for** (`AGENTS.md` §1.5):
something unexpected happened, and the response is to produce the traces and let the owner
reach the verdict rather than to tell a story from the code. The traces are
`~/rlm-derived/window-chain.log` and the queue's own state; the verdict is not mine to give.

## What is verified about the containers themselves

Content routing works on the real corpus at scale, measured twice: on samples
(`docs/20260921-1751-…`) and corpus-wide by the first window. The containers it rescued are
listed, their members are in `archive_members` (spot-checked: a listed extensionless
container has 8 member rows, after a probe of mine wrongly reported 0 — my query's error,
recorded in the window document).

## To finish the job, and to be sure it stays finished

1. **Re-run the chain from a shell that outlives the SSH session** — `systemd-run --user` or
   `setsid` rather than `nohup` through an interactive command, since that is a candidate for
   what stopped it.
2. **Check the log's last line before trusting a "finished" claim** — a torn line is the tell,
   and it is cheap to look for.
3. Then the `.rar` call, which is unchanged and still the owner's: libarchive reads 23 of the
   first 25 (no new dependency), and the real failures are four distinct causes — corrupt, two
   solid, two encrypted — none of which is the format.

## The state on disk, for whoever picks this up

* `~/rlm-derived/window-chain.log` — the stopped chain, torn last line
* `~/rlm-derived/window-content-routing.log` — the completed first window
* `~/rlm-derived/corpus.sqlite` — the queue, ~13 000 `list_archive` pending
* the repository is at `e94871b` on `lunacode`, clean
