# Diagnosis: Tier-1 100K Load-Gate Run (2026-07-25)

**Date:** 2026-07-25 (19:51)
**Input diagnosed:** `rlm_for_local/load-gate-tier1.txt` (run of `scripts/run_load_gate_100k.py`, 100,000-page synthetic corpus at `C:\Users\Mizahnyx\load-corpus-synth-100k`)
**Method:** result analysis + cProfile on the machine (3,000-page samples of the existing corpus)

---

## 1. The measured results

| Metric | Target | Measured | Verdict |
|---|---|---|---|
| Full reindex, 100K pages | < 7,200 s | **12,680 s (3.5 h)** | **FAIL** |
| FTS search p95 | < 300 ms | **158.1 ms** | PASS |
| `git status` (fsmonitor + warm) | < 2,000 ms | **193 ms** | PASS |
| Helper listing — index (`list_paths`) | informational | **370.9 ms** (9,954 paths) | — |
| Helper listing — full vault walk | informational | **1,633.7 s (27 min)** | — |

Generation: 267 s. The corpus is reusable — a rerun skips generation.

## 2. Root cause of the rebuild FAIL — two layered findings

### Finding 1 (environmental, dominant): file-open cost exploded during the measured run

Profile of a 3,000-page sample of the same corpus, same machine:

| Cost per page | 3K sample | The 100K gate run |
|---|---|---|
| File open+read | ~6.7 ms | ~110–120 ms (inferred) |
| SQLite ops (11 stmts/page) | ~2.2 ms | similar |
| YAML frontmatter parse | ~1.3–1.7 ms | similar |
| Everything else (pydantic, list mgmt) | ~1.7 ms | similar |
| **Total** | **~11.9 ms/page** | **~127 ms/page** |

The profile top line is unambiguous: `_io.open` = **56% of rebuild time** (20.1 s of 35.6 s; 6.7 ms per open — already 20–60× the normal ~0.1–0.3 ms). At the sample's own rate, 100K pages rebuilds in **~20 minutes, comfortably inside the gate**. The gate run's 127 ms/page means opens were ~18× slower still — consistent with **Windows Defender real-time scanning a backlog of 100,000 freshly-written files** (the rebuild started immediately after generation) plus possibly Search Indexing. This is an environment inflation, not a code-path defect: the code path is ~12 ms/page.

**Action E1 (do this first):** add the corpus directory (`C:\Users\Mizahnyx\load-corpus-synth-100k`) — and later the production vault directory — to Windows Defender's exclusion list (Windows Security → Virus & threat protection → Manage settings → Exclusions). Then rerun. Expected outcome: rebuild ≈ 20–40 min → PASS.

### Finding 2 (structural, proven): the full vault walk is catastrophic at scale and still has callers on the per-completion path

The gate measured it directly: `vault.list(kind="helper")` = **1,633.7 s** at 100K pages (it parses all 100K pages to filter 9,954 helpers) vs `Index.list_paths()` = **370.9 ms** — a **4,400× difference**. The bridge hot path was fixed in the last remediation (`repl_bridge.get_helper_definitions` uses the index), but `prompts.py:178` (`load_system_prompt_from_vault`, called **once per kernel-enabled `completion()`**) still walks. At 100K pages that is **~27 minutes added to every completion** — the G4b residual is not cosmetic; it is a hard blocker for kernel use at scale.

**Action C1 (code, small):** give `load_system_prompt_from_vault` the same index-backed fast path (pass the bridge's `index_path`/`Index` handle through, or convert it to use `Index.list_paths` when the index exists). Convert or delete the uncalled `get_helper_summaries` while there.

### Finding 3 (minor headroom, cheap wins for the rebuild path)

- **C2 — use libyaml:** `yaml.__with_libyaml__ == True` on this machine. Switching `yaml.safe_load` → `yaml.load(..., Loader=yaml.CSafeLoader)` in `schema.parse_page` is a ~10–30× YAML speedup (~1.5 ms → ~0.1 ms/page). One-line change with a fallback (`getattr(yaml, "CSafeLoader", yaml.SafeLoader)`).
- **C3 — batch SQLite writes:** `Index.build` issues 11 statements/page incl. a per-page `DELETE FROM tags` that is useless on a fresh build (tables were just emptied). Fewer statements + `executemany` shaves ~2 ms/page.
- **C4 — parallelize page reads:** opens are I/O-bound; a 4–8 worker thread pool for read+parse in `vault.list()` recovers most of the remaining open cost even with AV active (and is the correct general shape for the 500K-page design envelope).

With C1–C4 the rebuild should land at ~5–8 ms/page (≈ 10–15 min at 100K) even on a busy machine, giving real margin under the 2 h target.

## 3. What passes, and with what margin

- **Search p95 = 158 ms** — PASS at ~53% of budget. At the 500K envelope this will need re-checking, but the FTS path is healthy.
- **`git status` = 193 ms** — PASS at ~10% of budget; the fsmonitor/untracked-cache recipe (spec §5.4) is confirmed working on Windows.

## 4. Rerun protocol (for the user)

1. Add Defender exclusions for the corpus dir (and the future vault dir). *(E1)*
2. Optionally ask the implementation agent to land C1–C4 first (recommended; they're small), or rerun as-is to validate the environmental diagnosis.
3. Rerun: `.venv/Scripts/python.exe scripts/run_load_gate_100k.py 2>&1 | tee load-gate-tier1-rerun.txt` — generation is skipped (corpus exists); the run should take ~30–45 min.
4. Paste the `RESULTS` block back; if rebuild passes, Tier 1 is done and only Tier 2 (organic corpus) remains before the load gate is formally closed.

## 5. Honest ledger

- The gate as measured **FAILED on rebuild** — that stands until a rerun under E1 (± C1–C4) passes.
- The failure is **not** evidence of a bad indexing design: the code path profiles at ~12 ms/page, and the failure mode (AV-inflated file opens) is environmental and correctable, now with numbers to prove it.
- The run's most valuable output is arguably not the gate result but the **direct measurement of the G4b hazard**: 27 minutes vs 0.37 seconds. That number should be quoted whenever someone suggests "just walk the vault, it's simpler."

## 6. Updated open-items ledger for the rlm-kernel project

| Item | State |
|---|---|
| R1–R6 remediations, F1/F2/F4 follow-ups | ✅ closed |
| G3 — 100K load gate | ⏳ FAIL on rebuild (environmental; rerun protocol in §4); search + git PASS |
| G4b — `prompts.py` full-walk per completion | ❗ proven critical (27 min/completion at 100K); fix = C1 |
| N1 — dangling doc references in repo | open (trivial) |
| Tier 2 — organic corpus validation | awaiting corpus prep (runbook `docs/20260725-0953-load-gate-100k-runbook.md` §4) |
| K4-real (GEPA) | blocked until gate PASS + C1 |
