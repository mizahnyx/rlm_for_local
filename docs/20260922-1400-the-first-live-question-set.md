# The first live question set, and the prerequisite in front of it

**2026-09-22, 13:57:49.** RO17's first live question set has run. Aggregates only: the
answers, the passages and the question ids stay in the trajectories beside the corpus
(`~/rlm-derived/questions/`, rendered to `traces/`). The three ids quoted here are the
built-in aggregate questions, which live in the repository already.

## The warmth prerequisite, closed as diagnosed

| fact | value |
|---|---|
| `journal_mode` | **wal** — already on, so RO21's "run the index in WAL mode" candidate is satisfied |
| `mmap_size` | 0 |
| `cache_size` | -2000 (≈8 MB) |
| index bytes | 41 159 192 576 (38.3 GiB) |

A **warm pass cannot generalise** here, and RO21's own numbers say why: a search reads only
~44 KB, and the same query is 0.02 s warm against 101.24 s cold. The cost is disk latency at
single-digit MB/s on a LUKS volume, not index size — the pages a question needs are
unknowable in advance, so there is nothing to pre-load that would cover the next question.
What a live run actually needs is permission for cold I/O to finish, which RO16's two limits
provide. This run used them (`soft 60 s / hard 1200 s`) and recorded **no `cell_timeout`
events at all**.

## The run

`laptop` profile, `Qwen3.5-4B-Abliterated` (already loaded), max 6 turns, built-in aggregate
questions — 3 of them, run 13:19:13 → 13:57:49.

| # | wall | turns | forced | searches | served | not cited | bands | refusals |
|---|---|---|---|---|---|---|---|---|
| 1 | 953 s | 6/6 | False | 3 | 6 | 6 | none 6 | 0 |
| 2 | 262 s | 3/6 | False | **0** | 0 | 0 | — | 0 |
| 3 | 1 092 s | 6/6 | **True** | 4 | 20 | **20** | none 15, weak 5 | **uncited 1** |

All three: `answers=1`, `audit=complete`, and **`cited_answering=0`, `cited_exact=0`,
`cited_unserved=0`, `cited_unknown=0`** — not one address was cited by any of the three.

## What this establishes

- **The plumbing works end to end on the real corpus.** Every run produced an answer, the
  audit is complete on all three, coverage was served, and the uncited refusal **fired once**
  (question 3) exactly as designed rather than never or always.
- **No cell died on its budget.** The cold-index cost that killed cells in the RO13 run did
  not appear here, because the hard limit was set to tolerate it.
- **Question 2 answered without searching at all** (3 turns, 0 searches, 1 served helper):
  for "how much is indexed", reading the published coverage is the grounded path, and that is
  a legitimate answer rather than an ungrounded one — the unsearched nudge is evidence-based
  and did not fire.

## What this does not establish, and the trap in the question set

**Zero citations across three runs.** The tempting reading is "the 4B model will not cite",
and the run does not support that conclusion, because *the instrument cannot ask for one*:
these are **aggregate** questions, and their answers come from the coverage snapshot
(`corpus_coverage`), not from a passage that can be cited. Two further facts point the same
way: question 1's six served hits were all labelled band `none`, and question 3's twenty were
15 `none` and 5 `weak` — content-word searches for "how many entries are there" find passages
that do not answer it, so there was never a strong citation available to make.

So the honest statement is: **the harness completed, the guards fired, and this question set
cannot test citation.** Whether the model can find and cite a passage remains unmeasured, and
it needs a set derived from passages — which lives beside the corpus and needs the owner
(RO17's own next step, and the reason `rlm corpus sample` exists).

Two smaller observations, recorded rather than chased: question 3 hit `forced=True` at 6/6
turns, and the uncited refusal fired once *and did not change the outcome* — the forced
answer still cited nothing. That is the refusal doing its job (it fires, it is counted, it is
not a wall) rather than a defect, but it is the first live instance of a refusal that did not
lead to a citation.

## A third disclosure, same mistake, during monitoring

Monitoring this run put question ids into the session transcript twice more: once from a probe
that listed the trajectory directory, and once from the run script's own `ls`. The ids are
from an earlier session's passage-derived set, they are exactly the category `AGENTS.md` §1.9
names, and the repository is unaffected (the privacy scan passes on the tree).

The lesson is narrower than "be careful": **every one of this session's three disclosures came
from printing a directory listing or an exception, never from printing a measurement.** The
probes that reported counts, classes and booleans leaked nothing. A monitoring script should
therefore never `ls` the derived state — which is a rule an operator can follow, unlike
"watch what you print".

## Next, and this is the decision

The owner reads the rendered pages (`~/rlm-derived/questions/traces/index.md`) and judges
whether the three answers are grounded. Then the fork:

1. **A passage-derived question set** — the owner draws passages with `rlm corpus sample` and
   writes questions that *require* citing, which is the only way to measure the thing RO17
   exists to measure.
2. **Treat the zero-citation result as a defect** to be chased in the prompt or the model
   choice — but the argument above says the instrument, not the model, is what failed here.
