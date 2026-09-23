# Correction: question 1 was partly harness-bound, and I misread it twice

**2026-09-22.** `docs/20260922-1930-the-first-prose-derived-question.md` says of the first
prose-derived question that "the harness attributes little to itself: the cost is the model
decoding at 3–6 tok/s across six turns plus cold index reads." **That is wrong**, and the
trajectory said so plainly. This is the correction; the earlier document stands as written.

## What the events actually contain

Read from `prose-001.jsonl`'s guardrail events — the fields RO16 added so a budget death can
be attributed rather than guessed at:

| event | detail |
|---|---|
| `cell_timeout` | `block=1 limit=hard budget=1200s activity=5 last_helper=corpus_search corpus_calls=8 cell_timeouts=1` |
| `cell_extended` | `elapsed=84s soft=60s hard=1200s last_helper=corpus_search` |
| `cell_extended` | `elapsed=461s soft=60s hard=1200s last_helper=corpus_search` |
| `cell_extended` | `elapsed=357s soft=60s hard=1200s last_helper=corpus_find` |
| `corpus_last_turn` | `last turn reached with 13 corpus helper call(s) and no submission` |
| `corpus_citation` | `answers_with_address=False chars=777` |

So, correctly stated:

1. **One cell ran the full hard limit — 1 200 s — inside `corpus_search` and was blocked.** That
   is **~20 of the 65 minutes**, and it is the harness's own budget doing it, not the model
   decoding. Where the time went is therefore: ~20 minutes to a cell that never returned, three
   extended cells (84 s, 461 s, 357 s) totalling ~15 minutes, and the remainder turns and
   decoding. The harness is a **large** part of this number, not a little one.
2. **The model never submitted.** `corpus_last_turn` records the last turn reached with 13
   helper calls and no submission, so the 777-character answer was produced by **forced
   finalisation**. The earlier record reported `forced=True` but framed the answer as one the
   model gave; the honest framing is that the *forced* path wrote it.
3. **The event audit agrees with the aggregate**: `corpus_citation … answers_with_address=False`
   — one accepted answer, no address. The zero-citation finding stands, and now has both its
   aggregate and its event form.

This makes the cold-read cost that RO21 recorded **visible in a live run for the first time**:
a cell that spends twenty minutes inside a single `corpus_search` is exactly the failure the
warmth item was opened for, and it means the question "is a search affordable on this box?" is
still open — now with a concrete instance rather than a hypothesis.

## Why I got it wrong, twice, in the same way

The correction is worth more than the number, because the path to it was wrong twice and both
errors were mine:

1. **A check that looked in the wrong place reported nothing.** My first attempt filtered
   guardrail rows on `kind`, and the event stores its name under `guardrail`. It printed no
   output at all. I nearly read that silence as "no timeouts". A check that cannot see the
   truth must say *unknown*, and "my filter matched nothing" is the loudest possible form of
   "not checked" — it is not a negative result. What caught it was a substring census that had
   found the word `cell_timeout` in the file, which is the only reason I looked again.
2. **Then I read the first matching detail and concluded from it.** Seeing
   `elapsed=84s soft=60s hard=1200s`, I wrote that the soft limit fired at 84 s and that this
   *supported* the model-bound reading. The `cell_timeout` with `limit=hard budget=1200s` was a
   different event further down the file. Printing every matching event instead of the first
   would have shown both.

Both are the same failure as the four probe errors earlier today — concluding from a partial
view — and this one reached a pushed record, which is why it is written down rather than
quietly fixed.

## What this does not change

The set, the drafting, the sampler, the citation finding (`0` addresses while 10 `partial`
hits were served) and the escape-hatch explanation all stand. What changes is *where the 65
minutes went*, and what that implies: the next measurement of interest is not "will the model
cite" but "why does one `corpus_search` take twenty minutes", which is a harness question with
a known open item behind it.
