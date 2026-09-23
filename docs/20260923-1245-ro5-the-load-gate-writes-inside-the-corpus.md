# RO5, first finding: the Tier-2 load gate writes inside the corpus

**2026-09-23.** RO5 asks to revive the Tier-2 load gate against the real corpus — "reindex time,
search p95, and the git measurement that this host could not produce" — and to fix VD4 with that
real number. The safety check that came before the run found two things that have to be settled
first, one of them a defect in the gate itself.

## 1. The gate builds its vault *at* the corpus and writes into it

```python
@pytest.fixture(scope="class")
def vault(self, corpus_path):
    return LocalVault(corpus_path, init_git=False)      # the vault is the corpus path

def test_build_index(self, vault, corpus_path):
    idx_path = corpus_path / ".index" / "meta.sqlite"   # written inside the corpus
    idx = rebuild_index(vault, idx_path)
```

`tests/load/test_load.py`, Tier 2. Three consequences, in order of severity:

- **It violates layer 3** (`AGENTS.md` §1.8): derived state must be asserted *outside* the
  corpus root, and the project's own rule says a harness that would write inside it should fail
  hard rather than proceed. This fixture would write `<corpus>/.index/meta.sqlite`.
- **It would fail against the real corpus anyway** — `/srv/corpus` is a read-only mount, so the
  write is refused. On a *writable* copy it would silently succeed, which is the worse outcome
  because nothing would report it.
- It has never run against the real corpus: the Tier 2 class skips whenever
  `RLM_KERNEL_LOAD_CORPUS` is unset, which is every run in this repository's history, and the
  fast suite deselects `load` anyway. So RO5's "revive" is accurate — the gate exists and has
  never been pointed at the thing it is named for.

**The fix is small and testable**: take the index path from the derived root, and assert the
index lives outside the corpus (fail hard, as layer 3 prescribes) before anything is built.

## 2. The gate measures a *vault*, not a file tree — and the real corpus is a file tree

Tier 2 indexes `LocalVault` pages: markdown documents with frontmatter, keyed and versioned by
the kernel. Spec §12's targets are vault-shaped — **100K pages**, full reindex < 2 h, search p95
< 300 ms, `git status` < 2 s. The real corpus is 4 972 609 entries of arbitrary files at
`/srv/corpus`, which is not a vault and has no page semantics.

So "run it against the real corpus" needs a decision the roadmap does not make for us:

- **(a) Index a derived page set.** Build a vault beside the corpus from its text core (or from
  the 29M-chunk text index that already exists) and run the gate against that. Faithful to the
  spec's units, and the derived state is outside the corpus by construction.
- **(b) Treat the corpus's text files as pages.** Then the gate measures reindex time and search
  p95 over ~2.88M files rather than 100K pages — a different number, on the object the owner
  actually cares about, and not comparable to VD4's spec targets without saying so.

Both are honest; they answer different questions. This is an owner call, and it is the gate this
round stops at.

## 3. The recorded stall is the git measurement

`scripts/run_load_gate_index_phases.py`'s own docstring records that
`scripts/run_load_gate_100k.py` **stalls in its phase 5** — `git add -A` over 100K files does
not finish on this host. That is precisely "the git measurement that this host could not
produce", so VD4's number may have to be a *measured stall* (with the phase and the elapsed time
where it stopped) rather than a passing threshold. Recording a stall as a result is the honest
outcome; inventing a number is not.

## What this round establishes

- The gate has never run against the real corpus, and cannot as written.
- It would write inside the corpus, which layer 3 forbids and the mount refuses.
- Its units are a vault's pages, not a file tree's — so the first decision is *what object the
  gate measures*, not how to run it.

No code has been changed yet: the fix is defined and small, but which object (a) or (b) it
should measure changes what "reindex time" and "search p95" mean, and that is the owner's.
