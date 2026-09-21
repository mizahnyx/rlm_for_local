# The RO13 live run: the aliases worked, and the read is what stopped it

**Created:** 2026-09-21 10:14
**Status:** point-in-time. One live run, one question, one host — and it did **not** produce
the number RO13 was built to measure. It produced a different and more urgent one.
**Supersedes nothing.** It closes the measurement obligation left by
`docs/20260921-0118-mnemonic-aliases-ownership-and-three-corrections.md` §2 ("a live model
has not yet cited through an alias") with a result, and it re-opens a question
`docs/20260917-1040-corpus-a-read-that-scanned-every-chunk.md` recorded as fixed.

## The run

Same question, same index, same model, same budgets as the prompt experiment whose page
raised RO13 (`docs/20260920-2215-the-prompt-experiment-one-question.md`), so the two are
comparable: `Qwen3.5-4B-Abliterated`, `laptop` profile, `--max-turns 6`,
`--cell-timeout 90`, `--cell-timeout-hard 1200`, one question, on a host whose load
average was 1.7–2.1 with no mining window running. The question id and its trajectory
stay beside the corpus (`AGENTS.md` §1.9); the run's output directory is
`~/rlm-derived/questions-ro13-live/` and the rendered page is in
`~/rlm-derived/traces-ro13/`.

## What the alias layer did

**It worked, end to end, from the model's side.** In turn 3 the model called
`corpus_search` with the question's own words and was served **4 hits, all banded
`strong`, each carrying an alias**. In turns 4 and 5 it wrote:

```python
text = corpus_read(hit['alias'])
```

That is the sentence RO13 exists to make possible: the handle went in, and the parent
resolved it. The model also read the record's fields (`hit['alias']`, from a snippet it
described as a record with fields) rather than indexing into a string.

## The measurement

| what | baseline (prompt experiment) | this run |
|---|---|---|
| exception types in cells | **`IndexError=3, ValueError=1`** | **none** |
| cells that raised | 4 | 1 (`NameError` — see below) |
| cell timeouts | 0 | **2, both `limit=hard budget=1200s last_helper=corpus_read`** |
| hits served | 4 `strong` | 4 `strong` |
| cited answering | 1 (exact) | **0** |
| served, never cited | — | **4** |
| refusals (uncited / weak) | 0 | 0 |
| submission | voluntary, turn ~4 | **forced at 6/6** |
| wall clock | — | 4 613 s |

**`citation_repaired` was not written once, and that is not a defect in the event.** The
model never produced a citation, so there was no handle to repair. The number RO13 was
built to produce remains unmeasured — because the run could not reach the step where a
handle is copied.

**The address-surgery failure is gone.** The three `IndexError`s and the `ValueError` of
the baseline do not appear: with a record to call `hit['alias']` on, there is no address
string to slice. That is the specific obstacle RO13 was aimed at, and on this run it is
removed.

**The one `NameError` is the aliasing layer's known cost, seen once.** It is
`name 'hits' is not defined`, in the final turn, after a worker restart — the model tried
to reuse `hits` from an earlier cell, and the restart (triggered by the second consecutive
timeout) had cleared the namespace. So the second timeout cost the run its search results
as well as its cell.

## What actually stopped the run: the read

Both timed-out cells were stopped *while running `corpus_read`*, at the hard 1200-second
limit, with `activity=1` — that is, the harness saw a live request and the cell was
working, not stuck. The model's own narration names the diagnosis and the workaround it
tried, in order: `corpus_read(hit['alias'])`, then `corpus_read(hit['alias'], max_bytes=2000)`,
then `corpus_read(hit['alias'], max_bytes=500)`. All three on the same hit took the full
budget. **A byte cap does not help, because the cost is not in the bytes.**

Independent probes on the same host, immediately afterwards, through the same
`CorpusBridge` the model uses, on the same index and with the corpus mounted read-only:

| measurement | result |
|---|---|
| `handle_search` | **0.22 s in one probe, 93.24 s in another** — the same query, minutes apart |
| `handle_read(<the address search had just served>)` | returned `Error: no such path in the corpus` — instantly, and **for every one of five addresses tried** |
| `index.raw_for(display)` | `None` — the path index does not resolve the display the text index served |
| direct `SELECT COUNT(*) FROM text_chunks WHERE display = ? AND byte_start = ? AND byte_end = ?` | **did not finish inside a 20 s alarm** on a 29 015 791-row table |

So there are two distinct defects here, and the report must keep them apart:

1. **A served address can be refused.** `corpus_search` served an address whose display
   the path index cannot resolve and whose file the mount does not report as existing, so
   `corpus_read` answered "no such path" — a citation the harness itself handed over and
   then could not open. This is the RO11 symptom, on a plain file address, in the *fast*
   direction (an immediate refusal rather than a hang).
2. **A read can cost hundreds of seconds**, which is what the model actually hit. The
   candidate mechanism is the unindexed `display` filter that
   `docs/20260917-1040-…` fixed for the case where exact bytes are known: when `raw_for`
   returns `None`, the fallback filters `text_chunks` on the one column with no index, and
   the direct query above shows that costing more than 20 s on this table.

**Stated carefully:** the mechanism of defect 2 is *consistent with* the model's 1200 s
cells and is the same pathology the earlier record measured; it is **not** proved to be
the cause, because the model's cells were stopped rather than traced at the statement
level. What is proved is that two clients — the model and this probe — each spent a whole
budget inside a read of a served address, and that a direct query on that column does not
return in 20 s. The traces that would settle it are the cell-level ones this probe did not
capture.

**And the load was not the explanation.** `/proc/loadavg` was 1.7–2.1 with no mining lock
and no mining process, so a reader tempted to blame a busy host has the measurement
against it.

## Why this is the next thing, and not RO15

RO14 (the container-member path) is done and unrelated to this: it intercepts
`container!member` references only, and every address in this run was an ordinary file
address. RO15 (the cache freshness ledger) is about *staleness*, and the failures here are
not staleness — the text index and the path index disagree about a name that both claim to
hold, and one query is missing an index.

The honest ordering that falls out: **`corpus_read` must be able to open what
`corpus_search` serves, inside the cell budget.** Until it can, no live run can measure
anything downstream of reading — including the `citation_repaired` number RO13 was built
for, since a model that cannot get a passage back has nothing to cite.

The work, in the order the evidence supports:

1. **Instrument the read path at the statement level on the live index** — the index
   lookup, the fallback, and the mount resolution — so the next run's cell cost is
   attributable rather than inferred (the same treatment RO11 and CL6 got).
2. **Resolve defect 1**: find why a display served by the text index has no `entries` row
   and no file the mount can see. A served-and-unreadable address is the worst citation
   shape there is, and it may be the RO20 family (names that are not valid UTF-8) reaching
   further than the 98 paths the census counted.
3. **Then re-run this question.** The comparison to make is against the table above, not
   against the baseline's exception counts: this run's `IndexError=0` is real but it is not
   yet evidence that the model can *use* the corpus, because it never read one passage.

## Verification state of the RO13 change itself

Unchanged by this run: the feature is unit-verified (43 tests in
`tests/test_mnemonics.py`, 27 wiring tests in `tests/test_corpus_repl.py`, 13 mutation
entries all red). What this run adds is one live observation in its favour — the model
reached for `hit['alias']` unaided, twice, and the three `IndexError`s are gone — and one
live observation against the *run*, which is not about aliases at all.
