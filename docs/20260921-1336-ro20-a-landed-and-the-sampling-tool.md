# RO20 (a) landed, and the sampling tool's first reading

**Created:** 2026-09-21 13:36
**Status:** point-in-time. The writer half of RO20 is fixed and verified; the sampling tool
the owner asked for exists and has been run once against the live population. What is left
is the owner's call, and this record is the input to it.
**Supersedes nothing.** It continues `docs/20260921-1322-ro20-real-conditions.md`.

## (a) The writer no longer dies

`mine._index_display(rel)` renders a chunk's display the way `CorpusIndex` already stores a
path — exact bytes in `raw`, surrogate-free text in `path` — so `text_chunks.display` holds
the same string `entries.path` holds and `raw_for` keeps resolving it. Both writers
(`task_index_text`, and the immediate index inside `task_extract_text`) turn an
`UnicodeEncodeError` or `sqlite3.Error` into a recorded per-item FAILED outcome rather than
ending the pass.

The second half is the part that matters for a long queue: RO19's repair pass already
learned that one unstorable item must be counted rather than fatal, and RO20's
`index_text 11 failed` / `list_archive 3 failed` rows are what the old behaviour left —
a truncated run with no tail after it.

**Why not `backslashreplace`**, which the ledger offered as the other candidate: it is
storable but it does **not** match `entries.path`, and `read_address` resolves display →
bytes through exactly that lookup. A display the path index does not recognise resolves to
`None`, so the read path would answer "no such path" for a file sitting right there — worse
than the defect it fixes.

12 tests (6 writer, 6 tool), 3 mutation entries all red.

## The tool, and what it says about the population

`scripts/sample_encoding_wall.py`. It prints only aggregates; it refuses an output
directory inside the corpus root; the samples and the per-file table are written 0600 in a
0700 directory where the corpus is. MIME types are `mimetypes.guess_type` over the name and
every line of output says they are guesses.

**The population is 98 paths, 113 558 467 declared bytes — and 97% of those bytes are 29
`.mp3` and 1 `.mpg`:**

| suffix | files | bytes | guessed MIME |
|---|---|---|---|
| `.mp3` | 29 | 97 224 478 | `audio/mpeg` |
| `.mpg` | 1 | 15 232 792 | `video/mpeg` |
| `.gif` | 3 | 459 817 | `image/gif` |
| `.jpg` | 13 | 276 230 | `image/jpeg` |
| `.htm` | 2 | 206 964 | `text/html` |
| *(no extension)* | 38 | 108 707 | `unknown` |
| `.js` | 4 | 33 502 | `text/javascript` |
| `.xls` | 1 | 7 680 | `application/vnd.ms-excel` |
| `.png` | 2 | 7 013 | `image/png` |
| `.log` | 3 | 681 | — |
| `.ini` | 1 | 532 | — |
| `.csv` | 1 | 71 | `text/csv` |

So the split is clean and small: **text-plausible 50 files / 358 137 bytes**, **media 48
files / 113 200 330 bytes**, nothing in between. The media half is what a text index has
nothing to do with, and it is already excluded from text by kind; the `.htm`, `.js`,
`.xls`, `.log`, `.ini` and `.csv` half is less than half a megabyte in total.

The 38 extensionless files are the only genuine unknown (108 707 bytes, average ~2.8 KB),
and reading them is what the samples are for:

```
~/rlm-derived/encoding-wall/samples.jsonl     # the largest and smallest 5, 400 bytes each
~/rlm-derived/encoding-wall/summary.json      # the table above, machine-readable
```

Run once with `--n 10`: 10 files sampled, **2 000 bytes read in total** — a bounded preview,
not a copy of anything.

## The call, now that the numbers are in

It is a smaller question than it looked. Indexing these files means: two files can present
one address (a name with a genuine U+FFFD and a name with an invalid byte render the same),
and the alternative rendering that would avoid that breaks the read path instead.

Given the composition, the plausible answers are:

1. **Index them all** — 358 KB of text, one shared display per name, and the collision is
   the price. Simplest, and the collision has never been observed to occur (it needs two
   files whose names differ only by "real U+FFFD" vs "one invalid byte").
2. **Index only the text-plausible ones** — the same, minus 48 media files a text index has
   no business in. Costs a kind/extension predicate in the mining plan.
3. **Leave them out** and record why — the smallest change, at the cost of the text half
   being permanently unsearchable while `corpus_find` happily lists it.

The samples exist to settle whether the 38 extensionless files change any of that.
