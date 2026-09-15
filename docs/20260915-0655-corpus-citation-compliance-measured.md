# Citations: the requirement is in the prompt, the model does not follow it

**Created:** 2026-09-15 06:55
**Status:** measurement closed — the prompt-only phase failed, so the guard the owner made conditional on this result is now the next step
**Supersedes nothing.** Related: `docs/20260914-2135-corpus-live-runs-and-the-unsearched-nudge.md`
(the loop that got the model to look at all), `docs/20260912-1155-roadmap.md` §7 RO4.

## The question this answers

On 2026-09-14 the owner called it like this: **require citations in the prompt
first, measure, and add a refusal only if the requirement does not take.**
`CANNOT KNOW` was not an option, because the harness now records whether a corpus
answer carried an address: every accepted answer writes one `corpus_citation`
guardrail event with `answers_with_address=True|False`.

This document is the measurement.

## Scale, because a verdict means nothing without it

- Model: **`Qwen3.5-4B-Abliterated`** — the `laptop` profile's root and sub model.
  `live-ask.sh` passes `--profile laptop`, not `--model`, and I read the profile
  rather than assuming it (an earlier note in this repository said
  `Qwen3-4B-2507`; that was a different invocation, and it is corrected here).
- Harness: `rlm ask --profile laptop --max-turns 8`, corpus bridge over
  `/srv/corpus` and the 12 GB path/text index, router at `127.0.0.1:9010`.
- Host state: the same laptop was running a niced (`nice 10`, `ionice -c2 -n7`)
  `index_text` window throughout, so these runs competed with indexing; the
  router's warm/cold cache state was not controlled, and this project has already
  measured that model behaviour is not stable across those states.
- Index coverage at the time: **~34% of the 2,882,822 text files** (989,464
  indexed), up from 1.27% at the start of the evening's window.

## What was measured

Three consecutive runs after the citation requirement was deployed
(`af4e500`, pulled 06:10). All three used the same question, the same model, the
same profile.

| Run | Turns | Elapsed | Helper calls in cells | Addresses printed | Answer | Citations in answer | Recorded |
|---|---|---|---|---|---|---|---|
| 5 | 3 | 599 s | `corpus_search` ×1 | 1 | 207 chars | **0** | `answers_with_address=False` |
| 6 | 7 | 1385 s | `corpus_search` ×1, `corpus_read` ×5 | 2 | 180 chars | **0** | `answers_with_address=False` |
| 7 | 7 | 1117 s | `corpus_search` ×1, `corpus_read` ×5 | 2 | 197 chars | **0** | `answers_with_address=False` |

**0 of 3 answers cited anything**, and none contained a `Citations:` line. The
model is not ignoring the corpus — it searched, read up to five passages, and
printed the addresses into its own cell output. It simply drops them when it
writes the answer, and the requirement stated in the system prompt does not
change that at this scale.

Two secondary observations, offered as observations rather than findings at n=3:

- The searches shrank: runs 6 and 7 each made **one** `corpus_search` call where
  the pre-requirement run 4 made two, and then compensated with five reads.
- No run reported coverage or an explicit "the corpus does not contain this",
  which is the other half of the instruction and the escape hatch a refusal would
  need.

## What this decides

The owner's condition was "add a guard only if that fails". It failed, 3 of 3, on
a single model and a single question — so the guard is the next step, with the
escape hatch the instruction already names: an answer is acceptable when it
either cites an address **or** says the corpus does not contain the answer with
its coverage. Without that second arm a refusal would loop on the questions the
corpus genuinely cannot answer, which on a 34%-indexed corpus is most of them.

The counter-argument is recorded too, because it is the honest one: n=3 on one
model is a narrow base, and a guard that fires when a model legitimately has
nothing to cite costs turns. What makes the guard defensible is not the sample
size but the escape hatch — a refusal that can always be satisfied by telling the
truth about coverage cannot trap a truthful run.

## What is still not measured

- Whether a **larger** model ignores the requirement too. The verdict above is
  about `Qwen3.5-4B-Abliterated` under a 8-turn budget, and nothing else.
- Whether the requirement takes when the index is complete. "Cite what you read"
  is easier to satisfy when a search returns relevant hits; at 34% coverage a
  legitimate search can come back nearly empty, which may itself be why the model
  had little to cite.
- Whether the guard, once added, produces cited answers or merely more turns.
  That is the next run, and the same `corpus_citation` event measures it.
