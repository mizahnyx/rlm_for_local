# The full prose-derived set: six questions, two exact citations

**2026-09-22.** The first question set drawn from real prose finished. This is the full-set
evidence, read from the trajectories' **events** with
`scripts/summarise_question_set.py` — not from the probe's summary line, because reading one
line is what produced the wrong claim corrected in `docs/20260922-2010-…`.

Aggregates only. The questions, the answers, the passages and the addresses stay beside the
corpus (`~/rlm-derived/questions/`, `prose-drafted.tsv`, `.sources.tsv`, `traces/`), and the
rendered pages are at `~/rlm-derived/questions/traces/index.md` — **15 pages and an index**.

## The set

Six questions drawn as prose and drafted locally by `Qwen3-4B-2507`, answered by
`Qwen3.5-4B-Abliterated`, `laptop` profile, max 6 turns, soft 60 s / hard 1 200 s. Total wall
time **12 694 s ≈ 3 h 32 min**.

| # | wall | turns | helpers | hits served (bands) | citable | **cited** | refusals | harness events |
|---|---|---|---|---|---|---|---|---|
| 1 | 3 933 s | 6/6 | 15 | 53 (weak 33, partial 10, none 10) | 10 | 0 | 0 | **1 hard timeout**, 3 extensions |
| 2 | 3 892 s | 6/6 | 4 | 15 (none 15) | **0** | 0 | 1 | **2 hard timeouts**, 2 extensions (one to **2 496 s**) |
| 3 | 1 598 s | 6/6 | 7 | 15 (none 15) | **0** | 0 | 0 | 2 extensions |
| 4 | 631 s | 6/6 | 4 | 20 (partial 16, strong 4) | **20** | **0** | 0 | — |
| 5 | 634 s | 6/6 | 2 | 4 (strong 4) | 4 | **1 exact** | **3** | — |
| 6 | 2 006 s | 6/6 | 4 | 5 (partial 5) | 5 | **1 exact** | 0 | 1 extension |

Set totals: **6 answers, 2 with an address — both exact**; 112 hits served, **39 of them
citable**; 36 helper calls; 3 hard timeouts; 8 extensions.

## What this establishes

**1. The citation path works, end to end, on questions drawn from prose.** Two of the six
answered with an **exact** address (`cited_exact=1` each). Until today the harness's citation
discipline had never been observed against a live model on this corpus at all; now it has been
observed twice, on the same instrument that also records the failures.

**2. Where nothing citable was served, citing nothing is correct.** Questions 2 and 3 were
served *only* `none`-band hits — their searches found no relevant passage — so their answers
named coverage, which is exactly what the escape hatch is for. The instrument now separates
"the model ignored citable evidence" from "there was none", which it could not do this morning.

**3. The refusal machinery demonstrably changes behaviour.** Question 5's trajectory carries
**three** `corpus_uncited` refusals (`block=1 answer refused: no address and no coverage`,
`nudges=1/2`, `2/2`, then `3/2`) and the submitted answer has an address. That correlation is
the whole point of the nudge, seen live for the first time.

**4. One question is a genuine anomaly, and it is now specific.** Question 4 was served
**20 citable hits (16 partial, 4 strong)** in four helper calls and cited **nothing**, in a
207-character answer it submitted voluntarily (`forced=False`). Every other question with
citable evidence cited, or had its refusal fire first. This is the case to chase.

## Where the time went, and a number that needs a look

The harness's own budget is a large, uneven part of the 3 h 32 min: **three hard-limit stops**
(two of them in question 2) and **eight extensions**, one of which records
`elapsed=2496s soft=60s hard=1200s` — a cell extended well past the 1 200 s hard limit, which
is either a long read finishing or a limit that is not bounding what it claims to.

`docs/20260922-2025-the-search-counts-every-vendored-match.md` names the candidate for the
20-minute stops: `TextIndex.search` counts **every vendored match** for the query expression to
print one informational line, and its cost scales with how common the question's words are. That
is **inferred**, and the box is now idle, so the measurement that settles it is the next thing
worth running rather than the next thing worth assuming.

## Two instruments, one small disagreement

The probe's summary line reports question 1 as `served_not_cited=50` with bands totalling 50;
the events sum to **53**. The difference is small and does not touch any conclusion, but it is
recorded rather than smoothed over: the events are the ones carrying the per-search detail, and
a summary that disagrees with its own events is worth knowing about before it is trusted for
something that matters.

## Still open

- **Question 4**: twenty citable hits, no citation, and no refusal — why?
- **The 20-minute search**: mechanism identified by audit, not yet measured on an idle box.
- **The 2 496 s extension**: whether the hard limit bounds what it says it bounds.
- **Set size**: this set cost 3 h 32 min for six questions, a range of 631 s to 3 933 s per
  question — so the cost is in the tail, not the average.
