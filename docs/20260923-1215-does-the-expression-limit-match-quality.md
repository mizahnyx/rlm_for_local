# Does the search expression limit match quality? No — the terms' commonness does

**2026-09-23.** The second half of the objective: measure what limits match quality on the prose
question set *without the model in the loop*, comparing the harness's expression with
alternatives. Three questions, four expression shapes, bands computed against the question as
`handle_search` computes them (`covers n/m of the question's words`).

## The measurement

| variant | hits served | citable | aborted | total time |
|---|---|---|---|---|
| `and_all` — the harness's expression for the whole question | **0** | 0 | 0/3 | 7.0 s |
| `or_all` — the same words, any of them | 0 | 0 | **3/3** | 135.0 s |
| **`and2`** — two words, ANDed (the model's habit) | **16** | **4 (25 %)** | 1/3 | 75.8 s |
| `or2` — two words, ORed | 0 | 0 | **3/3** | 135.0 s |

Per question, `and2` served 8 hits on two of the three (2 citable each, and 12 of the 16 were
`weak`); on the third it hit the 45 s cap and returned nothing.

## What it says

**1. The expression shape is not the limiter.** `and2` — the shape the model actually uses —
serves hits, and a quarter of them are citable. Relaxing the constraint does not help: **`or2`
and `or_all` abort every time**, because an OR matches hundreds of thousands of chunks and
`ORDER BY bm25` must score every one of them to return eight. OR is not a cheaper alternative
here; it is unaffordable.

**2. The harness's own whole-question expression is dead weight.** `and_all` matches nothing,
every time, cheaply (7 s total). It was already visible in the first attempt at this
measurement, which reported zeros for every question and is how the fact surfaced.

**3. What limits match quality is the *terms'* commonness**, in two directions: a common word
makes any expression unaffordable (the third question's `and2` aborted while the other two
finished), and an uncommon word makes a two-word AND match nothing at all. The band a passage
earns is then decided by how many of the *question's* words it happens to contain — which is a
property of the retrieval, not of the model.

**4. So the lever is query construction, not expression shape.** The measured options are to
advise or require terms that are specific enough to be affordable and common enough to match,
or to prefilter by a rare term before bm25 ever scores. Both change what a search returns, so
the choice is the owner's — which is what this objective was asked to stop at.

## The instrument, corrected

This measurement failed twice before it ran, and both failures are worth keeping:

- **Uncapped**, it ran past the executor's limit without finishing one question.
- **Capped from `set_progress_handler`**, it produced nothing in ten minutes: a progress handler
  cannot bound a statement that is not stepping the VM, and a bm25 sort over a large match set
  is not. **RO16's own record already prescribes the right instrument** — "a watchdog thread …
  calling `sqlite3.interrupt()` rather than a progress handler, which cannot see a query waiting
  on I/O" — and this run used it, capping every statement at 45 s and reporting `ABORTED` where
  the cap fired. Reaching for the prescribed-against instrument cost two attempts and twenty
  minutes.
- A cleanup step also failed twice because a `pkill` pattern matched the shell running it, so
  two scans of a 38 GB index ran concurrently. The fix is a script whose own command line
  cannot contain the pattern, which is now how every long job here is launched.

## Caveats

Three questions of the six, one corpus, warm cache. `and2` uses the *first two* content words,
which is a stand-in for the model's choice, not the model's choice itself — the live queries are
recorded, and a follow-up could replay those exactly.
