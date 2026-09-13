# The corpus, live: first build, first question, first proof (roadmap RO1–RO4)

**Created 2026-09-13 01:20.** What happened when the read-only corpus finally ran
on the machine that holds it: the index built at real scale, a model answered a
corpus question through the harness, and the read-only proof was attempted — and
was wrong the first time. Counts in
`docs/20260912-1610-corpus-through-the-harness.md` describe that document's
commit; the current numbers are here.

---

## 1. The corpus, as measured

One read-only pass over the whole mount (`/srv/corpus` → the `backup/` subtree of
the LUKS container), recorded by the harness's own path index:

| | |
|---|---|
| Entries | 4,972,609 |
| Files | 4,281,585 |
| Directories | 649,536 |
| Symlinks | 41,361 |
| Other | 127 |
| File bytes | 1,084,767,249,184 (~1.01 TiB) |
| **Paths that are not valid UTF-8** | **98** |

The last row is the interesting one. Linux allows any byte but `/` and NUL in a
file name; 98 names in this backup use bytes that are not UTF-8. Python returns
those with surrogate escapes, and SQLite refuses lone surrogates in a TEXT column
outright — so the first build died 75,000 entries in. Every unit test passed
because every test file name was ASCII. The fix and its reasoning are in the
commit that introduced the byte-exact storage; the interface it produces is
described in `docs/20260912-1610-corpus-through-the-harness.md` §2.1.

**Aggregates only.** `AGENTS.md` §1.9 applies to this document as much as to a
session: counts and totals travel, names do not. The full inventory record,
including names, stays on the machine holding the corpus.

---

## 2. The build, at real scale

One streaming walk, no file contents read, index written outside the corpus:

| Build | Wall time | Notes |
|---|---|---|
| First (unoptimized, crashed) | ~10 min to 75,000 entries | died on a non-UTF-8 name; lost everything, because the whole load was one transaction |
| Second (unoptimized, killed) | ~1M entries in 30 min | one transaction, three live secondary indexes, `-wal` past 1 GiB |
| **Third (optimized)** | **95 minutes for 4,972,609 entries** | per-batch commits, secondary indexes rebuilt once at the end |

The optimized build finishes with `complete: 1`, so the index describes the whole
corpus rather than a prefix of it, and the same code can now answer "how far did
it get" after an interrupted run. `--progress-every` reports by entries, not per
5,000-entry batch (that flag was inert until it was fixed).

---

## 3. The first corpus question answered through the harness

```
rlm ask "Use the corpus_count helper and report exactly what it returns: …" \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite \
    --model Qwen3-4B-2507 --endpoint https://127.0.0.1:9010/v1 \
    --profile laptop --max-turns 6
```

- Exit 0, **904 seconds** wall clock, on `lunacode` with the router at
  `127.0.0.1:9010` (a 4B model on a 15 GiB host — the cost is prompt processing,
  not the model's turns).
- The answer, verbatim: *"The corpus contains 4,972,609 entries (4,281,585 files,
  649,536 directories, and 108,476,724,9184 bytes in files)."*
- Entries, files and directories are **exactly** the index's numbers. The byte
  total is the same digits with the thousands separators misplaced by one
  position — a transcription error by the model, not a wrong count, and worth
  noting because this project treats "the number is right" as a claim requiring
  evidence.

The question was chosen to have an aggregate answer on purpose: this is the first
live corpus question, and an answer made of counts can be shown and checked
without quoting a file name. The trajectory JSONL stays on `lunacode`.

---

## 4. The read-only proof, and what was wrong with it

The first proof was a hand-typed

```
find /srv/corpus -xdev -newer "$marker" -print -quit
```

which reported **PROOF FAILED** three seconds after the run: something under the
corpus has an mtime newer than the marker taken before the build. That proof was
wrong in two ways at once.

1. **It was shell work over the corpus**, which the owner has ruled out
   (`AGENTS.md` §1.9: the corpus is reached only through the harness).
2. **It cannot tell a write from a skew.** "Newer than the marker" is satisfied
   exactly as well by a file that was already dated *in the future* — a skewed
   clock, or a tool that wrote future dates — as by a file this run created. The
   scan offers no way to distinguish them, so it must not claim to. This is the
   same failure class as the `blockdev … || echo 0` probe recorded in
   `AGENTS.md` §1.8: a check that substitutes a default for the truth it cannot
   see.

The proof is now `rlm corpus verify` (commit `ccfd64c`): a walk through the same
mount provider, reporting aggregates only — counts, kinds, mtime bounds — and
classifying each newer entry by whether its mtime falls inside the run window
(a breach), after the scan (pre-existing future dates), or before the run (the
marker was taken early). Exit 0 means a complete scan found nothing newer. The
report contains no path, because a proof that has to name a file to make its point
is not one this project can quote.

**Outcome on this corpus: not yet recorded.** The re-run scan is in progress on
`lunacode`; its verdict is written to `~/rlm-derived/proof-result.txt` there, and
the roadmap's RO2 entry in `docs/20260912-1155-roadmap.md` carries the status.
Two possibilities, and they mean different things:

- *skewed dates* — the drive holds files stamped in the future, nothing wrote to
  it, and the read-only guarantee stands (with the corollary that this corpus can
  never be verified by a marker comparison alone: it needs the mtime **and** the
  pre/post manifest of `AGENTS.md` §1.8);
- *writes inside the run window* — a real breach, and the mount is not doing its
  job, which would outrank everything else in this document.

---

## 5. What this establishes, and what it does not

**Establishes.** The harness can index a 4.97M-entry corpus read-only in about
95 minutes without reading a single byte of file content; a model on the same
host can answer an aggregate question against it in 15 minutes; the corpus is
reachable only through the harness; and the proof that a run did not write to it
is itself a harness command that reports aggregates rather than file names.

**Does not establish.** That text extraction, retrieval and wiki synthesis are
feasible at this scale — that is RO6/RO7, and the arithmetic in
`docs/20260912-1155-roadmap.md` §7 is unchanged. It also does not establish that
the *contents* are indexed: this index knows every path, and nothing about what is
inside any file. And it does not yet establish that the drive is unmodified, for
the reason given at the end of §4.
