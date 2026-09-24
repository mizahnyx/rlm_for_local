# RO6: the cold cost, and a call in flight made visible

**Date:** 2026-09-23 (the calls below ran 01:21–02:00 UTC). **Completes:**
`docs/20260923-2000-ro6-the-cost-spread-is-the-prompt-cache.md`, whose "What is still running"
section deferred the cold half of the probe to this document. Its correction of
`docs/20260923-1900-ro6-what-a-description-costs.md` stands as written.

## The cold half, measured

The probe's second half ran on two documents from rank 60 of the value set — **never described by
anything** — so their first call is a genuinely cold prompt. Same design as before: each document
described twice, back to back, on the identical prompt, with the server's own timings recorded.

| call | chars in | prompt tokens | prompt time | prompt tok/s | decode tok | decode time | client s | unexplained |
|---|---|---|---|---|---|---|---|---|
| cold | 20 360 | 9 431 | 1 751 603 ms | **5.38** | 45 | 21 213 ms | **1 773.3** (29.6 min) | 0.52 s |
| warm | 20 360 | 4 | 1 469 ms | — | 45 | 20 849 ms | 22.4 | 0.04 s |
| cold | 6 923 | 2 354 | 285 204 ms | **8.25** | 51 | 16 989 ms | **302.5** (5.0 min) | 0.29 s |
| warm | 6 923 | 4 | 992 ms | — | 51 | 17 180 ms | 18.2 | 0.03 s |

Same document, same prompt, seconds apart: **1 773 s cold against 22.4 s warm** — a factor of 79 —
and decode is identical in both pairs (45 tokens ≈ 21 s, 51 tokens ≈ 17 s). The cold calls reused
an 87-token common prefix and evaluated 9 431 and 2 354 fresh tokens at **5.38 and 8.25 tok/s**.
The unexplained remainder is 0.02–0.52 s throughout.

This is the number the RO6 arithmetic is about, and it lands inside the row's own estimate:

| quantity | measured |
|---|---|
| a fresh 6.9 KB document | 302.5 s (5.0 min) |
| a fresh 20.4 KB document | 1 773.3 s (29.6 min) |
| the same document, prompt cached | 18–22 s (decode-bound) |
| the value set (143 documents), fresh | **≈ 12–70 h**, depending on the size mix |
| the cited set (13 documents), fresh | ≈ 1–2.5 h |

So the previous record's "5.6 h at the measured median" is the *cache-warm cost of re-describing
documents already described*, and must be read that way; the earlier inference of 11.9–23.8 h from
5–10 minutes per document was low for the larger documents but right in shape.

## A call in flight is now visible

The cold call above sat for **17 minutes with nothing in the log**. `measure` only wrote a row
when a call ended, so a call in flight, a call that never started, and a call that was killed were
the same silence — and the only way to tell was to watch the server's CPU. That is precisely the
gap the first run's two abandoned 901.9 s calls fell into.

The engine now writes a **`start` row before the request** and an **`end` row after it**, paired by
`(model, started_at)`, and the aggregate reports a start with no completion as *in flight* —
"running now, or killed before it ended" — never as a zero and never as a call:

- `start_record()` carries the same identifiers as its completion and, like everything in this
  log, no document text;
- `aggregate` excludes start rows from every count and median (a start counted as a call would drag
  the median toward zero and add a phantom empty reply), and reports `in_flight` per model;
- the renderer pairs them: a start row does not become a page, and an unfinished call appears in
  the index as in flight.

Verified: 96 tests across the metrics, the engine, the renderer, the command and the kernel
handler; two mutations go red individually — *a call in flight leaves no record behind* and *a
start row is counted as a call*. Doc lint 0 problems.

## Unverified

Whether the two abandoned calls in the first run left the server working on them after the client
gave up — consistent with the timings, not instrumented. And whether a description is useful to a
reader, which no metric here measures.
