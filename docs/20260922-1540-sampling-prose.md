# Sampling prefers prose now, and here is what that costs

**2026-09-22.** The owner's instruction: sampling must *"prioritize prose over scripts, code
or textual-shaped multimedia"*, because a question devised from minified JavaScript, a JSON
dump or a subtitle file is not a question about the corpus a person would ask. This records
the change, the measurement that justifies its floor, and what it costs.

## Measured on the real corpus, same seed, 20 passages each

| draw | scored ≥ floor | score min / median / max | cost |
|---|---|---|---|
| uniform (the old behaviour) | **0 of 20** | 0.311 / 0.496 / 0.617 | 20 reads, **50.1 s** |
| prose-preferred (the new default) | **20 of 20** | 0.621 / 0.679 / 0.981 | 92 candidates drawn, 65 rejected on content, 7 duplicates, **400.5 s** |

Two things in that table matter as much as the headline.

**The uniform draw produced no prose at all** — not "mostly not prose", zero of twenty. That
is RO1's composition showing up in a draw: the chunk population is dominated by scripts, code
and data (`.js`, `.h`, `.json`, `.c`, `.yml` in this sample of twenty), so a uniform sampler
was offering questions about source code.

**Ten of the twenty uniform draws had a prose-shaped name** (no extension, or a text-shaped
one) and still scored below the floor. So the name is not the signal — the content is. That is
why the two filters are separate and why the score is what decides.

## What the change is

`rlm corpus sample` now draws prose by default. `--any` restores the uniform draw and
`--floor` overrides the threshold, because an operator must be able to see the filter's
effect rather than argue with it.

Two filters, in the order that keeps the draw cheap:

1. **The name**, inside the indexed lookup `random_chunks` already performs: `vendored`,
   `origin`, and one `NOT LIKE` per non-prose extension. A candidate the name rules out is
   **never read** — which is a property with its own test, because it is the filter's entire
   justification for being in SQL.
2. **The content**, `prose_score >= 0.62`, on the text actually read.

`prose_score` is five capped ratios: letters dominate, spaces are frequent, parser
punctuation (`{}[]<>();=*/\|&$#@\`~^_%`) is rare, digits are rare, and sentences end.
The floor was chosen against separations it demonstrably makes — narrative prose clears it;
minified JavaScript, a JSON dump, empty text and whitespace do not.

**The honest division of labour**: a subtitle file *scores high* — timestamps and short lines
still have letters, spaces and sentence marks — and it is the **name** that rejects it. Tuning
the score until it caught subtitles would have bent it away from the thing it measures, and
there is a test asserting the rejection happens by name, so a future edit cannot quietly
move that responsibility onto the wrong filter.

## What it costs

**Eight times slower: 400.5 s for twenty prose passages against 50.1 s for twenty uniform
ones.** Sixty-five of ninety-two candidates were read and then rejected on content, and each
rejection is a real file read. That is the price of insisting on the content signal, and it
is paid when building a question set, not interactively. If it needs to be cheaper the lever
is a *name* pre-filter that is stricter than "not obviously code" — but the table above is
the argument that a name hint would leave most of the rejections in place.

The command prints what it rejected (`drew N, rejected N on content, floor, mean score`) and
says so explicitly when it returns fewer passages than asked, rather than presenting a short
draw as a corpus that holds no prose.

## The hang this found, which was a real bug

The first version counted only *new* chunks against the draw budget. Once the reachable pool
was exhausted, a batch of nothing-but-duplicates could not advance the count, so the loop
probed forever — precisely the "operator staring at a command that stopped responding"
failure the method's own docstring promises to avoid. It surfaced as a test run that hung,
not as a code review. Fixed by counting every candidate, duplicates included, with an early
exit on a batch that yields nothing new.

The mutation table then caught the test I wrote for it: the early exit *also* stops that loop,
so the test could not observe the accounting at all. It now asserts on the accounting
directly — one chunk in the table, a budget of thirty, more than one candidate reported
drawn.

## Guards

5 tests and 4 mutation entries, all red as required: the name filter riding in the draw, the
content score being consulted, the floor separating, and duplicates counting against the
budget.

## Next, and the gate

With a prose-preferred draw working, the non-default question set can be built — and there is
one genuine decision in front of it, left to the owner: **who writes the questions**. A
passage-derived set needs someone to read passages, and `AGENTS.md` §1.9 keeps corpus prose
out of this conversation, so I can neither read them here nor devise questions from them here.
Either the owner writes them (as on 2026-09-19) from a draw they run, or the local model
drafts them on `lunacode` from passages that never leave the machine — which is a different
measurement, since the model that drafts a question is also the model that answers it.
