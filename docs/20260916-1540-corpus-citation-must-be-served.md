# A citation has to be an address the harness served

**Created:** 2026-09-16 15:40
**Status:** closed — the hole is shut, and the run that found it is the test case
**Supersedes nothing.** Answers the finding in
`docs/20260916-1445-corpus-turn-budget-buys-speed-not-provenance.md`.

## The hole

A four-turn corpus run read nothing — it printed **no addresses at all** — and
still ended with a `Citations:` line naming `notes/song.txt#L4000-4100`: a
well-formed address for a passage it had never been given. The citation guard
accepted it, because the guard's rule was *pattern*, not evidence: any string
shaped like `path#L<start>-<end>` counted as a citation. The groundedness check
(`cited ⊆ printed`) flagged it in the trajectory, but nothing refused it, and an
answer carrying a fabricated citation is worse than an answer carrying none —
it looks checkable.

## The fix, in the project's own shape

`REPLSandbox` now records what it **served** (`corpus_addresses_served`):

- every address a `corpus_search` returned (the hit lines carry them);
- the address a `corpus_read` resolved, when the read **succeeded** —
  deliberately not when it failed, or *asking* for an address would be enough to
  legitimise it, which is the whole defect in a different costume;
- nothing from the path helpers (`corpus_find`, `corpus_list`, `corpus_count`,
  `corpus_coverage`), which serve no addresses and offer no evidence for a claim
  about a passage.

`RootLoop._unserved_citations` diffs what the answer cites against that set, and
`_refuses_uncited` refuses any answer with a non-member — **including one that
also names coverage**, because the escape arm exists for absence, not for a
receipt that points at nothing. Each refusal is logged as `corpus_uncited` with
`unserved=N`, so the fabrication rate is countable across runs rather than a
thing someone notices in a transcript.

One vocabulary detail mattered and is worth recording: the harness had **two**
address regexes in play. `ADDRESS_IN_TEXT_RE` finds the offset half (`#L0-21`)
while the citation it must be compared against is the whole token
(`notes/song.txt#L0-21`) — so the first version of this guard refused *every*
citation, including correct ones. `ADDRESS_TOKEN_RE` (the path plus the offsets,
in `rlm_kernel/textindex.py`) is now the single extractor on both sides, which is
why the served set and the cited set can be compared at all.

## Verification

- 4 sandbox tests: a search serves what it returned; a successful read serves the
  address it was asked for; a **failed** read serves nothing; the path helpers
  serve nothing.
- 4 loop tests: a fabricated citation is refused and a served one then wins; a
  served citation is accepted first time; coverage does **not** excuse an invented
  address; the refusal record carries `unserved=1`.
- 2 mutation entries, both observed red: the guard reverting to pattern-only, and
  the sandbox forgetting what it served.
- Brought to light by fixing it: the loop's test fixture had **no text index**, so
  no test there could ever search — it now classifies and indexes its corpus, and
  the two older tests that cited an address they never obtained (one after
  `corpus_find`, one after `corpus_coverage`) now cite what they were served or
  take the absence arm, which is what those tests always meant.

## What this hands the wiki

A groundedness test that does not depend on the model's prose: an address in a
generated page either corresponds to something a helper served, or the page is
wrong about its own evidence. That is the provenance check RO7 needs on every
page, and it is now enforced at the answer channel rather than hoped for in the
prompt.

**Still open**: the relevance signal (a weak match and a strong one still look
identical), and the dynamic-corpus refresh gap this work exposed in passing —
`task_index_text` skips a source it already holds, so an edited file keeps its
stale text until something re-adds it with `replace=True`.
