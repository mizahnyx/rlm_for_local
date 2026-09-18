# Decision brief: mnemonics, budgets, and one proof

**Created:** 2026-09-17 13:30
**Status:** a brief written to be *answered* — each section ends with the decision, the
options, what each costs, and what would change my mind. Nothing here is decided.
**Supersedes nothing.** Companion to
`docs/20260917-1215-mnemonic-addresses-design.md` (the mnemonic design itself).

## Settled already, so it is on the record

- **Session scope (owner, 2026-09-17): one chat conversation, spanning many runs.**
  Aliases accumulate for the life of a `chat` session and are stable within it; an
  `rlm ask` run is a session of length one. Consequence worth stating: the alias
  table is *session state*, so it lives in the chat process and is written into each
  run's trajectory — and a citation is checkable only against the session that
  produced it.
- **Who sees what (owner, 2026-09-17): "Harness only sees mnemonics, user only sees
  true addresses, in a transparent way."** Read as: the model reads and writes
  mnemonics; you read real addresses; and the translation is not hidden — the
  trajectory keeps the model's raw output (mnemonics and all), the mapping table is
  on the trace page, and every repair is an event. One sub-question remains and is
  asked first below, because it is the only part of that sentence with two readings.

---

## 1. Does the harness rewrite the delivered answer?

**What happens today.** The model's final answer is delivered verbatim: the string it
produced is what the CLI prints, what the log's `end` event holds, and what the trace
page shows.

**The tension.** You said you only see true addresses. Two mechanical ways to honour
that, and they differ in what is done to the model's own words:

| option | mechanism | what you read | cost / risk |
|---|---|---|---|
| **A. Substitute inline, and keep the raw** | a mechanical pass replaces every valid alias with `path#L<start>-<end>` in the delivered answer; the model's untouched output stays in the trajectory as `root_message`/`end.raw` | an answer with no mnemonics anywhere | the delivered answer is no longer byte-identical to the model's — a project that has never rewritten model output would start, with the mitigation that the raw is always kept and the substitution is logged |
| **B. Footer only** | the answer is delivered verbatim (mnemonics and all) *plus* a harness-written `Citations:` footer naming the true addresses | an answer with `KQ7-3` inline and a legend below | no rewrite at all; but "user only sees true addresses" is then false inline, and the answer is noisier |
| **C. No rewrite, no footer** | the model is *required* to write addresses; aliases are only for intermediate cells | addresses everywhere | the citation step still makes the model copy an address — the exact failure the mnemonic exists to remove — so this option defeats the purpose |

**My recommendation: A.** It is the only one that satisfies both halves of what you
said, and the transparency is real rather than rhetorical: the trace shows the raw
model output, the substitution is recorded as a `citation_substituted` event naming
what changed, and the mapping table is on the page.

**What would change my mind:** if you want the delivered answer to be *exactly* what
the model produced, always — then B, and accept a legend in the text.

---

## 2. Structured hits first, or both changes at once?

**What actually happens today, step by step.**

1. A cell calls `corpus_search('…')`.
2. The worker process sends `{"cmd": "corpus_search", …}` to the parent (the harness).
3. The parent asks the corpus bridge, which returns a **list of formatted strings**;
   each element is `<address>  [labels]\n    <snippet>`.
4. The parent JSON-encodes that list back to the worker, which returns it to the cell.
5. So `hits[0]` is a `str`. `hits[0][0]` is `'S'`. A model that expects a structure
   gets a letter and continues, which is worse than an error — that is your
   character-by-character finding, and it is the *same* encounter that produced the
   very first `len(hits) == 1233` bug in the original live run.

**What "structured hits" changes.** One element becomes a record with named fields —
`hit['address']`, `hit['alias']`, `hit['band']`, `hit['covers']`, `hit['snippet']`,
`hit['text']` — and:

