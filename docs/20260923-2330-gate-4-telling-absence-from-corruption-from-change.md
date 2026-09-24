# Gate 4: telling absence from corruption from a genuine change

**Date:** 2026-09-23. **Status:** design, deliberately not implemented — one part of it needs an
owner call (`AGENTS.md` §1.6, sandbox boundaries). Written from figures already recorded in this
repository; nothing here was measured while `lunacode` is away.

## The question

The owner's framing: *"we need a way to distinguish between operation errors like a missing symlink
or a corrupted archive due to hardware error, and a true change in the source material."*

The reason a single failure cannot answer it is that **all three look identical at the point of
failure**. A read that cannot return bytes returns "cannot return bytes". What separates the three
is not the failure — it is the failure *plus a prior observation of the same item*:

| class | what it means | how it is recognised |
|---|---|---|
| **Operational** | the read failed for a reason that says nothing about the source: a symlink whose target is absent, a permission change, the drive not mounted, a lock, an unmounted subtree | the failure *goes away* when the operation changes (remount, retry, different path form), or the raw entry is not a file at all |
| **Corruption** | the source had content and the medium lost it: bit-rot, a truncated archive, a bad block | a *prior* good observation exists and the bytes now differ or fail **reproducibly**, with damage that is internally inconsistent (a listing that succeeds while a member fails) |
| **Genuine change** | the source legitimately differs from what was recorded: edited, moved, deleted, or the backup snapshot was re-taken | a prior observation exists, the current read *succeeds*, and the difference looks like content rather than damage; or the path is absent and its neighbours are fine |

## What the harness can see today

Recorded (`20260913-0930`, `20260913-2110`, `20260922-1033`, `20260923-2200`):

- a **path index** of 4 972 609 entries with size, mtime and kind — so "present, same size and mtime"
  is already answerable for every path;
- a **classification** store, built by reading heads, which is why an unreadable entry is absent
  from it rather than marked in it;
- **container listings** for the containers the queue reached, which are a *stored observation* of
  each archive's member set — a weaker read than extraction, and therefore a useful reference point;
- `rlm corpus digest`, which walks the tree and produces a digest for the read-only proof;
- and the six unreadable cited documents: all `stat-refused`, none containing `!`, with the
  classification store independently silent on the same six (`20260923-2200`). That is *one*
  observed instance of the class this document is about, and its cause is still unestablished
  because the mount resolves a path before it `lstat`s it — deliberately.

**The gap that matters:** the harness has **no content baseline for the corpus**. It has size and
mtime, which change for innocent reasons and can be preserved across corruption. So today
"changed" and "corrupt" are not distinguishable for any file the harness has not read twice.

## The design

### 1. Classify the failure where it happens, and say what is unknown

One `ReadOnlyViolation` currently covers four different facts. Give it a reason code — no
containment change, because the mount already resolves and already knows which check failed:

| code | meaning | what it does **not** mean |
|---|---|---|
| `absent` | the resolved path does not exist | *not* "the file was deleted" — see §3 |
| `not_a_file` | the resolved path exists and is a directory, socket, fifo or device | *not* corruption |
| `unreadable` | exists, but open or read failed (permission, I/O, symlink loop) | *not* a statement about content |
| `escapes` | the raw path resolves outside the corpus root — the existing containment refusal | *not* an error in the corpus; it is a refusal by us |

A **dangling symlink is still not identifiable** from these, because `absent` covers both "the
target is gone" and "there was never anything there". Making that distinction requires `lstat` of
the *unresolved* entry, which is the containment boundary this project deliberately put in the way
— see §5.

### 2. Record a baseline at first read — the cheap, decisive step

The mining pass already reads every file it indexes and extracts. Hashing bytes it is **already
holding** costs CPU and nothing else: a digest of the first 64 KiB plus size is enough to recognise
"the same file as before" for text and documentation, and a full digest can be reserved for
containers and for anything a later read disagrees with.

That gives every path the harness has ever read a **content baseline**, and turns the whole table
above from speculation into arithmetic. For paths never read, the honest answer stays `unknown` —
the `AGENTS.md` §1.8 corollary, applied to a file rather than a probe.

### 3. A state per path, not an error per read

Derive a state from (prior observation, current observation, failure code), and record it *as a
state*, the way `needs_ocr` records an empty text layer rather than a failure:

| prior | current | state | what follows |
|---|---|---|---|
| baseline, digest D | reads D | `present` | nothing |
| baseline, digest D | reads **D′ ≠ D** | `changed` | re-derive; the derived artefact is stale, not wrong |
| baseline, size S | `absent` | `absent_since` | **normal for a backup** — mark dependents stale, delete nothing |
| baseline | `unreadable`, and a second read gives *different* bytes | `flaky_medium` | suspend the path, count it; repeated instances in one window ⇒ the drive |
| baseline | `unreadable`, reproducibly identical failure | `unreadable_stable` | record once; do not retry forever |
| baseline | `not_a_file` | `replaced_by_nonfile` | the name now means something else |
| none | any failure | `unknown` | not an error; do not invent a baseline |
| container listed, N members | now fails to list | `archive_broken` | corruption or replacement — re-list, compare member count |
| container listed, N members | lists N′, extracts fail on some members | `archive_partly_broken` | the listing is the weaker read; extraction failure is the stronger evidence |

### 4. Escalate on clustering, never on one file

Hardware failure and source change have different *aggregate* signatures, and this is the only
reliable way to tell a bad block from a busy editor:

- **hardware** clusters — several paths failing in one time window, or paths that share a device
  region, or the same failure appearing after a period of health;
- **source change** is per-path and does not cluster across unrelated directories;
- **operational** errors are the ones that vanish when the operation changes, which is a test the
  others fail.

So the rule is: **one unreadable file is a record; ten in an hour is an alarm.** A single file is
never labelled corrupt — it is labelled `unreadable_stable` and asked about later.

### 5. The one part that needs an owner call

Distinguishing *dangling symlink* from *nothing was ever there* needs `lstat` on the unresolved
entry, which the mount refuses by design (layer 2, `AGENTS.md` §1.8). Two ways forward, both the
owner's:

- **a diagnostic-only mode** — a separate command (`rlm corpus diagnose`) permitted to `lstat` the
  unresolved entry *for classification only*, never on a read path, never following the link, and
  reporting a kind rather than a target. It narrows the boundary rather than removing it, and it
  would answer the six unreadable cited documents;
- **leave it** — accept `absent` as the most the harness will ever say about those entries, and let
  the state vocabulary above carry the rest.

I have implemented **neither**, and the design assumes neither.

## Why this matters beyond the diagnosis

This is load-bearing for the wiki/LTM, not a cleanup. A page whose source has gone is not an error
condition on a backup — it is the expected case. Without the vocabulary above, an LTM built on this
corpus will either regenerate pages whose source vanished, or delete them, or keep serving them as
current. The state per path is what lets a page say *"built from a source that was present on
<date> and is now absent"*, which is the truthful thing and the one a reader can act on.

## Unverified

- No content baseline exists, so **no claim here has been exercised against a real corruption**. The
  failure modes are designed from the vocabulary of the tools (`EIO`, short reads, listing-versus-
  extraction asymmetry) rather than observed on this corpus.
- The digest-on-read cost is **not measured** — bytes are already in memory at the point of
  indexing, but the CPU cost of hashing them has never been measured on this box.
- Whether the six unreadable cited documents are dangling symlinks or addresses from an earlier
  snapshot remains **unknown**, for the reason in §5.
