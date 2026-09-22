# `.rar` verified live: 78 listed, 68 extracted

**2026-09-22, 12:06:04.** The policy the owner called is verified against the corpus, and
the run found a real defect in the first version of it. Aggregates only.

## The first attempt failed, and said so in the wrong words

The first live window (11:41) processed the 133 new `.rar` extraction rows and recorded
**`done 0, skipped 129, failed 4`**. The 129 skips all said `no_text_members`.

A direct probe of five of those files showed what was really happening:

| file | members listed | text-ish members | members read |
|---|---|---|---|
| 1 | 6 859 | 3 215 | **0** |
| 2 | 3 857 | 1 802 | **0** |
| 3 | 41 | 1 | **0** |
| 4 | 42 | 5 | **0** |
| 5 | 1 | 0 | 0 |

Listing worked perfectly; **every extraction failed**. The cause was mine: bsdtar's grammar
is `bsdtar <flags> archive [members]`, and `_libarchive_run` appended the archive **last**,
so `bsdtar -xOf <member> /dev/fd/N` read the *member name* as the archive.

**And the empty result was then reported as `no_text_members`** — a wrong answer shaped
exactly like a fact about the archives. 129 containers were marked "this archive holds no
text" when the truth was "the extraction never ran". That is the failure mode that
`AGENTS.md` §1.8 exists to prevent, caught here because the live run was measured rather
than assumed:

> An empty result from *no candidates* is a fact. An empty result from *every candidate
> failing* is a failure.

Both halves are fixed and guarded: the archive follows the flags, and an all-members-failed
extraction raises instead of returning an empty string. An archive with genuinely no text
members still says `no_text_members`, and that case has its own test.

## The second run

Window at 12:02, `list_archive,extract_text`, 20-minute budget, `--no-coverage-scan`.
The `.rar` extraction rows were re-opened from `no_text_members` and the listing skips from
`no_listing_engine`.

| | before | after |
|---|---|---|
| `list_archive` done | 98 176 | **98 254** (+78) |
| `list_archive` skipped | 123 (`.rar` 81) | **40** (`.7z` 39, `.001` 1) |
| `list_archive` failed | 136 | 141 |
| `extract_text` done | 5 354 | **5 422** (+68) |
| `extract_text` skipped | 517 | 575 |
| `extract_text` failed | 3 | 10 |

- **`.rar` is no longer in the skip set at all.** 78 of the 81 listed; the rest failed for
  reasons the earlier probe already recorded (truncated, encrypted).
- **68 containers produced extractable text**, so a member's words are now reachable — the
  point of the owner's "list + extract" call rather than list-only.
- The window stopped after 137.5 s because the queue was empty, and `archive members
  recorded: 16 757 795` is the published count being quoted rather than recounted.

## What is not yet verified

- **A member has not been read end-to-end through `corpus_read`.** The containers are
  extracted and the cache holds text, so the path should serve, but the read is the next
  measurement and this record does not claim it.
- The 5 new `list_archive` failures and 7 `extract_text` failures have not been classified
  individually; the probe's classes were truncated/encrypted, and nothing here contradicts
  that.

## Cost

The whole verification was 137.5 s of window time plus the two probes. No temporary file was
created at any point: members stream to stdout, and cache writes are atomic with their
temporaries removed on every path, including the failing one.
