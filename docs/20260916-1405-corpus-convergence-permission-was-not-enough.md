# Permission was not the missing piece: the fourth attempt at convergence

**Created:** 2026-09-16 14:05
**Status:** closed — the last-turn nudge fires and does not change the run's shape
**Supersedes nothing.** Follows `docs/20260916-1005-corpus-terminal-citation-measured.md`
(attempt 3) and the same question's two earlier attempts.

## What was tested

`NUDGE_CORPUS_LAST_TURN` (commit `aeead68`): before a corpus run's *final* turn,
if it has called at least one helper and never submitted, it is told that this
turn is for answering — stop searching, cite what you read, or say the corpus does
not contain it and quote the coverage line. It costs no turn of its own.

The hypothesis was that the model knew its budget (the header says `Turn 8/8.`)
and lacked only permission to stop.

## What happened

| | attempt 1 | attempt 2 | attempt 3 | **attempt 4 (nudge)** |
|---|---|---|---|---|
| turns | 5/8 | 8/8 | 8/8 | **8/8** |
| forced | yes | yes | yes | **yes** |
| elapsed | 7 250 s | 1 578 s | 1 474 s | **2 247 s** |
| helper calls | 9 | 7 | 6 | **12** |
| addresses printed | 0 | 1 | 1 | **5** |
| `corpus_last_turn` fired | — | — | — | **1** |
| citations in the answer | 0 | 0 | 1 | **0** |
| answer names coverage | no | no | no | **yes** |

**The nudge fires correctly and does not change the shape of the run.** The model
was told, on its final turn, that the turn was for answering; it still did not
submit, and the answer arrived by forced finalization for the fourth time. It did
not even search *less*: 12 helper calls against 6–9 in the earlier attempts.

The one thing that changed is the substance of the forced answer: it **names
coverage and has a `Citations:` line, but the line contains no address**, so the
citation record is `answers_with_address=False`. In other words it took the
absence arm in prose without ever making an absence *submission*.

## The insight this run produced

A complete index makes an unanswerable question **search-hard**, not search-empty.
A nonsense proper noun does not match, but the model searches the question's
ordinary words — *orbital*, *tether*, *maintenance*, *ratified* — and against
2.88M files those match plenty. It printed five addresses this time: passages that
contain the words and do not answer the question. So "there is no answer here" is
not something a search can show it, and searching more is a rational response to
what the corpus is telling it.

That reframes the problem. The failure is not stubbornness about the budget; it is
that **the harness gives the model no way to decide that the hits it found are not
an answer**, and no way to say so other than by stopping. Prose permission is not
a decision procedure.

## What the evidence now supports

1. **A structural budget, not a request.** If a run's *last* turn is for
   answering, make that true rather than advisory: on the final turn, tell it the
   helpers are no longer available (they are not — the run ends either way), or
   give it a submission-only turn. Prose failed once; the next attempt should
   change what the turn *is*.
2. **Or stop paying for exploration we do not use.** The forced answer is now the
   good one — attempt 3's was cited, attempt 4's states coverage — and it takes 8
   turns and 25–37 minutes to arrive. A smaller `--max-turns` for corpus runs is a
   two-run experiment with no code change: if quality holds at 4 turns, the loop
   gets twice as fast for the same provenance.
3. **Relevance is the missing signal.** A search that returns five weak matches
   looks identical to one that returns five strong ones. Until a hit carries
   something about *how well* it matches, "these are not an answer" is a judgement
   the model cannot make from the corpus's own output — which is exactly the
   judgement the wiki stage will need to make on every page.
