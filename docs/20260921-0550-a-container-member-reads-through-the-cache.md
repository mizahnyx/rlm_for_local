# A container member reads through the extraction cache (RO14)

**Created:** 2026-09-21 05:50
**Status:** point-in-time. The change is built and unit-verified; the expensive input it
removes is named precisely, because it was not the input the design document assumed.
**Supersedes nothing.** It closes the residual that
`docs/20260917-1040-corpus-a-read-that-scanned-every-chunk.md` §"The residual" left open
and that `docs/20260912-1155-roadmap.md` RO14 decided.

## Which input was expensive — and it was not the obvious one

The record above says *"a container member (`arch.zip!member.txt`) has no `entries` row,
so there are no exact bytes to resolve and the lookup still falls back to the unindexed
column — over 150 s for one address"*. That is right about the mechanism and wrong about
the input, and the difference decides what a test can prove. There are two shapes:

| input | what happened before this change |
|---|---|
| `pack.zip!inside/notes.txt` (a **bare** member name, which is the form `corpus_find` prints) | never reached the chunk lookup at all. It carries no `#L` fragment, so `_read_address` declines it, and the mount check answers `no such path` — cheap, and useless. The member was not slow to read; it was **unreadable**. |
| `pack.zip!inside/notes.txt#L0-9` (the **address** form) | took the `display` fallback in `TextIndex.find_chunk`, filtering the one column with no index — 29 015 791 rows, over 150 s against a 120 s cell limit — and then found nothing, because no member is ever indexed under that display. |

So the residual was a whole-table scan **and** a wrong answer, and the cheaper-looking
input was the worse failure. This is only visible by tracing which statement the read
actually issues, which is why the guards for this change are trace-based.

## The change

`CorpusBridge.handle_read` resolves a member reference *before* the address lookup:

1. `CorpusIndex.container_of_member` splits on the first `!`, requires the part before it
   to be a stored path, and returns it — one indexed lookup on `entries_path`. The whole
   name being a stored path **also** settles it, which is what keeps a file legitimately
   called `pack.zip!notes.txt`, sitting beside a real `pack.zip`, reading as the file it
   is.
2. `CorpusBridge._container_extraction` derives the container's cache key the way mining
   did — bytes from the path index, `head_hash` from `classification`, then
   `DerivationCache.key(head_hash)` — and reads the extracted text. Three indexed
   lookups and a file read.
3. A hit is served with a header naming the substitution, because the text is the
   *container's*, not the member's, and a reader who is not told cannot judge it. The
   ordinary byte cap applies.
4. A miss is the owner's rule made concrete: `CORPUS_CONTAINER_NEEDS_MINING` says the
   container has not been mined, names the operation that would mine it, and tells the
   model it may answer "the corpus does not contain this" — which is a truthful answer,
   where "no such path" invited a search for a member that will never appear.

Nothing about the mount, the containment rules or the read-only guarantee changed: the
container's bytes still come through `mount.open_readonly`, and the extraction cache is
derived state that lives outside the corpus.

## Two mistakes worth recording, because both produced a confident wrong answer

* **The cache was looked up by the wrong key.** `DerivationCache` is keyed by
  `key(head_hash)`, not by `head_hash`, and the first implementation passed the hash. Every
  correct container would have answered "not mined yet" — a plausible, wrong, and hard to
  notice answer.
* **A broad `except Exception` hid it.** The first version wrapped the whole body and
  returned "not mined" on any failure, so the wrong key looked like a legitimate miss. The
  handler is gone; the only exception caught now is `sqlite3.OperationalError` for a
  corpus with no `classification` table, which is a *state* and not a bug. This is the
  same shape as the `AGENTS.md` §1.8 corollary — a probe that swallows its failure and
  substitutes a default produces confident wrong answers — applied to a read path.

## What the tests can and cannot see

* The load-bearing assertions are trace-based: a member read must issue **no** statement
  filtering `text_chunks` on `display`. The trace callback renders bound parameters
  inline, so the assertion is on the column, and the cost is invisible in the answer.
* Two mutations came back **VACUOUS** on their first pass and both found something real:
  - the test written for the miss path used a *bare* member name, which never reaches the
    chunk lookup, so the mutation that restored the display fallback had nothing to
    restore. It now uses the address form — the input that was actually expensive (see
    "Which input was expensive" above);
  - the fixture's index had **no `text_chunks` table**, so "no statement scanned the
    chunks" was true for the wrong reason. The fixture now creates the table.
* The `entries`-row guard in `container_of_member` was **removed rather than kept**: the
  container check already refuses `inside/a!b.txt` (its container `inside/a` is not a
  file), so a mutation that deleted it changed nothing observable. What the whole-name
  check *does* decide — `pack.zip!notes.txt` beside a real `pack.zip` — has its own test,
  and that test is what the surviving mutation targets.

## Verification

* Fast suite and mutation table: see the commit message; RO14 adds 17 tests and 4 mutation
  entries, all red.
* **Not verified against the live corpus.** The mechanism is proved on fixtures and by
  which statements are issued; the 150 s figure is the earlier measurement of the path
  this change removes, not a re-measurement of the new one. A live `corpus_read` of a real
  member address is the check that would confirm it end to end, and it belongs with the
  RO13 live run rather than before it.
