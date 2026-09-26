# Descriptions reached the model, and changed nothing

**Date:** 2026-09-26. **Status:** the Gate 3 experiment ran to completion. One **positive
mechanism result** — descriptions do reach a live answer's context — and a **null outcome
result** for the one question measured. Follows `docs/20260925-0500-descriptions-are-indexed-but-invisible-to-search.md`
(the prerequisite), `docs/20260925-0600-the-description-pilot-ran-and-did-not-test-the-thing.md`
(why the first pilot was inconclusive) and `docs/20260926-2000-the-ab-measured-the-client-not-the-harness.md`
(the void first attempt, and the client timeout it exposed).

## What ran

One question, twice, on lunacode, one model resident, minutes apart, in a directory of
trajectories beside the corpus:

- `RLM_DERIVED_PASS=0` (arm `off`) — the derived-text pass disabled;
- `RLM_DERIVED_PASS=1` (arm `on`) — the pass enabled, as shipped.

Same question set, same position in it, same index, same model (`Qwen3.5-4B-Abliterated`,
`laptop` profile), `--max-turns 8 --timeout 1800`, `temperature=0.0`. Selection was by
position (`--only-index`), so no corpus-derived id appeared on a command line. Aggregates
only; the answers, the addresses and the passages stayed in `~/rlm-derived/ab-derived-pass/`.

A third data point is used below: the **recorded run of the same question** from 2026-09-22,
which is four days older than the derived pass itself.

## The two arms

| | `off` (pass off) | `on` (pass on) |
|---|---|---|
| wall clock | **556 s** | **821 s** |
| turns | 6/8, not forced | 6/8, not forced |
| helper calls | 2 (1 search, 1 read) | 2 (1 search, 1 read) |
| **addresses served by the search** | **5** (all `partial`) | **8** (5 `partial`, 1 `weak`, 2 `none`) |
| accepted answer with a citation | 1 | 1 |
| citation audit | `cited_answering=1`, `cited_exact=1` | `cited_answering=1`, `cited_exact=1` |
| refusals | uncited 1, weak 0 | uncited 1, weak 0 |
| parse-failure guardrails | 6 | 6 |
| `cell_timeout` / `cell_extended` | 0 / 1 | 0 / 1 |
| prompt characters sent (all turns) | 4 928 | 6 231 |
| **final answer** | 182 chars, digest `4cd7f8b325d896f8…` | 182 chars, **same digest** |
| citation detail | 35 chars, digest `d47d0aa0fabc65ee…` | 35 chars, **same digest** |

The answers are byte-identical and so are the citations. The arms differ in exactly two
measured ways: the `on` arm's context carried three more hits, and it took 265 s longer.

## The mechanism result: the pass fired, live

The two arms' search queries are **identical** (same string, checked by comparison, not by
eye). Asking the index the question the pass itself asks — `derived_only=True`,
`include_vendored=True`, `k=DERIVED_PASS_K` — on that query returns **3 hits, every one of
them `origin=cache`, and none of them in the address set the `off` arm was served**. That is
exactly the 5 → 8 difference above, and it is what the pass is for: the same file hits as
before, plus three cached descriptions the run would otherwise never have seen.

So the question the pilot could not answer — *can a description reach a live answer's
context at all?* — now has a measured yes. Before this, the only evidence was a unit test and
a deliberate `derived_only` search.

## The outcome result: nothing measurable changed

With three descriptions in front of it, the model produced the **same answer**, citing the
**same address**, after the **same number of turns** and the **same refusal**. The pass's own
labelling explains why, and it is the harness's own verdict rather than a story about the
model: the three descriptions came with `covers … (weak)` and `covers … (none)` — "this
passage does not answer the question" — while all five file hits came with `partial`. A model
that takes the `partial` evidence and ignores the `weak`/`none` evidence is doing what the
labelling asks.

**What the descriptions cost, measured:** the `on` arm's prompts are 1 303 characters longer
across the run (the three description lines, carried into every subsequent turn) and the run
took 265 s longer. At the measured 5.4 tok/s prompt rate that is the right order of
magnitude for ~1 300 chars added per turn, so the extra time is read as the price of carrying
them, not as reasoning about them. **Inferred**, not instrumented: the trajectory records the
prompt sizes, not the per-turn token accounting.

