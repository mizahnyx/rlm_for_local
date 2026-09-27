# What is waiting on the owner

**Date:** 2026-09-26. **Status:** the current decision-gate inventory, in the order I would take it,
with the data each one needs. Written because two gates closed today (Gate 5 in September, Gate 6
now: `docs/20260927-0200-the-typed-controller-is-abandoned.md`) and the remaining list is short
enough to state in one page.

Each item says: **the question**, **what is already measured**, **what the branches cost**, and
**what is still unknown**. "I can run it" means the measurement needs no decision and no
model — only machine time.

---

## 1. Gate 2 — what counts as in scope for text (recommended next)

**The question** (`docs/20260924-0130-gate-2-what-counts-as-in-scope-for-text.md`), in three parts:

- **A. Archives** (99 993 content-sniffed containers, 104.1 GiB): expanded into the text index, or
  left listed as containers?
- **B. PDFs** (4 318): OCR'd, or metadata/first-page only?
- **C. Source code** (1 623 717 sniffed-text files): searchable only, or described?

**Already measured.** Stage 1 classified all 4 281 585 files reading 41.7 GiB of heads in ~11.5 h.
Text is **2 882 822 files / 42.1 GiB, but only 1 382 200 distinct by head+size hash (28.6 GiB)**,
and **1 474 367 of those files are vendored/build/cache paths** — half the text corpus by count.
Listing runs at ~33 items/s; expansion is unmeasured. No OCR engine is wired, so OCR cost and
quality here are unmeasured.

