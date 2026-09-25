# Gate 3: the description-spend decision — context for deciding

**Date:** 2026-09-24 (series ordering; the machine clock runs behind it). **Status:** decision
context, nothing decided. **Every corpus figure below is recording-derived** — measured on
2026-09-23 while `lunacode` was up, taken from this repository's records, and **not** re-measured,
because the box is away. Sources are named per figure.

## 1. The decision

Whether to spend generation on describing documents — at what scale, and on what evidence. The
honest frame is that this is two decisions wearing one coat:

- **Is a description useful at all?** Nothing in this project measures that. Every figure below is
  about cost and mechanical quality.
- **If it is, to whom?** The corpus is 2 882 822 text files. No budget reaches them, so the answer
  can only be *a selected set*, which is why RO6 exists.

## 2. What a description costs

Measured cold and warm on the same documents, 2026-09-23 (`docs/20260923-2100-ro6-the-cold-cost-and-a-call-in-flight.md`):

| document | fresh prompt | the same prompt again |
|---|---|---|
| 6 923 characters | **302.5 s** (5.0 min) | 18.2 s |
| 20 360 characters | **1 773.3 s** (29.6 min) | 22.4 s |

Where the time goes, from the server's own numbers: prompt processing at **5.38–8.25 tok/s**, decode
at **1.8–2.5 tok/s**, and a client/server residual of **0.02–0.52 s** — so nothing is hidden. The
79× difference between the two columns is llama.cpp's prompt cache, not throughput: the warm calls
reused 9 514 and 2 434 prompt tokens and evaluated **4**.

Projections — *arithmetic on those two measurements, not measurements themselves*:
cited set (13 documents) ≈ **1–2.5 h**; value set (143) ≈ **12–70 h**; the whole text corpus
(2 882 822) ≈ **28 years** at the fastest measured rate, ~160 at the slowest. The last figure is why
selection exists at all.

## 3. What a description *is*

From the eight usable calls recorded in `docs/20260923-1900-ro6-what-a-description-costs.md`:
214–464 characters out, 30–67 words, compression ≈ 2%, **no** output hit the 400-token bound, **no**
reply was boilerplate. Produced descriptions are indexed as **derived** text (so they are findable),
cached under a key that includes the model (so re-description is free and a second model does not
inherit the first's work), and the logs and traces **quote no document text** — that is a property of
the code, and it is what makes the metrics readable anywhere.

**Groundedness, calibrated:** 0.333–0.727 across those eight calls. The same function scores
**1.000** for a document's own opening (an implementation check) and **0.000** median for a
*different* document's opening (the floor). So a description is about the right document and is not a
copy of it — and the gap to 1.0 is paraphrase and framing versus drift, **which this metric cannot
separate and I will not present as if it could**.

Two structural limits worth carrying into any plan: a description is **one** level of contraction
(400 tokens) with no expander and no second level — directly relevant to whether Laya-style
controlled expansion is worth building (`docs/20260924-0010-gate-1-…`, §5) — and it is written for a
reader deciding *whether to open a document*, not for the wiki/LTM.

## 4. What the value set actually is

From `docs/20260923-1700-ro6-the-value-set-measured.md`: **143 documents**, of which **13 cited** by
an answer. By provenance class: code 44, prose 29, markup 24, documentation 22, other 18, vendored
3, data 3.

And a hard limit on the cheapest option: **6 of those 13 cited documents cannot be read through the
mount at all** (all `stat-refused`, cause unestablished — `docs/20260923-2200-…`). So "describe the
cited set" is, today, "describe seven of them".

## 5. What is not measured — the honest gap

- **Usefulness to a reader or to a later question.** Nothing measures it. This is the gap the
  decision actually turns on.
- **Whether descriptions surface.** They are indexed, but nobody has measured whether a description
  ever appears in a search result, let alone whether it helps.
- **The cost of fresh descriptions at scale.** Two cold measurements exist. Everything above is
  arithmetic on them.
- **Whether the sequence matters.** Describing documents *before* they are retrieved, versus serving
  them on demand, has never been compared.

## 6. The options, with what each costs and what it leaves unknown

| option | cost | evidence it produces | what it still leaves unknown |
|---|---|---|---|
| **1. Nothing further** | 0 h | none | usefulness (unchanged) |
| **2. A bounded experiment** — describe 6–10 documents, then ask the existing question set with descriptions available | **~1–3 h** fresh | the first usefulness signal, and whether descriptions ever surface in retrieval | whether it holds at 143 documents |
| **3. Finish the cited set** | ~1–2.5 h | descriptions of 7 readable cited documents | usefulness |
| **4. The whole value set** | **12–70 h** | 143 descriptions | usefulness — cost without the signal |
| **5. Unattended windows** | as 4, plus hours of unattended generation | as 4 | as 4, with a larger bill |

**If I had to pick:** option 2. It is the only row that buys the missing quantity rather than more of
a quantity already known, and it is reversible — re-running the same documents costs seconds,
because the cache holds them. Options 3–5 spend hours to produce something whose value is still
unknown afterwards.

The experiment, concretely: describe 6–10 documents at the measured rates; then re-run a question set
that already has recorded results, with the descriptions present as derived hits; compare citations
and coverage per question against the baseline; and — because usefulness is a judgement rather than a
metric — have **you** read five descriptions and say whether they are the thing you would want.
(They could not be quoted to me anyway: they are corpus-derived prose.)

## 7. What is already settled

Descriptions stay **out of default mining windows** (deliberate, `docs/20260923-2200-…`); the command
defaults to **3 documents** and can be restricted to the cited set; the engine **defaults to the
configured model** and takes another per invocation; **one model at a time** on this hardware; and
`rlm summarise` honours the same pause flag and lock as the queue worker.

## 8. What this document cannot tell you

Anything about whether the descriptions are *good prose*. Groundedness is a floor-detector; the
calibration says they are on-topic and not copied, and nothing more. Any stronger judgement needs a
reader, and any reader has to be where the corpus is.
