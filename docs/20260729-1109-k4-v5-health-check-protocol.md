# Health-Check Protocol for the K4 v5 Optimization Run — READ-ONLY

**Date:** 2026-07-29 (11:09)
**Audience:** the implementation agent
**Prepared by:** project owner via Kimi Code CLI

---

## 0. The one rule that matters

**You are READ-ONLY for this task.** Do **not** modify, patch, fix, "improve", kill, restart, stop, or clean up **anything**: no code edits, no config edits, no file edits, no process control (`TaskStop`, `kill`, `taskkill`), no `git` mutations, no package installs, no vault writes. If you find a problem, **report it — do not repair it.** Any corrective action is the owner's call, executed separately.

## 1. What is running

A GEPA prompt-evolution run ("K4 v5") optimizing the harness's `contract/how-to-work.md` page inside the `rlm_for_local` project.

- **Process:** Python (`run_optimization`), started **2026-07-29 ~03:25 local**, background task id `bash-r5wvb7qc`.
- **Budget:** 40 metric calls (task evaluations), `max_workers=2`, `tiny` profile, model `Qwen3.5-4B-Abliterated` via llama-server at `https://localhost:9010/v1`.
- **Expected duration:** ~4–5 h under current load (so completion plausibly ~07:30–08:30+, later if the box is busy).
- **Key paths:**
  - Repo: `~/Documents/Misc/rlm_for_local`
  - Live stdout log: `~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt`
  - GEPA checkpoints (authoritative progress): `~/Documents/Misc/rlm_for_local/logs/k4-gepa-checkpoints/`
  - Vault: `~/.local/share/rlm-kernel/vault` (page under optimization: `contract/how-to-work.md`)
  - REPL temp dirs: `~/AppData/Local/Temp/rlm_repl_*`

## 2. Probe battery (run all five; ~2 minutes)

```bash
# P1 — log tail + tripwires
tail -c 3000 ~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt
grep -c "UnicodeEncodeError" ~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt
grep -c "ModuleNotFoundError" ~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt
grep -c "did not propose a new candidate" ~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt

# P2 — checkpoint freshness (authoritative; v4 leftovers may be present — only mtimes from TODAY's run count)
ls -lat ~/Documents/Misc/rlm_for_local/logs/k4-gepa-checkpoints/

# P3 — candidate-evaluation activity (page mtime advances on every candidate eval)
stat -c '%y' ~/.local/share/rlm-kernel/vault/contract/how-to-work.md

# P4 — completions spawning (fresh REPL workers)
ls -lat ~/AppData/Local/Temp/ | grep rlm_repl | head -5

# P5 — completion/promotion evidence (only meaningful if the run has ended)
grep -A8 '"status"' ~/Documents/Misc/rlm_for_local/k4-acceptance-run.txt | tail -10
grep -l "optimized_by" ~/.local/share/rlm-kernel/vault/contract/*.md 2>/dev/null
git -C ~/.local/share/rlm-kernel/vault log --oneline -4
ls ~/Documents/Misc/rlm_for_local/logs/k4-first-run/ 2>/dev/null
```

Do **not** use CPU/load probes — the machine's other duties make them meaningless.

## 3. Interpretation (signatures)

| Signature | Meaning | Verdict |
|---|---|---|
| tqdm `NN/40` advancing between two checks 15+ min apart | rollouts progressing | **healthy** |
| `Iteration N: Proposed new text…` lines | reflection + proposals working | **healthy** |
| Checkpoint files (candidates.json, gepa_state.bin) with today's, advancing mtimes | iteration checkpoints saving | **healthy** |
| `contract/how-to-work.md` mtime advanced since last check | candidate evaluations happening | **healthy** |
| Fresh `rlm_repl_*` dirs in the last ~20 min | completions executing | **healthy** |
| `UnicodeEncodeError` ≥ 1 in log | the v2/v4 killer — encoding crash | **crashed** |
| `ModuleNotFoundError` ≥ 1 | the v1 killer — missing dependency | **crashed** |
| `did not propose a new candidate` on 3+ consecutive iterations | circular loop (reflection dead) | **circular** |
| No checkpoint/page/REPL activity for 90+ min while task alive | stalled or overlong phase | **suspect — report** |
| `"status": "promoted" / "no_improvement" / "error" / "gate_error"` in log tail | run finished | **completed** |

## 4. What to report (exact format)

```
K4 v5 health check — <local timestamp>
- Task alive: yes/no
- Log tripwires: UnicodeEncodeError=N, ModuleNotFoundError=N, no-propose-loops=N
- tqdm/progress: <last visible rollout count, e.g. 12/40>
- Newest checkpoint mtime: <ts> | page mtime: <ts> | newest REPL dir: <ts>
- Verdict: HEALTHY / SUSPECT / CIRCULAR / CRASHED / COMPLETED
- Evidence (5 lines max): …
- Recommended owner action (if not HEALTHY): …
```

**Then stop.** Do not act on your own recommendation.

## 5. Historical failure modes (for recognizing what you see — do not fix)

- **v1 (circular, ~24 h):** `litellm` missing → every reflection failed → "did not propose a new candidate" forever. Signature: repeated no-propose lines, same seed score every iteration.
- **v2/v4 (crash):** GEPA logger printing/writing text containing `'\u2011'` to a cp1252 stream/file → `UnicodeEncodeError` at iteration 1–2. Signature: that exception in the log tail.
- **Stall-lookalike:** up to ~2 h with no candidate page writes during long reflection phases — **not** a stall if checkpoints and REPLs keep advancing. Don't cry wolf.
- **Budget semantics:** 40 "metric calls" = ~40 task evaluations (≈ 8 full-train candidates), checked only between iterations. Total wall time varies with machine load by 2–3×.

## 6. If the run has completed

Report the final `status` / `baseline_score` / `best_score` / `held_out_score` verbatim from the log tail, plus whether the vault shows promotion (`optimized_by` in `contract/how-to-work.md`, a `gepa-optimized` tag, a vault git commit, non-empty `quarantine/`). Attach the last ~15 log lines. **Do not** interpret, revert, or re-run anything.
