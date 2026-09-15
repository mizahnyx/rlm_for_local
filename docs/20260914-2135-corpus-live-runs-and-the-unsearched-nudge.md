# Three live corpus runs, and the guard the third one earned

**Created:** 2026-09-14 21:35
**Status:** closed — the third run's failure mode is fixed and covered
**Supersedes nothing.** Point-in-time record; corrections belong in a new document.
Related: `docs/20260913-0120-corpus-live-first-build-and-question.md` (the first
build and first question), `docs/20260912-1155-roadmap.md` §7 RO4.

## What this records

Three questions were asked of the real corpus through `rlm ask` on `lunacode`,
after the path index (4,972,609 entries), the Stage 1 classification pass
(4,281,585 files) and an initial text-index window (36,745 sources) were in
place. Two of the three runs failed, and each failure was a defect in the
harness rather than in the model. This is the record of what was observed, what
was changed, and what is still not good enough.

Nothing here was read from the corpus's contents by the author of this document:
the numbers are aggregates the harness printed, and the answer text was read on
the laptop. See `AGENTS.md` §1.9.

## Run 1 — the counting question (pass)

`Qwen3-4B-2507` on the router, `default`-scale verdict SUITABLE. The question
was about the corpus's own size, so it needed the path index only. The run
answered the entry/file/directory counts exactly, in 904 s. Recorded in the
roadmap's RO4 row; no change came out of it.

## Run 2 — a search hit the model could not read (defect)

The question needed the *content* index, so it required `corpus_search`, then a
read of the best hit.

`corpus_search` returned **one formatted string**. The model wrote the code any
caller would write against a list — `len(hits)`, `hits[0]` — and got a character
count (`1233`) and the letter `A`. It then passed `'A'` to `corpus_read`, which
answered, correctly and uselessly, `Error: no such path in the corpus: 'A'`. The
model's next move was to describe its own data as malformed.

Two fixes landed:

1. **`corpus_search` returns a list of hit strings** (`7712f49`). `len(hits)`,
   `hits[0]` and iteration now behave as a caller expects, and each element is
   `<address>  [labels]` followed by a snippet. The parent process forwards the
   list inside the existing JSON frame rather than flattening it, so the shape
   survives the worker boundary.
2. **`corpus_read` accepts an address anywhere in its argument** (`1cc7434`).
   A cell that prints a hit and then re-reads it passes the whole line, labels
   and all; the address is parsed out of it and answered verbatim. An address
   that names no indexed chunk is **refused**, not misread as a file path — the
   distinction is the difference between "not indexed yet" and "a file called
   that", and it is mutation-covered.

`corpus_coverage()` was added in the same wave. Over a partial index, "no
matches" is a weaker fact than the model assumes, so search answers carry their
coverage, and a miss returns a single element carrying both the reason and the
coverage rather than an empty list that reads like proof of absence.

## Run 3 — a submission with nothing behind it (defect, now guarded)

The third run **never called a single corpus helper**. Its cell was, in full,
`print(len(context))` and a slice of it — and `context` in a corpus run is a
placeholder, not the corpus. It then submitted an answer of the form "not
mentioned in the corpus".

This is the failure a prompt cannot fix and a parser can. The harness has a fact
the model's prose does not: **the parent process serves every helper request**.
`REPLSandbox.corpus_calls` (`src/rlm_local/repl.py`) counts them, and the root
loop now refuses a submission from a corpus run whose count is zero, appends
`NUDGE_CORPUS_UNSEARCHED` (`src/rlm_local/templates.py`), and restarts the turn.
The nudge names the shape of the answer it wants — search the question's key
terms, print the hits, read the best one — and tells the model what to do when a
search finds nothing: call `corpus_coverage()` and say so.

Three properties are deliberate:

- **It counts requests, not successes.** A search that finds nothing, a bridge
  that raises, and a mistyped verb all count. Each of those puts a
  harness-written message back into the cell, which is something the model can
  act on; the guard exists for the run that produced *no* corpus request at all,
  where there is nothing to act on.
- **It is bounded.** Nudges are capped by `max_consecutive_nudges` on their own
  counter, so a model that ignores the nudge still terminates. The same
  submission is refused at most that many times.
- **It is inert without a corpus.** A plain `rlm ask` over supplied context is
  never told to search a corpus it does not have.

The counter is incremented at the top of `_corpus_result`, before dispatch, for
the reason above: a call that fails is still a call.

## Verification

| Claim | Evidence |
|---|---|
| Every served corpus verb is counted | `tests/test_corpus_repl.py::TestTheSandboxCountsCorpusCalls` — one test per verb, plus accumulation, plus the failed-call and no-bridge cases |
| An unsearched submission is refused, then accepted after a search | `tests/test_root_loop_integration.py::TestCorpusUnsearchedNudge` — 5 tests through `RootLoop.run()` with a real bridge over a temp corpus |
| The counter and the nudge are both load-bearing | 3 new mutation entries, each observed red: counter removed, nudge never emitted, and nudge emitted with the turn not restarted |
| Nothing else regressed | `pytest tests/` — **1179 passed, 7 skipped, 12 deselected** (7 min) |
| The docs still lint | `scripts/check_docs.py` — 0 problems across 21 documents; `--self-test` 0 problems |

