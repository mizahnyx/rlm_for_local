# Findings digest — talking to a 1 TB legacy corpus with a 4B local model

**Created:** 2026-09-17 00:09
**Status:** point-in-time snapshot of everything established so far. Supersedes no
record; each claim below links to the dated document that measured it. Written to be
*acted on*, not read in full.

---

## 1. What works, with the numbers

| Capability | Evidence |
|---|---|
| Path index of the whole corpus | 4 972 609 entries / 4 281 585 files / 1 084 767 249 184 bytes, built in 95 min (`20260913-0120`) |
| Content classification | 4 281 585 files by head-hash + kind, 11.5 h, 41.7 GiB read (`20260913-2110`) |
| **Text index complete** | **2 881 592 of 2 882 822 sources, 29 015 791 chunks, 41.6 GB**, `[coverage: complete]` |
| Coverage quoted cheaply | published snapshot read in ~1 s vs `COUNT(*)` at 972 s (`20260915-2305`) |
| Read-only held | digest `8da70683…` identical across index build, 3 mining windows, 10 live runs (`20260916-1130`) |
| Citations that are evidence | a cited address must be one a helper *served*; fabrications refused (`20260916-1540`) |
| Terminal answer carries provenance | `FORCED_FINALIZATION_CORPUS_PROMPT`; one run cited a printed address (`20260916-1005`) |
| Hits say how much of the question they cover | `covers 1/4 of the question's words (weak)` (`20260916-1710`) |
| What each search served is recorded | `corpus_search_quality` events (`20260916-2245`) |

## 2. What was measured and **refused** (do not re-try these)

1. **Prompt-only citation requirement** → 0 cited in 3 runs (`20260915-0655`).
2. **Refusing uncited answers** → the guard never fires on the question that matters,
   because the model never submits (`20260915-1023`).
3. **A last-turn permission nudge** → fires, changes nothing: 8/8 turns, forced (`20260916-1405`).
4. **A smaller corpus turn budget** → 2.5–4× faster and it *invents citations*
   (`20260916-1445`). Default stays at 8.
5. **A relevance label alone** → the model was served **`weak=8`** and explored to the
   end anyway (`20260916-2200`).

**The pattern**: every *informational* lever has failed. The 4B model does not
conclude on an unanswerable question; it searches harder. Next lever is
**structural** (below).

## 3. Open defects, ordered by what they block

| # | Defect | State / next action |
|---|---|---|
| D1 | **Ordering- or load-dependent suite failure.** `pytest tests/` → 2 failures in `tests/rlm_kernel/test_corpus.py::TestSearchCoverageIsPublishedNotCounted` (an earlier run of the same tree showed 7 failures, 5 of which were worker-subprocess tests failing while the router was being reconfigured — so *load* is one confirmed factor). **Every narrower invocation is green**: the 4 tests alone (4 passed), `tests/rlm_kernel/` alone (547 passed / 0 failed), and the pairwise mix of `tests/test_corpus_repl.py` + the failing class (54 passed). So it needs a *larger* combination and/or a loaded host — it is not a simple module interaction, and it was **not** reproduced in a quiet, narrow run. | **Next**: re-run `pytest tests/` on the now-quiet host. Green ⇒ reclassify as load-correlated flake (and the digest's D1 changes meaning: not a leak but a host effect, still worth a guard); red ⇒ bisect upward from the pairwise pass, most likely by splitting `tests/` into halves. Nothing lands on top of it, and the timeout fix stays uncommitted until this is settled. |
| D2 | **Model read timeout kills a run** (exit 2, no `end` event, 30 min lost). | **Fix written, uncommitted**: survive a failure *after* the model has answered, propagate a failure *before* it ever did. 2 tests + 1 mutation red. Lands after D1. |
| D3 | **Non-convergence**: 8/8 turns, forced finalization, on every unanswerable question (5 attempts). | **Structural lever**: on the final turn, refuse corpus helpers at the parent so concluding is the only option. Then, if that fails, accept forced finalization as the designed terminal path. |
| D4 | **Escape arm never exercised**: no run has ever *submitted* an absence answer voluntarily. | Fix the arm's literal-`coverage` test to accept an absence claim when `corpus_coverage()` was actually called (the parent knows). |
| D5 | **Dynamic corpus refresh gaps** (found via the scraper's `library`): a *modified* file keeps stale text (`task_index_text` skips a source it holds); a *deleted* file lingers in the index; `build` is all-or-nothing. | Append-only libraries work today; edited content needs replace-on-change + deletion GC. |
| D6 | RO2's last obligation: hash a **sample of file contents** (the digest covers path/kind/size/mtime, not bytes). | Operator call on sample size; per-file hashes stay on the laptop. |
| D7 | Media: `vlm_describe`, `ocr_page`, `asr_transcribe` are queued names with no handler (517 documents await OCR). | The scraper's resize-first, hash-cached Qwen3-VL method is the plan. |
| D8 | `mine status` still counts (slow, operator path only); the search path still takes a write lock (`ensure()`). | WAL mode + read-only search connection are the recorded candidates. |

## 4. Invariants that cost blood — keep them

- **A check that cannot see the truth says "unknown"**, never zero (mount `ro=?`; coverage
  snapshot; match quality on a stopword-only question).
- **A probe must not change what it measures** (`os.kill(pid,0)` killed this harness once).
- **The search path never counts a big table.**
- **A citation must be an address a helper served.**
- **`len(hits)` is the contract**: no footers in a list of hits.
- **Never PowerShell text substitution on source files** (mojibake + BOM + CRLF; caught by
  `git diff`, repaired by revert).
- **`tr -d "\r"` in a PowerShell ssh one-liner deletes every letter `r`** in the file.
- **CPython validates a `.pyc` by size and second-truncated mtime** — a mutation can be
  invisible; each mutation run now gets its own `PYTHONPYCACHEPREFIX`.
- **One corpus per run**; addresses are not namespaced across corpora.
- **Derived state never inside the corpus** (`assert_derived_outside_corpus`, hard fail).

## 5. Decisions the owner may want to make

1. **Absence semantics**: is "cited an adjacent passage" acceptable when the best match
   is `weak`, or must the answer say "not found"? (The guard can enforce either.)
2. **The structural final turn** (D3) — approve refusing corpus helpers on the last turn.
3. **`library` corpus**: confirm the path and whether to set up its read-only bind mount;
   I will index, classify, run its first window, and leave a refresh script.
4. **Model profile** (OD6/OD7): the battery says `Nanbeige4.2-3B-Heretic` (100/100) and
   `MiniCPM5-2B` (100/100 in 818 s) beat `Qwen3.5-4B-Abliterated` (90/86.7) on protocol —
   but protocol does not predict corpus behaviour, which is what today's failures are
   about. A corpus-task probe suite is the recorded way to decide.
6. **RO7 scope**: text-only wiki first (media as a later batch)?

## 6. Honest limits of everything above

- **n = 1 or 2** in most live measurements; the model is unstable across router cache
  states (recorded). Treat every rate here as a hypothesis with a measurement, not a rate.
- **Match-quality thresholds are first approximations** (strong at ≥60% of the question's
  content words) — stated as constants so they can be calibrated.
- **The digests cover metadata, not bytes** (D6).
- **All live numbers come from one 15 GiB laptop** with a router that had 4 model
  instances resident; the owner has since unloaded them, which should reduce timeouts
  (D2) and would be visible as fewer `model_error` events.
- **Nothing here was verified by reading the corpus's contents** — aggregates travel,
  identifiers do not (`AGENTS.md` §1.9).
