# The containers the harness cannot open: 19 446 of them, and 46% of all containers

**Created:** 2026-09-21 17:19
**Status:** point-in-time. The measurement the archive-engines decision needs, asked for by
the owner alongside RO15. Aggregates only — no path, no name, no member.
**Supersedes nothing.** It supplies the population behind the `list_archive` row of
`docs/20260917-1510-cache-freshness-ledger-design.md` and the residual RO14 left.

## The state of container mining on the live index

| `list_archive` state | note | containers |
|---|---|---|
| done | — | 35 356 |
| done | cache | 19 559 |
| pending | — | **24 041** |
| **skipped** | **`no_listing_engine`** | **19 446** |
| failed | `BadZipFile` | 21 |
| failed | `ReadError` | 9 |
| failed | `UnicodeEncodeError` | 3 |

So of 84 361 containers the queue knows about, **19 446 (23%) have no engine that claims
them**, and 24 041 are simply not worked yet. The failures are a rounding error beside
those two numbers — 33 in total.

## What the unclaimable ones are

**200 distinct suffixes**, so this is a long tail rather than one missing format. The head:

| suffix | containers | bytes |
|---|---|---|
| *(no extension)* | **12 449** | 1 407 343 565 |
| `.aar` | 2 397 | 557 770 568 |
| `.beam` | 966 | 7 416 975 |
| `.dat` | 537 | 99 637 925 |
| `.nupkg` | 505 | 137 977 521 |
| `.wpt` | 242 | 8 960 140 |
| `.ogz` | 240 | 318 255 206 |
| `.rgd` | 194 | 1 118 777 |
| `.war` | 141 | 1 725 746 728 |
| `.qr_` | 138 | 186 530 |
| **`.rar`** | **90** | **10 602 867 436** |
| `.svn-base` | 72 | 54 754 156 |
| `.mpz` | 71 | 107 499 263 |
| `.fcstd` | 55 | 100 545 787 |

Three things stand out, and each changes what "add an engine" would buy:

1. **The largest group has no extension at all** — 12 449 containers, 1.4 GB. An extension
   list cannot reach them, so the only route is content sniffing (a zip starts `PK\x03\x04`,
   a tar has `ustar` at 257, gzip `\x1f\x8b`). Some will be zips or tars named without a
   suffix; others will be something else entirely. **The population is unmeasured in
   kind** — that is what a second pass would settle.
2. **`.rar` is 90 containers and 10.6 GB** — the largest byte total by a wide margin, and
   the only entry here that needs a third-party tool rather than the standard library
   (`rarfile` plus `unrar`/`bsdtar`). 10.6 GB is 7× the 1.4 GB extensionless group, so if the
   goal is bytes made searchable, `.rar` is the single biggest lever — and it is also the
   one with an external dependency.
3. **Most of the tail is formats a text search has little to gain from.** `.aar`/`.war`/
   `.nupkg` are zip-shaped build artefacts (Java/Android/.NET) and would work with the
   *existing* zip engine if the extension list were widened — a one-line change with no new
   dependency. `.beam` (Erlang), `.ogz` (a game archive), `.fcstd` (FreeCAD), `.rgd`,
   `.mcz`, `.wpt` are application blobs, not document containers.

## What the engines already claim, for contrast

`done` containers by suffix: `.jar` 38 197, `.gz` 9 968, `.tgz` 3 335, `.zip` 2 199,
*(none)* 531, `.bz2` 268, `.xz` 173, `.apk` 92, `.whl` 83, `.xpi` 30.

The `.jar` dominance is worth noticing: 38 197 of 54 915 listed containers are Java archives,
which is dependency/vendored material — the same set `textindex` already ranks down and hides
behind `include_vendored`. So the *existing* listing work is mostly pointed at vendored code,
while 23% of containers get nothing at all.

## The decision this feeds

Four routes, in ascending cost:

1. **Widen the zip/tar extension lists** to the shapes that are already handled by the
   existing engines (`.aar`, `.war`, `.nupkg`, `.whl` cousins). No new dependency, a small
   diff, and it reaches ~3 100 containers — most of the non-`.rar` named head.
2. **Sniff the extensionless 12 449** instead of trusting a suffix. No new dependency (the
   magic numbers are trivial), but it changes the *task* from name-driven to content-driven,
   which touches the mining plan rather than one list.
3. **Add `.rar`** via an external tool. 10.6 GB, the largest byte lever, and the only option
   that adds a system dependency to the host.
4. **Leave the long tail** and record it: 200 suffixes, most of them application blobs, where
   the marginal searchable text per container is close to zero.

My reading of the numbers: **1 and 2 are worth doing and 3 is the real decision.** Widening
the extension lists is nearly free and provably reaches ~3 100 containers; sniffing the
extensionless group is the only way to reach the 1.4 GB with no extension, and its *kind*
needs one more measurement before it is worth it. `.rar` is a 10.6 GB prize that costs a
host dependency, and given that this corpus's `.jar` work already turned out to be mostly
vendored code, the same question should be asked of `.rar` before paying for it: is it
documentation, or is it somebody's downloaded archive?

## Limits

Counts and byte totals come from the index's own `entries`/`mine_queue` columns, not from
reading any container. `archive_members` was not measured (the count did not return inside
the probe's window), so "how many members these containers hold" is unknown — which is the
number that would decide the `.rar` question outright. One index, one host, read-only.
