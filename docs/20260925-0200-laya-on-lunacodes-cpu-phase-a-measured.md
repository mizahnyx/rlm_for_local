# Laya on lunacode's CPU: phase A measured

**Date:** 2026-09-25 (lunacode's clock; the box came back online with `/srv/corpus` mounted `ro`).
**Status:** phase A of `docs/20260924-0200-gate-6-the-laya-controller-probe-design.md` — measured.
Design and client: `src/rlm_local/decisions.py`.

## What it cost to get a load at all — three wrong assumptions, each instructive

1. **It is not a flat HuggingFace repository.** Seven files: `model.safetensors` (842 609 220 bytes)
   at the **root**, `encoder/config.json` in a subfolder, the tokenizer under `tokenizer/`.
2. **The failure message lied, usefully.** A root-level `from_pretrained` for the tokenizer reported
   *"You need sentencepiece or tiktoken"* — installing sentencepiece changed nothing, because the
   real cause was that no tokenizer files exist at the root. A diagnostic that reports a missing
   dependency when the file is simply in another directory is `AGENTS.md` §1.8's corollary in a new
   costume.
3. **Config and weights live in different places.** `subfolder="encoder"` found the architecture and
   then failed on `model.safetensors`, which is at the root. Loading the config from `encoder/` and
   the weights from the root works.

## Measured on lunacode's CPU (2 torch threads), one model resident, nothing else running

| quantity | value |
|---|---|
| parameters | **394 781 696** (ModernBERT-large: `hidden_size` 1024, 28 layers) |
| config `architectures` | `ModernBertForMaskedLM` |
| load time | **116.4 s** |
| RSS before → after load | 381 MB → **2 104 MB** (2 119 MB after inference) |
| forward, 58 tokens | **0.643 s** median of five (0.636–0.659) |
| forward, 804 tokens | **7.603 s** |
| box memory after | 15 GiB total, **1 used, 10 free, 14 available** |
| disk, `/home` | 822 GB used, 43 GB free |

Two things follow directly, one measured and one inferred:

- **It fits, with room.** Laya holds 2.1 GiB; the box still reported 14 GiB available while it was
  resident. Coexistence with the resident 4B (which held ~5 GB in earlier measurements) is therefore
  plausible — but **it has not been tested with both loaded**, and this box has swapped before under
  a single model, so that test is still owed.
- **Latency scales steeply with card length** — 0.64 s at 58 tokens, 7.6 s at 804 — so card size is
  the cost dial, not the number of decisions. The ≤400-character cards this harness already produces
  sit near the cheap end (~1 s at a rough extrapolation, which is *inferred*, not measured).

Verdict on the design's pre-registered criteria for phase A: **it runs, it is not too large, and a
short decision is fast enough to sit between cells.** The co-residency half of that criterion needs
the two-model test.

## The gap phase B1 hits immediately

`output_keys` is `["last_hidden_state"]`. **The decision heads are not in this checkpoint** — the
model is the encoder, and `rl_agent_config.json` describes a *fine-tuned agent* on top of it
(7313 updates, 1 epoch, ~1.96 GPU-hours, per-option temperature calibration for `choice:2`,
`choice:3-5`, `choice:6-10`, `choice:11+`, `score:3-5`, `noul:2`, and an `escalate` threshold at
0.5). So typed decisions require the head implementation and those calibration constants applied.

That is the next piece of work, and it is what stands between phase A and phase B1: the runner
(`scripts/probe_laya_decisions.py`) and the client are written and tested, but nothing can answer a
`choice` question until the heads exist somewhere runnable.

## Not verified

- Both models resident at once (the co-residency half of the phase-A criterion).
- Per-decision latency at realistic card lengths — 0.64 s and 7.6 s are the only two points measured.
- Anything about whether its decisions are *good*: that is phase B, and it has not started.
- Whether the reference implementation runs on this CPU at all — it is referenced from the model card
  and has not been fetched.
