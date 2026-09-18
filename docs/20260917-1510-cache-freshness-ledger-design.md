# The cache freshness ledger — everything derived, and whether it is current

**Created:** 2026-09-17 15:10
**Status:** design, prompted by the owner's feedback of 2026-09-17 on the two decisions
in `docs/20260917-1400-two-decisions-explained-from-the-ground-up.md`. The two
answers are recorded below; the ledger is the structure they imply. Nothing built yet.
**Supersedes nothing.**

## The owner's two answers, recorded

**On archives: a container is a directory.** *"Despite being functionally different,
compressed files should behave transparently as directories, in that case, the correct
way is either route thru the extraction cache, or if that can't be done, it probably
signals that the compressed file must be marked as needed to be mined."*

Read as a rule with two halves, and it settles RO11's residual without an index:

1. `arch.zip!member.txt` is an address like any other path, and reading it goes
   **through the extraction cache** — the same place a mined member's text already
   lives. No `text_chunks(display)` index, no second lookup route.
2. When the cache **cannot** answer, that is not a read failure to paper over: it is
   **evidence that the container has not been mined**, and the harness's job is to say
   so and get that container onto the work list — not to fall back to a scan of 29M
   rows.

**On counts: the answer must include staleness.** *"Your recommendation looks right,
however it should answer also if the cached count is stale."* Age is not staleness: a
count from two hours ago over a corpus nothing has touched is *current*, and a count
from a minute ago taken before a mining window wrote is *stale*. So a published number
carries both: **when it was computed** and **whether the inputs it summarises have
changed since**.

## Why those two answers are one thing

The owner's closing observation is the architectural insight:

> *"Both questions have to do with the up to date state of different speed up caches
> against the corpus. We will probably need to add some form of 'to do list' of
> operations that would make all the caches actual with respect to the corpus. That
> will also be handy when we have more dynamic mounts like journals or personal
> notebooks."*

An archive member that cannot be read is *one instance* of a derived cache being
behind the corpus. A stale count is *another instance of the same thing*. Today each
is discovered by the operation that trips over it — a slow read, a wrong number, a
wasted mining window — and each has its own remedy in a different document. The ledger
is the single place that answers, for every derived artefact:

| question | why it matters |
|---|---|
| what is this cache derived from? | it names the input whose change makes it stale |
| is it **current**? | not "how old" — whether its inputs changed since it was built |
| what operation would make it current? | the remedy, so "stale" is actionable rather than alarming |
| how much work is that operation? | so the owner can decide *when*, and a dynamic mount's backlog is visible |

## What the ledger covers, and what makes each stale

| cache | derived from | staleness test | the operation that fixes it |
|---|---|---|---|
| path index (`entries`) | a walk of the tree | a walk sees a path/size/mtime the index lacks, or the reverse | `rlm corpus index` (full), or a resumable re-walk |
| classification (`kind`, `encoding`, head hash) | each file's head | a file whose head hash changed, or an unclassified file | `rlm corpus classify` (resumable) |
| text index (`text_chunks`, FTS) | readable text of classified files | a text file with no chunks, or a chunk whose source hash changed | `rlm mine run --tasks extract_text,index_text` |
| coverage snapshot | the text index | chunks added since `published_at` | `rlm corpus counters --refresh`, or a mining window's end |
| counts snapshot (proposed) | the path index | entries added/changed since `published_at` | the same refresh |
| archive listings (`archive_members`) | each container's directory | a container in the path index with no listing | `rlm mine run --tasks list_archive` |
| extraction cache | a container's members | **an address whose cache key is absent** — the owner's case | `rlm mine run --tasks extract_text` for that container |

The last row is the archive answer made general: **a cache miss is a work item.** The
read path reports the miss *as* a miss (`this member is not extracted yet; the
container needs mining`), and the same fact is what the ledger counts.

## The shape that keeps this honest

The project already has the two primitives this needs, and neither is new:

- **A cheap currency marker per input.** The mining queue moves only through the
  worker, and the path index is the only writer of `entries`; so each derived artefact
  can record, at the moment it is built, a compact fingerprint of its inputs: the path
  index's generation counter, the highest `mine_queue` row touched, and the number of
  chunks/sources it saw. "Current" then means *the markers still match*, which costs
  one row to read and needs no scan — the same rule as the coverage snapshot and the
  mount probe.
