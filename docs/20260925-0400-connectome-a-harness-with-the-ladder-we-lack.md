# Connectome: a harness that already designs the ladder we lack

**Date:** 2026-09-25. **Status:** reading note — a supplied artefact, added to the Gate 1 material.
Nothing here is verified beyond its own architecture document, and nothing was run.

Source: [`anima-research/connectome-host`](https://github.com/anima-research/connectome-host/blob/HEAD/ARCHITECTURE.md)
(the memory-wiki spec's counterpart on the harness side; supplied by the owner, missed in the first
reading list).

## What it is

A recipe-driven agent host in **TypeScript on Bun**, built on a stack of its own:
`@connectome/agent-framework`, `@connectome/context-manager`, `chronicle` (a lossless, **branchable**
store with Rust N-API bindings), `membrane` (model access; an `ANTHROPIC_API_KEY` is required), and
OpenTUI for the terminal interface. Configuration is a JSON recipe: system prompt, model, MCP servers,
module toggles, context strategy. It ships a lessons store, an LLM-as-retriever, subagents with
fork/spawn and depth limiting, and a slash-command surface including `/undo`, `/redo`, `/checkpoint`,
`/branches` and `/newtopic`.

## Four designs it already has that this project flagged as missing

| its component | the gap it answers here |
|---|---|
| **Hierarchical compression** in `AutobiographicalStrategy` — today single-level (raw → diary), roadmap to a **3-level pyramid**: merge N summaries at level K into one at K+1; **anti-redundancy** (exclude a summary whose children are all expanded); **budget carryover** (unused tokens at higher levels flow down L3 → L2 → L1); **self-voice framing** (summaries injected as the assistant's own recollections); **source-range tracking** for every compressed chunk | We have **exactly one level of contraction** (the 400-token description), no expander, and no second level — the gap `docs/20260924-0100-gate-3-…` names and the one a Laya-style controller was meant to fill |
| **Chronicle** — lossless, branchable, with undo/redo/checkpoints and per-session stores | Reversibility over derived state, and the *mapping* from a compressed chunk back to the messages it covers. Its own roadmap admits the mapping is the part still missing, which is honest and matches our problem |
| **Lessons** — persistent knowledge with `confidence` 0–1, `boost`/`demote` with diminishing returns (`+0.1·(1−c)`, `−0.1·c`), excluded below 0.3, injected as a `## Knowledge Library` block, with `evidence` references | A consolidation store with a **graded** policy where ours has a binary notion of staleness. It answers "what invalidates what" with confidence dynamics instead of a flag |
| **Retrieval module** — three steps: a cheap model flags which concepts need background, mechanical keyword matching fetches candidates, a cheap model validates them; cached by context hash; **fails open**; short-circuits at ≤3 candidates | Read-time admission decided by a *generative* cheap model rather than a typed-decision one — the same goal as the owner's Laya idea, reached differently, and now the more attractive of the two |

## Where it collides with this project

Its own document settles these, so they are facts about it rather than guesses:

- **Bun**, not Python. OpenTUI's native core requires it.
- **An `ANTHROPIC_API_KEY` is required** for model access through Membrane, so it is **cloud by
  construction** — which `AGENTS.md` §1.9 rules out for anything that could see corpus text. The
  *designs* are portable; the *product* is not, as shipped.
- **MCPL/MCP-centric**: its tool surface, its event gate and its "wake" subscriptions are built on
  that protocol.
- Runtime dependencies are local Rust/N-API packages, i.e. a build, not a pip install.

**Unverified by me:** its licence, whether the context manager is separable from the rest, whether any
component runs without the cloud model, and any performance claim — I read one architecture document
and ran nothing.

## What it changes

The Laya result (zero-shot typed decisions fail on our cards, `docs/20260925-0300-…`) matters less
than it looked an hour ago, because Connectome demonstrates that **the admission policy can be a
strategy — rules, budgets and a cheap generative check — rather than a learned classifier**. Its
hierarchical-compression roadmap is a concrete design for the expansion ladder this project lacks,
including the two details that are hard to invent: budget carryover between levels, and source-range
tracking so a contraction can be expanded back to what it came from.

That reframes the next step from "get labels and fine-tune a head" to "implement the ladder, with the
policy as a strategy first and a learned decision only if a signal ever appears".
