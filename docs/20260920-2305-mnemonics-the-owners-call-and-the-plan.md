# Mnemonics: the owner's call, and the plan to execute

**Created:** 2026-09-20 23:05
**Status:** point-in-time. It records the owner's finding on the prompt experiment's page, the
decision that follows from it, and an implementation-ready plan for RO13 — the mnemonic alias
table. **The feature is not built.** This document exists so the next unit starts from the
design and the evidence instead of re-deriving both.
**Supersedes nothing**; it activates `docs/20260917-1215-mnemonic-addresses-design.md`, whose
gates the owner has now closed.

## The finding

The owner read the page from the prompt experiment
(`docs/20260920-2215-the-prompt-experiment-one-question.md`, whose run logged
`IndexError=3, ValueError=1`) and reported the cause:

> *"The `IndexError` happens because the model tries to 'massage' the address part of the
> results of `corpus_search` in order to feed one of them to `corpus_read`. To me, this is a
> strong case to start using mnemonics instead of plain addresses, so the model never gets
> confused by the exact syntax of addresses that can be 'too much' for small local models."*

That is a new failure mode in the record, and it is the one RO13 was waiting for. Note what it
is *not*: the model found the right passages (four `strong` hits), cited one exactly, and then
lost cells to **string surgery on the address syntax** — `path#L<start>-<end>` is a long,
punctuation-dense token, and it was being sliced and rejoined to be handed back. The measured
shape across three runs of the same question, by exception type:

| run | prompt | cells with stderr | types |
|---|---|---|---|
| set 2 | old | 3 | `SyntaxError=1`, `TypeError=2` |
| single re-run | old | 1 | `TypeError=1` |
| prompt experiment | new | 4 | **`IndexError=3`, `ValueError=1`** |

Two obstacles have now been measured and removed in turn — the mistyped parameter, then
orientation — and each time the model's path continued into the next one. This is the third.

## The decision

**Start RO13, with the alias table first.** The owner's earlier ordering — *structured hits
before the aliases* — was set before this evidence existed; this instruction supersedes it, and
the two halves compose cleanly: a hit becomes a record that **carries** an alias, so the record
half (named fields, instructive integer indexing) lands with the alias rather than before it.

Everything else the owner decided on 2026-09-17 stands: aliases are **per chat session**
(an `ask` run is a session of length one), the **harness sees mnemonics while the owner sees
true addresses**, the delivered answer has the address **substituted inline with the model's
raw output kept** in the trajectory, and every repair is an event.

## The plan, ready to execute

**1. `src/rlm_local/mnemonics.py`** — the pure core, no I/O:

- `AliasTable`: append-only `address → alias` and `alias → address` maps for one run.
  `mint(address) -> str` is idempotent per address (the same passage keeps its alias within the
  run) and never collides: a new alias is drawn from the free space, and the table is never
  persisted, so **an alias cannot resolve to a different passage in a later run**.
- The code: two letters, a digit, a check symbol (`KQ7-3`), from an alphabet that excludes the
  confusable pairs `0/O`, `1/I/L`, `5/S`, `8/B`; the check symbol is a mod-N digest of the rest,
  so a corrupted alias is *detectably* corrupt rather than silently pointing elsewhere.
- `resolve(alias) -> Resolution`, in the design's order: exact; case/whitespace normalisation;
  confusable folding (`O→0`, `I/L→1`, `S→5`, `B→8`, `rn→m`); edit distance ≤ 1 **with a unique
  winner required** (two candidates means "ambiguous, here they are", never a guess); otherwise
  "no such alias in this run; the closest are X and Y". The result says which step answered, so
  the caller can log a repair.

**2. Wiring** (`repl.py` worker + `corpus.py` bridge + `root_loop.py`):

- `corpus_search`'s hits become records whose `__str__` is today's printable line unchanged, so
  `print(hits)` and the prompt's examples keep working; integer indexing raises a naming error
  (*"a corpus hit is a record; use `hit['address']` or `hit['snippet']`"*). The record carries
  `address`, `alias`, `band`, `covers`, `snippet`.
- `corpus_read` accepts an alias **or** a full address; the worker resolves aliases through the
  table before sending, so the parent still receives addresses only — one place learns the
  mnemonic vocabulary, and the mount, the containment check and the read-only guarantee are
  untouched.
- A citation written as an alias is mapped back to its address **before** the served-set check,
  so the citation audit stays exact; a repair writes a `citation_repaired` event, which makes the
  model's real error rate on addresses a number instead of an anecdote.
- The trace viewer shows alias and address side by side (`traceview.py`), and the delivered
  answer substitutes the true address inline while the model's raw output stays in the trajectory.

**3. Tests first, then mutations** (the entries name tests that must go red):

| guard | test | mutation |
|---|---|---|
| the code excludes confusables | minting never emits `O`, `I`, `L`, `S`, `B`, `0`, `1`, `5`, `8` in the letter/digit positions | put `O` back in the alphabet |
| aliases are one-to-one within a run | two addresses never share an alias, and the same address keeps its alias | mint without checking collisions |
| a corrupted check symbol is caught | flipping one character of a valid alias is refused, not silently resolved | skip the check-symbol verification |
| ambiguity is refused, not guessed | two one-edit candidates produce "ambiguous", not a pick | take the first candidate |
| an alias never resolves across runs | a fresh table refuses another run's alias | seed the table from a process-wide cache |
| integer indexing teaches | `hits[0][0]` raises with the field names in the message | return the character again |
| a citation is mapped before the check | an aliased citation passes the served-set audit, a fabricated one does not | check the raw alias against the served set |

**4. Documentation**: the operator guide's corpus section (what the model sees, what the owner
sees, where the mapping is), `AGENTS.md` §2 (the alias contract and the `citation_repaired`
event), and the viewer's page (alias and address side by side).

**5. The measurement after it lands**, against the baselines already recorded: the
`citation_repaired` count, the exception-type counts for the prompt-experiment question
(`IndexError=3, ValueError=1` — the number to beat), and the on/off-question split. A live run
of the same question on the same index is the comparison.

## Why this document and not the code

The alias table changes the model-facing contract in four places at once (search output, read
input, citation checking, the delivered answer), and every one of them has a guard that has to be
proved non-vacuous. Writing it with insufficient room to test it would produce either an
unverified module in the tree or a half-wired vocabulary — and a mnemonic layer that resolves
*ambiguously* is worse than the long addresses it replaces, because it would silently point a
citation at the wrong passage, which is the one failure this project treats as unforgivable.
The plan above is the smallest honest unit of progress available now.