- **"Unknown" is a legal answer.** A cache with no fingerprint (built before this
  existed, or by a tool that did not record one) reports **unknown**, never "current".
  That is the `AGENTS.md` §1.8 corollary again, and it is what stops the ledger from
  becoming a comfortable source of false assurance.
- **A stale count still gets delivered — with both numbers.** `corpus_count` would
  answer `4,972,609 entries, 1,084,767,249,184 bytes (published 12 min ago; the path
  index has changed since — 3 files added, 1 removed; re-run rlm corpus counters to
  refresh)`. The model gets a usable number and an honest label; the operator gets the
  work item.

## What it buys, beyond the two decisions

- **Dynamic mounts.** A journal or notebook mount changes constantly, so "the cache is
  behind by N items, and here is the cheapest operation that catches it up" is the
  difference between a usable second corpus and a permanently wrong one. The scraper's
  append-only library is the easy case; a journal is the hard one.
- **The wiki stage (RO7).** Synthesis reads caches; knowing which are current tells the
  wiki which of its claims rest on a stale view.
- **Honest answers under staleness.** Today, a search over a changed corpus can miss a
  file that *is* in the corpus and say "no matches". With the ledger, the miss can also
  say "…and the index is 3 files behind the tree".

## What I would build first, and what I would not

1. **The fingerprints and a read-only report.** Each cache records its input markers
   when it is built; a command (`rlm corpus freshness`, or a section of
   `corpus counters`) prints the table above with `current` / `stale (N items)` /
   `unknown` and the operation that would fix it. No writes beyond the markers, no new
   index, no behaviour change to any read.
2. **The archive cache-miss path** — read through the extraction cache, and on a miss
   report "the container needs mining" instead of scanning. This is the owner's rule,
   and it is small.
3. **Not yet: automatic refresh.** A supervisor that chases the corpus would fight the
   owner's machine; the ledger tells *the owner* what is behind, and the owner decides.

## The unit of cost, settled by measurement (2026-09-17)

The measurements in `docs/20260917-1530-what-a-corpus-call-costs.md` decided this, and
the answer folds the budget into the ledger rather than treating it separately:

- **The unit is storage bytes read**, not seconds and not engine steps. The same
  `COUNT(*)` is 231 s cold and 0.69 s warm; `SUM(size)` reads **3.43 GB**; a search
  reads ~10 MB; and two of the worst queries (~0M engine steps over 231 s and 637 s)
  would have sailed through any operation budget, because a query waiting on I/O
  executes no instructions to count.
- **Enforcement is a watchdog, not a progress handler**: a thread that polls
  `/proc/self/io` `read_bytes` and calls SQLite's `interrupt()` past the budget, with
  the wall clock kept only as the outer guard for something stuck.
- **Which is the ledger's business**, because bytes read *is* a cache-state
  measurement: the 231 s cold count is the same query over an un-warmed cache. So a
  cache that is current makes the work cheap, the ledger says which caches are current,
  and the budget counts what a stale one costs.

Corollary worth building early: the byte total that costs 3.43 GB is a **running total
maintained on write**. The path index is the only writer of `entries`, so a counter
updated at write time makes `SUM(size)` free forever — a ledger-shaped fix (keep the
derived value current) for a budget-shaped problem.

## Open questions for the owner

- **Where the ledger is read.** A CLI line, a section of `rlm trace`'s index page, or
  both? (It is an operator's view, so I would start with the CLI and let the console
  reuse it later.)
- **How stale is too stale.** Should a *search* refuse to answer "no matches" when the
  text index is behind, or answer with the caveat? Refusing is honest and annoying;
  the caveat is honest and quiet. I lean to the caveat for search, refusal only for a
  cache that is *unknown*.
- **Whether the mining queue is the ledger's action list or a view over it.** The queue
  already holds the work; the ledger could *derive* its remedies from it rather than
  keep a second list. I believe it should be a view — one source of truth for work.
