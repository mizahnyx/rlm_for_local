# Handoff: execute the mnemonic alias table (RO13)

**Created:** 2026-09-20 23:25
**Status:** a resume document. The design is decided, the evidence is in, the plan is written,
and **nothing is built**. This adds what a fresh context needs that the plan alone does not:
exact code shapes, the wiring points by name, the traps this session paid for, the
verification state, and one ambiguity in the design that has to be resolved before the first
line is written.
**Supersedes nothing.** Read it together with
`docs/20260920-2305-mnemonics-the-owners-call-and-the-plan.md` (the plan, with the
test/mutation table) and `docs/20260917-1215-mnemonic-addresses-design.md` (the design).

---

## 1. Why this, now

The owner, reading the page from the prompt experiment: the three `IndexError`s are the model
*"trying to 'massage' the address part of the results of `corpus_search` in order to feed one of
them to `corpus_read`"* — string surgery on `path#L<start>-<end>` — and *"a strong case to start
using mnemonics instead of plain addresses."*

Three obstacles have now been found, measured and removed in turn, and each time the model's
path continued into the next one: the mistyped parameter (`TypeError=2`, fixed by a teaching
message and the prompt), orientation (`first_helper_turn` 4 → 2, fixed by a prompt line), and
now address syntax. The baselines to beat are in §5.

## 2. What to build, in order

### 2.1 `src/rlm_local/mnemonics.py` — the pure core (no I/O, no bridge)

```python
#: Two letters, a digit, a check symbol — `KQ7-3`. One member of each confusable pair is
#: excluded, so an alias can never be misread as a different *valid* alias.
LETTERS = "ABCDEFGHIJKMNOPQRSTUVWXYZ"      # no L (confusable with I)
DIGITS = "234679"                          # no 0, 1, 5, 8
CHECK = DIGITS + "ACDEFGHJKMNPQRTUVWXYZ"   # a mod-N digest of the rest

AliasTable:
    mint(address: str) -> str        # idempotent per address; never collides; append-only
    resolve(alias: str) -> Resolution
    alias_for(address) / address_for(alias) -> str | None
    __len__                          # how many aliases this run has minted

Resolution: address | None, status in
    {"exact", "normalised", "folded", "repaired", "ambiguous", "unknown"},
    candidates: list[str]            # for ambiguous/unknown, the closest few
```

**The one ambiguity in the design, resolved here.** `docs/20260917-1215` writes the fold as
`O→0`, `I/L→1`, `S→5`, `B→8` — i.e. *toward* the digits `0/1/5/8`. That cannot be right: if the
alphabet excludes those digits, folding into them yields a string no valid alias can equal, so
the fold would turn a repairable slip into an unknown alias. The fold must go **toward the
surviving member of each pair**, and the alphabet must keep one member:

| input glyph (a slip) | folds to | alphabet keeps |
|---|---|---|
| `0` | `O` | `O` |
| `1`, `L` | `I` | `I` |
| `5` | `S` | `S` |
| `8` | `B` | `B` |
| `rn` | `m` | `M` |

`resolve()` in the design's order, and it must agree with this document's table:
1. exact match;
2. case and whitespace normalisation (`kq7-3` → `KQ7-3`, `KQ7 3` → `KQ7-3`);
3. confusable folding per the table above, then the check symbol re-verified;
4. edit distance ≤ 1 against the run's live aliases, **unique winner required** — two
   candidates is `ambiguous` with both named, never a pick;
5. otherwise `unknown`, with the closest few named.
   A repair (steps 3–4) is reported to the caller so it can write a `citation_repaired` event.

**Determinism for tests**: take an injected `random.Random` (the sampler in
`textindex.random_chunks` is the precedent — same signature shape, same reason).

### 2.2 The wiring, by name

| where | what changes |
|---|---|
| `repl.py` `_as_hits` (worker) | each hit line becomes a **record** carrying `address`, `alias`, `band`, `covers`, `snippet`; `str(record)` must equal today's printable line exactly, so `print(hits)` and every prompt example keep working; integer indexing raises `WORKER_HIT_NOT_A_RECORD` (new template) naming the fields |
| `repl.py` `_harness_corpus_read` (worker) | accepts an alias **or** an address: resolve aliases through the run's table before sending, so the parent only ever sees addresses |
| `repl.py` `corpus_search`/`corpus_find` reply path (parent) | the parent mints an alias per served address (`AliasTable` lives beside the sandbox, like `corpus_addresses_served`) and includes it in what the worker prints |
| `root_loop.py` `_refusal_reason` / the served-set check | map a cited alias back to its address **before** the check, so the audit stays exact; a repair from `resolve()` writes a `citation_repaired` guardrail event |
| `traceview.py` `render_run_markdown` | show alias and address side by side for every cited or served address |
| delivered answer | substitute the true address inline; the model's raw output stays in the trajectory (owner, 2026-09-17) |