- `str(hit)` prints exactly today's line, so `print(hits)` looks unchanged;
- `hit[0]` **raises** `TypeError: a corpus hit is a record; use hit['address'] …`;
- `corpus_read(hit)` keeps working (the worker reads the record's address);
- `len(hits)` stays the contract.

**Why the order matters.** The alias is a *field of a hit*. If records land first, the
mnemonic change is "add a field and a resolver"; if aliases land first, the element
type changes twice and the prompt is rewritten twice.

| option | steps | cost | risk |
|---|---|---|---|
| **Records first (recommended)** | 1 protocol change + prompt sentence + tests + mutations, then the alias work as a smaller second change | one extra transition and one extra live run if we want a measurement per step | each change is attributable; the observed `'S'` failure is fixed immediately |
| **Both together** | one protocol change, one prompt rewrite, one measurement | fewer transitions, ~1 live run saved | if the result is worse, the cause could be either half; the diff is roughly twice the size to review |
| **Aliases first** | the bigger change lands first, on the *string* element type | the smaller, already-observed bug stays | element type changes twice; the second change touches the same code again |

**My recommendation: records first.** The failure it fixes is already observed rather
than hypothesised, the change is about a tenth of the alias work, and it makes the
alias diff reviewable on its own.

**What would change my mind:** if you would rather review one contract change than
two, both together is defensible — I would then insist on two live measurements, one
per half, to keep attribution.

---

## 3. RO11's residual: what an archive-member address actually costs

**The mechanism, step by step.**

1. A search hit prints an address: `<display>#L<start>-<end>`.
2. To read it, the text index must find the chunk row. `text_chunks` (29 015 791 rows)
   has an index on `source` — the **exact path bytes** — and **no index on `display`**,
   the human-readable path.
3. For an address that names a real file (the common case) I resolve
   `display → exact bytes` through the *path* index (`entries`, indexed on `path`,
   one lookup) and then filter on the indexed `source`. **Measured: 3.86 s** for an
   address that previously did not return in 150 s.
4. For an address that names **no file** — a member inside an archive
   (`arch.zip!member.txt`), or an extracted document whose display is not a path —
   there is no `entries` row, so there are no bytes to resolve, and the only lookup
   left is `WHERE display = ?` over all 29M rows. **Measured: did not return in 150 s**
   (the viewer's first render hung >600 s on exactly such an address, which is how it
   was found).

**So the question is narrow: how common is case 4, and is there a cheaper route than a
new index?** Two facts I can get cheaply, read-only, before you decide anything:

- how many chunk rows have a display that contains `!` (a container member), and
- whether those rows' `source` bytes name the **container** (in which case the
  existing `source` index already resolves them: split the display on `!`, resolve the
  container through `entries`, then filter on `source` — no new index at all).

| option | mechanism | cost | risk |
|---|---|---|---|
| **Measure the residual first (recommended)** | two read-only queries on the real index, printing counts and booleans only; then decide | minutes, no writes | none — it is a measurement, and it may make the whole question moot |
| **Index `text_chunks(display)`** | `CREATE INDEX … ON text_chunks(display, byte_start, byte_end)` | SQLite scans the whole table to build it: I would estimate GB and minutes from read-only page/sample queries **and report before writing**; writes to the derived index (allowed: outside the corpus); needs free disk; a failed build rolls back and pays the time again | the fix is certain but the price is paid on your disk, permanently |
| **Resolve through the derivation cache** | open the archive, extract the member, read the cached text | bounded per read, but only for members mining has already extracted | does not cover members never extracted; more moving parts in the read path |
| **Leave it, documented** | the viewer marks those addresses "not embedded" and the model's `corpus_read` stays slow-but-correct | nothing | citations pointing inside archives stay slow; a run that leans on them loses cells to the budget |

**My recommendation: measure first, then decide** — and if the measurement says the
`source` route covers members, the answer is "no new index, a small code change".

**What would change my mind:** if you want the residual gone regardless of the
measurement, index it; I would do it as an operator command (like
`corpus counters --refresh`), never inside a cell, and report the cost first.

---

## 4. `corpus_count`: your hunch is better than my question

**Your hunch, restated:** *measure the cost in operations, not in fixed time.* I think
you are right, and here is why the 60 s number failed at all.

**Why a wall clock is the wrong unit.** A second is a property of *the machine at that
moment*: the same `corpus_count` is 4 s on an idle host and over 60 s on a loaded one,
so a time budget converts "how much work did you ask for?" into "how busy was the box?".
That is exactly the confusion your finding named — the harness reported a *harness*
limit as if it were a model failure — and raising the number only moves the threshold.