**What the branches cost.** Staying as we are costs 0 h and leaves the archive contents unknown.
Expanding archives grows the index by an unmeasured factor on top of a 38 GB index and a
root filesystem already at 96% full. Describing the value set's code is 4–22 h for 44 descriptions
(Gate 3's gap).

**What would close it, cheaply.** Two bounded measurements, both needing the box and neither
needing a model: **sample N archives, expand them, report the volume and mix of what came out**
(hours), and **try OCR on a sample of the `needs_ocr` PDFs** (needs an engine installed, and your
judgement of the output). I can run the first without any decision from you; the second needs an
engine choice.

**Why first:** every downstream cost — index size, mining hours, description hours — scales with
this answer, and the answer is currently a guess.

---

## 2. Gate 1 — the destination: per spec, harness-first, or adopt

**The question** (`docs/20260924-0010-gate-1-the-harness-against-the-memory-wiki-spec.md`): build
the memory-wiki service as specified, reach it harness-first, or adopt a local OpenWiki/LangChain
instance. Two sub-forks ride with it: **if there are two write paths, where does the gate sit?**
and **what is the LTM authoritative for** — a cache of interpretation with pointers back to source,
or "wiki as source of truth" as its own spec says?

**Already measured.** The reconciliation of the spec against the code is one table: **5 EXISTS /
18 PARTIAL / 15 ABSENT of 38 capabilities**, zero `/api` routes, no clustering or page generation,
`PageKind.topic` exists but `source` and `media` do not. The adopt arm's `AGENTS.md` §1.9
precondition (the vendor's outbound behaviour) is testable but untested.

**What the branches cost.** Harness-first is what the last three weeks have built. Per-spec means
an eight-state pipeline whose complexity is unproven against the existing `mine_queue`. Adopt means
bringing in a dependency whose data flow across `AGENTS.md` §1.9 is unverified.

**Still unknown:** whether the spec's pipeline earns its complexity; OpenWiki's outbound behaviour.

---

## 3. Gate 4's single call — a diagnostic-only `lstat`, or not

**The question** (`docs/20260923-2330-gate-4-telling-absence-from-corruption-from-change.md` §5):
distinguishing a *dangling symlink* from *nothing was ever there* needs `lstat` of the unresolved
entry, which the read-only mount refuses by design. Two ways: a **diagnostic-only command**
(`rlm corpus diagnose`) permitted to `lstat` for classification only, never on a read path and
never following the link; or **leave it**, accepting `absent` as the most the harness will ever say.

**Already measured.** Of the recorded unreadable items: **6 cited documents, all `stat-refused`**,
none containing `!`, and the classification store independently silent on the same six. The rest of
Gate 4's design — a failure vocabulary, a content baseline at first read, a state per path,
escalation on clustering — **needs no owner call** and is not built yet.

**Why it matters:** for the LTM, a page whose source has gone is the *expected* case on a backup,
not an error. Without the vocabulary, an LTM will regenerate, delete, or keep serving such pages.

**Size:** small. It unblocks the six documents and the absence handling, and it narrows — rather
than removes — the containment boundary.

---

## 4. Gate 3 — whether to spend the description budget

**The question** (`docs/20260924-0100-gate-3-the-description-spend-decision.md`): describe nothing
further, finish the cited set (~1–2.5 h), or describe the whole 143-document value set (12–70 h).

**What changed since that brief was written: its recommended option 2 has now been run.**
Measured 2026-09-26 (`docs/20260927-0000-what-the-descriptions-actually-reach.md`): over the six
questions of the drafted set, **descriptions reach the model for 2 of 6 and cannot for 4 of 6**; of
the two, one is served by ordinary ranking with the pass disabled (its description ranks #1), and
the only pass-only case produced a **byte-identical** answer with and without the descriptions. The
mechanism works; nothing measured has changed an answer.

**So the decision is now narrower than the brief:** stop (0 h), finish the cited set (~1–2.5 h) for
completeness, or spend the full 12–70 h — with the honest reading that no measurement yet shows a
description changing an answer.

---

## 5. Two small correctness calls on the summariser

From `docs/20260926-1800-a-cap-bound-document-does-not-finish-inside-its-timeout.md`:

- **The cap or the timeout.** A 32 KiB input is ~22–31 minutes of prompt processing against a
  ~34-minute derived timeout, so the largest documents sit on the edge; one was still running after
  2 061 s. Lower the cap to 16 KiB (comfortably inside the bound; the largest documents described
  from their opening) or raise the timeout (an hour per large document, retries included).
- **A cache-correctness hole.** The derivation key does not include the input cap
  (`summary<=v1,400tok`), so changing the cap would leave descriptions of *truncated* documents
  looking valid and never re-described. Cheap to fix (put the cap in the params string), but it
  invalidates the cached descriptions — hours.

---

## 6. Two privacy calls

- **Trajectory naming.** Measured: **15 of the 64** trajectories under `~/rlm-derived` have a stem
  matching a token on the private list, 9 of them in probe output directories, so a `ls`/`xargs`
  listing prints sensitive names. Printing is fixed (position labels, `--only-index`, no file name
  in a summary); the *names* remain. Renaming to `0001.jsonl` with `questions.txt` as the map
  removes the class at the source and changes the artefact layout you browse.
- **The published history.** `check_privacy.py --history` reports **6 occurrences of 3 tokens
  across 4 commits** (2026-09-14 … 2026-09-19), all pushed, all predating the token list. Whether
  they are genuine identifiers or ordinary words is **not established** from here; if genuine, the
  remedy is a history rewrite and force-push, which changes every hash from 2026-09-14 forward.

---

## 7. Still open from the ledger, deferred earlier

- **OD2 — embeddings (`sqlite-vec`)** for retrieval quality. Measured: an off-question query was
  served `none` 40 of 40; an on-question query `strong=36, weak=44`. The order that fell out was
  **fix query construction at the prompt first (free), then re-measure** — that first step has not
  been done.
- **OD6 — the root-tier model.** Battery evidence exists (4B 90.0/86.7 with P6 10/15; two 100/100
  alternatives, one in 818 s); the deciding measurement — the graded question per model, ≥3 runs —
  has not been run.
- **OD7 — task-shaped profiles and a corpus-task probe suite.** Recorded, not scheduled; needs
  `--config` plumbing before a personal profile is data rather than a source edit.
- **OD5 — `max_instances=4` on a 15 GiB box** (OOM risk) and **OD3 — K5 remote vault/media/UI**
  (later phases). **OD1 — the tier-2 load corpus** revives unchanged.
- **The X-scraper as a pattern source** — a reading, not a gate.

---

## If you want one answer

**Take Gate 2, and let me close its measurement gap first.** It is the only gate whose answer
multiplies every other cost, and two cheap measurements turn it from a guess into arithmetic. Gate 1
is the next one that needs *you* rather than time; Gate 4's call is ten minutes of your attention
and unblocks the LTM's hardest correctness case; Gate 3 is now a spend decision with its
recommended experiment already done.
