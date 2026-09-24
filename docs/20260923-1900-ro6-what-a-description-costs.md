# RO6: what a description costs, and two ways it failed silently

**Date:** 2026-09-23. **Commits:** `8d41a4f` (the `summarise` command), `b4b25be` (the derived
client timeout), `daede10` (the description that was never stored). Prior context:
`docs/20260923-1700-ro6-the-value-set-measured.md` (the selection),
`docs/20260923-1800-ro6-the-handler-and-the-arithmetic-that-bounds-it.md` (the handler and the
arithmetic this measurement now tests).

The owner's instruction that produced this: *"use the same model as usual, but allow it to be
configured as different if the need arises … you can perform meaningful summarizing metrics that
can provide additional data for the decision gates."* And, on hardware: *"as few models running
concurrently … ideally just 1"*. Every run below used the one resident model
(`Qwen3.5-4B-Abliterated@localhost:9010`), and no second model was loaded.

## What ran

`rlm summarise --cited-only` over the enrichment plan's cited set: 13 documents, of which the
harness could hash and read **7** (see finding 5). Three runs, all with the same model, no
concurrency, and every call logged as one JSON line carrying counts and ratios only — no
document text, by construction.

## The measurement

Eight model calls, six of them usable. Per call (all figures measured; token counts estimated
at 4 characters each):

| seconds | chars in | chars out | words out | groundedness | outcome |
|---|---|---|---|---|---|
| 901.9 | 30 838 | 0 | 0 | — | `ReadTimeout` (three 300 s attempts) |
| 901.9 | 15 932 | 0 | 0 | — | `ReadTimeout` (three 300 s attempts) |
| 29.3 | 30 838 | 228 | 33 | 0.400 | described |
| 45.0 | 15 932 | 464 | 67 | 0.475 | described |
| 130.7 | 1 652 | 296 | 47 | 0.333 | described |
| 504.4 | 7 484 | 237 | 33 | 0.500 | described |
| 802.9 | 20 382 | 214 | 30 | 0.476 | described |
| 150.2 | 32 768 | 259 | 33 | **0.727** | described (input exactly at the 32 KiB cap) |

Usable calls: median **140.5 s**, min 29.3 s, max 802.9 s. Output: median 248 characters
(33 words), median compression 0.020, **no** output hit the 400-token bound and **no** reply was
boilerplate. Groundedness: median 0.476, range 0.333–0.727.

**Projections at the measured median** (arithmetic on the measurement, assuming the next
documents resemble these): the cited set (13) ≈ 0.51 h, the whole value set (143) ≈ 5.6 h. At the
*minimum* 29.3 s the value set is 1.2 h; at the *maximum* 802.9 s it is 31.9 h. That spread is
the finding below, and it makes any single projection unreliable.

## Finding 1 — the input cap and the client timeout contradicted each other

The first two calls died at 901.9 s each on documents of 30 838 and 15 932 characters. That is
exactly `stop_after_attempt(3)` × the HTTP client's 300 s default. The kernel caps a summary's
input at 32 KiB, which the handler will always send whole, so the largest documents were the ones
guaranteed to fail.

Fixed in `b4b25be`: the timeout is **derived** from the cap and the output bound
(`summarise_timeout_seconds`, ×1.5 for a box that is also mining or swapping) and passed to the
client by the command. A test pins the incident as well as the rule — the constants must still
explain why three 300 s attempts were too few, so if the throughput figures change, the
explanation stops holding and the test says so.

## Finding 2 — the descriptions were paid for, cached, and never stored

`add_text` is idempotent on `(raw, origin)` and defaults to the **file** origin. For a cited
document — which the mining windows had already indexed as plain text — the derived description
collided with the document's own bytes and was silently dropped. The handler then reported
`empty_summary`, which is a **lie about work that had happened**: the model had replied, the
reply was cached, and the queue said the model had said nothing. `ORIGIN_CACHE` is the
established convention for a derived artefact (the extraction handler already used it); the
summariser simply omitted it.

Measured: 5 of 5 described documents were stored nowhere, and the queue showed
`skipped empty_summary 5` with **no `done` row at all** — which is how it was caught, by asking
the queue rather than the model. Fixed in `daede10`, with two consequences: a cache hit now
*ensures* the description is in the index (so a paid-for description survives an index rebuild),
and the unreachable `too_short_to_index` branch was deleted rather than shipped untestable —
`chunk_ranges` only drops whitespace, so it could never fire.

