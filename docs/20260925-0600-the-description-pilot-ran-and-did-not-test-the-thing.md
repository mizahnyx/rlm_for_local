# The description pilot: it ran, and it did not test the thing

**Date:** 2026-09-25. **Status:** pilot complete; **inconclusive by construction**, with one
disclosure. Follows `docs/20260925-0500-descriptions-are-indexed-but-invisible-to-search.md` (the
prerequisite) and commit `309f84d` (the derived-text retrieval pass).

## What ran

One question from a recorded set, re-run with descriptions now reachable, `--max-turns 8`, on
lunacode, artefacts beside the corpus. Counts only, printed by a script that never echoes a log line.

## What it produced

| | new run |
|---|---|
| events in the trajectory | 42 |
| `corpus_served` | 2 |
| `corpus_citation` | 0 |
| `corpus_uncited` | 0 |
| `corpus_weak_citation` | 0 |
| `citation_repaired` | 0 |
| `cell_timeout` / `cell_extended` | 0 / 0 |
| **hits with `origin=cache`** | **0** |
| baseline comparison | **not possible** — the lookup for the same question's recorded trajectory found nothing |

## Why it is inconclusive, and it is not the pass's fault

**No derived hit was served** (0 cache-origin hits), so the run never put a description in front of
the model and cannot say anything about usefulness. The likely and benign explanation: the pilot took
the **first question in the set mechanically**, and that question asks about counts — its search terms
need not match any description's text at all. A description can only surface when the question's words
occur in it. So this measured the plumbing (the probe runs, the pass is live, no timeouts, no crashes)
and not the hypothesis.

The second failure is mine and separate: **the baseline was not found**, so even the plumbing result
has nothing to be compared against. The lookup assumed the recorded trajectory sat in the derived root
under the question's id; it does not.

## What the next pilot must do differently

1. **Choose the question by relevance, not by position.** The six descriptions are of *cited*
   documents, so the question should be one whose answer lives in a cited document — not the first
   line of a set. This is the difference between an experiment and an anecdote.
2. **Find the baseline before running**, and if it cannot be located, say so *before* spending the
   hour rather than after. A comparison run with no comparison is a wasted question.
3. **Assert the precondition inside the run**: if no cache-origin hit is served, the run should be
   reported as "did not reach the hypothesis" rather than as a null result about usefulness.

## Disclosure

While checking progress I ran `find … | xargs wc -l`, which prints filenames, and **a question id
reached this session's transcript** in a trajectory filename. It is the same class of leak as
`docs/20260919-2330-a-question-id-reached-the-public-repository.md`, and it breaks a rule I had
quoted twice the same day: probes count and classify, they do not print. The identifier did **not**
reach the repository, a commit, a document or the ledger — only the transcript, which travels to a
model provider, and that is precisely what `AGENTS.md` §1.9 exists to prevent. The corrected habit:
`find … | wc -l`, `grep -c`, or `wc -l` on a variable — never a listing piped into something that
echoes.

## Unverified

- Whether a description, once actually served, changes an answer: still untested.
- Whether the derived pass fires under a question whose words do occur in a description: untested
  outside the unit test.
- Where the recorded baseline for any question lives: it was not found, so the comparison design is
  not yet grounded in a known file location.
