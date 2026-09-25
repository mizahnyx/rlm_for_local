# Descriptions are indexed but invisible to search

**Date:** 2026-09-25. **Status:** the Gate 3 experiment's prerequisite, measured — and it **fails**.
Measured on lunacode against the live index; aggregates only.

## What was asked

Before spending hours asking whether descriptions *help* an answer, establish whether they can
influence one at all: are the six cached descriptions for the cited set indexed, and does a normal
search return them?

## What was found

| probe | result |
|---|---|
| descriptions read from the cache | 6 (12 files: the text and a metadata sidecar) |
| found with `derived_only=True`, k=8 | **6 of 6** |
| found with the ordinary filter, k=64 | **0 of 6** |
| origins among 48 hits from six ordinary searches | `file` 48, `cache` **0** |
| last 2 000 rows of `text_chunks` | 2 000 all `origin=cache`, all `derived` |

So the descriptions are **in the index and searchable in principle** — a search restricted to derived
text returns every one of them. They are simply **never returned by an ordinary search**: at k=8 the
top hits are all file chunks, and clearing the ranking to 64 does not change that.

This is a *ranking* result, not an indexing bug. A one-chunk description competes against file chunks
of the same corpus on a term that, by construction, occurs in it — and BM25 with a bounded `k` prefers
the files. The description exists, is findable by a filter nobody uses, and is invisible to every
search the harness actually issues.

## Why this matters more than the experiment it was meant to precede

The Gate 3 decision is whether to spend **12–70 hours** describing the value set. The experiment was
supposed to supply the missing usefulness signal. What this run establishes instead is prior to that:
**as things stand, a description cannot influence an answer at all**, because nothing retrieves it. The
`corpus_search` helper the model uses (`handle_search`) accepts `derived_only` — but the worker-side
helper does not pass it, so the model cannot ask for a pass over derived text even if it wanted to.

Two consequences, both uncomfortable and both worth stating plainly:

1. **Spending on descriptions now would buy nothing visible.** The artefacts would be written, cached,
   labelled and never surfaced. That is the same failure this project has already named twice in other
   clothes — a description nobody can find, and a check that cannot see the truth.
2. **The missing decision is a ranking decision, which is an owner call** (`AGENTS.md` §1.6, scoring
   semantics). Making derived text *surface* means changing what a search returns: merging derived
   chunks into the ranking, giving them their own retrieval pass, or leaving them out of retrieval
   entirely and treating descriptions as something for a reader rather than for the model.

## What was not done

Nothing was changed. No ranking weight, no filter default, no helper parameter. The three options above
are stated for the owner to choose between, and the experiment's second half — re-running a question
set with descriptions present — is **not runnable** until one of them is chosen, because "present in
the index" and "present in the answer" are not the same thing and only the first is true today.

## Unverified

- Whether any *specific* description would have changed any *specific* answer: unanswerable while they
  are unretrievable.
- Whether the ranking would surface them at all if merged: the two probes bracket the question (0 at
  k=64 unfiltered, 6 of 6 restricted) but nothing has been tried in between.
- Whether a term chosen by a human rather than mechanically would behave differently — the probe picks
  the longest distinctive word, and a rarer term might rank a single chunk higher.
