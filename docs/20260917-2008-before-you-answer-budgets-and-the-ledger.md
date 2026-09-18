# Before you answer: the budget unit, and three questions about the ledger

**Created:** 2026-09-17 20:08
**Status:** a briefing written so the remaining open calls can be answered from facts
rather than from my summaries. It decides nothing.
**Supersedes nothing.** Companion to
`docs/20260917-1510-cache-freshness-ledger-design.md` (the ledger) and
`docs/20260917-1530-what-a-corpus-call-costs.md` (the measurements).

---

## 0. The frame: what is already decided

| decided | by whom | what it commits us to |
|---|---|---|
| A container is a directory: members read through the extraction cache, and a miss means "this container needs mining" | owner, 2026-09-17 | no `display` index; the read path reports misses as work, not as failures |
| A cached count must say both its age **and** whether its inputs changed | owner, 2026-09-17 | a published snapshot carries a currency marker, not just a timestamp |
| Structured hits land before mnemonic aliases | owner, 2026-09-17 | the hit becomes a record with named fields; integer indexing raises |
| Aliases are **per chat session**; harness sees mnemonics, owner sees true addresses | owner, 2026-09-17 | the model's raw output stays in the trajectory; the delivered answer carries addresses |
| The cost unit cannot be seconds, and cannot be engine steps | measured, not decided | see §1 — this narrows the answer but does not give it |

## 1. The facts any answer has to live with

Measured on the real index, 2026-09-17 (aggregates only):

| operation | time | engine steps | bytes read |
|---|---|---|---|
| `COUNT(*)` path index, **cold** | 231.46 s | ~0M | — |
| same, **warm** | 0.69 s | ~0M | 5.3 MB |
| `SUM(size)` (the byte total) | 42.43 s | 23.5M | **3 429 MB** |
| `COUNT(*)` chunk table, 29M rows | 637.62 s | **~0M** | — |
| `corpus_search(k=8)`, warm | 14.43 s | — | 10.4 MB |
| `corpus_search(k=4)`, warm | 0.02 s | — | 0 MB |
| a `LIKE '%!%'` scan over 29M chunk rows | aborted at 732 s | 80M (cap) | — |

Four things follow, and they constrain every option below:

1. **Time measures the machine, not the request.** The identical count is 231 s cold
   and 0.69 s warm.
2. **Engine steps measure the engine, not the work.** Two of the worst operations did
   almost no engine work — they were waiting for pages — so a step budget guards the
   cheap operations and sleeps through the expensive ones.
3. **Bytes read is a property of the request** (given the cache state): 3.43 GB for the
   byte total, 10 MB for a search, 0 MB for a repeat.
4. **The live run died on `corpus_search`**, three times, at 60 s — the verb whose cost
   is eight snippet reads, not SQL.

## 2. Question A — what should a cell's budget count?

A "cell" is one block of model-written Python executed in the REPL worker. Today it may
run for `cell_timeout` seconds (60 s on `laptop`, 120 on `workstation`), and a cell that
exceeds it is killed. Nothing else is budgeted: a cell may read four bytes or four
gigabytes for the same "cost".

**What each candidate unit actually measures, and how it fails:**

| unit | what it is a property of | how it fails | enforcement mechanism |
|---|---|---|---|
| seconds | the machine *and* its cache state | the same call passes idle and fails loaded — how a harness limit got reported as a model failure | trivial (already built) |
| engine steps (SQLite VM ops) | the query plan | ~0 steps during I/O waits, so it misses the expensive calls entirely | `set_progress_handler` |
| **bytes read from storage** | **the request, given cache state** | needs a mechanism to stop a call already in flight | watchdog thread polling `/proc/self/io`, calling `sqlite3.interrupt()` |
| rows examined / files opened | the request | same as bytes, but says nothing about *size*: one row can be 1 KB or 1 GB | same watchdog |

**The sub-questions hidden inside "which unit", because they will come up the first
time it fires:**

