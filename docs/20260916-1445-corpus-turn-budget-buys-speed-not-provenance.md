# A smaller corpus budget buys speed and spends provenance

**Created:** 2026-09-16 14:45
**Status:** closed — the experiment says no to simply lowering the budget, and hands
the next fix its evidence
**Supersedes nothing.** Tests the cost lever left open by
`docs/20260916-1405-corpus-convergence-permission-was-not-enough.md`.

## What was run

Same question, same model (`Qwen3.5-4B-Abliterated`, `laptop` profile), same
complete index, same script — only `--max-turns` changed: two runs at 4, two at 2,
against four 8-turn baselines. No code or configuration changed; `--max-turns` is
per-invocation.

## Results

| run | turns | forced | elapsed | helper msgs | addresses printed | citations | names coverage | citation grounded | guardrails of interest |
|---|---|---|---|---|---|---|---|---|---|
| 4a | 4 | yes | **669 s** | 2 | **0** | 1 | yes | **no** | `corpus_last_turn` ×1 |
| 4b | 4 | yes | **529 s** | 2 | **0** | 0 | yes | — | `corpus_last_turn` ×1, `corpus_uncited` ×1 |
| 2a | 2 | yes | **442 s** | 2 | **0** | 0 | no | — | — |
| 2b | 2 | yes | **375 s** | 2 | **0** | 0 | yes | — | — |
| *attempt 3 (8)* | 8 | yes | 1 474 s | 6 | 1 | 1 | no | yes | — |
| *attempt 4 (8)* | 8 | yes | 2 247 s | 12 | 5 | 0 | yes | — | `corpus_last_turn` ×1 |

## The cost claim holds, and the provenance claim does not

**Speed: confirmed.** 375–669 s against 1 474–2 247 s. A 4-turn corpus run is
roughly 2.5–4× cheaper, and a 2-turn run is cheaper still. If cost were the whole
question, this would be the answer.

**Quality: it breaks, and in the worst way.** With 4 turns the run prints **no
addresses at all** — it reads nothing — and run 4a nevertheless ends with a
`Citations:` line. The cited address was never served to it, so the citation is
**fabricated**: a printed-looking address for a passage that was never read. The
harness's own groundedness check catches it (`cited ⊆ printed` is false and the
record says `answers_with_address` only because an address is present), but
**nothing refuses it** — the citation guard accepts any address, and a fabricated
one is worse than none, because it looks checkable.

**2 turns is below the floor.** No addresses, no citation line, and in 2a not even a
statement about coverage: prose about an unanswerable question with no provenance
at all. Also worth recording: at 2 turns the last-turn nudge *could not fire* in
either run — it is evaluated before the final call, and in these runs the first
helper call happened *inside* that final turn, so the condition "has looked but not
submitted" was false at the only moment it was checked. The nudge is structurally
blind to a run that looks for the first time on its last turn.

## What this decides, and what it hands to the next fix

- **Do not lower the default corpus turn budget.** The exploration is what
  produces a grounded citation (attempt 3: 8 turns, 1 cited, printed; these runs:
  4 turns, 0 printed, one fabricated). The cost is real, but buying it back with
  unverifiable citations is the wrong trade for a project whose entire citation
  apparatus exists to prevent exactly that.
- **The next fix writes itself: a citation must be an address the harness served.**
  The parent serves every `corpus_search`/`corpus_read`; it can keep the set of
  addresses it returned or resolved, and `_refuses_uncited` can require membership
  instead of merely matching the address pattern. That is the same
  evidence-based shape as the unsearched guard, it would have refused 4a's
  fabrication, and it gives the wiki stage a groundedness test that does not
  depend on the model's prose.
- **Then the relevance signal**, which is the durable answer to "these hits are
  not an answer": the reason a shorter run reads nothing is that it has no way to
  tell a strong match from a weak one.
- **Keep 8 turns for now**, and treat the cost as the price of groundedness until
  one of the two fixes above changes the arithmetic.
