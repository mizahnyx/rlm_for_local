# The answer that gets delivered is the one that must cite

**Created:** 2026-09-16 09:02
**Status:** fix landed; its live measurement is the next run
**Supersedes nothing.** Continues the citation sequence:
`docs/20260915-0655-corpus-citation-compliance-measured.md` (prompt-only: 0 cited in 3),
`docs/20260915-1023-corpus-citation-guard-live.md` (guard: 1 cited in 1),
`docs/20260915-2305-corpus-search-cannot-count-its-own-index.md` (why the next
attempt died in two hours).

## The hatch attempt, and what it actually measured

An unanswerable question (the `Zxqvarn Protocol`) was asked twice. The first
attempt died on cell timeouts — a search was counting the index it searched — and
that defect is fixed. The second attempt is the one that produced a verdict:

| | attempt 1 | attempt 2 (fixed search path) |
|---|---|---|
| elapsed | 7 250 s | **1 578 s** || failing cells (stderr) | 4 | 2 |
| helper calls | search ×3, coverage ×4, count, list | search ×4, read ×2, coverage ×1 |
| addresses printed | 0 | 1 |
| `corpus_uncited` refusals | 0 | **0** |
| turns | 5 of 8, forced | **8 of 8, forced** |
| answer | harness failure text | 460 chars of prose, **0 citations, no coverage line** |

Three things follow, and only the first is what the test was for:

1. **The search fix works**: 26 minutes instead of two hours, and the cells ran.
2. **The escape hatch is still untested**, because it never fired: the model never
   *submitted* anything. The citation guard refuses uncited **submissions**, and a
   run that never submits walks around it entirely.
3. **Non-convergence is the real failure mode.** The answer arrived by forced
   finalization, both times, on a question the corpus cannot answer — the case
   that is the *common* case against a real corpus. Eight turns of exploration,
   no voluntary conclusion, and a delivered answer with no provenance.

## The fix: ask at the terminal point, never refuse there

`FORCED_FINALIZATION_CORPUS_PROMPT` is the corpus variant of the prompt that asks
for the final answer: same request, plus the evidence requirement — end with a
`Citations:` line naming the addresses actually read, **or** say the corpus does
not contain the answer and quote the line `corpus_coverage()` printed. Both arms
are named because a question the corpus cannot answer has to remain answerable.

Refusing at that point would be worse than useless: it would turn a weak answer
into no answer at all, which this project ranks below a bad answer nowhere — it
ranks it below *saying nothing*. Asking is what the path allows, and the same
`corpus_citation` event measures whether it takes.

## Verification

- 2 new loop tests: a corpus run that forces finalization is asked for its
  evidence and the request is the **last** user message; a run without a corpus
  keeps the plain prompt and never sees the corpus wording.
- 1 new mutation entry, observed red: the terminal prompt falling back to the
  plain one for a corpus run.
- Full fast suite, mutation table and doc lint re-run for the commit.
- **Not yet verified**: whether a 4B model complies at the terminal point. That is
  the next live run, and the `corpus_citation` event on the forced answer is the
  instrument — it recorded `answers_with_address=False` for both of the attempts
  above, so the baseline this change has to move is explicit.

## What remains

- **Convergence** (the hypothesis, not yet a fix): the turn header should say how
  many turns remain, and a corpus run that reaches its last turns without an
  address should be told once that "I did not find it" is a complete answer. The
  evidence that this is worth trying is the run above: 8 of 8 turns, forced.
- **The hatch's harness-side arm**: if the terminal fix makes the model state
  absence deliberately, the escape hatch becomes load-bearing immediately — and it
  is a literal-`coverage`-word test today, so a truthful paraphrase is refused.
  The fix then is to accept an absence claim when the run actually called
  `corpus_coverage()`, which the parent process knows because it served the call.
- **The read-only digest proof**, still due: three mining windows read the corpus
  after the last proof.
