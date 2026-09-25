# Gate 6: the Laya context-controller probe — design, thresholds, and what needs the box

**Date:** 2026-09-24 (series ordering; the machine clock runs behind it). **Status:** probe design.
Nothing has been run against the corpus or against Laya — the box is away. The feasibility script is
staged at `.tmp_probe/laya_feasibility.sh`.

## 1. Three questions, deliberately separated

| | question | needs |
|---|---|---|
| **A** | Does a typed-decision model run on this CPU, at what RAM and latency, and does it coexist with the resident 4B? | the box |
| **B** | Can it make the expand/contract decision *at all*, and is its confidence informative? | the model, on any machine |
| **C** | Would acting on its decisions improve a question's outcome? | the box, a baseline, and A+B passing |

Conflating them is how a project ends up with a controller nobody can justify. A and B can fail
independently, and C cannot be answered before both pass.

## 2. The decision being asked of it, stated precisely

The owner's framing: *"what is expanded, contracted to summary, or contracted to one line on the
model context"*. In **this** architecture the unit matters, and it is not the document: the root
model never sees documents, its window holds tool results, cards and REPL output. So the controller's
unit is a **card**, which search already produces (≤400 characters).

**Input per item:** the card text; the current question; the turn number and remaining turns; and,
when the card came from a search, its band and `covers n/m` label.

**Output:** one of `expand` (fetch the passage or document into the cell), `summarise` (fetch the
cached description instead), `one_line` (keep the header line only), `drop`.

**Shape:** Laya's `choice` type with four criteria is an exact fit (2–26 labels). Two extra questions
can ride in the same request — `noul` ("is this relevant to the question at all?") and `score` ("how
important?") — since it evaluates 1–16 named questions independently.

**The hard constraint that shapes everything:** Laya's default checkpoint allows **1 024 formatted
tokens per question**, and over-long input is *rejected*, not truncated. That makes it a card-level
controller by construction: it cannot read a 30 KB document to decide about it. Any design that needs
it to is out of scope before it starts — which is why the existing ≤400-character cards and 400-token
descriptions are the right inputs.

## 3. The signal — the crux, and the honest gap

There is no ground truth for "was this the right context composition". Two sources exist, and both
are used:

- **Recorded, weak, real.** Every trajectory records which addresses were *served* and which were
  *cited*, with bands (`corpus_served`, `corpus_citation`, and the served-address set). So for past
  runs we can ask: would the controller have chosen differently from the harness, and does its choice
  track what was actually cited? That is correlational, not causal — it cannot show that acting would
  have helped, only whether the controller agrees with the record.
- **Synthetic, controllable.** Hand-built cards where the right answer is obvious (the card that *is*
  the answer; a duplicate; a long document where a summary suffices; an irrelevant hit). This
  measures whether the task is doable and whether confidence means anything, with no corpus at all.

**Therefore: shadow mode first.** The controller decides and logs; nothing acts on its decisions
until B passes and a baseline exists to compare against.

## 4. The protocol

**Phase A — feasibility (box, ~15 min plus downloads).** Isolated `~/laya-eval` venv (never the
harness venv), CPU torch wheel, the model; measure load time, RSS, five forward passes, and `free -g`
before and after so co-residency with the resident 4B is visible rather than assumed. Script is
written and staged.

**Phase A2 — ONNX (box, optional).** Export with `optimum-cli export onnx` and re-measure. The owner
wants an ONNX artefact for this box, and the ecosystem already runs Laya under ONNX Runtime
(`receptron/laya`) and Transformers.js (`open-jev-laya`). **Expected friction, stated in advance:**
Laya is an encoder plus *decision heads*, and the heads are where a generic export usually needs the
reference implementation rather than plain `AutoModel` — so A2 is an attempt, not a promise.

**Phase B1 — synthetic capability (no corpus; any machine).** 20–30 cards with known-correct answers.
Report accuracy, the confidence distribution, and latency. This is the phase that can run *before*
the owner is home, and it is the only phase that can.

**Phase B2 — shadow mode (box, ~1 h, no model generation).** Replay 20–50 recorded cards (the value
set's served and cited items, plus the descriptions already cached) through the controller; log every
decision; compare against what the harness actually served and cited. Disagreements get read by a
human, on the box, because "the controller was wrong" and "the harness was wasteful" look the same in
a table.

**Phase C — acting (box, only if B passes).** Act on one question run: expand/summarise/one-line per
decision, then measure citations, coverage stated, tokens served, and wall time against the recorded
baseline for the same question set.

## 5. What would make me recommend abandoning it — pre-registered

Written now, before any of it runs, because a test whose kill criteria are decided afterwards is not
a test:

- **A fails:** it cannot co-reside without pushing the box into swap, or a decision costs more than a
  few hundred milliseconds, or CPU latency is so poor that deciding costs more than the decode it
  saves (decode is 1.8–2.5 tok/s, so the bar is low in absolute terms but it must be measured).
- **B1 fails:** accuracy is near chance on obvious cards, or confidence is uninformative (uniform
  probabilities, or confident and wrong at the same rate as confident and right).
- **B2 fails:** its choices disagree with the record *and* the disagreements are not defensible on
  reading.
- **The task is out of domain:** the published checkpoint is specialised for customer service,
  invoices, security incidents and agent traces. Our cards are technical document fragments. If B1
  fails *for that reason*, the honest conclusion is not "Laya is bad" but "this checkpoint is the
  wrong one" — and the fallback is a head trained on our own cards (an open 150M reproduction trains
  in ~30 minutes on a free T4), which needs the signal in §3 to exist first.

## 6. The chicken-and-egg, and how it is escaped

A trained controller needs a signal; the signal needs the controller to generate contrast. The
escape: **bootstrap on the record**. The recorded served/cited sets are weak labels *already paid
for*, and B2's disagreement analysis upgrades them to a training set for a head — no new corpus runs,
no new questions. That is why B2 is in the design rather than jumping straight to training.

## 7. What needs deciding before any of it runs

1. **May B1 run on this Windows box now?** It needs no corpus and no `lunacode` — only a ~1 GB
   download (CPU torch plus the model) in a separate venv. It would answer "can it do the task at
   all" before you are home, at the cost of your disk and bandwidth; I have **not** done it.
2. **ONNX (A2) or torch-CPU only?** ONNX is what you asked for and likely smaller and faster to
   start, but the export is an attempt with a known friction point (the decision heads).

Everything else in this document is ready to run as written, and neither question needs an answer
from you today.
