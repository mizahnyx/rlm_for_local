# Container routing by content, and the .rar question under the owner's describability rule

**Created:** 2026-09-21 17:51
**Status:** point-in-time. The owner's instruction of 2026-09-21 reframes the container work
around **describability**, and two measurements follow from it: the extension list (done) and
the extensionless group (measured). The `.rar` question is left exactly where the owner asked
for it — at the light of that rule and the roadmap.
**Supersedes nothing.** It continues
`docs/20260921-1719-the-containers-the-harness-cannot-open.md`, whose head row it dissolves.

## The owner's rule, recorded

> *".jar is mostly Java bytecodes, and most of it vendored. The harness must prioritize by
> describability, and later on the roadmap, provide filters that convert some suitable
> formats in either an image rendering or a textual description in some cache. A vendored
> .jar file should probably only be described by a single line like 'shalala Java library,
> version X.Y.Z, presumably part of project fizzbuzz...'."*

Two consequences, and they order the work differently from the byte totals:

1. **The unit of value is "can this be described at all", not "how many bytes does it hold".**
   10.6 GB of `.rar` that turns out to be downloaded media has a describability of nearly
   zero, while 1.2 GB of gzip is a single line per file ("a gzip stream, N bytes") and
   possibly more if it names a file.
2. **Depth of description is a function of what the format affords.** A vendored `.jar`
   earns one metadata line, not a listing of its 38 000 members — which is also the answer to
   why the existing `.jar` work looks disproportionate: it is producing member lists for
   material whose describable content is a version string.

That reframing is now the axis for everything below.

## (1) Done, and it is small: the extension list was narrow, not the engine

`mine.ZIP_CONTAINER_EXTENSIONS` / `TAR_CONTAINER_EXTENSIONS` /
`SINGLE_STREAM_EXTENSIONS` replace the literals at the call site, and the zip list gained
`.aar`, `.war`, `.ear`, `.nupkg`, `.jmod`, `.egg`, `.deb`, `.rpm` — all shapes `zipfile`
already opens. Tests: a zip-shaped archive under each new suffix is listed, and an unknown
suffix is still `no_listing_engine` rather than guessed at. One mutation entry, red.

Two things this does **not** do, stated because the numbers invite the opposite reading:
`.deb` and `.rpm` are *not* plain zips (ar and rpm lead), so they will land as `BadZipFile`
rather than a listing — added to the policy because they are the same family, but they will
need their own readers before they describe anything. And `.jar` remains the largest group by
count; under the owner's rule its listing work is mostly producing detail nobody will read.

## (2) Measured: the extensionless 12 449 are not a missing engine — they are missing routing

Read-only, by magic bytes through the mount, all 12 449 of them:

| magic number says | containers | bytes | an engine the harness already has |
|---|---|---|---|
| **gzip** (`\x1f\x8b`) | **11 377** | 1 035 476 582 | **yes** |
| **zip** (`PK\x03\x04`) | **1 071** | 168 067 881 | **yes** |
| rar (`Rar!\x1a\x07`) | 1 | 203 799 102 | no |
| unreadable | 0 | — | — |

**12 448 of 12 449 are listable by engines that already exist.** The only reason they were
skipped is that `task_list_archive` routes by *extension*, and they have none. This is not a
new dependency, a new parser, or a new format — it is the same class of mistake as the
extension list in (1), one level deeper: the harness trusts a name where it could look at the
bytes.

The one `rar` is worth noticing for a second reason: it is a single container holding
203.8 MB, which is the kind of thing a `.rar` of media looks like.

### What the fix would be, and the one thing it must not become

Route a container with no recognised extension by its magic number. Two implementation
notes that matter more than the diff:

* **It must not become "try zip on everything".** The 200-suffix tail beyond these 12 449 is
  application blobs (`.beam`, `.ogz`, `.fcstd`, `.rgd`, `.mcz`); claiming them all would turn
  19 446 recorded skips into recorded *failures*, which tells a reader less. A magic-number
  table is a claim about a format, and an unlisted magic stays `no_listing_engine`.
* **A gzip member is a single unnamed stream**, so its description is one line ("gzip, N
  bytes") unless the inner name is recoverable — which is exactly the describability axis:
  cheap, honest, and shallow. Whether the 11 377 are tar-inside-gzip (which would make each
  one a real container) is measurable by decompressing a sample, and is the next thing I would
  measure rather than assume.

## (3) `.rar`, at the light of the rule and the roadmap

The numbers: **90 containers, 10 602 867 436 bytes** — the largest byte total in the
unclaimable population by 7×. What the rule does to that:

* **Bytes are not the metric any more.** 10.6 GB of media has a describability near zero;
  10.6 GB of documents is a different proposition. **Nobody has measured which it is**, and
  that is the whole of my answer: the `.rar` decision is currently being asked in the units
  the owner just told us not to use.
* **It is the only option that adds a host dependency.** `zipfile`/`tarfile`/`gzip` are the
  standard library; `.rar` needs `rarfile` plus `unrar`/`bsdtar` on the box. Against a
  standard-library-only corpus pipeline, that is a real cost, and it is the kind of decision
  `AGENTS.md` §1.6 reserves.
* **The roadmap already has the machinery this should use.** RO18 — *"representation before
  reading: render, then describe"* — is the stage for exactly this: a renderer per format
  writing to the derivation cache, then a description. Under that design a `.rar` does not
  need a *listing engine* at all; it needs an **extraction** step feeding the describer, and
  the question becomes "is this format common enough to earn a member of RO18's renderer
  family", which is a scope call with a place to live.

**My recommendation, in the owner's units:** do (2) as a content-routing change (free, 12 448
containers, no dependency), then **measure the `.rar` population's describability** — how many
containers, holding what kinds of member, with what proportion text — and decide `.rar`
*inside RO18* rather than as a `list_archive` engine. If the sample says these are media
archives, the honest answer is a one-line description and no third-party dependency; if it
says they are document archives, RO18's renderer family already has a place to put the
reader.

