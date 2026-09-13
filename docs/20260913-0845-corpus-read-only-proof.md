# The corpus is unmodified: the digest proof (roadmap RO2)

**Created 2026-09-13 08:45.** The outcome of the proof attempt recorded as
pending at the end of `docs/20260913-0120-corpus-live-first-build-and-question.md`
§4, and the end of that document's question — whether this drive survived a
night of indexing, answering and reading untouched.

---

## 1. The verdict

```
PROOF: the two snapshots are identical — 4972609 entries,
digest 8da7068336d3ed8c…, 1084767249184 file bytes.
Nothing under the corpus changed between them.
```

| | Snapshot A | Snapshot B |
|---|---|---|
| Source | the path index, built 2026-09-12 22:26–00:02 | a fresh walk, 2026-09-13 07:31–08:41 |
| Entries | 4,972,609 | 4,972,609 |
| Files / dirs / symlinks / other | 4,281,585 / 649,536 / 41,361 / 127 | identical |
| File bytes | 1,084,767,249,184 | identical |
| Digest | `8da7068336d3ed8c…` (full value in `~/rlm-derived/digest-*.json`) | identical |

The walk took **4,156 seconds (69 minutes)**; reading the index took seconds,
which is the point of taking one side from the index.

Mount state at the end, unchanged from the start of the session:
`/dev/mapper/usb_crypt_ro[/backup] ext4 ro,nosuid,nodev,relatime`, block layer
`ro=1`.

The two digests are equal, and they were produced by two *independent
enumerations* of the tree — the index by a walk during the run, the digest by a
walk afterwards — so this is not a proof that reads its own homework.

---

## 2. Why the first proof said the opposite

The first attempt used a hand-typed `find /srv/corpus -newer <marker> -print
-quit` and reported a breach within three seconds. The first entry it found is a
**directory stamped 2036-01-06**, ten years ahead of the scan: this drive
contains future-dated entries, so "newer than the marker" is true forever and
that proof could never come out clean. The harness's own classifier, run
afterwards over the whole tree's worth of such entries, put every one of them in
the "dated after the scan" bucket and none in the run window
(`rlm corpus verify`, `docs/20260912-1155-roadmap.md` §7).

Two false-alarm classes were found and fixed in the process, both the same
mistake in different clothes — *a check that substitutes a number for a truth it
cannot see*, the corollary in `AGENTS.md` §1.8:

1. the marker scan, which cannot tell a write from a pre-existing future date;
2. directory mtimes in the digest, which moved a millisecond between two
   consecutive reads of the same directory while every file's held still. Had
   they been included, the digest would have reported the corpus changed on
   perfectly idle hardware.

---

## 3. What this proves, and what it does not

**Proves.** Between 2026-09-12 22:26 (the index build) and 2026-09-13 08:41 (the
second walk), no entry was added, removed, renamed, resized or retimestamped
anywhere in 4,972,609 entries. That window contains everything the harness did on
this session: the index build, the 15-minute live corpus question, and every
read the proof itself performed. Aggregate-only, quotable, reproducible with
`rlm corpus digest`.

**Does not prove.**

- **That nothing was written and undone.** A manifest comparison is blind to a
  change that restores the size and the mtime. No snapshot method can see that;
  it is inherent, and it is why the mount, not the proof, remains the guarantee.
- **That file *contents* are unchanged.** The digest covers path, kind, size and
  mtime — not bytes. A file rewritten to the same length with its timestamp
  restored would pass. `AGENTS.md` §1.8 prescribes hashing a *sample* of contents
  on top of the manifest; that has not been done, and it is now the one remaining
  piece of the RO2 proof obligation.
- **Anything before the index build.** The census and the mount setup ran before
  snapshot A existed; this pair brackets the live session, not the whole history
  of the drive.

---

## 4. Note on the two index digests in the log

The first index digest taken this session (`c00dfd10…`) does not match the one in
the table (`8da70683…`), and the difference is not the corpus: the code changed
between them. The first run read the index's *display* path column; the fixed
version reads the exact path bytes, and it also changed the timestamp encoding
after an int64 overflow. Both snapshots compared above were taken with the same
version, which is what makes the comparison valid — and the fact that the two
index digests differ is the evidence that the fix mattered.
