# The mining campaign did not touch the corpus: the digest proof, re-run

**Created:** 2026-09-16 11:30
**Status:** closed — the read-only proof now covers the whole mining history
**Supersedes nothing.** Re-runs the proof of `docs/20260913-0845-corpus-read-only-proof.md`
against a much longer interval, and answers the obligation left open there.

## What was run

Two commands, no model, no writes:

```
rlm corpus digest --from-index --corpus-index ~/rlm-derived/corpus.sqlite --out proof-20260916-index.json
rlm corpus digest --corpus-root /srv/corpus --compare proof-20260916-index.json
```

The first reduces the *index's own rows* to one digest. Those rows were written by
`rlm corpus index` on 2026-09-13, **before any mining ran**, and they store each
entry's path, kind, size and mtime as they were then. The second walks the corpus
now, through the same read-only mount provider, and compares.

## Result

```
PROOF: the two snapshots are identical — 4972609 entries,
digest 8da7068336d3ed8c…, 1084767249184 file bytes.
Nothing under the corpus changed between them.
```

Both the entry count and the digest are **the same values recorded on 2026-09-13**
(`docs/20260913-0845-corpus-read-only-proof.md`: `8da7068336d3ed8c…`,
1 084 767 249 184 bytes). The fresh walk took 6 426 s (107 minutes; the earlier one
took 69 on a quieter machine).

## Why this is stronger than the first proof

The first one cleared an index build and a handful of reads. This interval contains
the whole campaign:

- **three chained 12-hour mining windows** — extraction, archive listing and the
  text index: 2 881 592 sources indexed out of 2 882 822 text files, **29 015 791
  chunks, 41.6 GB of text read** through the mount;
- **ten live model runs**, each calling `corpus_search`/`corpus_read` in cells and
  re-reading passages through the mount;
- the classification pass's 4.28M file heads read on 2026-09-13–14;
- and it is the *same digest value* as the earlier proof, not merely a consistent
  pair of new snapshots. Two proofs, three days apart, one digest.

The three layers that make this credible are unchanged and worth restating:
`cryptsetup open --readonly` with `ro=1` in sysfs, a read-only mount, and a mount
provider with **no write verbs at all** — the first is the guarantee, the second and
third remove the accident.

## What it does not prove, stated plainly

- **The digest covers path, kind, size and mtime — not file contents.** A write that
  preserved a file's size and mtime would be invisible to it. That is exactly the
  obligation RO2 still lists: hash a sample of file contents.
- **Directory mtimes are excluded by design**, because they moved a millisecond
  between two consecutive reads of the same directory on this drive — covering them
  made a clean corpus report a breach that never happened (same record, §3).
- It says nothing about durability or about what happened before 2026-09-13.

## What remains for RO2

One item: **hashing a sample of file contents** on top of the manifest. It is a
read, so it is permitted under the constraint; it needs a deliberate choice of
sample size and a place to record aggregates only (the per-file hashes are
corpus-derived and stay on the laptop, per `AGENTS.md` §1.9).
