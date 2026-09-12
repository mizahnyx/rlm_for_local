# P4 live confirmation — and what it turned up about single-trial probes

**Date:** 2026-09-11 13:59
**Answers:** the first open item in §6 of
`20260911-1219-assessment-follow-ups-hardening.md` — "no live confirmation of the
P4 diagnostic".
**Status:** the diagnostic fired on the recorded model and the verdict change is
confirmed. Two findings came out of it: the model's P4 behaviour is **not stable**
across runs on this serving setup, and the diagnostic could not name one of the
mechanisms it was plausibly looking at. Both are fixed or recorded below.

## 1. The run

Same methodology as the original sweep, so the comparison is like-for-like:
quick battery (P1+P4+P6), `--profile tiny`, router restarted before the model via
`--before-each`, single resident instance (measured 4 GiB used / 11 GiB available
during the run).

```bash
uv run python scripts/assess_router_models.py \
    --only Qwen3.5-2B-Instruct \
    --out logs/router-model-battery-recheck.jsonl \
    --before-each "ssh lunacode systemctl --user restart llama-router.service"
```

## 2. Result: the failure mode reproduced, and the verdict moved

| | Recorded 2026-09-11 (p1-heavy) | This run (default) |
|---|---|---|
| P1 | 20/20 | 20/20 |
| P4 | 0/15 | 0/15 |
| P6 | 10/15 | 10/15 |
| Weights | `p1-heavy` | `default` |
| **Score** | **60.0** | **46.7** |
| **Verdict** | MARGINAL | **NOT SUITABLE** |
| Elapsed | 1783.8 s | 1778.8 s |

The per-probe outcome is **identical**; only the scale changed. The predicted
score was 46.7 (`(10 + 0 + 20×10/15) / 50`), and the run returned 46.7 — the
reweight arithmetic is now confirmed against a live model rather than on paper.

The contradiction reproduced too, and this time it came with an explanation:

```
[P4] answer['ready'] = True found in model output.
[P4] No final answer detected.
[P4] DIAGNOSTIC [submission_not_reached_at_runtime]: submission line is a real
     statement in an executed block (turn 12) that produced no traceback and no
     submission — it was not reached at runtime. The harness cannot tell which of
     these it was: a conditional or loop body that did not run, `answer` rebound
     to something that is not a dict, or an earlier block in the same turn that
     ended the turn first.
[P4] WARNING: forced finalization — model did not submit voluntarily.
```

That is the item §6 asked for, and it is also where the earlier wording was
wrong: the first version of this detail asserted "the line was not reached at
runtime (a conditional, a loop, or `answer` rebound …)" as if it knew which. It
does not, and §4 below shows there is a fourth possibility it could not see at
all.

## 3. The model's P4 is not stable — and that moves the verdict by a band

The probe deletes its own trajectory, so the block behind the diagnostic could
not be read after the fact. Five further P4 runs were made with the trajectory
kept. Same query, same context, `temperature=0.0`, same router — four different
outcomes:

| Run | Router state | Submission text | Submitted | Forced | P4 | Verdict under `default` |
|---|---|---|---|---|---|---|
| Battery (§2) | restarted, P1 warm-up first | yes | **no** | yes | 0/15 | **46.7 NOT SUITABLE** |
| Cold, kept trace | restarted | no | no | yes | 0/15 | 46.7 NOT SUITABLE |
| Hot ×3, kept traces | warm, prompts cached | yes | yes | no | 15/15 | **86.7 SUITABLE** |
| Inspection (earlier) | warm | yes | yes | no | 15/15 | 86.7 SUITABLE |

**So the same model, on the same prompt, scores 46.7 (NOT SUITABLE) and 86.7
(SUITABLE) minutes apart.** Nothing about the harness differs between those runs;
what differs is the router's cache state. P4 is a single-trial probe worth 20 of
the 50 quick-battery points, so one flip moves a verdict a full band.

This is a property of the *measurement*, not of the model, and it is now stated
in the operator guide (§5.3) rather than left for the next person to discover.
The obvious remedy — P4 as N trials with a pass rate, the way P1 already works —
is a scoring-semantics change and therefore an owner decision, so it is recorded
as a follow-up rather than enacted (§6).

