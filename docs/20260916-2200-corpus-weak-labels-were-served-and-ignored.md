# The model was served `weak` eight times and explored anyway

**Created:** 2026-09-16 22:00
**Status:** closed — the relevance signal is informative and insufficient; the next
lever is structural
**Supersedes nothing.** Answers the question left open by
`docs/20260916-2015-corpus-match-quality-first-measurement.md`.

## What the instrumentation showed

Two 8-turn runs of the unanswerable question with `corpus_search_quality` live. The
event reports the labels a search **served**, so this is what the model was handed
rather than what the harness believes it built:

| run | outcome | searches served | labels served on the substantive search | citations | answer states coverage |
|---|---|---|---|---|---|
| q1 | 8/8 turns, **forced** | 4 (3 misses, 1 with hits) | **`weak=8`** (3 863 chars of hits) | 0 | yes |
| q2 | **no `end` event** — ended abnormally | 4 (3 misses, 1 with hits) | **`weak=8`** (3 907 chars of hits) | 0 | — |

The three `no_quality_labels chars=180` events in each run are the **misses** (the
no-matches line plus notes, 180 chars: nothing to label) — the instrumentation
reporting "nothing was served" rather than inventing a band.

**So the open question is answered: the model saw `weak` on every hit it was
given — eight of them, a 3.9 kB result — and it still explored to its last turn and
was answered by forced finalization.** The label is informative, it is served, and
this model does not act on it. `q1` also recorded one `corpus_uncited` refusal, so
the guard engages when a submission happens.

## What this decides

The relevance signal stays — it is a fact in the trajectory now, it costs nothing,
a reader (or the wiki stage) can use it, and a model that *can* act on it will. But
it is not the missing piece for convergence, and the evidence says plainly that no
further prompt text will be either: three prompt levers have now been measured and
refused (`docs/20260916-1405-…`, the last-turn permission nudge; the citation
requirement, enforced by a guard the run never reached; the budget cut, which
traded provenance for speed).

**The next lever is structural, exactly as the last record predicted**: on a corpus
run's final turn, make the turn *be* submission-only rather than asking for
submission. The helper functions are not needed then — the run ends after it either
way — so removing them from the prompt for that turn converts "please conclude"
into "you cannot search any more". If that fails too, the honest conclusion is that
forced finalization *is* the terminal path for this class of question and should be
treated as designed rather than as a fallback: it is the path that produced the one
cited answer and the coverage-stating answers.

## Also worth recording

`q2` produced four quality events and **no `end` event**, so its outcome is unknown
— the summary table shows it as `None`. Whether it crashed, hit a client timeout, or
was still finishing when the summary ran is not established here, and I am not
reporting it as a result. The verdict above rests on `q1` alone, whose end event is
present and complete.

One structural note for whoever debugs that next: the run's `--log-path` JSONL is
flushed per event, so an `end`-less trajectory with events is a *distinct* signal
from an empty file, and it is worth a probe of its own rather than being read as
"no data".
