# RO20's real conditions: 98 paths, none indexed, and not the read refusal

**Created:** 2026-09-21 13:22
**Status:** point-in-time. Investigation of RO20 as the ledger recorded it, with the
population measured on the live index, the mechanism read off the code, and a trap in
one of the two candidate fixes that the ledger could not see.
**Supersedes nothing.** It narrows RO20 and corrects two things its row asserts.

## What the row said, and what is true

RO20's ledger row calls the cause exact and offers two candidate fixes: *store `display`
for such a path in a form SQLite accepts and that still round-trips to the same bytes (a
`backslashreplace` rendering, resolved back through the path index which keys on the raw
bytes), or key everything on `source` and derive the display at read time.* Measured
against the live index, three of its assumptions move:

**1. The population is 98 storage names, and the index already handles them — in the
*path* table.** A 333-second pass over all 4 972 609 `entries` rows found **zero** paths
holding a surrogate. That is not luck: `corpus.path_text()` stores a path twice — exactly
as bytes in `raw`, and as surrogate-free text in `path`
(`rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")`). This is the fix
RO20 proposes, already shipped, and already correct for the path index.

**2. The 98 are the paths whose display therefore holds U+FFFD** — 8 directories and 90
files, **113 558 467 declared bytes**. `raw_for(display)` resolves 96 of them to their
exact stored bytes; the other 2 return `None` because `raw_for` refuses an ambiguous
lookup (`LIMIT 2`, `len(rows) != 1`), which is the *documented* behaviour for a path that
appears twice, not a RO20 failure. The mount sees all of them. So those files are
**readable today**: `corpus_stat`, `corpus_read` and a direct path all work.

**3. The wall is on the indexing side, and it is `mine.py`, not the index.** The 98 are
**absent from the text index** — `text_chunks.source` holds none of them, 0 chunks
across all 98 — while `mine_queue` records exactly the failures that predict it:

| task | state | note | count |
|---|---|---|---|
| `index_text` | failed | **`UnicodeEncodeError`** | 11 |
| `list_archive` | failed | `UnicodeEncodeError` | 3 |
| `list_archive` | failed | `BadZipFile` | 21 |
| `list_archive` | failed | `ReadError` | 9 |
| `extract_text` | failed | `BadZipFile` | 3 |

`task_index_text` and `task_extract_text` pass the **raw** `rel` straight into
`display=`, and `text_chunks.display` is TEXT, which SQLite refuses to encode when the
string holds a lone surrogate (confirmed in-process: inserting `'a\udcedb'` raises
`UnicodeEncodeError: surrogates not allowed`). The path index had already solved this with
`path_text`; the mining tasks never called it.

## So RO20 is not the read refusal

The served-address-refused case that `docs/20260921-1014-…` recorded and
`docs/20260921-1047-…` left unreproduced is **not** produced by this population: all 96
unambiguously resolvable names resolve, and the mount opens them. The refusal has some
other producer or is transient; RO20 does not explain it, and it should stop being
described as a candidate for it.

## A trap in the first candidate, which the ledger could not see

The ledger offers a `backslashreplace` rendering as a fix. It is reversible only if the
escape character is escaped too, and even then it produces a display that **differs from
`entries.path`** for the same file — which silently breaks the read path, because
`raw_for(display)` is how a display becomes bytes. A display the path index does not
recognise resolves to `None`, and `handle_read` answers "no such path" for a file that is
sitting right there. That is a worse failure than the one it fixes.

The rendering that keeps the two tables agreeing is the one already in use: **display the
surrogate-free text the way `entries.path` stores it** (`path_text(rel)`), so
`raw_for(display)` keeps resolving and nothing else changes. It has a collision, and the
collision is real: a name with a genuine U+FFFD and a name with an invalid byte render to
the same display (measured: `True`), while their raw bytes differ (`True`). The `raw`
column is what survives that — which is exactly why the index stores both.

## The honest split of the work, and where the owner's call is

* **(a) The writer must not die.** One unstorable item should be a recorded failure, not
  the end of a pass. This is the same discipline RO19's repair pass already learned
  ("must count them rather than die on them"), it needs no owner call, and it is what makes
  the 11 `index_text` + 3 `list_archive` `UnicodeEncodeError` rows a number instead of a
  missing tail.
* **(b) Whether those 90 files get text-indexed at all.** Here the collision matters. With
  `path_text`, a display can name two different files; `raw_for` disambiguates, but only if
  the lookup is allowed to be ambiguous — today it returns `None`, and the read path then
  refuses. So indexing them means either accepting a display that two files share (and
  deciding what `raw_for` should do then), or storing a display that is unique but not
  `entries.path` (and teaching the read path to translate it).

That second half is a contract question about names that are not valid UTF-8 — which
`AGENTS.md` §1.6 puts on the owner: it is a vault/format-shaped decision, and the corpus
holds 113 MB of files behind it. **(a) I can land without asking; (b) I should not decide
alone.**

## Limits

One index, one host, read-only throughout. The 333-second scan measured the `path` column
only; the U+FFFD count (98) is one indexed `LIKE` query, not a second full pass. The
declared byte total is the index's own `size` column, not a measured read. The `raw_for`
`None` count (2 of 98) is attributed to the documented ambiguity rule by reading that
rule, not by inspecting those two rows.
