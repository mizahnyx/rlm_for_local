# The search-latency decision: what it is, what bears on it, what is unknown

**2026-09-23.** Written because the owner asked what to read in order to decide, and the honest
answer was "nine records, in four different trains of thought". This is the brief; the records
below are the detail. Nothing here is new measurement — every number is a citation.

## The decision

**Whether to change what a search returns, so that it is affordable on this corpus.** On this
host, search p95 over the queries the model actually issues is **≥ 60 s**, against a spec target
of 300 ms — and 60 s is the *soft cell limit*, so a single search can consume a cell's entire
budget (`docs/20260923-1400-ro5-the-load-gate-measured.md`).

It is the owner's call because every candidate changes results, and results are the product.

## What is measured, and what it says

| fact | number | where |
|---|---|---|
| search p95, real queries, watchdog-capped at 60 s | **≥ 60 s** (7 of 15 censored; median 51.8 s) | RO5 record |
| what a search costs, warm | 0.5 s – 89.6 s, driven by **match count** | `-1050-what-a-search-actually-costs` |
| the informational vendored count | was **202 s** on a common query; now capped | `-1050`, fix in `fda1fdd` |
| expression shape | AND-all **matches nothing**; **OR always aborts**; AND-of-two serves 8/question, 25 % citable | `-1215-does-the-expression-limit-match-quality` |
| what the model asks | **one or two words**, never the question | `-1050`, `-1215` |
| bands by provenance | markup 1.14, prose 1.05, code 0.85, documentation 0.50 | `-0945-the-density-hunch-measured` |
| git at 100K files | status **0.3 s**, add 9.9 s — spec met | RO5 record |

Three consequences worth carrying into the decision:

1. **The cost is the match set, not the query text.** `ORDER BY bm25(text_fts) LIMIT 8` must
   score *every* matching row, so a common word costs a minute before the model sees anything.
2. **Relaxing the expression is worse, not cheaper.** OR aborts every time it was tried; more
   matches means more scoring.
3. **"Prefer prose" would not fix it.** Band quality barely separates prose from markup, and
   documentation — prose-shaped — is the *worst* class. Rank order is not the lever.

## The options, and what each one actually changes

| option | what changes | measured risk |
|---|---|---|
| **1. Affordable terms** — advise or require ≥ 2 content words, flag or refuse very common ones | what the model may ask | needs a term-frequency signal; the model's natural one-word queries get refused |
| **2. Rare-term prefilter** — find the rarest term's postings first, then filter, so bm25 scores a smaller set | what the ranking sees; results order | an engine change; FTS5 will not do it without a second index or an explicit rare term in the expression |
| **3. Cheaper ranking** — cheaper order than bm25, or cap the scored set | what is returned | quality loss unmeasured |
| **4. Do nothing** | nothing | every question costs 10–65 minutes, and half its searches exceed the soft cell limit |

## What is unknown — and this is the honest gap

- **What each option does to band quality.** There is no measurement of a prefilter's effect;
  only the shape comparison above (AND vs OR) exists.
- **Whether a cheap term-frequency signal exists.** A lead, *not* a verified fact: SQLite's
  FTS5 has an `fts5vocab` virtual table that exposes per-term document counts without counting
  postings. If it works on this index it makes option 1 and 2 cheap; nobody has tried it here.
- **Whether the model can be steered by prompt** toward specific-enough, affordable terms. The
  question drafter was steered once already (prose preference), and the effect was measured —
  no such experiment exists for search terms.

## The smallest experiment that would inform the decision

Model-free, minutes, on the index we have: for each of the fifteen recorded queries, get the
rarest term (from `fts5vocab` if that works, else from bounded counts), re-run the search with
`rare AND one-other` and compare **bands served and latency** against the original. If rare-term
expressions serve better bands in less time, options 1 and 2 both have evidence under them; if
not, the ranking itself is the problem and option 3 is the only one left standing.

## The reading path, if you want the detail

In order, and each answers one question:

1. `docs/20260923-1400-ro5-the-load-gate-measured.md` — **how slow is it, and in what units?**
2. `docs/20260923-1050-what-a-search-actually-costs.md` — **what is the cost made of?**
3. `docs/20260923-1215-does-the-expression-limit-match-quality.md` — **can the query be shaped to help?**
4. `docs/20260923-0945-the-density-hunch-measured.md` — **would ranking by provenance help?** (your hunch, measured)
5. `docs/20260915-2305-corpus-search-cannot-count-its-own-index.md` — **why counts are banned from this path** (the origin of the rule the vendored count was breaking)
6. `docs/20260922-2025-the-search-counts-every-vendored-match.md` — the audit that named the wrong half, kept because the correction in (2) cites it.

Beyond the decision, the roadmap's own order continues at **RO6** (selective enrichment) and
**RO7** (wiki synthesis) in `docs/20260912-1155-roadmap.md`; both inherit this latency as their
real budget.