- **Per cell or per helper call?** Per cell is what the model experiences ("your cell
  asked for too much"); per helper is what an operator can act on ("`corpus_count`
  wants 3.4 GB"). My answer would be both: the cell has a budget, and each helper
  reports what it spent, so the log attributes the cost.
- **Who sets the number, and can the ledger raise it?** A cold cache legitimately costs
  more bytes than a warm one — so a byte budget is *stable* where a clock is not, but it
  will still fire on the first call after a mining window. That is arguably correct: the
  work is real. It argues for the failure message pointing at the ledger ("this needs
  3.4 GB because the count snapshot is stale; refresh it and the same call reads one
  row").
- **Does the clock survive?** It should, as the outer guard: a cell can consume no bytes
  and no steps and still hang (a sleep, a wedged mount read). Two limits with different
  jobs, and the event must say which one fired — that part is already built.
- **What does the model see?** Today: "that cell was stopped after 60s; ask for less".
  Under a byte budget: "that cell wanted to read 3.4 GB; the same answer is available
  without reading it — call `corpus_count()` with no arguments for the published total,
  or narrow the count with `under=`". The second is a better teaching message, which is
  a real argument for doing the work.

**Cost of each option, honestly:** the watchdog is a small, self-contained mechanism
(one thread, one poll, one interrupt) plus per-helper byte accounting where the harness
itself reads files. The unified "work units" version is the largest, because every
reader — mount, text index, derivation cache — must report what it read. The
"shrink the work" option (fewer hits read per search, snippets cached) is the cheapest
and targets the measured cause, but it narrows what a search returns, and the numbers
above **cannot yet separate "k=4 is cheaper" from "the pages were already resident"**:
that needs a cold-versus-cold comparison, which is one more measurement.

## 3. Question B — where should the ledger be read?

The ledger is a table: for each derived artefact, whether it is current, and what
operation would make it so. The question is only *where a human meets it*.

| option | what it looks like | what it is good for | what it costs |
|---|---|---|---|
| a CLI line (e.g. `rlm corpus freshness`) | text, in a terminal, next to `rlm corpus counters` | scripting, and reading before a mining window | smallest |
| a section of the trace index page | appears beside the run list on the page you already browse | seeing *which run's* evidence was built on a stale view | small, reuses `traceview` |
| the console (`rlm_web`) | a page in the browser | at-a-glance, from another device | a route, auth already exists, and a service to keep running |
| all three | one computation, three renderings | no decision about where to look | the CLI must be first or the others duplicate logic |

What each option implies that is not obvious: the ledger can only be *cheap* if each
cache records a fingerprint of its inputs when it is built. Until those exist, every
report is either expensive (recompute) or dishonest (guess). So this question and the
fingerprints are the same work; choosing "the console" first would put a UI in front of
a table that cannot be filled yet.

## 4. Question C — how should a search behave when the index is behind?

This is the sharpest of the three, because it touches the answer contract. Today a
search over an out-of-date index says `(no text matches in what is indexed)` plus a
coverage line, and the model is then *entitled* to answer "the corpus does not contain
this" — which is the escape hatch the whole citation design leans on, and which the
owner has already judged to work correctly on unanswerable questions.

The danger is precise: **a stale index can make a file that is in the corpus look
absent**, and the model's honest "not found" then becomes a *false* claim about the
corpus. Options:

| option | what the model sees | what could go wrong |
|---|---|---|
| silent (today, without staleness) | "no matches" + coverage | a false "not found" is indistinguishable from a true one |
| **caveat** | "no matches… and the text index is 3 files behind the tree" | the model may ignore the caveat — but the record shows it was told, and the *answer* can carry the caveat too |
| **refuse when behind** | an error naming the staleness and the operation to fix it | the model cannot search at all, so it cannot answer questions that are answerable with what *is* indexed; a mining backlog becomes an outage |
| **refuse only when freshness is unknown** | an error saying "how much of this is indexed cannot be established" | the honest case (no fingerprint) is treated as unusable, while a *known-stale* index is usable with a caveat — which matches the project's rule that a check which cannot see the truth says unknown |

My recommendation is the last row: **caveat when staleness is known, refuse when it is
unknown** — the same distinction the mount probe makes between `ro=1` and `ro=?`.
The reasoning: a known-stale index still supports "here is what I found, and here is
what I have not looked at", whereas an index whose currency cannot be established
supports nothing but a guess, and this project ranks a confident guess below silence.

## 5. Question D — is the mining queue the ledger's action list?

The mining queue today holds units of work (`list_archive`, `extract_text`,
`index_text`, …) with attempts, claims, and a resumable window worker. The tempting
design is: the ledger's "operation that would make it current" column *is* the queue,
so there is one list of outstanding work.

Mostly true, and that is why the question is worth answering rather than assuming:

- **Queue-backed remedies** — "N containers need listing", "M files need text
  extraction" — are literally pending queue items, and the ledger should count them
  rather than keep a parallel list. A second list would drift the first time a window
  runs, which is the same failure mode as any duplicated derived state.
- **Non-queue remedies** exist and cannot be queued: `rlm corpus index` (a full
  re-walk), `rlm corpus counters --refresh` (recompute a snapshot), and
  `rlm corpus classify` (a pass over heads). These are operator commands, sometimes
  hours long, deliberately run by a human.
- So the real design question is: **does the ledger report "work items" (like the
  queue) and "commands you must run" (like `index`) in one table with a type column, or
  does it report queue-backed staleness only and leave the rest to the operator?**

My recommendation: one table, one row per cache, with the remedy named and typed
(`queue` vs `command`), because the owner's stated purpose — "a to do list of
operations that would make all the caches actual", and doing so for journals and
notebooks later — needs both kinds in one place. Naming the type keeps it honest: the
ledger never claims a queue item can do what only a full re-walk can.

## 6. Short version, if you want one

| question | my recommendation | the reason in one line |
|---|---|---|
| budget unit | **bytes read, with the clock kept as the outer guard**, and per-helper cost reported | it is the only unit that is a property of the request; the clock catches what bytes cannot |
| where the ledger is read | **CLI first** (`rlm corpus freshness`), then the trace index page | it cannot be rendered anywhere until fingerprints exist, and the CLI is the smallest honest first step |
| search over a stale index | **caveat when known, refuse when unknown** | a known-stale index still supports an honest partial answer; an unknown one supports only a guess |
| the queue as action list | **one table, remedies typed `queue` or `command`** | one source of truth for work, without pretending a queue item is a re-walk |
