# The probe printed the id it was measuring

**Date:** 2026-09-26. **Status:** fixed, tested, mutation-proved; one residual named and left
for an owner call. Follows `docs/20260925-0600-the-description-pilot-ran-and-did-not-test-the-thing.md`
(the first disclosure of this class) and corrects part of what that record implied. The rule
is `AGENTS.md` §1.9.

## What happened

Three times in two days a **question id reached this session's transcript**, each time
because a tool or a monitoring habit printed it:

| # | when | what printed it | where the id came from |
|---|---|---|---|
| 1 | 2026-09-25 | `find … \| xargs wc -l` over a trajectory directory | a trajectory's *file name* |
| 2 | 2026-09-26 | tailing the probe's live stdout | the probe's own progress line |
| 3 | 2026-09-26 | `pgrep -af` to check whether a run was alive | the `--only <id>` argument on a process's command line |

None reached the repository, a commit message or a document; all three reached this
conversation, which is sent to a model provider — and `AGENTS.md` §1.9 exists precisely
because that counts as leaving the machine.

## Which ids, and whether it mattered — measured, not assumed

The class is not uniform, and the third incident made that worth checking rather than
asserting. Measured on lunacode today, in counts only, against the project's own token list
(`~/rlm-derived/private-tokens.txt`, 10 tokens, 9 of them letters-only):

| question | measurement | reading |
|---|---|---|
| are the drafted set's ids sensitive? | the 6 ids of `prose-drafted.tsv` match **0** of the 10 tokens | no. They are `<word>-<NNN>` position labels, and the repository has published the whole range since `docs/20260922-1930-…`. Incidents 2 and 3 leaked nothing that was not already public. |
| are trajectory *names* sensitive? | **15 of the 64** trajectories under `~/rlm-derived` have a stem that matches a token, and **9 of those sit in `run_question_probe` output directories** | yes. Incident 1 printed one of them, so that listing was a real leak of a sensitive name. |

And the defect is live rather than historical: the probe prints whatever id its set carries,
so the next run over a content-derived set — the shape `AGENTS.md` §1.9 describes, an id like
`<person>-<place>` — puts a sensitive name on stdout. That is why the fix is unconditional
rather than conditioned on which set was run.

## The cause, and it is two causes

**The tool printed it.** `scripts/run_question_probe.py` announced each question with its id
(`# [1/1] <id> — running`), `render_line` prefixed every aggregate line with the same id, and
`render_summary` — documented as "the only output of this module that may travel" — began with
the trajectory's *file name*, which for a probe is the id. `select_questions` went further: a
`--only` filter that matched nothing listed **every id in the set**. The probe's stdout is
what an operator reads *while waiting*, so every one of those lines is a line that gets read
into a transcript. Incident 2 is a defect in the tool, not a slip by the operator.

**The monitoring habits printed it.** Incident 1 was a listing piped into something that
echoes file names; incident 3 was `pgrep -af`, which prints command lines, and the command
line carried `--only <id>` because selecting a question *by id* was the only way to select
one. Both were mine, and both follow from the same design decision: an identifier that must be
typed, and artefacts named after it.

## What changed

| where | before | now |
|---|---|---|
| `question_probe.progress_line` | (new) | `# [2/7] running` — a position and nothing else |
| `question_probe.render_line(run, label=…)` | always `f"{run.question.id}: …"` | labels by the caller's `label`; the default is the literal `question`, **not** the id |
| `traceview.render_summary` | `f"{run.path.name}: turns=…"` | starts at `turns=…`; the file name is gone |
| `rlm trace summary` / `--summary` | one bare summary line per trajectory | `1: turns=…`, `2: turns=…` — labelled by position |
| `question_probe.select_questions(..., only_index=)` | (new) | `--only-index N` selects by 1-based position, so an id need not go on a command line |
| the unmatched-filter refusal | listed every id in the set | states how many questions the set holds, and that its ids are in the file rather than on stdout |
| `scripts/run_question_probe.py` | printed the id twice per question | `progress_line(index, total)` and `render_line(run, label=str(index))` |

The guard on the last one is deliberately **source-level**: a test parses the script's syntax
tree and fails if any `print(...)` call reaches an `.id`. A behavioural test on `render_line`
cannot see a call site that formats the id itself, and a call site is exactly what leaked.

## What this does not fix

- **A trajectory is still named `<id>.jsonl`.** That is the residual, and it is now measured
  rather than suspected: 15 of 64 trajectories under `~/rlm-derived` carry a sensitive name,
  9 of them in probe output directories. `find … | wc -l` is safe; `ls`, `find -print` and
  `xargs` are not. Naming them by position (`0001.jsonl`, with `questions.txt` as the map)
  would remove the class at the source, but it changes the artefact layout the owner browses
  and every page `rlm trace render` has written, so it is recorded in the roadmap ledger as an
  **owner call** rather than done here.
- **The projection path.** Only the question probe, the summary renderer and the trace
  commands were hardened. Any *other* script that prints a name it was given still does; the
  rule is what changed, not every instrument.
- **Nothing about the corpus.** No sensitive id reached the repository:
  `scripts/check_privacy.py --tokens ~/rlm-derived/private-tokens.txt` reports "no occurrence
  in the tree" against the working tree at commit `6a04e44`.

## Verification

- `tests/test_question_probe.py`, `tests/test_traceview.py`, `tests/test_cli_trace.py` — all
  green, including the new guards: the progress line is a position; the aggregate line labels
  by position; the default label is not the id; the script's syntax tree contains no `print`
  reaching an `.id`; the summary starts at `turns=` and carries no file name; a directory of
  summaries is labelled `1:` and `2:`.
- Six new entries in `scripts/check_guard_nonvacuity.py`, each confirmed red with its guard
  removed: four for the probe (the summary line's id, the progress line's id, the refusal
  listing ids, a clamped out-of-range position) and two for the summary renderer (the file
  name, the positional labels).
- Living docs updated: `docs/operator-guide.md`, `docs/rlm-local-manual.md` §16.6, the
  roadmap's RO10 and RO17 entries, and `AGENTS.md` §1.9.

## Unverified

- Whether an operator told to select by position in fact stops typing ids. Incident 3 was a
  command line; only the next A/B says whether `--only-index` is used.
- Whether any *other* project script leaks the same way. Nothing checked for it, so nothing is
  claimed.
