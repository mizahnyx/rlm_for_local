# Laya phase B1: the shipped checkpoint cannot make this decision zero-shot

**Date:** 2026-09-25. **Status:** phase B1 of `docs/20260924-0200-gate-6-…` — measured, and **failed its
pre-registered criteria**. Phase A: `docs/20260925-0200-laya-on-lunacodes-cpu-phase-a-measured.md`.

## What ran

The 14-case synthetic set through `scripts/probe_laya_decisions.py --sdk`, in process, on lunacode's
CPU, against `convaiinnovations/laya-typed-decisions` (the checkpoint fine-tuned for typed decisions).
Median decision time **3.34 s** for one card's three questions. Two cases carry no expectation and are
reported but not scored, so accuracy is over twelve.

## The result

**Accuracy 3/12 = 0.25.** It answered `expand` on **12 of 14** cards.

| expected | chosen |
|---|---|
| expand (5) | expand 3, summarise 2 |
| summarise (2) | expand 2 |
| one_line (3) | expand 3 |
| drop (2) | expand 2 |

| case | expected | chosen | confidence | relevant | importance |
|---|---|---|---|---|---|
| answer-in-the-card | expand | expand | 0.023 | 0.60 | 2.09 |
| answer-in-a-large-manual | summarise | expand | 0.133 | 0.36 | 2.05 |
| pointer-only | one_line | expand | 0.092 | 0.001 | 1.78 |
| irrelevant | drop | expand | 0.034 | 0.035 | 1.60 |
| absent-metadata | drop | expand | 0.018 | 0.20 | 1.26 |

Confidence: **0.030 when correct, 0.058 when wrong** — near zero everywhere, and *higher* when wrong,
so on this set confidence points the wrong way rather than merely being weak. Relevance spans 0.001 to
0.63 with no relation to the expected answer; importance sits 1.26–2.12 on a 0–3 rubric for cards that
vary from "the answer itself" to "a canteen menu".

A constant answerer that always said `expand` would score **5/12 (0.417)**; the model scored **0.25**,
so it is worse than the trivial baseline it collapses toward, because on two cards it chose
`summarise` where `expand` was right.

## The verdict against the criteria fixed in advance

The design pre-registered: *"B1 fails: accuracy is near chance on obvious cards, or confidence is
uninformative (uniform probabilities, or confident and wrong at the same rate as confident and
right)."* Both halves fire. **Phase B1 fails.**

## What this does and does not mean

**It does not mean Laya is a bad idea**, and the distinction was written down before the test ran. The
library's own published limits say the same thing this run measured: the base checkpoints score
near chance on typed-decisions zero-shot (0.362 and 0.352 against a 0.318 random and a 0.461
majority-class baseline), the 0.766 headline comes from a checkpoint fine-tuned on that benchmark's
*own* training split, and its authors' instruction is *"treat Laya as a fast base to specialise, not as
a zero-shot decision engine."* Our cards are also outside the checkpoint's stated domain (customer
service, invoices, security incidents, agent traces).

Two of the library's documented failure modes showed up as predicted in this run: `score` is its
weakest primitive, and `noul` can follow its option labels rather than the state — the card that
*states the answer* was scored 0.60 relevant, and a canteen menu 0.035, which is directionally right
and nowhere near usable.

**What it does mean:** zero-shot use is ruled out **by measurement**, and the only remaining path for
a Laya-style controller is the fallback the design already named — **fine-tune a head on our own
decisions**. That is not speculative: the project ships a fine-tuning notebook (4–5 hours on 2×T4 for
4 epochs over ~30k questions, with RLCD training against proper scoring rules and per-type temperature
fitting), and a community project trains a head per decision on the frozen encoder from labelled rows.

## The gate that now sits in front of it

Fine-tuning needs **labelled decisions**, and we have none. The honest source is the weak signal phase
B2 was designed to mine: every trajectory records what was *served* and what was *cited*, so a
disagreement analysis over the recorded sets can produce candidate labels — and then a human decides
which are right. That is the next real piece of work, and it is a data-annotation task, not a
modelling one.

## Unverified

- Whether a fine-tuned head would work on our cards — no labelled data exists yet, so nothing about it
  has been tested.
- ONNX export (phase A2) — untouched, and now lower priority, since the checkpoint's zero-shot value
  is what failed, not its runtime.
- Both models resident at once: still untested, and now less urgent for the same reason.