The third mutation is the one worth naming: emitting the nudge while letting the
turn end would look like a fix in the logs and change nothing about the answer.
It was checked.

## The fix, live (the guard works; the answer still cites nothing)

The same question was run again after the guard was deployed to `lunacode`
(`d9a3382`, pulled 22:12). `live-ask.sh` appends to one trajectory, so all four
of its invocations are in the same file; they are labelled `T1`–`T4` in append
order below, which is *not* the narrative's numbering above (the narrative's
"run 3", the unsearched one, is `T3` here — the one entry with no helper call).

| Run | Turns | Elapsed | Helper calls in cells | Addresses printed | Answer | Citations in answer |
|---|---|---|---|---|---|---|
| T1 | 5 | 632 s | `corpus_search` ×2, `corpus_read` ×1 | 0 | 165 chars | 0 |
| T2 | 5 | 572 s | `corpus_search` ×2, `corpus_read` ×1 | 0 | 147 chars | 0 |
| T3 | 2 | 323 s | **none** | 0 | 144 chars | 0 |
| T4 | 7 | 1517 s | `corpus_search` ×2, `corpus_read` ×3 | 7 | 179 chars | **0** |

`T4` is the one this change was for. It searched, read, and printed seven
addresses into its own cell output — and no `corpus_unsearched` guardrail fired,
because it never submitted unsearched. The guard's job is done: the run that
produced nothing to act on (`T3`) is the run that no longer happens.

The remaining defect is one level up and is **not** fixed here: the final answer
is 179 characters and cites none of the seven addresses it printed. The harness
knows the model looked; it cannot from that alone know the answer is *grounded*.
That is a prompt/contract question — whether a corpus answer must carry its
citations — and it is the owner's call, not a guard to invent quietly. It is
recorded as the next step for RO4 rather than patched over.

After `T4` a 12-hour `index_text` window was started on `lunacode`
(22:40, `--tasks index_text`, pause file `~/rlm-derived/PAUSE-INDEX`), which is
the only lever that changes what a search *can* find: the queue went from 36,745
to 37,143 indexed sources in its first two minutes, of 2,882,822 text files.

## One observation about the mutation harness itself

While running the full table for this change, one entry came back **VACUOUS** —
`R23 the migration does not rewrite the key` — and the same entry is **red** in
isolation. A later full run reported a second entry VACUOUS
(`RO2 the digest starts covering directory timestamps`), and repeating that one
in isolation showed a green verdict in roughly one run in four. One other entry
was reported as *target not found* because the worker's scaffold list had gained
two verbs in an earlier commit; its target text is updated and it goes red again.

Both VACUOUS results turned out to have real causes, neither of them in the
table's own logic, and both are now fixed (roadmap `CL5`):

1. **Stale bytecode made a mutation invisible.** CPython validates a cached
   `.pyc` against the source's *size* and its *mtime truncated to whole seconds*.
   The R23 entry shortens `schema_version` to `schema` — exactly 7 bytes, the same
   shortening as the entry immediately before it — so in a full run the second
   entry's test process could import the first entry's bytecode, see none of its
   own mutation, and pass. Each mutation run now compiles into its own
   `PYTHONPYCACHEPREFIX`, and that entry went red 5 times out of 5.
2. **A test whose sensitivity to the mutation was a coin flip.** The RO2 entry
   puts directory mtimes into the corpus digest; the test that caught it compared
   a walk against an index build, which only *usually* sees a directory-timestamp
   difference. It is now asserted directly — move a directory's mtime, require the
   digest not to move — with a control that moves a *file*'s mtime and requires it
   to move. That entry is red 5 times out of 5 too.

The table also confirms a green first result once before reporting it (it prints
`RERUN`), because an instrument that cries wolf stops being read, and a false
alarm about a dead guard costs more than a second test run.

## What is still not good enough

**Coverage.** The honest number: **36,745 sources indexed of 2,882,822 text
files — 1.27%**, 610,522 chunks, cache 684.3 MiB of derived text. The answer the
harness gives today is citation-correct and retrieval-poor: it can read a
passage it finds, and it will not find most passages. Indexing the rest is
~24 hours of windows at the measured ~33 files/s, which is why the mining queue
commits per item and stops on `--for`/`--until`.

**The nudge is a floor, not a standard.** It guarantees the model *looked*. It
cannot guarantee the search terms were good ones, and on a 1.27% index a
well-formed search legitimately finds nothing. The next quality step is not more
nudging but more index.

**Read-only, re-proved.** The three runs read the corpus through the mount
provider, and so did the mining windows that extracted and indexed text. The
digest comparison that proves this for the whole tree
(`docs/20260913-0845-corpus-read-only-proof.md`) predates the 2026-09-14 mining
windows and live runs and has **not** been repeated after them, and the
sample-of-contents hashing obligation from RO2 is still open. Neither is claimed
here as done.

**One operational note.** A permission-probe scratch root from an earlier
session (`rt_<pid>/`, mode 0700) cannot be deleted from inside this environment,
and `git status` reported it as an unreadable directory rather than an untracked
one. It is now in `.gitignore` beside `.tmp_*/`, with the removal instruction
recorded there.