## The recorded baseline, and why it is not the comparison

The same question's run on 2026-09-22: **2 006 s, 6 turns, forced finalisation, 4 helper calls
served, and no accepted answer at all** (`citation=0`). Today's arms each produced an accepted,
cited answer in 6 turns. That difference cannot be attributed to descriptions — the `off` arm
has the pass disabled and produced the same accepted answer as the `on` arm — and the baseline
is four days and several commits older, on a colder index. It is recorded here as context, and
the **`off` arm is the comparison this experiment rests on**.

## A metric corrected: `derived_hits_shown` cannot be counted from a trajectory

The earlier records named `derived_hits_shown` — occurrences of the `derived:` label in a
trajectory — as the count that would decide the mechanism question. **It is always zero, and
not because no description was served.** Measured across all three trajectories: zero
occurrences of `derived:` **and** zero of `covers `, because helper results are never written
to the trajectory — the hit lines the model is shown do not exist in the file that was being
grepped. The same driver counted `"event": "corpus_citation"`, which never occurs either:
those are `guardrail` events, so its citation, refusal and weak counters were 0 by
construction.

The quantity that *is* measurable is the one used above: the **set of addresses the parent
logged serving** in `corpus_served`, which is the parent's own record of what it handed over.
Anyone writing another A/B should count that, and never a label they hope is in the file.
`docs/20260922-2345-a-citation-on-the-dict-is-a-citation.md` is the same lesson one layer up:
the instrument, not the intention, decides what is countable.

## The failed first attempt, and the host it exposed

The first `on` arm reached turn 7, wrote 53 events, and then made **no progress for 22
minutes**: the process sat in `D` state with `wchan=folio_wait_bit_common`, **no model request
was in flight** (no connection to the router, and the model server's CPU delta was 0 over a
20 s sample), and **neither disk had I/O outstanding** (device `inflight` 0, counters frozen
over 20 s). The index's disk measured a healthy 72.7 MB/s on a 64 MiB read from the middle of
the file. What was true at the same time: a stray diagnostic of mine was reading the same
38 GB index in the same minutes, and the root filesystem is **96% full** (41 GB free of 911 GB).

The arm was killed, its torn trajectory kept as `on-stalled/`, and the arm re-run alone: it
completed in 821 s with identical counts to the stalled attempt's first three turns. So the
stall is read as **I/O contention with my own concurrent index work** — an operator error, not
a defect of the harness — and the standing rule that follows is the one this project already
has for the model: **one heavy reader of the index at a time.** The 96%-full root filesystem is
recorded as a host condition because it is the kind of thing `AGENTS.md` §1.8's second
corollary is about, and it was not the cause here.

## What this establishes, and what it does not

**Establishes:**

1. A cached description **can** be served to a live run: three were, in a real answer's
   context, with the file hits unchanged.
2. For this question those descriptions carried `weak`/`none` labels and the answer was
   **byte-identical** with and without them — so descriptions are not free, and this one
   question gives no evidence they help.
3. The cost of three descriptions is ~1 300 prompt characters and ~265 s of wall clock on
   this hardware.

**Does not establish:**

- That descriptions do not help. One question, whose descriptions the harness itself labelled
  `weak`/`none`, cannot answer that: the natural next question is one whose descriptions come
  back `strong`/`partial`, and that is a different draw, not a different experiment.
- That the model *read* them. The harness recorded serving them; the trajectory does not
  record helper results at all (above), so "in the model's context" is the parent's record of
  the handover, not an observation of use.
- Anything about scale, other questions, or other models. `n=1` per arm, one model, one
  profile.
- That the arms are perfectly controlled. They are the same prompt until the search returns,
  and the two independent `on` attempts produced identical counts to each other — but the
  model is known to be unstable across router cache states (`AGENTS.md` §4), and one run per
  arm cannot separate the flag from that instability.
