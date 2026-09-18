# Two decisions explained from the ground up

**Created:** 2026-09-17 14:00
**Status:** an explanation, written for a reader who has not spent a week inside this
index. It replaces the short version in
`docs/20260917-1330-decision-brief-mnemonics-budgets-and-proofs.md` §3 and §4, which
the owner found obscure — the obscurity was mine, not the subject's.
**Supersedes nothing.**

---

## Part 1 — Why one passage is fast and another is not, and what the options do

### What the pieces are

- **The corpus** is the read-only file tree: 4,972,609 files and directories, 1.08 TB.
- **The path index** is a SQLite table called `entries` with one row per file or
  directory: its exact path bytes, the readable path, its parent, its kind, its size,
  its modification time. About 5 million rows of *metadata* — no file contents.
- **The text index** is a second table, `text_chunks`, with one row per *passage* of
  extractable text: which file it came from, the byte range inside that file, and how
  to get the text back. On this corpus it holds **29,015,791 rows**. It is the thing
  `corpus_search` finds things in.
- **An address** is how a hit names its passage: `some/path.txt#L1204-1360` — the file
  and the byte range. That string is what a model must repeat to read the passage
  again, and what a citation is checked against.

### What "an index" means here, concretely

Imagine a printed book of 29 million lines, sorted arbitrarily, and you want the line
that starts with a particular phrase.

- With **no index**, you read all 29 million lines. That is what happens today for the
  slow case: it takes minutes to hours, and it is why a 60-second cell budget dies.
- With an **index**, the book has a second, much smaller book that is *sorted by that
  phrase*, so you jump straight to the right page. One lookup, milliseconds.

`text_chunks` has an index on its **`source`** column — the exact path *bytes* of the
file a passage came from. It has **no index** on its **`display`** column — the
human-readable path, which is what an address shows you. So:

| you ask for a passage by | what the engine can do | measured |
|---|---|---|
| a path that is a real file | I resolve readable path → exact bytes through the *path* index (one indexed lookup), then filter on the indexed `source` column | **3.86 s** |
| a path that is **not** a real file | there is no bytes row to find, so the only column left to compare is `display`, which is unindexed → full scan of 29,015,791 rows | **>150 s, did not return** |

The second row is the whole problem, and it is narrower than it sounds.

### Which addresses fall into the slow row?

An address names a real file in almost every case — that is the normal shape, and it is
now fast. The exception is an address that names **something inside a container**:

```
photos-archive.zip!holiday/notes.txt#L40-120
└────── the container ────┘└── the member ──┘ └─ byte range ─┘
```

The member is **not a file on the disk**. It exists only after something opens the
container and extracts it. Mining does exactly that (`list_archive`, `extract_text`)
and caches the extracted text, but the *address* still points at the member name, and
the member name is not in the path index — so there are no exact bytes to look up, and
we land in the slow row.

This is where my earlier explanation was obscure, so here is the concrete question that
decides it. When mining extracted that member, it stored a row whose:

- `display` is `photos-archive.zip!holiday/notes.txt` (what the address shows), and
- `source` is *either* the member name again (same problem), *or* the **container's**
  real path `photos-archive.zip` (which **is** a row in the path index, and therefore
  has exact bytes I can look up).

**If `source` is the container's path, the existing indexes already solve it**: split
the address at `!` to get the container, resolve the container to bytes through the
path index, and filter on the indexed `source` column. No new index, a small code
change. **If `source` is the member name, nothing existing helps** and the choice is
between building an index or accepting the slowness.

Nobody has looked. Two read-only queries answer it in seconds:

```sql
-- 1. How much of the index is this shape at all?
SELECT COUNT(*) FROM text_chunks WHERE display LIKE '%!%';
-- 2. For those rows, does `source` name the container (a path in `entries`) or not?
SELECT COUNT(*) FROM text_chunks t
 WHERE t.display LIKE '%!%'
   AND EXISTS (SELECT 1 FROM entries e WHERE e.raw = t.source);
```

The second number is the answer: if it is close to the first, the existing indexes
already cover these addresses and **the decision disappears**. If it is near zero, we
choose between the options below.

### The options, in plain terms

| option | what it literally does | what it costs | when it is right |
|---|---|---|---|
| **Measure first** | run the two queries above; print counts only | minutes, reads only, changes nothing | always — it may make the choice unnecessary, and it cannot make it worse |
| **Index `display`** | build a second sorted book of 29 million lines, sorted by readable path, so every address lookup is a jump instead of a scan | the engine must read all 29M rows once and write the sorted copy: probably **several GB** of disk and **tens of minutes**, on the disk that holds your derived state. It is a one-off; afterwards every read is fast forever. It is a *write* to the derived index (allowed — that is outside the corpus), and it must be run as an operator command, never inside a model cell | if the measurement says members' `source` is the member name, and archive-member citations matter to you |
| **Route through the extraction cache** | for a member address, open the container, extract the member, read the cached text | no new index; only works for members mining already extracted; more machinery on the read path (open, find, verify) | rarely — it is the most code for the narrowest coverage |
| **Leave it, documented** | the trace viewer refuses to read those addresses and prints why; the model's own `corpus_read` stays slow-but-correct | a citation that points inside an archive costs a cell, and may hit the budget | if the queries say this shape is rare in your corpus, and you would rather not spend the disk |