**Where the aliases must not go**: the bridge (`rlm_kernel/corpus.py`) keeps its address-only
vocabulary, and the mount, the containment check and the read-only guarantee are untouched. One
process — the worker — learns mnemonics.

### 2.3 Tests and mutations

The seven-row table is in the plan (`docs/20260920-2305-…` §3). Add two more it does not have:

| guard | test | mutation |
|---|---|---|
| the fold goes toward the surviving glyph | a `0` in an input resolves as `O` | reverse the fold (`O→0`) |
| the record prints exactly as before | `str(hits[0])` equals the old formatted line | drop a field from `__str__` |

### 2.4 Documentation

Operator guide (what the model sees, what the owner sees, where the mapping lives),
`AGENTS.md` §2 (the alias contract and the `citation_repaired` event), and the viewer.

## 3. Traps this session paid for

1. **A fixture's text length is load-bearing.** `tests/test_traceview.py`,
   `test_root_loop_integration.py` and `test_cli_trace.py` build addresses and byte offsets out
   of their own fixture text (`#L0-21`). A 9-character replacement for a 7-character word broke
   **ten** tests; a same-length one fixed all ten **without touching an assertion**.
2. **Tests must carry invented addresses and aliases only.** A real address or a question id is
   corpus-derived; `scripts/check_privacy.py --tokens ~/rlm-derived/private-tokens.txt` fails
   the build if one appears, and the incident that produced it is
   `docs/20260919-2330-a-question-id-reached-the-public-repository.md`.
3. **Never edit source while `scripts/check_guard_nonvacuity.py` runs** — it restores the file
   it read, so an edit inside that window is lost (`AGENTS.md` §3).
4. **The list contract must survive**: `corpus_search`, `corpus_find` and `corpus_list` return
   lists and the rest return text, and a mistyped parameter is answered with the parameters that
   exist (`_teaching`, `_as_hits`, `WORKER_CORPUS_BAD_ARGUMENTS`). The record type must keep
   `len(hits)` and iteration working.
5. **A silent wrong resolution is the unforgivable failure here.** `ambiguous` must refuse; the
   check symbol must be verified; aliases must never be reused across runs. A mnemonic layer
   that points a citation at the wrong passage is worse than the long addresses it replaces.
6. Verifying costs wall clock: fast suite ≈ 9 min, full mutation table ≈ 25 min, doc lint
   seconds. Run the suite and the table *sequentially*, never together.

## 4. Verification state at this handoff

`main` at **`90706e1`**, clean, pushed, and synced to `lunacode` (`~/Misc/rlm_for_local`).
Last full verification: fast suite **1377 passed / 8 skipped / 12 deselected / 0 failed**;
mutation table **208 guards / 0 problems**; doc lint **61 documents clean** (the two docs added
after that run changed no code, and the lint was re-run on them).

## 5. The measurements to compare against, after it lands

| what | where | value to beat |
|---|---|---|
| exception types, the prompt-experiment question | `~/rlm-derived/questions-prompt-a/` | `IndexError=3, ValueError=1` (this is what mnemonics should remove) |
| the same question, older runs | `~/rlm-derived/questions-set2/` (index 5), `…/questions-after-repair/` | `TypeError=2`+`SyntaxError=1`, and `TypeError=1` |
| cited-answering / bands / orientation | `~/rlm-derived/traces/questions-prompt-a/index.md` | `cited_answering=1` (exact), `strong=4`, `first_helper_turn=2` |
| on/off-question split, all runs | `.tmp_probe/query_overlap.py` (scratch, not in the repo), and the totals in `docs/20260920-2104-…` | on-question `strong=40, weak=44`; off-question `none=40` |
| the model's real address error rate | `citation_repaired` events (new) | no baseline — this is the number the feature exists to make measurable |

Scratch tools that are useful and currently **only** in `.tmp_probe/` (gitignored, so they do
not travel): `compare_runs.py` (events, verbs, orientation), `query_overlap.py`,
`safe_summary.py` (identifier-stripped `trace summary`), `error_types.py` (exception types
only), `one_question.py` (copy a question by index). If a later session wants them permanently,
they belong in `scripts/` with the same identifier hygiene — none of them prints a run id,
a question, a query, a path or an address.

## 6. The owner's decisions, verbatim, that this must honour

- *"the harness sees mnemonics while the owner sees true addresses, transparently"* — the
  trajectory keeps the model's raw output, and the mapping is on the trace page.
- *"the harness substitutes the true address inline"* in the delivered answer.
- **Per chat session**: aliases span the runs of one chat session; an `ask` run is a session of
  length one.
- Every repair is an event; aliases are **never reused across runs**.
- This document's activation supersedes the earlier *structured hits first* ordering: each hit
  becomes a record that **carries** its alias, so both halves arrive together. (Owner-approved
  update; if the original order is preferred, revert that clause and land records first.)
