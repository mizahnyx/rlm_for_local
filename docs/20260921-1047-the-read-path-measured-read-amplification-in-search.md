# The read path, measured: the index is 38 GB and each search re-reads every hit's text

**Created:** 2026-09-21 10:47
**Status:** point-in-time. Investigation of the two defects
`docs/20260921-1014-the-ro13-live-run-and-the-read-that-stopped-it.md` recorded. One of
them is now measured with a mechanism; the other is bounded but not reproduced, and is
stated as such.
**Supersedes nothing.** It supplies the mechanism that record left as "consistent with"
rather than proved, and it corrects the shape of the fix it implied.

## The measurement

All on `lunacode`, against the live index and the read-only mount, through the same
`CorpusBridge` the model uses. Timings and counts only; no path, address or passage.

| operation | result |
|---|---|
| `random_chunks(24)` — the harness's own bounded draw | **230.35 s** for 24 draws (~9.6 s each) |
| `open_readonly` + 64 KiB read, 12 real corpus files | min **58.7 ms**, median **87.0 ms**, max **3 431 ms** |
| `corpus_read(<real file address>)`, 6 in a row | **0.10 – 1.19 s**, none refused |
| `handle_search(<one word>)` | **0.3 s, 4.1 s, 13.7 s, 30.3 s, 51.7 s, 60.8 s, 93.2 s, 116.1 s** |
| `EXPLAIN`-free index facts | `text_chunks` has **one** index: `text_chunks_source ON text_chunks(source)`. Nothing on `display` |
| index file size | **38 066 794 496 bytes (38 GB)** |
| host memory | 15 899 MB total, 10 049 MB used, **6 108 MB in buff/cache** |

So a single `corpus_read` of a real file address is **fast** — a tenth of a second to about
a second — and the expensive operation is the **search**.

## Why the search is expensive, from the code

`handle_search` labels every hit with `covers n/m … (strong|partial|weak|none)` and prints
a 300-character snippet. Both need the passage's words, and the FTS table is
**contentless** (`content=''`, by design — the words live once, in the file), so the words
come from `text_index.read(hit, mount=…, cache_root=…)` — a real read of the file for
**every hit**, inside the search call:

```python
for hit in result.hits:
    text = text_index.read(hit, mount=self.mount, cache_root=self.cache_root)  # per hit
    covered, total = term_coverage(text, terms)
    snippet = " ".join(text.split())[:300]
```

With `k=10` that is ten file opens and up to ten full-chunk reads per search. On this host
the per-open cost is a median of 87 ms but a tail of seconds, and the index is 38 GB
against 6 GB of page cache, so a *cold* index turns each of those into random reads on a
large B-tree. That is the whole shape of it: **the cost is per-hit I/O that the search
performs to decorate an answer it has already found.**

`random_chunks`'s 9.6 s per draw is the same family from the other side: a random `id`
probe is one point lookup, but with 38 GB of index and 6 GB of cache the page it lands on
is usually not resident.

## What this explains, and what it does not

**Explained.** The live run's two dead cells were stopped *inside `corpus_read`* at the
1200 s hard limit with `activity=1`. A single read of a real address measures 0.1–1.2 s,
so the cells were not one slow read: they were a search-and-read sequence inside one cell,
or a read of an address whose lookup took one of the second-scale paths, repeated until
the budget was gone. The measured search costs (up to 116 s for one word) are of the right
order to consume a cell that does a search and then reads.

**Also explained — and this is the part the RO13 run could not show.** The same 15 GiB box
holds the corpus, the index, the model and the harness. A 38 GB index against 6 GB of page
cache means the first client to touch a region pays for it, and the second gets it cheap.
That is exactly the variance in the table above: the same query, minutes apart, is 0.3 s
and 116 s. **A live run's wall clock on this host is therefore not a property of the model
or of the harness logic alone.**

**Not explained.** A served address that `corpus_read` refuses with `no such path`. One
early probe saw it for five addresses in a row; later probes through the same bridge read
real addresses successfully and fast, and the statement traces of reads that succeeded show
the path index resolving normally. I could not reproduce the refusal, so I am recording it
as **unreproduced** rather than offering a cause. The candidates that remain, in the order
the evidence ranks them, are: an address whose display is not `entries.path` (the RO20
non-UTF-8 family, whose population the census put at 98 paths); a stale page of the *path*
index during the window when the search held its write lock (the same probe's direct
`SELECT … WHERE display = ?` did not finish in 20 s); or a transient mount condition.

## The fix this points at, and the one it does not

The earlier record suggested instrumenting the read path. That is right but it is not the
first move, because the mechanism is now visible without it: **the search reads passages it
does not need to read.** Both consumers of that text are bounded and both are cheaper from
the index alone:

* the **snippet** needs the first ~300 characters of the chunk, and the chunk's byte range
  is a column — a partial read, or `substr` over one bounded read, not a whole-chunk read;
* the **coverage label** needs the question's content words against the *chunk's* text.
  The words are what the FTS row was built from, and `text_fts` is contentless — but the
  chunk is addressable by `(source, byte_start, byte_end)`, so coverage could be computed
  from a bounded read rather than a full one, or from the FTS match itself for the query
  terms.

The change worth making first is therefore **to bound what a search reads per hit**, by
bytes: the snippet is 300 characters and the label needs a bounded window, so a hit should
cost a bounded read rather than a whole chunk. That is measurable before and after with the
same table above.

**What I would not do** is add an index on `display` to fix the refusal. The refusal is
unreproduced, and the earlier record's own measurement is that a `display` index costs a
multi-GB build on the owner's disk for a case that — with RO14 now in place for container
members — no longer has a known producer. Measure the refusal to a reproduction first.

## Host state, recorded because the numbers are meaningless without it

15.9 GB RAM; `corpus.sqlite` 38 GB; page cache ~6 GB; load average 2.0–3.0; no mining
window running; no `PAUSE-INDEX` file; the corpus mounted read-only at `/srv/corpus`
(4 972 609 entries, 1 084 767 249 184 bytes, per the standing read-only proof). The model
for the live run was `Qwen3.5-4B-Abliterated` on the same box, through the router.

## What this changes about the roadmap

RO15 (cache freshness) is still about staleness and still not this. The next item is a new
one — **bound the per-hit read in `corpus_search`** — and it should be scoped by the
measurement above rather than by the RO11/RO14 line of work, because it is a different
resource: not a missing index but a read amplification the search performs on purpose.