Note the two effects are separable: the cold single run **never emitted** the
submission line at all (so no diagnostic — correctly, there is no contradiction
to explain), while the battery run emitted it and did not act on it. Both score
0/15 by different routes.

## 4. What the investigation found, and what was added

Reading the submitting runs' trajectories showed the model's habit, and the
battery's own P6 evidence shows it too:

```
[P6] Needle 1: PASS — 'ALPHA-42' found in answer
     '```python\nanswer['content'] = 'The new access code for the server room is ALPHA-'
```

Its submitted *answer* literally contains code. A model that writes code as text
is a model that can write `answer['ready'] = True` inside a `print(...)` or a
comment: the ready-line regex matches, the interpreter only prints it, nothing
submits and no traceback appears. The diagnostic would have called that
`submission_not_reached_at_runtime` — the right class, the wrong explanation.

**Added:** `_text_kind_at`, a best-effort Python lexer (quote state for single,
double and triple quotes with escapes, plus `#` comments) that runs over the
*block body* — never over a whole message, where a prose apostrophe would open a
string that never closes and poison every later block. A fenced occurrence that
is text now yields `submission_text_is_quoted_or_commented`, and that verdict
outranks a traceback elsewhere in the same cell: the line never being a statement
is the actionable fact, and reporting the traceback would send the reader
somewhere else.

Guards: `tests/test_model_check.py::TestQuotedSubmissionText` (8 lexer cases plus
four trajectory-level tests, including the prose-apostrophe case and the
"real statements still get the runtime diagnosis" case), and two new mutations
(`R26 the string/comment lexer stops distinguishing text from code`,
`R26 the text/code verdict is computed but never recorded on a site`) — both red
as required, 43 mutations total. Fast suite after the change: **759 passed, 12
deselected** (771 collected), 257 s.

## 5. What this does and does not establish

**Established:** the recorded contradiction reproduces live; the diagnostic fires
on it and names the turn and the class; the reweight arithmetic matches a live
run exactly; the verdict change (MARGINAL → NOT SUITABLE) is real at that
per-probe outcome; and this model's P4 outcome — therefore its verdict — is not
stable on this serving setup.

**Not established:** the specific mechanism inside the battery run's turn-12
block. Reproducing it requires the same cache state, and five further attempts
(one cold, three hot, one inspection) did not recreate that exact outcome with a
kept trace. The new text/code check would name that mechanism if it applied, but
it was not observed applying here, and this document does not claim it did.

**Closed 2026-09-12 02:19:** the mechanism *was* observed applying, in the
three-trial live battery recorded in
`20260912-0219-p4-multi-trial-live-confirmation.md` — trial 1 wrote the
submission line inside a string in an executed block, and the check added here
named it. The lexer hypothesis in §4 is therefore verified on a real model rather
than inferred from this model's habit of writing code as text.

## 6. Follow-ups

- **P4 is single-trial.** Consider N trials with a pass rate (as P1 does) or a
  variance flag on the verdict. Scoring-semantics change — owner decision.
  **Enacted later the same day**, on the owner's decision:
  `20260911-1518-p4-multi-trial.md`. P4 now runs three different questions,
  scores the mean per-trial credit, and passes only on a majority.
- **Model verdicts on this router are cache-state sensitive.** Any sweep whose
  verdicts are worth comparing should state the router state (restarted vs warm),
  which the sweep tooling now records per model via the `weights` and
  `p4_diagnostic` fields but not yet as a cache-state field.
- **The two models skipped after the screen** (`Qwen2.5-VL-3B`,
  `LFM2.5-2.6B-Heretic`) remain un-battery-tested, and the earlier follow-ups
  (P3's scoring, `--screen` is not a gate, host `max_instances` risk) are
  unchanged.

## 7. Artifacts

| Path | Contents |
|---|---|
| `logs/router-model-battery-recheck.jsonl` | The §2 record: score, weights, per-probe, `p4_diagnostic`, evidence. Local artifact — `*.jsonl` is gitignored — so the numbers it contains are reproduced in §2 above rather than referenced. |
| `.tmp_p4/` | Scratch: the kept trajectories and the two inspection scripts used for §3 and §4. Now covered by the `.tmp_*/` ignore rule, which this session added to `.gitignore` — the 2026-09-10 validation doc had referred to that rule since it was written, but only `.tmp_pytest/` and `.tmp_rt/` were actually listed. |
