# The first prose-derived question, measured

**2026-09-22.** The sampler change and the drafting pipeline exist so that a question set can
be drawn from *real prose* in this corpus and run. The first question of the first such set
has finished, and it is the first measurement of its kind: a question a local model wrote
from a passage, answered by a different local model, against the read-only corpus.

Aggregates only. The question, the answer, the passages and the address mapping stay beside
the corpus (`~/rlm-derived/questions/`, `prose-drafted.tsv`, `.sources.tsv`, `traces/`).

## The set

Six questions, drawn as prose and drafted locally by **`Qwen3-4B-2507`** — deliberately not
the model that answers (`Qwen3.5-4B-Abliterated`), so a poor citation rate cannot be blamed
on the drafter and the answerer being the same model. Structural checks on the set itself:
6 of 6 end in a question mark, **none names a file, a folder or a path** (the one instruction
whose violation makes a question unanswerable by design), and the ids are `prose-001` …
`prose-006`.

## Question 1 (`prose-001`): the number that matters

| | |
|---|---|
| wall clock | **3 933 s (65.6 minutes)** |
| turns | 6/6, **forced finalisation** |
| helpers served | 15 — `corpus_search` 8, `corpus_read` 3, `corpus_find` 3, `corpus_coverage` 1 |
| hits served | **50** — bands `partial` 10, `weak` 30, `none` 10 |
| **citations** | **0** (`cited_answering` 0, `cited_exact` 0, `cited_unserved` 0, `cited_unknown` 0) |
| served but not cited | **50** |
| refusals | uncited 0, weak 0 |
| audit | complete |
| answer | 777 characters, names the coverage line, contains no address |

The audit is complete, so this is not an instrumentation gap. Three things follow, and only
the first is comfortable.

1. **The harness engaged properly.** Eight searches, three passages read, thirteen other
   helper calls — the model used the corpus rather than answering from memory, and the audit
   can prove what it was served. On the earlier *aggregate* question set, one question
   searched **zero** times; here there is real traffic to audit.
2. **Citable evidence was available and went uncited.** Ten of the fifty served hits were
   band `partial` — sufficient, by the harness's own rule, for a citation to be acceptable.
   Nothing was cited. This is the difference from the aggregate set, where the *instrument*
   could not ask for a citation: here it could, and the answer still cited nothing.
3. **The escape hatch is why no refusal fired.** The answer names the coverage line, which
   `NUDGE_CORPUS_UNCITED` accepts by design — so `refusals_uncited=0` is the guard behaving
   correctly, not the guard failing to notice.

So the honest reading is: **a 4B model, given a question drawn from prose and fifty served
passages, answered by describing the coverage instead of pointing at a passage**, and the
harness accepted that for exactly the reason it was allowed to. Whether that is a prompt
problem, a model-size problem or the correct behaviour for a question whose answer it could
not locate is now a measurable question — which it was not before today.

## What this costs, and what it implies for the loop

**65.6 minutes for one question**, of which the harness attributes little to itself: the
earlier diagnosis stands, so the cost is the model decoding at 3–6 tok/s across six turns
plus cold index reads. A six-question set is therefore a **six-hour** run. That is a real
constraint on the owner's evaluation loop and it is the reason the aggregate set (three
questions, 38 minutes) looked affordable and this one does not.

## What is not yet done

Questions 2–6 were still running when this record was written; question 2 had started. The
trajectories are rendered beside the corpus as they complete
(`rlm trace render ~/rlm-derived/questions --out-dir ~/rlm-derived/questions/traces …`), and
the set's remaining measurements belong in a follow-up record rather than in this one.

Also recorded, because it happened four times today and this is the only one caught before it
reached a document: a structural check of the set first reported its ids as malformed. That
was my regex (`r'prose-\\d{3}'` in a raw string matches a literal backslash), not the set.
The set was fine; the probe was wrong.
