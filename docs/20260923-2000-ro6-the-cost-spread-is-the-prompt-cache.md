# RO6: the cost spread is the prompt cache — a correction

**Date:** 2026-09-23/24 (the calls below span 01:21–01:46 UTC). **Corrects:**
`docs/20260923-1900-ro6-what-a-description-costs.md`, whose finding 3 is wrong. Prior context:
`docs/20260923-1700-ro6-the-value-set-measured.md`, `docs/20260923-1800-ro6-the-handler-and-the-arithmetic-that-bounds-it.md`.

## The correction, first

`docs/20260923-1900` reported that the fastest description moved ~7 700 prompt tokens in 29.3 s —
about **260 tok/s** — and concluded that the 6.6 tok/s prompt figure the project's arithmetic is
built on was wrong by ~40×, and that the RO6 row of the roadmap needed revisiting.

**That was my error, not the roadmap's.** The 29.3 s call had been preceded by an identical,
abandoned call whose prompt the server had already processed; the second call was answered from
llama.cpp's **prompt cache** and evaluated only **4 fresh tokens**. I measured the cache and
called it throughput. The owner's instinct was right — *"I'd rather repeat the probe with better
metrics"* — and repeating it settled the question in one run.

## The probe, and what the server said

`scripts/probe_summarise_cost.py` describes each document **twice, back to back, on the identical
prompt**, and the engine now records the server's own `timings` per call. Cited set, three
documents, one readable failure (4 calls):

| call | input chars | prompt tokens | fresh prompt tok | `cache_n` | prompt ms | decode tok | decode ms | client s | residual s |
|---|---|---|---|---|---|---|---|---|---|
| cold | 15 932 | 5 424 | 4 | 5 420 | 1 339 | 108 | 42 772 | 44.7 | 0.61 |
| warm | 15 932 | 5 424 | 4 | 5 420 | 1 159 | 108 | 43 826 | 45.0 | 0.03 |
| cold | 30 838 | 14 258 | 4 | 14 254 | 1 868 | 48 | 27 240 | 30.2 | 1.06 |
| warm | 30 838 | 14 258 | 4 | 14 254 | 1 815 | 48 | 26 656 | 28.5 | 0.04 |

Aggregate: **4 of 4 calls served from the prompt cache**, median 37.4 s, prompt median 1 577 ms,
decode median 35 006 ms, and the client seconds the server does not account for: **median 0.3 s**.

Everything follows from those columns:

1. **The spread is prompt-cache state.** A call whose prompt is in the cache evaluates 4 tokens and
   costs only its decode. The "cold" first call in the table was *also* a cache hit, because the
   server had processed that same document's prompt in the runs two hours earlier — so the cache
   survives at least that long, and every fast call in the previous record was fast for this
   reason.
2. **Decode is the floor**, at ~1.8–2.5 tok/s for 48–108 token replies. A description can never be
   quicker than its own output.
3. **Nothing is unaccounted for.** The residual is 0.03–1.06 s against calls of 28–45 s, so the
   client's seconds are prompt processing plus decode, with no hidden queueing. The earlier record
   suspected router queueing; the numbers do not support that.
4. **The published throughput stands.** The router log's own per-request lines (parsed for numbers
   only) show fresh prompt processing at **4.35–8.90 tok/s** — 7 376 tokens in 1 231 s, 5 233 in
   787 s, 3 592 in 482 s, 960 in 108 s. The 6.6 tok/s figure is right, and the two 901.9 s client
   timeouts in the previous record are explained: a 7 376-token prompt needs ~20 minutes, and the
   client abandoned it at 15.
5. **The RO6 arithmetic is therefore supported**, not contradicted. A 10 KB document is ~2 500–3 500
   tokens at 6–9 tok/s — the row's 5–10 minutes — and "100K documents ≈ 1–1.7 years" keeps its order
   of magnitude. The roadmap row has been corrected back.

Two corollaries for anyone measuring this again:

- **A cost measurement must say whether the prompt was cached.** Without `server.cache_n` it cannot:
  the same document costs 30 s or 20 minutes depending on state that is invisible from the client.
- **`estimated_output_tokens` is optimistic.** These documents tokenise at **2.16–2.94 characters
  per token**, not the 4 the estimate assumes, so every token figure in the metrics understates by
  30–45%. The measured `server.prompt_tokens` is the number to use; the `estimated_*` fields should
  be read as a lower bound.

## What is still running

The second half of the probe (two documents from rank 60 of the value set, never described by
anything) is in flight as this is written: its first call had been running **17 minutes** without
completing, which is the cold cost being earned rather than inferred. It writes
`~/rlm-derived/summaries-probe-cold.jsonl` and logs to `~/rlm-derived/summarise-probe.log`.

**Until it returns, the honest projection for the value set is unknown**, and the "5.6 h at the
measured median" in the previous record should be read as *the cache-warm cost of re-describing
documents already described* — not what 143 fresh documents would cost.

## The traces

`scripts/render_summarise_traces.py` renders a metrics log as per-call Markdown pages, an
`index.md` table, and a hierarchical `traces.jsonl` (one object per call with `when`, `client`,
`document`, `server`, `outcome`, `quality` nested), written 0600 in a 0700 directory beside the
corpus.

**These traces quote nothing.** Unlike `rlm trace render`, whose pages carry the passage behind
every citation, a summarise trace contains only counts, ratios and timestamps — `measure` has no
text field by construction — so these pages can be read, diffed or opened in a JSONL viewer
without anyone's document travelling (`AGENTS.md` §1.9). A call is identified by its position in
the log, its timestamp, its input size and the server's numbers.

Unverified: the cold numbers (in flight); whether the abandoned requests in the first run left the
server working on them after the client gave up (consistent with the timings, not instrumented);
and whether a description is *useful* to a reader, which no metric here measures.