**The work unit that is actually available.** SQLite exposes a progress handler: the
engine calls back every N virtual-machine instructions and can be told to abort. So a
query can carry a **deterministic budget in engine operations** (for example "this
count may examine 2 million steps"), which is:

- **host-independent** — the same cell fails on the same query on any machine, loaded
  or idle;
- **attributable** — the failure says *what it would have scanned* ("this would
  examine ~4.97M rows"), not "60 seconds elapsed";
- **convertible to advice** — the abort message can point at the cheap alternative.

**And the cheap alternative for this verb.** `corpus_count` counts the *path* index
(4 972 609 entries) to answer "how many files, how many bytes". Those aggregates change
only when mining changes the tree, so they can be **published** the same way coverage
already is (`corpus counters --refresh`, and at the end of every mining window), read
as one row with an age. The common question then costs nothing, and a *live* count
still exists for `kind=`/`under=` filters, where the query is a bounded range scan.

| option | mechanism | what the model sees | cost |
|---|---|---|---|
| **Publish aggregates + op budget (recommended)** | snapshot of the path-index counts refreshed with coverage; progress-handler budget on the counting verbs; wall clock kept for non-SQL work (mount reads, sleeps) | the cheap answer instantly with its age; a live filtered count; a clear refusal if a live count would scan too much | one snapshot key, one refactor of the counting path, one operator command already exists |
| **OP budget only** | no snapshot; every count is live but bounded | a count that is honest about refusing, never a stale number | the common question sometimes refuses on a loaded host, even though the answer has not changed in days |
| **Raise the wall clock only** | `--cell-timeout 240` | the same timeout class, later | cheapest to do, and leaves the unit of measurement wrong |

**My recommendation: publish the aggregates, and add the op budget as the general
mechanism** — the snapshot removes the *cost*, and the op budget replaces the wrong
*unit* for everything else (including the next expensive helper, which we have not met
yet).

**One honest consequence of publishing:** a published count is a *snapshot*, so it must
say its age ("4 972 609 entries as of 2 h ago") exactly as the coverage line does — and
a search that finds nothing over a stale snapshot still says `unknown` where it cannot
tell. That is the same rule the mounting probe and the coverage note already follow.

**What would change my mind:** if the path-index counts turn out to be cheap with the
`entries` indexes (a plain `COUNT(*)` on 4.97M rows is a table scan of a *small* table —
it may well be a few seconds, with `SUM(size)` the expensive part), then the snapshot is
unnecessary and the op budget alone answers your finding. **That is measurable in
minutes, read-only, and I would measure it before building either.**

---

## 5. The live runs: what each one is for

**What "run" means here**: one `rlm ask` of the graded unanswerable question, profile
`laptop`, `--max-turns 8`, against the complete index, writing a new trajectory; then
`rlm trace render` turns it into a page you can read. ~45 minutes of the laptop, one 4B
model resident, nothing written to the corpus.

**Run A — the instrument run (what it would settle):**

| question it answers | instrument | why it matters |
|---|---|---|
| Does the model actually emit invalid Python? | `syntax_retry` / `syntax_giveup` counts | the fix I just landed is proved only in fixtures; if the event never fires, its cost (extra calls inside a turn) is zero and we know that instead of guessing |
| Does anything die on the cell budget, and on which helper? | `cell_timeout` events with `budget=`/`last_helper=` | confirms or refutes your `corpus_count` finding on this host — the one number I have been unable to measure here |
| **How badly does the model corrupt addresses?** | the served set (`corpus_served`) vs the addresses the answer cites, classified three ways | **this is the number that decides whether the mnemonic work is worth building at all** |
| Does it cite an answer it never read? | citation audit (`cited_answering` / `cited_non_answering` / `cited_unserved` / `served_not_cited`) | the relevance question you asked the viewer to answer |

The address classification needs one small addition I would write first: a `traceview`
report that takes the cited addresses and the served set and buckets each cited
address as **exact match**, **one edit away** (repairable — what a mnemonic would have
saved), or **unmatched** (what the guard refuses today). Today those last two are
indistinguishable, which is precisely why the size of the mnemonic prize is unknown.
It runs on a trajectory, costs seconds, and needs no model.

**Run B — the comparison run**, after whichever change you approve, same protocol, so
the two forms are compared on the *same* model, host state recorded, ≥2 runs each if we
want a rate rather than an anecdote.

| option | cost | what you get |
|---|---|---|
| **A now (recommended)** | ~45 min | the decision input for the mnemonic work, plus the first live exercise of both fixes |
| **A and B now** | ~90 min | a before/after in one sitting — but B presumes a design you have not approved, so it may measure a form we then change |
| **Hold both** | nothing | the instruments stay unexercised; the mnemonic decision stays unmeasured |

**My recommendation: A only, now.** It is the input to the decision rather than a
consequence of it, and it is the cheapest way to stop arguing about the mnemonic prize
from first principles.

**What would change my mind:** if you would rather not spend laptop time until the
change is built, then run A and B together after it — but then we decide the mnemonic
question on reasoning alone, which this project has been wrong about before.
