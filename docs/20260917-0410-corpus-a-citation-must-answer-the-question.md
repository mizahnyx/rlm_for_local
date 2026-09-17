# A citation must answer the question, not merely have been served

**Created:** 2026-09-17 04:10
**Status:** implemented and unit-proved; **not yet observed against a live model** —
every measurement below is a fixture, and the first live run is the obligation this
record leaves open
**Supersedes nothing.** Continues the sequence in
`docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md`, which measured
that the label is served and ignored.

## Where this starts

Three facts were already established and are not re-measured here:

| Fact | Where it was measured |
|---|---|
| A citation must be an address a helper **served**; a fabricated one is refused | `docs/20260916-1540-corpus-citation-must-be-served.md` |
| Every hit carries how much of the **question** it covers (`covers n/m … (band)`) | `docs/20260916-1710-corpus-match-quality-signal.md` |
| The model was served **`weak=8`** — eight hits, 3.9 kB — and cited the hits anyway | `docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md` |

The third fact is what this change answers. The served-address rule closed
*fabrication* (a citation pointing at nothing); it says nothing about a citation
pointing at a passage that does not answer the question. On a complete index that
is the remaining failure: the search is **hard**, not empty — the model searches the
question's ordinary words, gets real hits, and nothing in the harness made the
consequence of citing them real rather than advisory.

## The rule

`RootLoop._cites_only_unanswering_evidence` + `_refusal_reason`, with the band map
in `REPLSandbox`:

1. **The band is read from the hit's own header line**, never from the snippet —
   `_served_bands()` splits each hit element at the first newline and parses the
   label out of `<address>  [labels]`. A passage that happens to quote the word
   `(weak)` cannot thereby acquire a band; the passage is the thing being judged.
2. **The strongest band per address wins** (`corpus_address_bands`), so finding the
   same passage again through a question it does not answer cannot demote it.
3. **An answer is refused when every address it cites was served `weak`/`none`**
   and it names no coverage. `NUDGE_CORPUS_WEAK_EVIDENCE` restarts the turn, on the
   existing `corpus_uncited_nudges` budget, then forced finalization.
4. **Three exemptions, each deliberate:**
   - one `strong`/`partial` citation is enough — only an answer whose *entire*
     evidence is weak rests on nothing;
   - an address with **no** band (handed over by `corpus_read`, or any hit from a
     question with no content words) is `unknown` and refuses nothing. This is
     AGENTS.md §1.8's corollary applied once more: a check that cannot see the
     truth says `unknown`, and unknown is not a verdict;
   - naming coverage still accepts the answer. Absence plus coverage remains the
     only route for a question the corpus does not hold, which is what keeps the
     refusal from being a trap.
5. **It is its own event.** `corpus_weak_citation` carries `band=` and
   `addresses=`; `corpus_uncited` keeps meaning "nothing checkable was offered".
   Fabrication, silence and a non-answer are three different failures and an
   operator counting one should not be counting another.
6. **The prompt states the rule** (`CORPUS_SECTION_LINES`): *"an answer whose
   citations are all weak or none is refused: the label is the harness's, and the
   harness reads its own label back."* A harness that judged by a rule it never
   gave the model would be a trap.

## What was verified

- **10 new tests.** Sandbox: header-line-only parsing (including the passage that
  quotes `(weak)` and gets no band), the band remembered from a search, the
  strongest band kept, an unlabelled address left unlabelled. Root loop: refusal
  then absence-and-coverage accepted; the absence arm accepted on the first
  submission (near-miss named as a near-miss, with coverage); one strong citation
  among weak ones accepted; a stopword-only question accepted; the refusal logged
  as `corpus_weak_citation` and *not* as `corpus_uncited`; the refusal budget
  bounded.
- **6 mutation entries, all red as required**, verified individually before the
  full table: the rule stops refusing; an unlabelled address counted as weak; the
  band map not remembered; the snippet allowed to label its own hit; last-write
  demotion; the weak refusal routed to the uncited nudge.
- **One existing test had its question changed, and that is evidence.**
  `TestCorpusUnsearchedNudge::test_an_unsupported_claim_is_not_accepted_the_first_time`
  ran on the question `"Question"` while citing a hit from
  `corpus_search('Cuicani')`; those two share no content word, so the search had
  labelled that hit `none` — and the new rule refused it. The test's premise was
  incidental (it is about the *unsearched* nudge), so its question is now the one
  the cited passage answers. Recorded because a guard that quietly changes what
  another test was asserting deserves to be visible, not silently patched.

## What is **not** verified

1. **No live model has ever been refused by this rule.** Every test above is a
   fixture. What the 4B laptop model does when the nudge arrives — cite better,
   state absence, or repeat the same near-miss until the budget runs out — is
   unknown, and this project has been wrong three times about what that model does
   with a prompt.
2. **Over-refusal is the risk, not under-refusal.** The `unknown` exemption bounds
   it (nothing the harness cannot label is refused) but does not measure it. The
   first live run must be read for **both** `corpus_weak_citation` counts **and**
   whether the run still terminated with an answer worth having.
3. **The bands are first approximations** (`strong` at ≥60% of the question's
   content words, `MATCH_STRONG_RATIO`). A `weak`/`none` verdict is the harness's
   judgement, not a property of the passage.
4. **One arithmetic consequence worth knowing before reading live events:** a
   question with **one** content word can only produce `strong` (the word is
   present) or `none` (it is not) — `weak` and `partial` are unreachable, because
   `covered >= total` wins first. So on a one-word question this rule is a
   present/absent test.
5. **The escape arm is still unexercised** (D4). The prediction is that this guard
   makes an absence answer *more* likely — it names "say the corpus does not contain
   it and quote `corpus_coverage()`" twice, in the nudge and in the prompt — but a
   prediction is not a result.
6. **The read-only guarantee was not touched by this change.** Nothing here reads
   the corpus; the band is parsed out of a tool result the harness already had.

## A second finding, from the verification itself

Cancelling the full mutation table mid-run left the mutation it was applying **in the
working tree**: `src/rlm_web/app.py` came back with `require_same_origin` deleted from
the `POST /jobs` dependencies, i.e. a state-changing endpoint without its origin
check, recoverable only by reading `git diff` (the run restores the file it read
*after* the test finishes, so a kill skips the restore). `git checkout --` fixed it.
`AGENTS.md` §3 already said an edit made during a mutation run is silently lost; it
now says the converse too, and that `git status --porcelain` is checked before
anything else after an interrupted run. This is a hazard *of the instrument*, not of
the code under it — the same shape as every trap in that section: a convention that
works until it silently does not.

## Next

The graded question, run live with the guard in place, read for
`corpus_weak_citation` / `corpus_citation` / `corpus_uncited` counts, turns and the
answer's shape — i.e. the same protocol as every earlier live run, plus one new
countable. Then D4's harness-side evidence (accept an absence claim when the run
actually called `corpus_coverage()`), which this change makes more pressing rather
than less.
