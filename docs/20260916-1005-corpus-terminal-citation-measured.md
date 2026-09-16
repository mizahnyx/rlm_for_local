# Measured: the terminal answer now cites, on the question that never did

**Created:** 2026-09-16 10:05
**Status:** closed — the fix works on the run it was written for; n=1 and one arm
of the requirement remains unexercised
**Supersedes nothing.** Follows `docs/20260916-0902-corpus-terminal-answer-provenance.md`
(the fix) and `docs/20260915-0655-corpus-citation-compliance-measured.md` (the
prompt-only phase, 0 cited in 3).

## The measurement

Same question, same model, same profile, same script, third attempt — now with
`FORCED_FINALIZATION_CORPUS_PROMPT` on the terminal path. Nothing else changed
between attempt 2 and attempt 3.

| | prompt-only (T5) | guard, other question (T8) | attempt 2 | **attempt 3** |
|---|---|---|---|---|
| answer's citations | 0 | 2 | 0 | **1** |
| `Citations:` line | no | yes | no | **yes** |
| cited address was one it printed | — | yes | — | **yes** |
| `corpus_citation` record | `False` | `True` | `False` | **`True`** |
| `corpus_uncited` refusals | 0 | 2 | 0 | 0 |
| turns / elapsed | 3 / 599 s | 8 / 1 574 s | 8 / 1 578 s | 8 / 1 474 s |

Attempt 3: `corpus_search` ×3, `corpus_read` ×3, one address printed, and a
467-character answer whose `Citations:` line names that printed address — the
trajectory's own `corpus_citation` event reads `answers_with_address=True`, where
attempt 2's read `False` on the same question.

**What this establishes:** the terminal path was the gap. A run that never submits
— which is what this model does on a question the corpus cannot answer, twice
measured — now delivers its answer with provenance, and the provenance is
*grounded* rather than decorative: the cited address is one the run had printed
into its own cell output, checked as a set comparison over the trajectory.

**What it does not establish:**

- **n = 1.** One question, one model, one attempt per condition. The pattern is
  consistent with the two earlier baseline runs, but it is not a rate.
- **The escape arm is still unexercised.** The model did not say "the corpus does
  not contain this"; it cited a passage it had read. On an unanswerable question
  that is a different behaviour from the one the prompt offers as the alternative,
  and whether an adjacent citation is a *good* answer is a content judgement on the
  laptop, not something this document can call.
- **Convergence is unchanged.** 8 of 8 turns and forced finalization in all three
  attempts. The fix made the forced answer citable; it did not teach the model to
  conclude.

## Open, in the order the evidence supports

1. **Convergence.** The turn header should say how many turns remain, and a corpus
   run reaching its last turns without an address should be told once that "I did
   not find it" is a complete answer. Three attempts at 8/8 turns and forced
   finalization is the evidence for trying it.
2. **The escape arm's vocabulary check**, which becomes load-bearing the moment a
   run says "not found": it is a literal `coverage` test today, so a truthful
   paraphrase ("the index covers only…") is refused. The fix is the harness-side
   arm — accept an absence claim when the run actually called `corpus_coverage()`,
   which the parent knows because it served the call.
3. **The read-only digest proof**, running while this was written: the index's
   stored metadata predates every mining window, so its digest against a fresh walk
   is a statement about that whole interval.
