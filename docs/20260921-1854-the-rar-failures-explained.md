# The rar failures, explained: 52 of the "rar" files are zip, and 7 real failures remain

**Created:** 2026-09-21 18:54
**Status:** point-in-time. The explanation the owner asked for, measured over the whole
population rather than the two that happened to fail first. Full per-file table and the
failure list are beside the corpus.
**Supersedes nothing.** It continues
`docs/20260921-1751-container-routing-by-content-and-the-rar-question.md`, whose "two
failures to explain" it replaces with seven explained ones — and whose premise it partly
dissolves.

## The population is not what its names say

All 136 rar containers, classified by their magic bytes rather than their extension:

| what the bytes say | containers | bytes | listed by `bsdtar`? |
|---|---|---|---|
| **zip** (`PK\x03\x04…`) — **mis-named `.rar`** | **52** | 57 461 906 | **all 52** |
| rar4 (`Rar!\x1a\x07\x00`) | 76 | 10 714 212 565 | 72 |
| rar5 (`Rar!\x1a\x07\x01\x00`) | 6 | 218 476 197 | 5 |
| *unreadable* (refused by containment) | 2 | 8 192 | 0 |

**Fifty-two files named `.rar` are zip archives.** The harness skipped all 52 — not because
their format is unsupported, but because `.rar` was not on the *zip* engine's extension list.
This is the third instance of the same defect in this line of work, and the sharpest: the
population's name is not its format, and the routing believed the name.

It also means the previous record's headline ("90 containers, 10.6 GB") was measuring the
wrong set: the real rar population is **82 containers / 10 932 688 762 bytes**, and 52 more
files were counted as unreadable rar when they are readable zip.

## The seven failures, one by one

| marker | bytes | `bsdtar` says | what it means |
|---|---|---|---|
| rar4 | 666 955 776 | `Truncated input file (needed 11559656 bytes, only 2510857 available)` | **the file is incomplete** — a truncated or still-downloading archive. Not a reader limitation. |
| rar4 | 5 606 718 | `RAR solid archive support unavailable` | **solid compression**: members are not independently addressable |
| rar4 | 14 769 279 | `RAR solid archive support unavailable` | same |
| rar4 | 1 129 884 | `RAR encryption support unavailable` | password-protected headers |
| rar5 | 203 799 102 | `Encryption is not supported` | password-protected |
| *unreadable* | 4 096 | `Error opening archive: Error reading '…/Ad…'` | **not a file at all** — a path the containment rules refuse |
| *unreadable* | 4 096 | same | same |

So the seven are four distinct causes, and only two of them are "libarchive cannot read this
rar":

1. **Two are not containers** (4 KB, refused by containment — the query's `%.rar` matched a
   path that is not a regular file). These are my probe's artefacts, not the harness's.
2. **One is corrupt** (truncated). No reader fixes it; it should be a recorded failure.
3. **Two are solid archives.** This is a genuine limitation, and it is the interesting one:
   a solid rar compresses members as one stream, so the member list is only recoverable by
   decompressing — which is exactly the cost profile of the extraction cache, not of a
   listing.
4. **Two are encrypted.** A password is not something the harness has, and inventing one is
   off the table; "this archive is encrypted" is the honest one-line description.

**None of the seven fails because the format is rar.** libarchive read 129 of 136, and the
failures are a corrupt file, two solid archives, two encrypted archives, and two probe
artefacts.

## What this changes for the decision

* **`.rar` needs no new dependency** (confirmed twice now), and it needs no owner call on
  tooling. What it needs is the content-routing change, because the extension `.rar` covers
  two different formats.
* **The 52 mis-named zips are the immediate win and the cheapest**: they are 57 MB, they are
  *already* readable by the zip engine, and they are currently unsearchable only because of
  their name. Content-routing fixes them with no rar support at all.
* **Solid and encrypted archives are a describability question, not a reader question.** A
  solid rar's one-line description ("RAR solid, N members, M bytes") is available from the
  header without decompressing; an encrypted one's description *is* "encrypted". Under the
  owner's rule both are correctly described by one line, and neither needs the members
  enumerated to earn that line.
* **The corrupted one belongs in the failure ledger** as a recorded failure, which is what
  the mining queue already does with it.

## Where the files are

Beside the corpus, mode 0600 in a 0700 directory, never in this repository (`AGENTS.md` §1.9):

```
~/rlm-derived/rar-probe/rar-paths.txt      # the 136, one full path per line
~/rlm-derived/rar-probe/rar-table.jsonl    # 25 tested, with member counts and samples
~/rlm-derived/rar-probe/rar-failures.jsonl # every container: marker, size, encrypted_hint, verdict, stderr
~/rlm-derived/rar-probe/rar-failures.txt   # only the ones that did not list (7)
```

## Limits

One host, one index, read-only. `encrypted_hint` is parsed from the RAR4 header flags at the
documented offset and is a *hint* — the two RAR5 files carry `None` there because RAR5's flag
encoding differs and I did not implement it; for those two the verdict is `bsdtar`'s own
message rather than my parse. The solid-archive claim is `bsdtar`'s message, not an
independent parse. The "not a file" pair was identified by size (4 096 bytes) and a
containment refusal, which is consistent with a directory or a special path but was not
confirmed by a `stat`.