**The fix was then exercised on the damage it had already done.** `mine retry --task summarise
--skipped-note empty_summary` re-opened the five mislabelled rows (the retry path grew that flag
for exactly this: a skip that recorded the harness could not do something it now can), and a
re-run reported `done 6, skipped 1, failed 0, cache hits 5` — the five descriptions were indexed
without **any** model call, because they were already paid for. The queue now reads
`done 1, done cache 5, skipped too_short_to_summarise 1, skipped unclassified 6`: all thirteen
cited documents accounted for, and no lie among them.

## Finding 3 — cost does not track document size, by a factor of 70

The 30 838-character document was described in 29.3 s; the 1 652-character one took 130.7 s; a
20 382-character one took 802.9 s. Input size explains none of it, and output length explains
little (33, 47 and 30 words took 29.3 s, 130.7 s and 802.9 s).

The fastest call moves ≈7 700 estimated prompt tokens in 29.3 s — about **260 tok/s**, against
the 6.6 tok/s prompt figure this project's arithmetic has been built on (`AGENTS.md` §4,
roadmap RO6). The slowest moves ≈5 100 tokens in 802.9 s — about 6.4 tok/s, close to the
published figure. So the published number describes the *slow* mode, and the fast mode is ~40×
faster.

**What is not established: why.** Candidates, none instrumented and none of them concluded here —
the router's on-demand slot handling and orphaned generations after a client timeout, the box's
swapping (5.4 GiB of swap in use with a 5 GB resident model), and llama.cpp's prompt cache state.
Distinguishing them needs the *server's* logs (slot timings, `--verbose`), which this measurement
does not have. The honest summary: a description costs between 30 seconds and 13 minutes on this
host, and no cost model has been shown to predict which.

## Finding 4 — groundedness, calibrated

The metric reports whether a description's content words occur in the document, and 0.475 means
nothing on its own. Two controls, same function, computed on the same cited documents:

| control | result |
|---|---|
| a document's own opening text (implementation check) | **1.000** every time |
| the opening of a *different* document (the floor) | median **0.000**, max 0.833 |

So the model's 0.475 sits far above "about something else" and well below "a copy of the text".
That is a real signal: the descriptions are about the right document. What the gap to 1.0 is
made of — legitimate paraphrase and framing ("this document appears to be…") versus drift — is
**not** separable by this metric, and it should not be reported as if it were.

## Finding 5 — half the cited set cannot be read at all

Six of the 13 cited documents could not be opened through the mount (`ReadOnlyViolation: not a
regular file`), and the queue independently skipped **six** documents as `unclassified`, because
`source_hash_for` finds them in no `classification` row. The counts match, which is consistent
with one cause — those documents are not regular files in the index's sense (symlinks, or paths
resolving elsewhere) — though the identity of the two sets is **inferred from equal counts, not
proved**. Either way the consequence is concrete: the enrichment value set is, today, only about
half describable, and `rlm summarise` reports those documents as candidates and then skips them.

## A disclosure

The calibration script's first version caught only `OSError`, so a `ReadOnlyViolation` traceback
printed a corpus path **into the session transcript** — the same class of incident as
`docs/20260922-1245-the-member-read-verified-and-a-disclosure.md`, and a rule this project
already wrote down ("a probe classifies its exceptions, it does not print them" —
`AGENTS.md` §1.9). The script now catches and counts. The repository is unaffected: no path
reached a file, a commit or a document, and the privacy check over the tree is clean.

## The gate

- **The selection is affordable, the cost is not yet predictable.** 143 documents is ~5 h at the
  measured median and ~32 h at the measured worst case. The earlier inference (11.9–23.8 h, from
  5–10 min per document) was in the right order of magnitude by accident: it is right about the
  slow mode and wrong about the fast one.
- **The RO6 arithmetic in the roadmap needs the owner's call.** Its 5–10 minutes per 10 KB
  document is not supported by these calls (some are 20× faster), and its "100K documents ≈ 1–1.7
  years" inherits that. I have not rewritten the row's numbers: the estimate is a recorded
  historical claim, and correcting the project's *throughput* figure is a bigger call than this
  measurement.
- **Before spending on the full value set**, two things are worth fixing: the six unreadable
  cited documents, and the pause flag (`rlm summarise` takes the mining lock but does not honour
  `mine pause`, so a run cannot be asked to stop between items).

Unverified: whether a 30-second description is *useful* to a reader (nothing here measures that);
the cause of the cost spread; and the decomposition of the groundedness gap.
