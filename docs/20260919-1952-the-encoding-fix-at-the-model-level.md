# The encoding fix, at the model level: the same question, answered instead of declined

**Created:** 2026-09-19 19:52
**Status:** point-in-time measurement. The owner's finding 1 — accented words not found in
Windows-Latin files — is now verified fixed twice over: in the index
(`docs/20260919-1708-the-encoding-repair-measured.md`) and here, in what a model does with
it.
**Redacted 2026-09-19 23:05**: this document originally named the owner's question by its
id. A question id is an identifier derived from the corpus — a name out of the prose the
harness read — so putting it in a public repository breaks `AGENTS.md` §1.9. The id is
replaced by "question 1", and `scripts/check_privacy.py` now fails the build if a token
from the local list reappears. The redaction, and the fact that the pushed history still
contains it, are recorded in `docs/20260919-2305-a-question-id-reached-the-public-repository.md`.

**Supersedes nothing.** It is the model-side half of the RO19 fix.

## The runs

The owner's question 1 of the first question set, re-run on the
repaired index. Same model (`Qwen3.5-4B-Abliterated`), same profile (`laptop`), same
question, same corpus — **but not the same limits**, and that matters for reading it:

| | before the repair | after the repair |
|---|---|---|
| limits | soft 90 s / hard 1 200 s | soft 60 s / hard 600 s |
| turns | 5/6 | 6/6 |
| forced | **False** (voluntary submission) | True (forced finalization) |
| first helper turn | 4 | 4 |
| helper calls | 2 | 3 |
| searches | 1 | 1 |
| **bands served** | **none** | **strong=4** |
| **cited answering** | **0** | **1** (and `cited_exact=1`) |
| served, not cited | 0 | 3 |
| refusals | 0 | 0 |
| answer length | 155 chars | 419 chars |
| **answer names coverage** | **True** | **False** |
| wall clock | 326 s | 1 577 s |

## What that says

Before the repair, the search on this question returned hits that carried **no
match-quality band at all**, and the model answered honestly from the escape hatch: *the
corpus does not contain this, here is the coverage* — a truthful answer to a question the
index could not answer, because the word it needed was in a cp1252 file and had been
indexed as fragments.

After the repair, the same search returns **four `strong` hits**, and the delivered answer
**cites one of them exactly** — an address that was served *and* whose band answers the
question. The answer no longer claims absence, and it is 2.7× longer.

This is the first evidence that the encoding fix changes what the harness can *do*, not
just what its index holds. It is also the cleanest available answer to the owner's
original question about question 1.

## What it does not say

- **n = 1, and not a controlled comparison.** The limits differ (soft 60/600 against
  90/1200), the turn count differs, and this project has recorded repeatedly that this
  model's behaviour is not stable across router cache states. The *index* is the variable
  that was deliberately changed; everything else moved with it, so this is evidence, not a
  measurement of effect size.
- **The answer was delivered by forced finalization** (`forced=True`), which bypasses the
  citation guard by design: the harness asked for evidence and recorded what came back. The
  citation is genuine (it was served, and it answers the question) but the run did not
  submit it voluntarily within its six turns.
- **Nothing here says the answer is *right*.** Whether the cited passage actually answers
  the owner's question is exactly the judgement the pages exist for: the page is in
  `~/rlm-derived/traces/questions-after-repair/`, named after the question's id, and the
  passage behind the cited address is on it.
- One structural thing is unchanged: the first helper call still comes on turn 4, so the
  orientation cost the owner identified is untouched by this fix.

## A tool defect this run exposed, fixed in the same commit

The first attempt to re-run this question was given as `--only <the question's id>` without
`--questions`, and printed `No questions to run.` — `--only` filters *the set that
`--questions` selected*, which is the three built-in aggregate questions unless a file is
named. Nothing in that message said so.

`select_questions` now refuses with the set it searched and the ids that set holds:

```
Error: no question id in built-in (aggregate questions only) matches '<the id you asked for>'.
Ids in this set: how-many-entries, how-much-is-indexed, what-kinds-of-material.
If your own set is a file, pass it with --questions PATH.
```

One test covers the message (it must name the set, the id asked for, what the set holds, and
`--questions`), one covers the selection semantics, and one mutation entry restores the
silent version and goes red.
