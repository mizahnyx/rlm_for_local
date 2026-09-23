# The density hunch, measured — and it does not hold as stated

**2026-09-23.** The owner's hypothesis: *"prioritising from 'more prose' to 'less prose' seems
to me always correct, regardless of the context. Prose being considered more information-dense
than code, so we prioritise in order of information density, and thus we avoid overwhelming the
small model with too much low information-density hits. This is my hunch, but I can be
deluded."*

It was testable from what the harness already records, and the answer is: **directionally right
at the extremes, not supported as a general ordering, and the likely mechanism is lexical
overlap rather than density.**

## The measurement

Every served hit is recorded as `{address, alias, band}`, and the address says what the passage
*is* (`provenance_class`). Band is a function of `covers n/m of the question's words`, so if
prose is denser *for the question*, prose hits should carry better bands. Over all ten recorded
prose-set questions (6 in the first set, 2 + 2 in the A/B runs), `strong=3, partial=2, weak=1,
none=0`:

| class | n | mean band | citable (partial+strong) |
|---|---|---|---|
| other | 17 | **1.47** | 52.9 % |
| markup | 35 | **1.14** | 42.9 % |
| prose | 19 | **1.05** | 26.3 % |
| code | 54 | 0.85 | 14.8 % |
| documentation | 16 | **0.50** | 12.5 % |

Prose against everything else: **1.05 vs 0.98 mean band, 26.3 % vs 27.9 % citable.** The
aggregate difference is inside the noise, and on citable share prose is marginally *worse*.

## What that says

- **The extremes support the hunch's direction**: prose (1.05) does beat code (0.85), and much
  more decisively than the aggregate suggests when the two are compared directly.
- **But the ordering breaks where it matters**: **documentation — prose-shaped text shipped
  with code — is the worst class of all (0.50, 12.5 %)**, below code. And **markup beats prose**
  (1.14, 42.9 %). A rule that ranks "more prose first" would put documentation *above* markup
  and code, which is the opposite of what this data wants.
- **The mechanism is probably not density at all.** Band counts how many of the *question's*
  words a passage contains. Markup wins because HTML and XML carry titles, headings and
  attribute values — the question's vocabulary, lexically present. Documentation loses because
  licence, changelog and boilerplate prose is wordy and *about* nothing the question asked.
  That is a statement about **word overlap**, not about information density, and it points the
  lever at query construction and question wording rather than at provenance ranking.

## Caveats, stated because n is small

10 questions, one model (4B), one corpus. The classes are confounded with the questions — a
question whose wording matches markup will serve markup with high bands regardless of class.
Eight `unknown` bands are `corpus_read`s (a read serves an address with no band), not bad hits.
This is a first measurement, not a distribution, and the decisive test remains the ranking A/B
(same questions under bm25, prose-tie-break, and a smaller k).

## The change this produced

The owner asked for the band and the label line on `corpus_served`:

- **The band was already there.** Each entry has carried `{address, alias, band}` since RO10.
  My earlier claim that the address↔band pairing was unrecorded was **wrong**, and wrong three
  times over in the same measurement: I first parsed the human-readable label line instead of
  the structured list, then read `Path("a/b.txt#L0-9").suffix` without stripping the fragment,
  then pointed the scan at `…/questions/questions`, a directory that does not exist. Each
  failure looked like a missing field. The lesson is the one this session keeps teaching: check
  the *shape* of the record before concluding the record cannot answer the question.
- **The label is now there too**: every served entry carries `provenance`
  (`documentation`/`markup`/`code`/`data`/`vendored`, or null for prose), so a trajectory
  records what was served in the harness's own vocabulary rather than requiring a reader to
  re-derive it from the path.

**And the work found a real defect**: `provenance_class` did not strip the byte-range fragment,
so `Path("notes/a.txt#L0-9").suffix` was `.txt#L0-9` and **every address classified as
`other`**. The hit-line labels were unaffected (they pass `hit.source`, a path) — which is
exactly why it could sit in the live path unnoticed: one caller passed paths, the other
addresses. Caught by the integration test that asserts a *served* `.txt` is called prose, and
invisible to the unit tests, which only ever passed bare paths. Fixed, with an address-shaped
unit test and two mutation entries, both red.