**My recommendation: run the two queries now, and decide afterwards.** I expect the
answer to be either "already solved by the container path" or "this shape is a small
fraction, leave it and document it" — and both are cheaper and more honest than
building a multi-gigabyte index on a guess.

**What would make me recommend the index instead:** query 2 coming back near zero
*and* query 1 coming back large. Then the index is the only real fix, and I would
report the estimated size and time before writing anything.

---

## Part 2 — `corpus_count`, and why the unit should be operations

### What `corpus_count` actually does

The model calls `corpus_count()` to ask "how many files, and how many bytes?" — a
sensible question, and the corpus prompt even tells it to use counts instead of
listing paths. Under the hood the harness runs four separate aggregations over the
5-million-row `entries` table:

1. how many entries in total,
2. how many of them are files,
3. the total size in bytes, and
4. a per-kind breakdown.

Three of those are cheap-ish; **the byte total is not**: adding up a number from every
row means touching every row, and on a loaded machine that is where 60 seconds goes.

### What a "published snapshot" is, and why it is honest

Coverage already works this way. Instead of recomputing "how much of the corpus is
indexed" on every question (which took **972 s** — the CL6 finding, and the reason a
search once died inside a cell), the harness computes it once when you ask it to
(`rlm corpus counters --refresh`, and automatically at the end of every mining window)
and writes the answer into a small `meta` table. A search then reads one row and says:

```
[coverage: 2,881,592 sources indexed (99.9% of the 2,882,822 text files; …)]
```

Two properties make that acceptable rather than a lie: it is **cheap** (one row), and
it **says its age** ("snapshot 41 min ago"). When there is no snapshot at all, it says
**unknown** — never zero.

The same treatment for `corpus_count` means: the harness publishes "4,972,609 entries,
1,084,767,249,184 bytes, as of 10 minutes ago" alongside the coverage numbers, and
`corpus_count()` reads it. The model gets an instant answer to the *common* question,
labelled with its age. A count with a filter (`kind='file'`, `under='some/dir'`) stays
live, because that is a bounded range scan over the index and is genuinely fast.

The cost of that choice: a published number can be out of date, so it must carry its
timestamp, and a stale one must be visibly stale. That is the same rule the mount probe
(`ro=?`) and the coverage line already follow — this project's rule that a check which
cannot see the truth reports "unknown" rather than a confident default.

### What an "operation budget" is, and why your hunch is right

Today's limit is a **clock**: "a cell may run 60 seconds". A second is a property of the
*machine at that instant*. The identical `corpus_count` call is a few seconds on an idle
laptop and over a minute when the box is busy, so a clock budget converts "how much work
did you ask for?" into "how busy was the box?" — which is exactly how a harness limit
got reported to you as a failure of the run.

SQLite can be asked to call back every N internal steps of a query, and to **abort** the
query when a counter is exhausted. That gives a budget in **engine operations**: "this
count may examine at most N steps". Its properties:

- **host-independent** — the same query fails at the same point on any machine, loaded
  or idle;
- **attributable** — the message can say *what it would have scanned*, e.g. "this count
  would examine about 5 million rows; the published snapshot of 4,972,609 entries is
  10 minutes old — use it, or narrow the count with `under=`";
- **actionable** — it turns a mystery timeout into a sentence the model, and you, can
  act on.

The clock is still needed for the things SQLite does not cover: reading files through
the mount, a cell that sleeps, a sub-call. So the design is **both, with different
jobs**: operations as the *unit of work accounting* (the real budget), and the clock as
a last-resort guard against something that is not consuming operations at all.

### The options, in plain terms

| option | what the model experiences | what it costs | when it is right |
|---|---|---|---|
| **Measure the count's real cost first** | nothing changes yet | minutes, read-only: time the four aggregations separately on the real index | first, always — if the byte total is 3 s on your host, the snapshot is unnecessary and we only need the operation budget |
| **Publish aggregates + operation budget** | the common question returns instantly with its age; a filtered count stays live; a huge unfiltered live count is refused with an explanation | one snapshot key, the refresh command already exists, plus the progress-handler plumbing | if the byte total is genuinely slow, i.e. if your finding reproduces as a *cost* problem rather than a *load* problem |
| **Operation budget only** | every count is live but bounded, and sometimes refuses even though the answer has not changed in days | less machinery, no staleness question | if you would rather never show a number that might be out of date |
| **Raise the clock only** | the same failure, later | trivial | never, on its own: it leaves the unit wrong |

**My recommendation: measure first, then publish + operation budget.** The measurement
takes minutes and decides whether we need the snapshot at all; the operation budget is
worth building either way, because it replaces the wrong unit for every future helper,
not just this one.

### What I would report back, either way

- the four aggregation times, separately, on the real index, with the host state;
- whether the count fits in the current 60 s budget on an idle host;
- the estimated size and time of a `display` index, if the archive question needs one.

No writes, no corpus reads beyond index metadata, and only counts and seconds leave the
laptop.
