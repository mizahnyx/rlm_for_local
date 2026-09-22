# The container listing is complete

**2026-09-22, 10:27:36.** `list_archive` reached **pending 0**. Every container in the
corpus that any implemented engine can open has been listed, and the two families that no
engine claims are now a known, counted set rather than a queue.

This is the end of the work the owner asked for on 2026-09-21 ("mining windows until all
containers are listed"), run on the RO22 fix from
`docs/20260922-0855-the-window-that-counted-instead-of-finishing.md`.

## The chain

| window | start | end | result |
|---|---|---|---|
| 1 | 09:16:28 | 09:43:05 | 13 131 → 6 739 pending; 6 392 items in 1 502.0 s |
| 2 | 09:43:05 | 09:56:34 | 6 739 → **0 pending**; exited early because the queue emptied |
| publish | 09:56:34 | 10:27:36 | one deliberate `counters --refresh` |

Two windows and one ~31-minute publication. The closing phase of each window was ~97 s
(measured live, `docs/20260922-0946-the-closing-phase-measured-live.md`), against ~76
minutes for the same phase before the fix — which is the difference between a chain that
finishes in an hour and one that would have spent five hours counting.

## The queue as it stands

`list_archive`: **done 98 176** (65 949 fresh + 32 227 cache hits), **pending 0**,
skipped 123, failed 136.

- **123 skipped** (`no_listing_engine`), by extension: `.rar` **81**, `.7z` **39**,
  `.001` 1, none 1, `.svn-base` 1. This is the set the `.rar` decision covers.
- **136 failed** — an engine claimed the file and refused it: `BadZipFile` 109,
  `ReadError` 24, `UnicodeEncodeError` 3 (the RO20 sibling, still open and still the same
  three rows).

## What the listing produced

- **`archive_members`: 16 757 795 rows.** The number is measured once, deliberately, by the
  same refresh — and `mine status` now **quotes** it, verified live, instead of counting it.
  This is the second half of RO22 and it is the number that used to cost an hour.
- **Coverage published**: 29 015 992 chunks, 2 881 603 sources indexed,
  `text_coverage` 0.9996 (of 2 882 822 text files), 41 588 539 679 indexed bytes,
  5 354 documents extracted, 517 needing OCR.
- **The freshness ledger reads `current`** for all three caches (coverage, archive
  listings, extraction) — RO15's fingerprints recorded by the same publication, which is
  what makes "current" a checked claim rather than a recent one.

## Costs and residuals, measured

- `mine status` takes **118 s**: the member count is quoted now, but the
  `GROUP BY task, state` over `mine_queue` (~3M rows) is untouched — the operator path CL6
  records as counting, and the largest remaining item in a window's closing phase.
- The publication is still ~31 minutes and must be paid in an idle moment; it is now paid
  once per chain rather than once per window.
- Nothing here reads the corpus for writing: the listing only read files and wrote derived
  state beside the corpus.

## Next

The `.rar` decision, whose measured basis is in
`docs/20260922-0946-rar-and-7z-what-is-measured.md`: `.rar` is listable **and extractable**
with the already-installed libarchive (6 of 6 members extracted with real bytes), `.7z` is
listable but not reliably extractable (1 of 4), and the largest recorded archive is 7.45 GB
on a disk that reads single-digit MB/s — so any policy needs a size/solid guard and a
one-line description for those, per the owner's describability rule.
