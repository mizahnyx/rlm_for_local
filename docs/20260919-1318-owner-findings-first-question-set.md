# The owner's findings from the first real question set, with the numbers under them

**Created:** 2026-09-19 13:18
**Status:** the owner's verdict on the first live question runs (RO17), recorded as given,
with a verification pass over the trajectories and the index for each finding that could
be measured. The verdicts are the owner's; the numbers are mine, and the two are kept
apart on purpose.
**Supersedes nothing.** The run's aggregate lines are in
`docs/20260918-0750-sampling-passages-and-probing-with-real-questions.md` §"What this
does not do, yet" — this is what came of actually reading the pages.

## The runs

Six questions, `Qwen3.5-4B-Abliterated`, `laptop` profile, `--max-turns 6`,
soft 90 s / hard 1 200 s, `temperature=0.0`, one model instance, on `lunacode`.
Total wall clock ≈ 2h08m. Pages: `~/rlm-derived/traces/questions/` (6 pages + index).

## The findings, as the owner wrote them

1. **Encoding matters for non-ASCII characters.** Files with Windows Latin codepoints
   have words with tilded vowels that are not found when the encoding does not match.
   This was question 1's problem.
2. **The model wastes turns realising that the search has to be done in the corpus**,
   not in the context or a file.
3. **The model wastes turns realising that `corpus_search` has no `limit` argument.**
4. **Question 2 should be considered successful**: too vague on the owner's part, and so
   it received no conclusive answer even with cited relevant data — but it shows the model
   reasoning with rigour.
5. **Questions 2 and 3 show correct reasoning and should be considered successful.**
6. **In some parts of the harness instructions, the model tries to input direct addresses
   instead of mnemonics**, and gets stuck in the process.
7. **MIME types matter, a lot.** Some nominally textual files (`.obj`, `.svg`) would be
   better understood by translating them first into an image rendering or representation,
   using either the model's multimodal capability or an ancillary lightweight vision model
   to describe them.
8. **An overwhelming quantity of the text is code or scripts**, and most of it consists of
   snapshots of old open-source projects.

## What the trajectories and the index say about each

### 1. Encoding — confirmed, and the population is bounded

`classification.encoding` already records the answer for every file the sniffer read:
of **2 882 822** text files, **2 834 521** are `utf-8`, **46 107 cp1252**, **628 latin-1**,
1 566 with no encoding recorded. So **46 735 files (1.6% of the text corpus)** are the
population the owner hit — a bounded, known set.

The mechanism is not diacritic folding: the index is FTS5 `unicode61 remove_diacritics 2`,
which folds accents at both index and query time. It is the **decode**: `add_text` indexes
`text[start:end].decode("utf-8", "replace")` and `TextIndex.read` uses the same call, so
cp1252 bytes become U+FFFD and `canción` is indexed as something like `canci` + separator
+ `n`. The search then cannot match it, and the honest answer the model produced — "not
found, here is the coverage" — was correct for what the index held.

### 2. Turns lost before touching the corpus — confirmed and quantified

The turn of each run's **first** `corpus_served` event, against `max_turns=6`:

| run | turns used | first helper turn | turns before the corpus |
|---|---|---|---|
| 1 | 5 | 4 | 3 |
| 2 | 6 | 4 | 3 |
| 3 | 6 | 3 | 2 |
| 4 | 6 | 3 | 2 |
| 5 | 6 | **6** | 5 |
| 6 | 5 | 3 | 2 |

So **2 to 5 of a 5–6 turn budget is spent before the model touches the corpus at all**, and
in the worst case the only helper call of the run happens on its last turn. This is the
single largest identified waste in the runs, and it is now a number: *turns before the
first helper call*. Nothing in the current trajectory renders it, so the first step is to
give `render_summary` that field.

### 3. `limit=` — confirmed, and it accounts for the one traceback per run

The prompt does document `corpus_search(query, k=8)`, and the worker's wrapper is
`_harness_corpus_search(query, k=8, include_vendored=False)` — so a call with `limit=`
raises Python's own `TypeError`, which reaches the model as a traceback in the cell's
stderr. Every one of the six runs logged **exactly one `stderr` guardrail** and no other
error, which is consistent with that being the mistake, once per run, in most or all of
them. It is also the class of failure the harness is supposed to make cheap: the model
spends a whole turn discovering a parameter name that a one-line teaching message could
have given it as the tool result.

### 4 and 5. Questions 2 and 3 — the owner's verdict, recorded

Recorded as the owner's judgement: question 3 is the run whose citations answered the
question (`cited_answering=2`, both exact, against `strong=8` served, and it took two
`corpus_uncited` refusals before the model produced them — the guard working as designed),
and question 2 served eight `strong` hits and cited none of them. Both are counted as
successful on reasoning; the owner's note is that question 2 was under-specified by *them*,
which is why no conclusive answer was possible.

