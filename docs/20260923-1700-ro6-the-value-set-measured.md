# RO6's arithmetic, measured: the value set is tens of documents, not 100 000

**2026-09-23.** RO6 says the corpus cannot be uniformly summarised on this hardware — one
500-token summary of a 10 KB document is ~5–10 min at the measured decode rate, so 100K documents
is 1–1.7 years — and that it must therefore be "summarised by value". *"The arithmetic decides
this, not taste."*

The arithmetic needs a definition of value, and the harness already records one: every helper
call that **served** an address, and every address an answer **cited**. So the demonstrated value
set is what the system has actually used, and it can be counted instead of assumed.

## Measured over everything recorded so far

48 trajectories (the prose sets, the exemplar A/B, the citation A/B, and the owner's earlier
passage-derived set):

| | addresses | at 5–10 min each |
|---|---|---|
| **served** across all runs | **180** | **15–30 hours** |
| **cited** by an answer anywhere | **13** | **1.1–2.2 hours** |
| for comparison: the whole text core, uniformly | ~2 880 000 | **1.0–1.9 years** |

By provenance class: served — code 49, documentation 40, prose 33, markup 27, other 20,
vendored 8, data 3. Cited — **prose 5, code 4**, documentation 2, other 1, markup 1.

## What that decides

1. **Selective enrichment is affordable today, at the scale the system has demonstrated.** The
   set an answer has ever *cited* is **thirteen documents** — an afternoon of inference, not a
   research programme. The set retrieval has *touched* is 180, which is a long weekend. Both are
   reachable; neither is a year.
2. **So the binding constraint is not the model's speed, it is having a value signal** — which we
   do have, in the trajectories, and which is *evidence of use* rather than a heuristic guess.
3. **And the ledger's framing was too pessimistic by four orders of magnitude** — not because the
   rate is wrong, but because it compared the cost of summarising *everything* against a corpus
   where only a tiny fraction has ever been reached. The useful question was never "can we
   summarise the corpus", it was "which documents has this system ever needed".
4. **Code appears in the cited set (4 of 13).** Worth noting against the earlier hypothesis that
   prose should always outrank code: an answer about what a project *is* or *uses* cites code and
   manifests, and those are among the documents the loop has actually needed.

## What this does not settle

The value signal is **backward-looking**: it ranks the documents the system has already used. A
first enrichment pass built only from citations would summarise thirteen documents and never
widen — so the design question the gate turns on is **how the set grows**: from usage alone, from
usage plus retrieval-touched, or from an explicit owner list. The measurements above give the
cost of each: 1–2 hours for the cited set, 15–30 for the served set, and the whole corpus is out
of reach for reasons that are arithmetic rather than taste.

## Next

Implement the selection — a ranked candidate list derived from recorded usage, written beside the
corpus — then the `SUMMARISE` handler behind it. The handler is the first **generation** task the
mining loop would own, which is a design step in itself: which model, where the summary is cached,
and how the batch is bounded so a window cannot run for days.
