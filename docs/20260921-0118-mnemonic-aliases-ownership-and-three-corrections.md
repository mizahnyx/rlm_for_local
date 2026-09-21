# Mnemonic aliases (RO13): the ownership call, and the three corrections the measurements forced

**Created:** 2026-09-21 01:18
**Status:** point-in-time record. The feature **is built** and unit-verified; a live model has
not yet cited through an alias. This document answers the ambiguity the handoff
(`docs/20260920-2325-handoff-mnemonics.md` §2.2) left open, and records three places where
the design's reasoning was **wrong** and only measurement showed it.
**Supersedes nothing.** It corrects two claims in
`docs/20260917-1215-mnemonic-addresses-design.md` (the check symbol's guarantee, and the
repair-filter direction) and one in `docs/20260920-2305-…` (the code space's width).

## 1. Who owns the alias table — the parent, not the worker

The handoff's wiring table said two things that cannot both be true: `_harness_corpus_read`
in the **worker** should "resolve aliases through the run's table before sending", while
`AliasTable` "lives beside the sandbox, like `corpus_addresses_served`" — and
`corpus_addresses_served` is a `REPLSandbox` attribute, i.e. the **parent**.

**Decision: the parent owns the table and does the resolving.** The worker never learns the
mnemonic vocabulary; it receives hits with an alias already in them and hands an
alias-shaped string back untouched, and the parent translates it on receipt. Reasons, in
order of weight:

1. **One authority, so nothing can disagree.** The parent already owns the served set
   (`corpus_addresses_served`), the band map (`corpus_address_bands`), and the citation
   audit. The alias mapping is the fourth thing that has to agree with those; a second copy
   in the worker is a second copy that can drift, and the failure mode of a drifted alias
   table is a citation pointing at the wrong passage.
2. **The repair policy has to be the parent's anyway.** A cited alias is repaired during the
   audit (`_refusal_reason` → `_audit_text`), and a repair is an event the *parent* writes to
   the trajectory. A worker-side repair would be invisible to the event stream that exists
   to count it.
3. **It keeps the handoff's own boundary.** The handoff's stated rule is that the bridge
   (`rlm_kernel/corpus.py`) keeps its address-only vocabulary and the mount, containment
   check and read-only guarantee are untouched. Parent-side resolution satisfies that
   exactly; a worker-side table would satisfy it too, but only by adding a second stateful
   component beside the served set for no gain.
4. **The worker's failure mode is teaching, not translation.** When the parent cannot resolve
   an alias it answers with a message naming the aliases in play
   (`WORKER_CORPUS_UNKNOWN_ALIAS`). That message needs the table, and the table is in the
   parent.

Cost of the choice, stated: the worker cannot validate an alias locally, so a `corpus_read`
of a stale alias costs one round trip before it is refused. That is one socket message on a
path that already crosses the socket.

**Consequence for the handoff's claim that "one process — the worker — learns mnemonics"**:
the opposite is true, and deliberately. Exactly one process learns mnemonics, and it is the
parent. The handoff's sentence about the bridge is honoured; its sentence about the worker is
replaced by this one.

## 2. A live model has not yet cited through an alias

Nothing in this change has been exercised against a model. The unit and integration evidence
is in §5; the measurement the feature exists to produce — the `citation_repaired` rate on real
answers — is still unmeasured, and the baseline it must beat is the exception-type record in
the handoff §5 (`IndexError=3, ValueError=1` for the prompt-experiment question). A live run
of that question on the same index is the next step, and until it happens this document
claims instrumentation, not improvement.

## 3. Correction one: the check symbol is a corruption detector, not an injective encoding

`docs/20260917-1215` §"Proposed design" says the check symbol is "a mod-N digest of the rest
so a corrupted alias is *detectably* corrupt rather than silently pointing elsewhere". I built
the digest believing it also had a second, stronger property — that the check symbol of two
bodies is equal only when the bodies agree in pairs of positions, which would have guaranteed
that no string is one edit from two live aliases and made the unique-winner rule vacuous by
construction. **That property is arithmetically impossible**, and a brute-force probe over the
whole code space showed it immediately: 3 750 bodies map onto 27 check symbols, so ~139 bodies
share each one. No mod-N digest is injective over more than N inputs. The first implementation
was also **broken in a way the probe caught**: it indexed the digest through the printable
`CHECK` alphabet, which deliberately lacks `B`, `I`, `O` and `S` — so `code_to_check` raised
`ValueError` for four of the body's own letters. The digest therefore has its own alphabet
(`CHECK_DIGEST`), a superset of the body's glyphs.

What survives is the honest version: the digest is corruption *detection* for a string we are
guessing at, and the guarantee that repairs are safe comes from the uniqueness rule instead.

## 4. Correction two: the uniqueness rule is the guard, and the check filter *breaks* it

The design's repair step (4) says: edit distance ≤ 1 against the run's live aliases, unique
winner required. My first implementation narrowed the candidate set to aliases whose check
symbol matched the one the model wrote, on the reasoning that a candidate with a different
check symbol represents a *second* corruption and should not be considered.

**Measured, and it is the opposite.** Over 400 aliases and 3 110 sampled one-glyph slips:

| repair rule | slips resolved to their own passage | slips resolved to a **different** passage |
|---|---|---|
| check symbol must match (my first implementation) | 2 458 | **11** |
| unique winner only (shipped) | 3 069 | **0** |

The filter *causes* the failure. Narrowing the candidate set can leave exactly one survivor —
which is then picked — even though the string is one edit from two live aliases. A slip that
lands on another live alias is one edit from that alias **and** one edit from the alias it came
from, so with the full candidate set two candidates exist and `ambiguous` refuses. The
uniqueness rule is the whole guard, and the check symbol's role is confined to deciding
whether a *folded* string could be a code we issue.

## 5. Correction three: the code space had to be 25× wider, and that was measured too

The design's alias is three body glyphs and a check symbol (`KQ7-3`) — 25 × 25 × 6 = **3 750
codes**. A slip that lands *on* another live alias resolves to that alias's passage, and no
check symbol can see it: the slipped string is a perfectly valid code carrying a valid check
symbol. The defence is space, not cleverness. At 3 750 codes a sampled 400-alias session
produced exactly that wrong resolution — a one-glyph slip of one alias landing on a second
live alias — which is the one failure this layer must not have. The case is not reproduced
here as a literal token: the aliases came from a seeded sampler and are not the corpus's, but
a record that names a *citation handle* it never verified against the token list is exactly the
habit `AGENTS.md` §1.9 exists to stop.

The shipped body is three letters and a digit (`KQM7-3`) — 25³ × 6 = **93 750 codes**, the same
printed length as `KQ7-3` plus one glyph. At that width the same probe resolves every sampled
slip to its own passage or refuses it, across sessions of 50, 200 and 500 aliases. It is still
a probabilistic claim and is documented as one: two live aliases one glyph apart would make
the slip ambiguous rather than wrong, which is the safe direction.

## 6. What the owner reads, and how the substitution is kept honest

The owner's two calls are implemented as given: the **trajectory keeps the model's raw
output**, and **the delivered answer carries the true address**. Two details were decided
during the work:

- **Substitution reports as it rewrites.** `substitute_addresses(text, table, repairs=…)` both
  rewrites and hands back the `(written, Resolution)` pairs it repaired. A separate reporter
  could disagree with the substitution about which citations were clean, and the
  `citation_repaired` count would then describe something other than what happened to the
  answer.
- **Only the alias spans are rewritten.** The scan runs on an upper-cased copy (so `kqm7-3` and
  `KQM7-3` take the same path) but the output is rebuilt from the original text, so the model's
  prose keeps its own case. Shouting the whole answer back at the reader is not a feature.

A repaired alias **is** substituted, not just clean ones. A recoverable slip is the case the
layer exists for.

## 7. Verification state

- **Fast suite:** 1 447 passed / 8 skipped / 12 deselected / 0 failed, 9m46s — 70 tests more
  than the handoff's 1 377: 43 in `tests/test_mnemonics.py` and 27 added to
  `tests/test_corpus_repl.py` (44 → 71). The trace tests were extended in place rather than
  added to.
- **Mutation table:** **221 guards, 0 problems** — the table's 208 entries plus the 13 new
  `RO13` ones, all verified red. They were also verified **by hand** first, because the first
  pass reported four of them VACUOUS and inspecting them found that the *tests* were weak, not
  the code. Three of those four are worth recording because each is a way a guard proves
  nothing:
  - a test asserted `status != "exact"` where it needed `status == "repaired"`; the weaker
    assertion passed with the guard removed;
  - a collision test relied on two random draws colliding out of 93 750 codes, which mostly
    they do not, so it proved nothing most of the time. It now forces the draw to repeat;
  - a mutation replaced a `return` whose text appears in the file twice, so the runner
    reported the target as ambiguous rather than testing it.
  One entry was **deleted rather than kept**: `verify_check=False` on `resolve` guarded a step
  that is unreachable — every table entry is valid by construction — and a guard whose removal
  no test can detect is a claim, not a guard. The parameter now lives on `code_to_check`, where
  the glyph validation it disables *is* observable through the fold.
  Four **pre-existing** entries needed re-deriving rather than deleting, because this change
  rewrote the exact lines they mutated (the `corpus_search` return, the prompt's citation
  requirement, the forced-finalization measurement, and the served payload).
- **Doc lint:** 63 documents clean, and `--self-test` reports 0 problems across its five
  planted defect classes and the clean control.
- **Privacy:** the tree scan must run where the corpus is, so it was run on `lunacode` against
  `~/rlm-derived/private-tokens.txt` after syncing this commit: **8 tokens checked, no
  occurrence in the tree** (exit 0). The `--history` scan reports **6 occurrences in four
  commits that predate this change** (`--only must name the set it searched`, `a citation must
  answer the question`, `corpus_search returns a list of hits`, `the model can search the
  corpus's words`); this commit is not among them. Removing a token from *history* needs a
  rewrite and a force-push, which the checker itself calls the owner's call, and it is
  recorded here rather than done.
- **Not verified:** any live model behaviour. `citation_repaired` is an instrument with no
  number under it yet, and the baseline to beat is the prompt-experiment question's
  `IndexError=3, ValueError=1`.

## 8. Where it lives

| what | where |
|---|---|
| the pure core — alphabet, digest, fold, `resolve`, substitution | `src/rlm_local/mnemonics.py` |
| its tests | `tests/test_mnemonics.py` |
| the hit record and the alias in the reply, the read translation | `src/rlm_local/repl.py` (`HitRecord`, `_hit_record`, `_serve_aliases`, `_resolve_read_target`) |
| the audit mapping and the `citation_repaired` event | `src/rlm_local/root_loop.py` (`_audit_text`, `_map_citations`, `_finalize_answer`) |
| alias beside address on a rendered page | `src/rlm_local/traceview.py` |
| the strings | `src/rlm_local/templates.py` (`WORKER_HIT_NOT_A_RECORD`, `WORKER_CORPUS_UNKNOWN_ALIAS`, `HIT_ALIAS_MARKER`, `CITATION_REPAIRED_DETAIL`) |
| the model-facing contract | `src/rlm_local/prompts.py` (`CORPUS_SECTION_LINES`) |
