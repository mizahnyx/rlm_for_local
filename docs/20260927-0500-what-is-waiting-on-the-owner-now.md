# What is waiting on the owner, now that OCR is decided

**Date:** 2026-09-26/27. **Status:** the open-gate inventory, revised. Replaces the ordering in
`docs/20260927-0300-what-is-waiting-on-the-owner.md` (which it cites rather than rewrites) after two
owner calls and one steer: **the OCR campaign is deferred** (*"Not yet, I would prefer to have more
progress on this harness"*, 2026-09-26) and the typed controller is abandoned
(`docs/20260927-0200-the-typed-controller-is-abandoned.md`).

## What changed since the previous inventory

| then | now |
|---|---|
| Gate 6 (typed controller) open | **closed** — abandoned; the measurement survives as unshipped code |
| Gate 2 sub-question B (OCR) open | **answered** (OCR, recent ML model) and **deferred as a campaign** ~15 days; the branch is wired and verified |
| Gate 5 (cell budgets) decided | still decided |
| the order was scope-first (Gate 2) | re-ranked for the owner's steer: **harness progress first** |

The steer matters for the ordering, so it is written down rather than inferred: a gate that makes
the *harness* better is now worth more than a gate that decides how much *corpus* to process. Gate 2
decides scope — which is hours of machine time — and its two remaining sub-questions add ingest work
rather than capability.

## 1. Gate 1 — the destination: per spec, harness-first, or adopt (recommended next)

**The question** (`docs/20260924-0010-gate-1-the-harness-against-the-memory-wiki-spec.md`): build the
memory-wiki service as specified, reach it harness-first, or adopt a local OpenWiki/LangChain
instance. Two sub-forks ride with it: **if there are two write paths, where does the gate sit?** and
**what is the LTM authoritative for** — a cache of interpretation with pointers back to source, or
"wiki as source of truth" as its own spec says?

**Already measured.** The reconciliation of the spec against the code: **5 EXISTS / 18 PARTIAL / 15
ABSENT of 38 capabilities**, zero `/api` routes, no clustering or page generation, `PageKind.topic`
exists but `source` and `media` do not. The adopt arm's `AGENTS.md` §1.9 precondition (the vendor's
outbound behaviour) is testable and untested.

**Why first for the owner's steer.** It is the only gate that decides *what the harness becomes*: the
last three weeks built retrieval, mining and the corpus loop, and the memory/LTM half — pages,
clustering, link suggestion, the wiki — is the part the spec describes and the code does not have.
Every other item below is either a correctness call or a spend decision.

**Size:** a decision, then a design; the adopt arm also needs one outbound test.

## 2. Gate 4's single call — a diagnostic-only `lstat`, or not

**The question** (`docs/20260923-2330-gate-4-telling-absence-from-corruption-from-change.md` §5):
telling a *dangling symlink* from *nothing was ever there* needs `lstat` of the unresolved entry,
which the read-only mount refuses by design. Either a **diagnostic-only command** permitted to
`lstat` for classification only, never on a read path and never following the link, or **leave it**
and accept `absent` as the most the harness will ever say.

**Why it is next after Gate 1:** it is harness capability, it costs ten minutes of attention, and the
rest of Gate 4 — a failure vocabulary, a content baseline at first read, a state per path, escalation
on clustering — needs **no owner call** and is unbuilt. For an LTM over a *backup*, "the source is
gone" is the expected case, not an error.

## 3. Two summariser calls (harness correctness, ~minutes each)

From `docs/20260926-1800-a-cap-bound-document-does-not-finish-inside-its-timeout.md`:

- **The cap or the timeout.** A 32 KiB input is ~22–31 minutes of prompt processing against a
  ~34-minute derived timeout; one document was still running after 2 061 s. Lower the cap to 16 KiB
  or raise the timeout.
- **The cache-key hole.** The derivation key does not include the input cap (`summary<=v1,400tok`),
  so changing the cap would leave descriptions of *truncated* documents looking valid. Cheap to fix;
  it invalidates the cached descriptions.

## 4. Gate 2 — the two remaining sub-questions (scope, not capability)

**A. Archives** (99 993 containers, 104 GiB): expanded into the text index, or left listed?
**C. Source code** (1 623 717 sniffed-text files): searchable only, or described?

Both are cheap to *close* — the archive sampling measurement needs no decision and no model
(hours), and the code question is really Gate 3's spend in another costume (44 descriptions = 4–22 h).
Neither adds harness capability, which is why they have moved below Gate 1 and Gate 4 under the
owner's steer. (`docs/20260924-0130-gate-2-what-counts-as-in-scope-for-text.md`.)

## 5. Gate 3 — the description spend, and the OCR campaign: both deferred

Their experiments are done: descriptions reach **2 of 6** questions and nothing measured changed an
answer (`docs/20260927-0000-…`); OCR costs **187 s a page** and the 517 documents are **~15 days**
(`docs/20260927-0400-…`). Both are now spending decisions with the evidence in hand, and both are
deferred — OCR explicitly, descriptions by the same reasoning. Re-opening either needs only a
window, not another measurement.

## 6. Two privacy calls (small, and one is a rewrite of published history)

- **Trajectory naming**: 15 of 64 trajectories under `~/rlm-derived` have a stem matching a token on
  the private list. Renaming to `0001.jsonl` with `questions.txt` as the map removes the class.
- **The published history**: 6 occurrences of 3 tokens across 4 commits (2026-09-14 … 09-19), all
  pushed, all predating the token list. Whether they are genuine identifiers is decidable only on the
  machine holding the list; if genuine, the remedy is a force-push.

## 7. Still deferred from the ledger

**OD2** embeddings (`sqlite-vec` — the free step first: fix query construction, then re-measure
`weak`), **OD6** the root-tier model (the graded-question test is unrun), **OD7** task-shaped
profiles, **OD5** `max_instances` OOM, **OD1** the tier-2 load corpus, **OD3** later-phase
vault/media/UI, and the X-scraper as a pattern source (a reading).

## If you want one answer

**Gate 1.** It is the only remaining gate that decides what the harness *is*, and the memory/LTM half
is the half the code does not have. If you would rather take something small first, Gate 4's single
call is ten minutes and makes the harness able to say *why* a file cannot be read — which the LTM will
need on every page whose source has gone.
