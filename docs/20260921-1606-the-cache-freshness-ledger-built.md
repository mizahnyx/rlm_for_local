# The cache freshness ledger, built (RO15)

**Created:** 2026-09-21 16:06
**Status:** point-in-time. The first piece the design asked for is built and verified: the
fingerprints and a read-only report. The archive cache-miss path (RO14) and the running byte
total are separate items, and one of the design's three open questions is answered by the
roadmap's own record rather than by this work.
**Supersedes nothing.** It implements the first item of
`docs/20260917-1510-cache-freshness-ledger-design.md`, whose owner gates are all closed.

## What it does

`rlm_kernel/freshness.py` and `rlm corpus freshness`. For each derived artefact it answers
the design's four questions: what it is derived from, whether it is **current**, the
operation that would make it current, and how much work that is.

```
cache freshness (derived state against the corpus)
  coverage           current   as of 12 min ago
  archive_listings   stale     mining moved: listings_done 15123→15480  — rlm mine run …
  extraction         unknown   no fingerprint recorded …
```

**The currency test is "have the inputs moved", not "how old is it".** That is the owner's
own distinction from the design — *"a count from two hours ago over a corpus nothing has
touched is current, and a count from a minute ago taken before a mining window wrote is
stale"* — and it is what makes age a reported detail rather than a verdict.

## The two rules that keep it honest, both from this project's history

**A cache with no fingerprint reports `unknown`, never `current`.** Nothing can vouch for a
cache built before fingerprints existed, or by a writer that did not record one. This is
`AGENTS.md` §1.8 with a new subject, and it is the rule that stops the ledger becoming a
comfortable source of false assurance. A mutation that makes it return `current` is one of
the two entries added for this item, and it goes red.

**The diagnosis is never the expensive operation.** Every marker compared is either an
indexed `COUNT(*)` over `mine_queue` or a value already stored in the coverage snapshot. A
staleness check that counted `text_chunks` (29 015 791 rows) or grouped over
`archive_members` would be the CL6 defect wearing a new hat — the ledger would become the
slow thing it exists to warn about. So the tests are **trace-based**: one asserts the
ledger issues no statement touching `text_chunks` or `archive_members`, and a second asserts
that any `COUNT(*)` it does issue is on the indexed queue. Neither claim is about the
answer, which is why they had to be made about the statements.

**And an artefact with no implemented check reports `unknown` too.** A spec added to
`CACHES` without a branch would otherwise fall through to the final `return` — and the
failure mode of that is a cache reported current because nobody wrote the check. The
mutation that makes the fall-through say `current` goes red, which is what makes the
fallback a guard rather than a formality.

## What is deliberately not in it yet

Two of the design's six rows are absent, and for the same reason: their honest staleness
test is expensive, and this module may not answer by scanning.

| cache | why it is not here | what would let it in |
|---|---|---|
| path index (`entries`) | "a walk sees a path the index lacks" is a walk of the corpus | a generation counter bumped by the writer, so "current" is one row to read |
| classification | "a file whose head hash changed" is a re-read of every head | the same counter, or a fingerprint over the classified set's own aggregate |

`text_index` is also absent as a separate row, because its staleness *is* the coverage
snapshot's — comparing it twice would be two names for one number. When those first two get
a cheap marker, they join `CACHES` with one branch each.

The design's last two structural items are separate work and are not implied by this one:
the **archive cache-miss path** is RO14 (done — the miss already says "the container needs
mining", which is the ledger's own phrasing), and the **running byte total** for `SUM(size)`
is a write-time counter in the path index, which is a budget item rather than a freshness
one.

## Open questions the design left, and where they stand

* **Where the ledger is read.** Settled by the roadmap before this work: *the report is read
  on the CLI first, then the trace index page, then the console.* The CLI command is what
  landed; the other two are not built.
* **How stale is too stale for a search.** Not addressed here. The design leans "caveat for
  search, refusal only for `unknown`", and that is still a decision with no code behind it.
* **Whether the mining queue is the ledger's action list or a view over it.** This
  implementation is a **view**: the remedy strings name `rlm mine run …`, and the markers
  read the queue's own counts rather than keeping a second list of work. That follows the
  design's stated belief, and it is worth recording that it was a belief implemented rather
  than a question re-opened.

## Verification

* 11 tests in `tests/rlm_kernel/test_freshness.py`, including the two trace-based
  no-scan assertions and the age-is-not-staleness case.
* 2 mutation entries, both red.
* The fingerprints are recorded where a cache is known current: the end of every mining
  window and `rlm corpus counters --refresh`, both through `publish_coverage_snapshot`.
* Not measured against the live index yet: the report has not been run on `lunacode`, so
  every cache there currently reports `unknown` until a window or a refresh records a
  fingerprint. That is the correct behaviour and also the first thing to check on the host.

## Addendum: the live index reports `unknown` for all three, which is the right answer

Run against `lunacode`/`~/rlm-derived/corpus.sqlite`, the first reading was:

```
coverage           unknown  no fingerprint recorded, so nothing vouches for it being current
archive_listings   unknown  no fingerprint recorded, …
extraction         unknown  no fingerprint recorded, …
```

Every cache there was built before fingerprints existed, so nothing can vouch for any of
them. This is the module's central promise demonstrated on real state rather than asserted:
the alternative — grandfathering a pre-existing cache as `current` — is precisely the
confident default that would have made the ledger a source of false assurance on its first
day. The first real `current`/`stale` reading needs one window or one refresh on the host.

