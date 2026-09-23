# RO5: the load gate measured on the corpus we have

**2026-09-23.** RO5 asked to revive the Tier-2 load gate against the real corpus and produce
reindex time, search p95, and the git measurement this host could not previously produce. Two of
the three are measured below; the third is not re-run and is reported from what is already
recorded, labelled as such.

**The object, and this is revertible.** The gate's spec §12 targets are vault-shaped — 100K
*pages*, reindex < 2 h, search p95 < 300 ms, `git status` < 2 s — while `/srv/corpus` is
4 972 609 arbitrary entries with no page semantics. I took option **(b)**: measure the corpus we
actually have, and say so. Reverting to (a) — a derived page set beside the corpus — is a re-run
against a different object, not a migration, because the choice decides what the numbers describe
and nothing about the code.

## The git measurement — the number VD4 wanted

A scratch tree outside the corpus, growing file counts, each phase capped at 120 s:

| files | `git add -A` | `git status --porcelain` |
|---|---|---|
| 1 000 | 0.3 s | 0.0 s |
| 5 000 | 0.6 s | 0.1 s |
| 10 000 | 0.9 s | 0.0 s |
| 25 000 | 1.9 s | 0.1 s |
| 50 000 | 4.2 s | 0.1 s |
| **100 000** | **9.9 s** | **0.3 s** |

- **The spec target is met with room to spare**: `< 2 s` for `git status` at 100K pages, and this
  host does **0.3 s at 100 000 files**.
- **And the recorded stall is not about file count.** `scripts/run_load_gate_index_phases.py`
  records that `run_load_gate_100k.py` "stalls in its phase 5: `git add -A` over 100K files"; over
  100K plain files here it takes **9.9 s**. So the stall's cause is something else about that
  tree — its content (a large SQLite index inside the repository, large or binary pages) or where
  it lives. **That is a candidate, not a conclusion**: this measurement rules out the file count
  and does not identify what replaced it.

## The search measurement — and it is the finding

Latency of the **queries live runs actually issued** (read from the `corpus_served` events),
against the real 38 GiB corpus index, each statement stopped by a watchdog calling
`sqlite3.interrupt()` after 60 s:

```
searches=15  censored=7 (capped at 60s)  median=51.76s  p95=60.00s
```

- **Seven of fifteen queries could not finish inside sixty seconds**, so **p95 ≥ 60 s and the
  true value is unknown** — the number is a *lower bound*, and the design prints it as one
  rather than reporting a fast-looking p95 from the eight that happened to complete.
- Against the spec's `< 300 ms`, that is **two hundred times over** on this corpus.
- **And 60 s is not an arbitrary cap: it is the *soft* cell limit.** A single search can consume a
  cell's entire soft budget, which is what the `cell_timeout` events on `corpus_search` have been
  saying since the first live runs. The load gate has now measured the reason.

This is the same root as the costs measured the day before: `ORDER BY bm25 LIMIT 8` must score
every matching row, and the queries the model issues are common enough to match hundreds of
thousands of chunks. The load gate did not find a new problem; it priced the one we knew about,
on the object the owner cares about, in the units of a spec threshold.

## Reindex time — not re-measured, and labelled

Rebuilding the 29M-chunk index takes hours, so it was not re-run for this gate. What is already
recorded, and is *not* from this run: `rlm corpus index` wrote 4 972 609 paths in **95 minutes**,
and Stage 1 classification read 4 281 585 file heads in **11.5 hours**. The spec's `< 2 h` was
written for 100K vault pages; on this corpus the *path* index alone is 95 minutes for fifty times
as many entries, and the text index is a different order of work. Any comparison to the spec
threshold would be on the wrong object, so none is made.

## The defect this required fixing

Tier 2 built `LocalVault(corpus_path)` and wrote `<corpus>/.index/meta.sqlite` — inside the corpus
root, which `AGENTS.md` §1.8 layer 3 forbids and the read-only mount would refuse; on a writable
copy it would have succeeded silently. `derived_root()` now takes the root from
`RLM_KERNEL_LOAD_DERIVED` and **asserts it is outside the corpus before returning**, with a fast
guard test (inside → `ReadOnlyViolation`) and a mutation entry, red as required. The corpus is a
read-only *source*; the gate writes elsewhere or does not run.

## What this leaves

`docs/20260923-1245-ro5-the-load-gate-writes-inside-the-corpus.md` records the finding;
`docs/20260923-1050-what-a-search-actually-costs.md` and
`docs/20260923-1215-does-the-expression-limit-match-quality.md` record why search costs what it
does. RO5's decision gate is the same one those left: **search latency on the real corpus is the
constraint**, and reducing it means changing what a search returns (affordable terms, a rare-term
prefilter, or a cheaper ranking) — an owner's call, now with a spec-shaped number under it.