## Limits

The magic numbers are matched on the first 600 bytes; one `rar` and no `7z` is what the head
found, and a container whose magic sits later than 600 bytes would be counted `unrecognised`
— there were none, but the rule is stated rather than implied. Byte totals are the index's
`size` column, not a read of every file. Counts are one index, one host, read-only.

---

## Addendum: the rar population, and the host can already open it

The owner asked for the full paths of the problem rar files, to judge their contents by
heuristics and context. They are at **`~/rlm-derived/rar-probe/rar-paths.txt`** (136 lines,
mode 0600, directory 0700, beside the corpus — not in the repository; `AGENTS.md` §1.9),
with a machine-readable companion `rar-table.jsonl` carrying size, the tool that listed it
and a five-member sample per tested file.

**The population is 136 containers, 10 990 158 860 bytes** — the 135 named `.rar` in the
index, plus **one extensionless container whose magic bytes say `Rar!\x1a\x07`**, which the
extension-driven routing could never have found. That one is the addendum's own small proof:
content routing reaches a file that no suffix would.

**And the dependency is not needed.** `bsdtar` (libarchive) is already installed at
`/usr/bin/bsdtar`, and it listed **23 of the first 25** rar containers without any new
package. `unrar`, `rar`, `7z` and `rarfile` are all absent; `bsdtar` was there all along.
The two that failed did not fail as rar — `bsdtar` fell through to its tar reader and said
`This does not look like a tar archive`, so they are either a rar feature libarchive does not
implement (RAR5 with certain headers, or an encrypted archive) or a different format whose
magic happens to start with `Rar!`. **That is the one thing left to check before claiming
rar support**, and it is checkable on the same 25.

The shape of the largest members, from the table: one container of **7 450 230 637 bytes with
49 members**, one of 666 955 776, one of 110 160 493. The first is the one that decides the
owner's describability question — 7.45 GB in 49 members is either a small number of enormous
files (media, most likely) or a large archive of many documents. The 49-member count argues
for the former, and the owner now has the path to look.

**So the recommendation from §3 changes in one respect:** the `.rar` question is no longer
"does the host get a third-party dependency". It is "does libarchive's rar reader cover this
population", which needs no install and no owner call to test — only a decision about whether
`.rar` belongs in the container policy at all, and where the describer for it lives (RO18).

---

## Addendum: content routing landed, and the live corpus made the defect look small

The magic-number change is in `task_list_archive`: the extension decides when it names a
format, and when it does not the first bytes do. Verified against the **real** corpus,
read-only, on samples from each measured population:

| population | sampled | what the bytes say |
|---|---|---|
| files named `.rar` | 40 | **14 zip**, 26 no engine (the rar ones) |
| extensionless containers | 40 | **34 gzip**, **5 zip**, **1 tar** |
| `.tgz` | 25 | **25 tar** |
| `.tar.gz` | 20 | **15 tar**, 5 gzip |
| `.gz` | 20 | **15 tar**, 5 gzip |
| `.bz2` | 20 | 20 bzip2 |
| `.xz` | 20 | **16 xz**, 4 tar |

Two things here were not known before this run, and both matter:

**1. The answer to the question the earlier record refused to assume: a `.tgz` is a tar.**
25 of 25. And more usefully, a compressed stream's `ustar` marker lives at offset 257 of the
**decompressed** bytes, so detection has to decompress a bounded prefix rather than read the
compressed head. That is what `_wrapped_tar` does, with the outer codec taken from the
compressed head first, and any decompression failure answering "the stream it is" rather than
guessing.

**2. The defect was larger than measured, and the exposure is in the extensions the harness
already trusted.** Files named `.gz` and `.tar.gz` were described as **one opaque stream**
when 15 of every 20 are tar containers holding many members. So the bug was never confined to
the 12 448 extensionless files or the 52 mis-named zips: the *named* populations were being
under-described too, and content routing corrects them in the same change. That is the
arguable core of it — a container's own bytes are the only honest source for what it is, and
reading them costs one bounded read.

What this does not claim: the routing is verified on samples (40/40/25/20), not on all
19 446 containers; the counts above are what the samples showed, and a full pass is a mining
window's work rather than a probe's.
