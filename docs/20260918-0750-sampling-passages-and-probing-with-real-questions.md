# Two tools for asking real questions: a passage sampler, and a question probe

**Created:** 2026-09-18 07:50
**Status:** point-in-time record. Both tools are landed, tested and proved non-vacuous.
**Supersedes nothing.** It answers the owner's request of 2026-09-18 — *"the probes only
tested the timeouts, in a superficial way. We need real questions that produce more
meaningful traces. And also maybe a simple tool for me that outputs random text blocks
with their addresses from the corpus, so I can devise meaningful questions for you to
use as probes."*

## Why the sleeping-cell probe was not enough

`scripts/probe_cell_budget.py` measures a **mechanism**: whether a cell that runs past its
soft limit is extended, and whether a stuck one is stopped. It answered that question
(`docs/20260918-0510-probe-two-limits-at-the-real-limits.md`) and cannot answer the next
one, because a cell that sleeps never searches, never reads a passage, never cites
anything and never fails the way a real retrieval fails. The traces that say whether this
harness can navigate 4.28M files are traces of **real questions**.

## `rlm corpus sample` — passages, with the addresses that re-read them

```bash
python -m rlm_local.cli corpus sample --n 5 --seed 1234 \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite
```

Five design choices, each of which had a failure mode attached:

1. **Draw by random `id` probe, not by `ORDER BY RANDOM()`.** A sort of 29M chunk rows is
   not an option at this scale, and taking the head of a scan (the obvious cheap thing)
   would sample one corner of the index — which would seed questions that say nothing
   about the corpus as a whole. `WHERE id >= ? ORDER BY id LIMIT 1` is an indexed lookup,
   so `--n 20` costs the same here as on a small index.
2. **Reproducible from `--seed`, and the seed is printed.** A question set written from a
   draw has to be re-runnable against a changed harness, or "the same question, a
   different answer" means nothing.
3. **Vendored paths and container-member chunks are out by default**
   (`--include-vendored`, `--include-derived` to ask). One is noise the ranking already
   down-weights; the other is the address family that still costs >150 s to re-read
   (RO11), which is a poor thing to hand someone who wants to ask questions.
4. **The header says the output is corpus text.** It is the one corpus command whose whole
   purpose is to print content, so the rule is stated where the content is: read it where
   the corpus is, and do not copy it into a chat, a commit or this repository
   (`AGENTS.md` §1.9).
5. **It is honest about its own statistics.** A uniform random `id` weights a row by the
   gap of deleted ids in front of it. On this index (29M chunks, few deletions) that is
   indistinguishable from uniform over rows; it is written in the docstring rather than
   claimed away.

## `scripts/run_question_probe.py` — real questions, aggregates out, answers kept local

Runs a question set through the harness against the read-only corpus with the live model,
one trajectory per question, and prints one line per question. The line is
`render_summary`'s — the same renderer `rlm trace summary` uses — so the operator reads
one vocabulary everywhere, and it carries **no question, no address, no quote and no
answer**. The answers and the passages stay in the trajectories beside the corpus. The
question set that was run is copied into `--out-dir` (`questions.txt`), so the page and
what it was asked stay together.

The question set that may live in *this repository* is `DEFAULT_QUESTIONS`: three
questions about the corpus's own aggregates — counts, coverage, kinds of material. A
question devised from a passage is corpus-derived and belongs in
`~/rlm-derived/questions/` on the machine holding the corpus.

Two behaviours that were built rather than assumed:

- **A failing question does not take the probe with it.** A router that dies on question
  four is recorded as `error=…` on that line and the remaining questions still run. Five
  answers and one recorded failure is a measurement; a lost run is not.
- **No wall-clock ceiling of its own.** The owner's call is that the harness has none
  (`docs/20260917-2042-decisions-recorded-budget-and-ledger.md`), so the probe is bounded
  by what it is passed — `--max-turns`, `--cell-timeout`, `--cell-timeout-hard` — and a
  question that hits them says so in its own line.

## A vacuous test the mutation table caught (again)

The sampler's first distinctness guard asserted that a draw contains no duplicates **on a
single draw of three from a four-chunk table**. Removing the dedupe entirely, the mutation
table reported it **VACUOUS**: three random probes into four chunks usually land on three
different chunks, so the test passed with the guard gone. The replacement draws 4 from 4
over 30 seeds — certain to be duplicate-free with the dedupe, near-certain to show a
duplicate without it — and the mutation is red. This is the fourth time this project has
shipped a test that could not see its own subject; the antidote worked, and the count of
times it has *not* been caught late is now one higher.

## Verification

- Fast suite: **1347 passed / 8 skipped / 12 deselected / 0 failed** (21 new tests).
- Mutation table: **191 guards / 0 problems**, run in full after the change — five new
  sampler entries and two new probe entries among them, all red as required.
- Doc lint: 51 documents clean, self-test 6/6.
- The two privacy properties are tested, not asserted: the probe's line cannot contain the
  answer (a run whose answer is a unique phrase, checked absent from the line and present
  in the trajectory), and a question id cannot walk out of `--out-dir`.

## What this does not do, yet

- **No real question has been run through it.** The tool is landed and unit-proved; the
  first live question set is the owner's to write from a `corpus sample` draw, and until
  it runs there is no evidence about retrieval quality — only about the tooling.
- **The sampler draws passages, not questions.** Turning a passage into a question that
  the harness *should* be able to answer is the owner's judgement, deliberately: a
  machine-generated question would encode this project's guesses about what matters.
- **The aggregates the probe prints are per question, not per set.** Trends across a set
  (does turn count predict failure?) are for the owner reading the pages, and for a later
  record.
