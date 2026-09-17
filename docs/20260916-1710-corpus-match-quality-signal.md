# The relevance signal: a hit now says how much of the question it covers

**Created:** 2026-09-16 17:10
**Status:** implemented and unit-verified; its live effect is the next measurement
**Supersedes nothing.** Implements the item the previous records identified as the
root cause of both open behaviours.

## Why

Against a **complete** index an unanswerable question is search-**hard**, not
search-empty. A model asked about the Zxqvarn Protocol searches *orbital*,
*tether*, *ratified*, gets real hits — passages containing the words that do not
answer the question — and nothing in the result said how weak those matches were.
Five weak hits looked exactly like five strong ones, so the run kept searching
(8 of 8 turns, twice) and at a shorter budget invented a citation to look
finished. Both open behaviours trace back to a model that cannot tell "these are
not an answer" from the corpus's own output.

## What it is, and the one design error it caught

Every hit is labelled with how much of the **question** the passage covers:

```
<path>#L<a>-<b>  [file, covers 1/4 of the question's words (weak)]
    <snippet>
```

`content_terms()` takes the question's content words (stopwords and 1–2 character
tokens dropped, ES + EN, deduplicated, ordered); `term_coverage()` counts how many
appear in the passage (word-start match, so `protocols` counts for `protocol` and
`ratification` does not count for `ratified` — a documented lower bound);
`match_quality()` bands it `strong` / `partial` / `weak` / `none` / `unknown`.

**The error worth recording**: the first version measured the hit against the
*search query*. That is vacuous, because the FTS expression is an **AND** — every
hit necessarily contains every search word, so the label would have read `strong`
for everything and told the model nothing. What makes the number informative is
comparing the passage with what was *asked*, so the run's question is plumbed into
the bridge (`CorpusBridge.question`, set by the CLI from `args.query`) and the
label is omitted entirely when there is no question, rather than faked from a
number that is true only by construction.

`match_quality` also had to stop punishing short questions: covering **every**
content word is the strongest match there is, whatever the count, so `1/1` is
`strong` and `1/6` is `weak`.

Two structural decisions, both from earlier mistakes:

- **The label rides on each hit; there is no footer.** `len(hits)`, `hits[0]` and
  iteration are the contract, and a footer would corrupt exactly the thing the
  first live run was broken by. `match_note()` states the set-level verdict as a
  single sentence where a list element is the right place for prose: the
  no-hits case.
- **`unknown` exists.** A question with no content words ("who is it") cannot be
  judged, and saying `weak` would be a confident wrong answer about the result the
  model is holding — the same rule as the coverage snapshot.

## Verification

- 12 unit tests on the vocabulary: stopword and length filtering, dedup and order,
  case-insensitivity, word-start but not word-inside matching, all seven band
  boundaries, `1/1` strong, a stopword-only question being `unknown`, and the note
  carrying its numbers as well as its verdict.
- 4 bridge tests: a hit covering one of the question's four words is `weak`; a hit
  sharing **no** word with the question is `none`; a miss carries
  `match quality: none`; and with no question, no coverage is claimed.
- 2 mutation entries observed red: the label measuring the search instead of the
  question, and every match being called `strong`.
- The prompt now explains the label and what to do with it, with a test asserting
  the explanation is there — a label the prompt does not explain is decoration.

## What is not verified, and what is next

**The live effect.** The prediction is testable and will be tested: on the
same unanswerable question, a run should now see `weak` or `none` on every hit and
either say "the corpus does not contain this" or state coverage — instead of
exploring to its last turn. The baseline is recorded (8/8 turns, forced, 8–12
helper calls, one fabricated citation in six short-budget runs).

**The band boundaries are first approximations** (`strong` at ≥60% of the
question's words, `partial` at ≥2 words). They are stated as constants with the
reasoning beside them so they can be argued with and calibrated against real
questions rather than tuned by feel.
