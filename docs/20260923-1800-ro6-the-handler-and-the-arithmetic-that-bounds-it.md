# RO6: the handler, and the arithmetic that bounds it

**Date:** 2026-09-23 (the clock on this machine read 14:02 when this was written; the
filename continues the series' ordering — the previous record is `-1700` — so that the
documents sort in the order the work happened rather than in the order a drifting clock
reports).

**Commit:** `dc41342` (`RO6: describe a document by value, with the model injected`),
pushed. Selection half: `612c7b6`, recorded in
`docs/20260923-1700-ro6-the-value-set-measured.md`.

RO6 is *selective enrichment*: summarise the fraction worth a generation pass, not the
corpus. The value-set half landed first (`src/rlm_kernel/enrich.py`, `scripts/enrich_plan.py`:
55 trajectories scanned, 143 documents selected, 13 of them cited). What was missing until
now was anything that could **write** a description — and the kernel has no model by
construction, which is the whole reason the gap survived this long.

## What landed

`task_summarise(ctx, rel, size, source_hash)` in `src/rlm_kernel/mine.py`:

- **The engine is injected, never imported.** `ctx.engines["summarise"]` is a callable
  `(text, max_tokens) -> (summary, meta)`. No engine is a skip *with a reason*
  (`no_summarise_engine`) — never a fabricated description, never a silent zero. This is
  what keeps a kernel testable without a model, and it is why the arithmetic below is the
  only place a model's throughput enters the kernel's design.
- **A summary already paid for is a cache hit** (`DerivationCache`, key
  `summary<=v1,400tok`). The token bound is *in* the key, so raising it re-describes
  documents rather than reusing descriptions written to a different bound.
- **The reply is indexed as derived text** under the document's own display, so the
  description is findable by `corpus_search` and the address it is filed under still
  resolves to the document. With no text index the summary is still made and cached: the
  description is the deliverable, the index is how it becomes reachable.
- **An empty reply is skipped, not cached** (`empty_summary`). Caching it would turn one
  bad minute into a permanent claim that a document says nothing.
- Registered in `TASK_HANDLERS`, deliberately **not** in `IMPLEMENTED_TASKS`: `mine plan`
  still counts summarisation unimplemented, which is the truthful description of a task no
  mining window can yet be asked to run.

## The arithmetic that bounds it

Inputs (published, `AGENTS.md` §4, measured on this host): **~6.6 tok/s prompt, ~3.0 tok/s
decode, one model resident at a time** (15 GiB RAM). Char-per-token is taken as ~4, which
is *inferred*, not measured here; every figure below inherits that assumption and is
labelled accordingly.

| quantity | arithmetic | result |
|---|---|---|
| the output bound, per document | 400 tok ÷ 3.0 tok/s | **≤ 2.2 min** of decode |
| the input cap, per document | 32 KiB ≈ 8 192 tok ÷ 6.6 tok/s | **≈ 20.7 min** of prompt |
| worst case, one document | 20.7 + 2.2 | **≈ 23 min** |
| a 10 KB document | 2 560 tok ÷ 6.6 + decode | **≈ 8.7 min** |
| the 512-byte floor | 128 tok ÷ 6.6 + a short reply | **≈ 30 s** |
| 100 000 documents, worst case | 23 min × 100 000 | **≈ 4.4 years** |
| the measured value set (143 documents) | 5–10 min each | **11.9–23.8 h** |

Two things fall out, and the second is a correction to this project's own habit of
justifying every bound with time:

1. **The 10 KB figure reproduces the RO6 row.** The roadmap's estimate for a 10 KB document
   was 5–10 min; the same throughputs give ≈ 8.7 min. The row's arithmetic is sound, and
   the conclusion it supports is the uncomfortable one: uniform summarisation of the corpus
   is not a matter of patience, it is arithmetically impossible here — hence *by value*, and
   hence why the input cap exists at all (without it, prompt cost is unbounded in document
   size while the output is not).
2. **The floor is a value judgement, not a saving — and I wrote it as a saving first.** The
   handler's original comment said the 512-byte floor prevents "paying ten minutes of a 4B
   model to re-state five hundred bytes". That is wrong by the table above: the same call
   costs ≈ 30 s, because a small document produces a short reply and the expensive term
   (the 400-token decode bound) is not reached. The floor is corrected in place to say what
   is true — that describing five hundred bytes costs more attention than those bytes are
   worth — and the correction is recorded here rather than quietly edited, because a bound
   justified with a plausible-sounding number that the arithmetic does not support is
   exactly the failure `AGENTS.md` §1.5 exists to catch.

The honest shape of the cost model is therefore: **the input cap bounds the prompt, the
output bound bounds the decode, and only the input cap scales with the document** — so the
document's size, not its value, decides what a pass costs, which is precisely why a
*selection* criterion has to exist in front of the handler.

## Verified

- 8 tests in `tests/rlm_kernel/test_summarise.py`: summarise-cache-index, a cache hit costs
  no model call, the short floor, no engine, a raising model is a recorded failure and not
  the end of a pass, an empty reply is not cached, the input cap holds, and the floor is
  below the cap. The engine is a recording fake, so a model call is counted, not inferred.
- Two mutation entries, each run individually and red:
  `RO6 the summariser runs even with no engine to run` and
  `RO6 a summary already paid for is paid for again`.
- Fast suite 1596 passed / 9 skipped / 13 deselected in 782 s; doc lint 0 problems across 95
  documents, self-test clean; privacy scan over the tree clean (8 tokens, no occurrence).
- The `--history` privacy scan exits 1 with 6 occurrences in four commit messages
  (`1603c59`, `d14796a`, `7712f49`, `81af8fb`). That is the **recorded, already-decided**
  incident `docs/20260919-2330-a-question-id-reached-the-public-repository.md` — same
  commit, same shape, resolved as "fix forward only, no history rewrite, no force-push" —
  and it is restated here so the exit code is not mistaken for a new finding.

## Not verified — the gate

- **No engine is wired, so this has never met a model.** Every number in the table above is
  derived from published throughput, not observed for *this* task. The first live
  measurement (three documents from `~/rlm-derived/enrich-plan.tsv`, cited set first, ~15–30
  min of wall clock) is the first real data point on whether a 4B description is worth the
  minutes it costs. That needs a time budget, which is the owner's.
- **The derived-text index path has only ever been exercised against a fake engine.** That a
  real summary lands findable in `text_chunks` is reasoned from the code, not observed.
- **Usefulness is unmeasured.** Nothing here says a reader — or the model that answers a
  question later — will be served better by a summary than by the document's own prose.
- **The default window decision is open**: whether summarisation enters a mining window at
  all (it is excluded so it cannot run by accident), and where it sits against the
  already-recorded `PRIORITY_SUMMARISE = 90`.
