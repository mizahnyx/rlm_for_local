# Traces a human can assess — and the live run that arrived while building them

**Created:** 2026-09-17 09:15
**Status:** point-in-time. The viewer is implemented and unit-proved; **the owner has
not read a page yet**, so its usefulness is a hypothesis. The live measurement below
is real and reads only aggregates.
**Supersedes nothing.** Answers the requirement the owner stated on 2026-09-17, and
continues `docs/20260917-0410-corpus-a-citation-must-answer-the-question.md`.

## 1. The requirement

> *"We probably need some sort of readable by me traces. You can then point me to the
> location of the traces, and I can browse and assess how much the process is suitable
> to the task, which corpus citations are relevant in some scale, etc. For that
> purpose, a trace viewer tool could probably be devised. In this way you can get a
> more involved assessment by me, while keeping the privacy protocols."*

The owner chose **static Markdown** (one page per run plus an index, on `lunacode`)
over an HTML page or a route in the console, and **yes to embedding the passage
text** — which is the decision that makes relevance judgeable rather than inferred
from a band label, and the decision that makes the rendered directory corpus-derived
data.

## 2. The gap the viewer exposed before it could exist

A trajectory recorded *counters* about retrieval but not its *result*:
`corpus_search_quality` writes `served weak=8 chars=4150`, which says a search
served eight weak hits and says nothing about **which** addresses, so a citation
audit ("was this address served, and did the passage behind it answer the
question?") was not answerable from the trajectory at all — after the run, the
only record of what the model was shown was whatever the model itself had printed.

So the instrumentation came first: **one `corpus_served` event per helper call**,
carrying `verb`, `query`, `chars`, the addresses with the band each was served with,
and `ok`. A failed call is `ok=false` with no addresses — asking for an address is
not being served it, and that distinction has to survive into the trace. The parent
answers every helper call, so the parent is the only party that can write this.

## 3. The live run: the absence-band guard's first live test

The band guard (`docs/20260917-0410`) was measured the same day it landed, with the
graded question, `Qwen3.5-4B-Abliterated`, `--profile laptop --max-turns 8`, on the
complete index. Aggregates only:

| fact | value |
|---|---|
| outcome | **forced finalization** (`forced=true`) — 6th consecutive non-convergence |
| turns | 8 of 8 |
| elapsed | **2 684.9 s** (~45 min) |
| searches served | 6 |
| bands served on the substantive search | **`weak=8`, four times** (4 150 chars each) |
| bands served on a second search | `weak=1` (613 chars) |
| parse guardrails | 8, **all `final_answer=False`** — no turn submitted |
| accepted answer | 1, `answers_with_address=True`, 811 chars |
| `corpus_uncited` refusals | **0** |
| `corpus_weak_citation` refusals | **0** |

**The band guard did not fire, and the reason is structural rather than
disappointing:** it judges *submissions*, and this model made none in any of the
eight turns. Eight searches, `weak=8` four times over, and the answer arrived by
forced finalization — which is measurement-only by design, because refusing the
terminal answer would leave a run with nothing. So the guard is **insurance on a
path this model does not take**, exactly as the fabrication guard turned out to be
(`docs/20260916-1555-corpus-fabrication-not-reproduced.md`). What the run confirms
is the older finding, now with the enforcement in place: *the label cannot help a
model that never concludes.*

The honest consequence for the roadmap: the absence-band guard's live value is
unmeasured and may stay that way with this model. Its measured value so far is
that a *submitting* run can no longer dress a weak hit as evidence — and the trace
viewer is what will show, per run, whether any run ever does.

## 4. What the viewer is

`rlm trace render <trajectory-or-directory> --out-dir DIR [--corpus-root R --corpus-index I]`
writes `index.md` plus one page per run; `rlm trace summary <path>` prints counts and
writes nothing.

A page carries: the question, model, profile and flags; the outcome (voluntary
submission or forced finalization, and at which turn); elapsed; a counts table;
a **citation audit** in four buckets; and the turn-by-turn transcript — what the
model said, what each cell printed, the guardrails that fired, **what each helper
served with its bands**, and every harness nudge. Every cited *and* every served
address is resolved through the read-only mount and printed with its passage.

| audit bucket | what it means |
|---|---|
| cited, and the passage answers the question | the strongest evidence class |
| cited, but the passage does not answer it | what the absence-band rule refuses |
| cited, but no helper served it | fabrication |
| cited, but whether a helper served it is unknown | the run predates the instrumentation — `unknown`, not fabrication |
| served, and never cited | relevance left on the table |

**The fifth bucket exists because running the tool on real data caught the
first four misreading it.** The first summary pass over the 17 recorded
trajectories printed `cited_unserved=1` for several runs — a fabrication claim about
runs that simply have no `corpus_served` events. An empty served set is *unknown*,
not empty, so those citations now land in `cited_unknown` with a note, and
`audit=partial` on the same line. Nothing in the unit tests would have caught it:
the tests asked whether the buckets were filled correctly and never asked what an
absent set means. It took the owner's own question — *"which citations are
relevant"* — pointed at real runs to surface it.

Two properties are enforced, not intended (and each has a mutation proving it):
`--out-dir` inside the corpus root is refused (`assert_derived_outside_corpus`,
layer 3), and the files are 0600 in a 0700 directory. The command prints only the
output path and counts; `render_summary` carries no question, no address and no
quote, which is what makes it postable while the page stays beside the corpus.

**An audit can be partial and says so**: a run recorded before `corpus_served`
existed has no served set, so its page reports that citations "cannot be checked"
rather than listing them as fabrications; a trajectory whose last line was torn by
a kill likewise. That is the `AGENTS.md` §1.8 corollary applied to the viewer itself
— a check
that cannot see the truth must say "unknown".

## 5. Verification

| Check | Result |
|---|---|
| New tests | **30** (15 `test_traceview.py` — one POSIX-only, skipped on Windows — 9 `test_cli_trace.py`, 4 sandbox, 2 root-loop including an end-to-end page) |
| New mutation entries | **9**, all red as required; the whole table re-run |
| Mutation table | **164 guards, 0 problems** |
| Fast suite | **1295 passed, 8 skipped, 12 deselected, 0 failed** (7:42, quiet host, router idle) |
| Doc lint | 38 documents clean; self-test 6/6 |
| The 17 recorded runs, summarised | all `audit=partial`, `searches=0`, `bands=none` — every one of them predates the `corpus_served` instrumentation, which is the first thing RO10's own output said |

## 6. What is not verified

1. **No human has read a page.** The owner's assessment is the measurement this
   feature exists to enable; until they read one, "the viewer is useful" is a
   hypothesis with a design argument behind it, not a result.
2. **The passage is the passage behind the *chunk*, not the whole file** (default
   1 200 characters, `--max-passage` raises it). Whether that is enough context to
   judge relevance is the owner's call to make from a page.
3. **Pages for the 16 pre-instrumentation trajectories are partial by
   construction** — they were recorded before `corpus_served` existed.
4. **The viewer reads; it does not judge.** It computes the four buckets and shows
   the bands, and it deliberately does not score a run: a "suitability score" would
   be the project inventing a verdict it has not calibrated.

## 7. Next

Render the existing trajectories for the owner to browse; then the two open items
the evidence still points at — the escape arm's harness-side evidence (D4, which
the absence-band nudge makes more pressing), and RO7 batch synthesis, which is what
the trace viewer's passages make reviewable in bulk rather than one run at a time.
