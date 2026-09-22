# `.rar` and `.7z`: what is measured

**2026-09-22.** The measured basis for the owner's decision on whether the two remaining
container families join the mining policy. Aggregates only: no path, member name or message
text is in this file, and the per-file records stay beside the corpus in
`~/rlm-derived/rar-probe/` (0600).

This follows `docs/20260921-1854-the-rar-failures-explained.md`, which explained the earlier
failures. What is new here is **extraction**, and the queue counts that say how much work
each decision covers.

## What is actually unlistable

`list_archive` terminal state, by extension (`no_listing_engine`, 121 rows):

| extension | rows |
|---|---|
| `.rar` | 80 |
| `.7z` | 38 |
| `.001`, `(none)`, `.svn-base` | 1 each |

Separately, 136 rows are `failed`, and they are a different question — not "no engine" but
"the engine refused":

| extension | rows | note |
|---|---|---|
| `.apk` | 67 | `BadZipFile` — an `.apk` is a zip; these are not valid zips |
| `.bin` | 18 | `ReadError` |
| `.zip` | 15 | `BadZipFile` |
| `.tgz` | 11 | `ReadError`, and 3 of the corpus's `UnicodeEncodeError` siblings |
| `.unitypackage` | 6 | `BadZipFile` |
| `.gz` `.jar` `(none)` `.part0` `.bz2` `.obb` `.svn-base` | 21 | mixed |

## The 136 `.rar` paths, re-read

The recorded verdicts (which the earlier probes wrote) say **129 listed, 7 failed**. The
content markers split them by what they really are:

| marker | rows | meaning |
|---|---|---|
| RAR4 | 76 | genuine, old format |
| RAR5 | 6 | genuine, current format |
| `not-rar:504b0304…` | 52 | **ZIP magic** — zips wearing a `.rar` name, already routed by content |
| `unreadable:ReadOnlyViolation` | 2 | the mount itself refused the read |

So the genuine-RAR population in that set is **82**, and the 52 mis-named zips need no new
code (content routing has handled them since 2026-09-21). Of the 7 failures, the messages
mention *truncated* once and *solid* twice, one carries an encryption hint, and three are
undetermined — the recorded `verdict` field is the authoritative tally, not a keyword scan.

Sizes over the 136: min 2 467 B, median 5 259 555 B, **max 7 450 230 637 B** (7.45 GB).

## libarchive, measured on the live files

`bsdtar` is libarchive, already installed. No `unrar`, `rar`, `7z`, `7za` or `unar` is.

| family | sampled | listed | extraction attempts | extracted |
|---|---|---|---|---|
| `.rar` (25 from the recorded table) | 25 | **23** | 3 | **3** |
| `.rar` (6 genuine-RAR candidates) | 6 | 6 | 6 | **6** |
| `.7z` | 12 | 11 | 4 | **1** |

The `.rar` extraction results are byte totals of what actually landed on disk, not return
codes: 65 042 … 12 682 383 bytes across the six. The `.7z` failures were one encrypted and
two unclassified (libarchive's 7z reader lists more than it decompresses).

Listing 23 of 25 reproduces the earlier probe's number exactly, and the two misses are one
truncated and one encrypted file.

## What this supports

- **`.rar` can be a first-class container**: 80 files in the skip set, listable *and*
  extractable with a tool that is already installed, so members would be readable through
  the RO14 extraction cache and not merely named.
- **`.7z` is at best list-only** with libarchive (1 of 4 extracted), so its members could be
  found by name but their text not read; a real 7z implementation would be a new dependency.
- Either way a policy needs a **size and solid guard**: the largest of these is 7.45 GB on a
  disk that reads at single-digit MB/s, which is exactly the shape that eats a window. Under
  the owner's describability rule, an archive like that earns **one line** — "RAR solid,
  N members, M bytes" — rather than an enumeration.
- **Two files the mount refuses** (`ReadOnlyViolation`) are a separate, smaller finding and
  are not explained by this.

The decision itself is the owner's, and is recorded in the roadmap ledger when it is made.
