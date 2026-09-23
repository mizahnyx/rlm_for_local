# The exemplar was the quest: neutralised, and re-measured

**2026-09-22.** Two owner findings from reading the rendered pages, and the fixes, with the
verification that makes them more than plausible.

Aggregates only. Questions, answers, passages and addresses stay beside the corpus
(`~/rlm-derived/questions/`, `prompt-ab/`).

## Finding: the model was imitating the harness's own example

The owner: *"on Prose 002 and Prose 003 the model just lost sight of the goal and started
asking and searching for a 'vault access code'."* The cause was in the harness.
`FEWSHOT_SUBMISSION_EXAMPLE` — the worked example whose whole job is to teach *submit the
moment the evidence is verified* — asked **"What is the vault access code?"** and answered
`KX-2210`. A 4B model given a broad question, with nothing concrete to hold, imitated the one
concrete goal in its prompt.

The exemplar was **kept**, because it is what produced the only voluntary submission in the
set (question 4), and its content was changed: it now asks for a *sample token* it calls an
example of the shape of a run rather than a task, its token (`EXAMPLE-0000`) matches nothing,
and the assistant states the discipline the failure lacked — *"I will not go looking for
anything the question did not ask for."*

## The A/B, same two questions, same model

| | prose-002 before → after | prose-003 before → after |
|---|---|---|
| wall clock | 3 892 s → **675 s** | 1 598 s → **509 s** |
| searches | 3 → **1** | 3 → **1** |
| bands served | none 15 → weak 5 | none 15 → weak 5 |
| citations | 0 → **1 exact** | 0 → **1 exact** |
| `refusals_uncited` | 1 → 3 | 0 → 3 |
| hard timeouts / extensions | 2 / 2 → **0 / 0** | 0 / 2 → **0 / 0** |
| **quest words** (`vault`, `access code`, `kx-`) | — → **0, 0, 0** | — → **0, 0, 0** |

Both questions stopped chasing the exemplar's scenario and both submitted an answer carrying
an **exact** address, where before they cited nothing at all.

## What is verified, and what is not

**Verified**: the imitated-content failure is gone (zero quest words in both trajectories);
the model searched **once instead of three times**, which is a behavioural difference the
prompt can carry; and both answers cite an address that matches a served one exactly
(`cited_exact=1`, `cited_unserved=0`, `cited_unmatched=0`).

**Not verified, and labelled rather than claimed**: the **wall-time reduction is confounded**.
The first run happened against a cold-cache index and this one after hours of use — and RO21
measured the same query at 0.02 s warm against 101.24 s cold. The searches *are* fewer, which
is attributable; the five-to-eight-fold time difference is not, and the honest reading is that
part of it is the page cache.

**Also worth stating**: `cited_answering=0` and `cited_non_answering=1` on both. The address
is real and exact, but every hit served was band `weak`, so the audit says the citation does
not answer the question. The weak-evidence refusal did **not** fire (`refusals_weak=0`) while
`refusals_uncited=3` did — so what happened is that three uncited refusals pushed the model
into citing, and it cited a weak passage. Whether the weak path should have refused as well is
a question the aggregates cannot settle here.

`n = 2`. This is a strong signal, not a distribution.

## Second finding: a hit line now says what it is

The owner: *"most of the prose cited is in fact vendored code documentation, instead of just
free prose."* Measured on the set: of **84 distinct addresses served**, **30 were markup**
(`.htm` 12, `.dtd` 7, `.html` 6, `.xml` 5), **23 carried a documentation word** in the path,
and only **5 were plain `.txt`** — and the path-marker vendored filter flagged **0 of 84**.
That was visible only by reading fifteen rendered pages, which is too late.

`provenance_class` / `provenance_label` now name what a passage is — `vendored`,
`documentation`, `markup`, `code`, `data`, `prose` — on the hit line, in both builders
(`TextIndex.format_hits`, `CorpusBridge.handle_search`). Precedence is deliberate: vendored
first (the strongest statement available), then documentation *paths*, then the suffix groups,
and only then prose — so an extensionless file with nothing else to say about itself is called
prose, which is what this corpus's free prose mostly looks like. Prose stays unlabelled, since
a label on every hit is noise.

**It is a label, not a filter, on purpose.** Which passages a search returns is a ranking
decision and therefore the owner's call; naming what came back costs one string and makes the
finding checkable on every hit from now on. Making search *prefer* free prose is the open
question this label was built to inform.

## Guards

- Exemplar: `test_the_submission_example_is_not_a_quest` (no `vault`/`access code`/`secret`/
  `password`/`server room` in the block, and it must say it is an example),
  `test_the_submission_example_keeps_the_lesson` (the same-turn lesson and the scope line are
  demonstrated). Both mutation entries red.
- Provenance: 4 tests, 2 mutation entries red. One test was wrong on its first version — it
  expected `build/docs/index.html` to be documentation when `build` is a vendored marker here —
  and the correction is recorded in the test rather than quietly applied.

Living doc `docs/extensibility-guide.md` updated to the new exemplar; the dated records that
quote the old wording are history and were not touched.
