# A read that scanned every chunk in the index

**Created:** 2026-09-17 10:40
**Status:** point-in-time. The defect is measured and fixed; the fix is measured. One
residual case is deliberately left unread by the viewer and recorded as open.
**Supersedes nothing.** Found while building the trace viewer
(`docs/20260917-0915-corpus-traces-a-human-can-audit.md`), by running it on the real
corpus rather than on a fixture.

## The measurement

A probe on `lunacode`, against the complete text index (2 881 592 sources,
29 015 791 chunks), through `CorpusBridge` on the read-only mount:

| operation | before | after |
|---|---|---|
| open the bridge | 0.19 s | 0.05 s |
| `corpus_search(<one query>)` | **43.14 s** | — |
| `handle_read(<one address>)` | **did not return in 150 s** | **3.86 s** (1 405 chars) |

The address is a real one, cited by a real run: a file of 189 462 bytes, a chunk span
of 1 292 bytes, and the file exists, so nothing about it is unusual. The REPL cell
limit is 120 s.

## The cause

`TextIndex.find_chunk` looked the chunk up with

```sql
SELECT … FROM text_chunks WHERE display = ? AND byte_start = ? AND byte_end = ? LIMIT 1
```

`text_chunks` has indexes on `source` (`text_chunks_source`) and nothing on
`display`. So one passage read scanned **29 015 791 rows**. `corpus_search` never
calls `find_chunk` — it already holds the row from the FTS match — which is exactly
why search worked (43 s) and the read did not.

This is the CL6 family again (`docs/20260915-2305-corpus-search-cannot-count-its-own-index.md`):
a per-item operation paying a whole-table price. CL6 was a `COUNT(*)` on the search
path; this is a `SELECT` on the read path, and it hid longer because a read is
supposed to be the cheap operation.

## Why it matters more than a slow command

`corpus_read` is not a convenience. It is how a citation stops being a claim and
becomes a quote, and it is the helper the whole provenance design assumes the model
will use: the system prompt says *"quote the ones you read, not the ones you
searched for"*, and the served-address rule makes a citation checkable. At full
scale **the model could search but not open what it found** — inside the cell budget,
`corpus_read` could not return.

Three earlier observations are consistent with this and are now explained rather than
merely recorded:

- the four failing cells (cell timeouts and worker restarts) in the run that died
  after two hours;
- runs that cite an address they never read — the read was the thing that failed;
- the search path's own 43 s, which is the FTS query plus one read per hit, and which
  is what makes an 8-turn run cost 45 minutes.

It is stated carefully: this is *consistent with* those observations, and it is not
proof that the read was the only cause. The mechanism is measured; its share of the
blame is inferred.

## The fix

`find_chunk(address, raw_source=…)` filters on the **indexed** `source` column when
the exact path bytes are known, and `CorpusBridge._read_address` resolves them with
`CorpusIndex.raw_for(display)`, which is itself one indexed lookup
(`entries_path`). The display filter remains as the fallback for an address that
names no file on disk.

Tests are trace-based, in the style of the CL6 guard: they assert the read's `WHERE`
clause is on `source` and never on `display` (the trace callback renders bound
parameters inline, so the assertion is on the clause rather than on a placeholder),
plus one that the fallback still resolves a container-member address. Two mutation
entries, both red.

## The residual, and what the viewer does about it

A container member (`arch.zip!member.txt`) has no `entries` row, so there are no
exact bytes to resolve and the lookup still falls back to the unindexed column —
**over 150 s for one address**. The trace viewer therefore *refuses to read* such an
address and prints why on the page, because a renderer that can hang for hours on
one address is worse than one that says "not embedded". The corpus helper keeps the
slow-but-correct behaviour, since a model asking for a passage should get one.

The real fix for that case is to index the display column, or to resolve members
through the archive cache — measured costs and a decision are roadmap **RO11**.

## Limits

- One measurement per state, one address, one host. The 3.86 s contains the chunk
  read of a 189 KB file; a larger file would be slower, and the shape of that curve
  is not measured.
- The trace-based tests prove *which column is filtered*, not that SQLite uses the
  index; the 3.86 s is the evidence for the outcome.
- A second small observation, from writing the tests: `TextIndex.add_text` skipped a
  second insert for a source it already held (it returned 0 chunks). That is D5 seen
  again from another angle — an edited file keeps stale text — and it is not new here.