### 6. Addresses vs mnemonics — the gate RO13 was waiting for is now open

RO13 (mnemonic addresses and structured hits) was recorded as **data-gated**: Run A's
forced answer cited no address at all, so the size of the prize was unmeasured. The owner,
reading these pages, reports the model getting stuck trying to handle direct addresses.
That is the missing evidence, and it moves the decided order — **structured hits first,
aliases second** — from "designed, waiting for data" to "actionable".

### 7. MIME and rendering — confirmed and sized

Files whose meaning is visual or spatial, which no amount of decoding will fix:

| family | files |
|---|---|
| `.svg` | 17 439 |
| `.obj` | 1 739 |
| `.stl` | 1 348 |
| `.dxf` | 44 |
| **vector + 3D, textual by extension** | **20 570** |

Plus `media` (489 066 files) and `binary` (724 571), which the sniffer already excludes
from text. The owner's proposal — render, then describe with a multimodal model or a small
vision model — is a **new task type** in the mining vocabulary (`render_preview`, then
`describe_image`), not a change to the text path. It needs an owner call on tooling and on
whether a vision model is available on this host.

### 8. The corpus is mostly code — confirmed, and it changes the wiki's arithmetic

Of 4 972 609 indexed entries:

| family | entries | share of entries |
|---|---|---|
| code (`.js` 670 466, `.py` 159 148, `.c/.h` 129 166, `.java` 110 280, `.php` 5 171, `.sh` 4 924) | **1 079 155** | 21.7% |
| markup (`.html` 182 802, `.xml` 74 086) | 256 888 | 5.2% |
| data (`.json` 113 662, `.csv` 503) | 114 165 | 2.3% |
| **prose** (`.md` 63 412, `.txt` 42 404) | **105 816** | 2.1% |
| documents (`.pdf` 4 319, `.docx` 614) | 4 933 | 0.1% |

Against the 2 882 822 *text* files, code alone is ≈37% and code + markup + data ≈50%; the
human prose is ≈**3.7%**. Three consequences worth stating plainly:

- **RO6's arithmetic gets much better, not worse.** "Summarise the corpus" was measured in
  years because it assumed every text file needed a model. The prose-and-documents core is
  ~110 000 files, and a top-ranked fraction of *that* (1% ≈ 1 100 files at ~5–10 min each)
  is days, not years. Code and scripts can be summarised structurally — module docstrings,
  READMEs, dependency manifests — with no model at all.
- **`is_vendored` deserves a measurement.** It down-weights vendor/build/cache paths by
  path pattern, which is a rule written when the corpus was assumed to be documents. With
  half the corpus being code, the default filter may be hiding the majority of *anything*.
  The fraction of indexed chunks that are `vendored=1` is unmeasured — and counting 29M
  rows is the expensive shape this project has already paid for twice, so it belongs in a
  mining window, not in an interactive command.
- **RO1's "what counts as text" decision now has numbers under it** — which was its stated
  precondition.

## What this does not say

- Nothing here measures retrieval quality on a *stable* question: five of the six runs ended
  `forced=True`, so their delivered answers came from forced finalization, which bypasses the
  citation guard by design (it asks for evidence, then records whatever came back). An
  accepted answer with no citations is therefore possible and is not a guard failure.
- The escape hatch ("the corpus does not contain this, here is the coverage") **fired live
  once, verifiably**: the one run that ended on a *voluntary* submission had no citations at
  all and was accepted, which the guard only does because the answer named the coverage line
  (`_refusal_reason` returns None on the coverage marker). A second run's delivered answer
  also named coverage, but that answer came from forced finalization, where the guard does
  not apply — so it is not evidence of the hatch. This closes the item `AGENTS.md` §2 has
  carried as *still unverified* since 2026-09-17, on one observation rather than two.
- The 46 735 cp1252/latin-1 files are *known*, not *lost*: the bytes are intact and the
  encoding is recorded. What is missing is a decode step in the index and the read path.

## The next work, in the order the evidence supports

1. **Decode with the recorded encoding** in the text index and the read path, then re-index
   the 46 735 files. Clear defect, bounded population, testable without a model.
2. **Teach instead of traceback** for a mistyped helper keyword, generically across the
   `corpus_*` helpers, and say the parameters in the prompt. The metric is the `stderr`
   guardrail count, which should go to zero per run.
3. **Measure the orientation cost**: add *turns before the first helper call* to
   `render_summary`, then try a corpus-run turn-1 nudge and re-run **one** question.
4. **Structured hits** (RO13, first half), now that the owner's read supplies the evidence
   the item was gated on.
5. **A new task type for visual formats** (RO18): render, then describe — owner call on
   tooling and the vision model.
6. **Schedule the vendored-fraction count** and the code/prose split of the *indexed*
   chunks, in a mining window, since both need a scan of the chunk table.
