# K4 Acceptance Run — Complete Operator's Guide

**Date:** 2026-07-26 (for the overnight of your choosing)
**Audience:** the project owner, running the run personally
**Prerequisite state (already done as of this writing):** the two live-run defects found during guide preparation have been **fixed and suite-verified** — `optimize.py` now builds the real `GEPAConfig`/`ReflectionConfig` (D-R1) and threads a `KernelBridge` through the evaluator so candidates actually reach the model (D-R2). Suite: **182 passed, 0 failed**, including two new non-vacuous guard tests. Note: these fixes are in the working tree, **not yet committed** — commit them (or ask the implementation agent to) before the run so the vault's git and the code's git tell the same story.

---

## 1. What this run is (60-second orientation)

The K4 milestone's acceptance test: an **overnight GEPA optimization run** against one live text artifact of the harness — the `contract/how-to-work.md` page (the orchestrator instructions in the system prompt). GEPA (reflective prompt evolution) iteratively: injects a candidate text into the vault page → evaluates it by running real `rlm_local.completion()` calls on a small verifiable task suite → reflects on failures with the root-tier model → proposes improved text. A candidate is **promoted into the live page only if** it beats the baseline on the train split **and** doesn't regress on a held-out split **and** passes structural validation. Everything is logged; rollback is `git revert` in the vault.

Budget: **150 metric calls** (one metric call = one full harness completion of ~1–8 minutes on your hardware) → expect **4–10 hours** depending on model speed.

## 2. Prerequisites (15 minutes of checks, in order)

### 2.1 Commit the pending fixes
```bash
cd ~/Documents/Misc/rlm_for_local && git status --short
# If optimize.py / test_optimize.py show as modified: commit them first.
```

### 2.2 Model server up and answering
The harness reads model/endpoint from `config.py` profiles (default: `https://localhost:9010/v1`, model `LFM2.5-VL-1.6B`, both tiers). Bring your llama-server up, then verify:
```bash
curl -k https://localhost:9010/v1/models
```
You must see the configured model id in the list. (The harness talks to the server with `verify=False`, so self-signed certs are fine; if your server is plain `http://`, update `root_endpoint` in `config.py` or pass the correct URL in the snippet of §3.2.)

### 2.3 Vault seeded and indexed
The evaluator reads the prompt pages from a vault. Default location `~/.local/share/rlm-kernel/vault`:
```bash
cd ~/Documents/Misc/rlm_for_local
.venv/Scripts/python.exe -m rlm_kernel.cli init --vault ~/.local/share/rlm-kernel/vault
.venv/Scripts/python.exe -m rlm_kernel.cli index --rebuild --vault ~/.local/share/rlm-kernel/vault
.venv/Scripts/python.exe -m rlm_kernel.cli search "how to work" --vault ~/.local/share/rlm-kernel/vault
```
The search must return the `contract/how-to-work.md` page. If you already have a vault with your own edits: **do not reseed blindly** — `init` skips existing pages, so it's safe, but if your `how-to-work.md` is stale vs. the package text, that's fine; the run optimizes whatever is there.

### 2.4 Target: use `how-to-work` — NOT `nudges`
Only two of the five optimization targets are **live** tonight: `how-to-work` (its page body is assembled into the system prompt) and `helper-docs` (helper one-liners + code injected into the REPL). The `nudges`, `prologue`, and `fewshots` pages are currently **introspection-only** — the runtime reads those texts from `rlm_local.templates` constants, not from the vault (vault-template loading was deliberately deferred in R3-D10). Optimizing them would change nothing. `how-to-work` is also the highest-leverage text: it's the decomposition discipline itself.

## 3. Pre-flight (20–30 minutes — do not skip)

### 3.1 Baseline sanity (5 min)
You need the baseline in the **30–70% band** — 0% means no signal, 100% means saturation (the optimizer has nothing to fix). Check with:
```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from rlm_kernel.optimize import evaluate_candidate, _make_bridge
from rlm_kernel.vault import LocalVault
from pathlib import Path
v = LocalVault(Path.home()/'.local/share/rlm-kernel/vault', init_git=False)
b = _make_bridge(v)
r = evaluate_candidate('x', 'needle_search', None, profile='tiny', max_turns=6, split='train', kernel_bridge=b)
print(f'baseline: {r.score:.0%} ({r.passed}/{r.total})')
print(r.feedback)
"
```
- **30–70% → proceed.**
- **0% or errors** → something's broken (usually the server/endpoint); fix before proceeding. If the model is simply too weak for `needle_search`, try `counting` (easier) in the run command's `suite_name` (§4.2).
- **100%** → tasks too easy; use a harder suite (`multi_hop`) or accept a no-improvement outcome.

### 3.2 Smoke test: 2 metric calls (~10 min)
This exercises the full pipeline live — GEPA config, evaluator injection, and the reflection call (litellm → your server — the one path the suite can't cover offline):
```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'src')
from rlm_kernel.optimize import run_optimization
from rlm_kernel.vault import LocalVault
from pathlib import Path
v = LocalVault(Path.home()/'.local/share/rlm-kernel/vault')
r = run_optimization(v, target='how-to-work', suite_name='needle_search',
                     profile='tiny', max_metric_calls=2, max_turns_per_task=4)
print(r['status'], '| baseline:', r.get('baseline_score'), '| best:', r.get('best_score'))
"
```
Expected outcomes: `no_improvement` (most likely — 2 calls teach nothing), possibly `promoted`. What matters is that it **completes without** `status: error`. If you see `error` with a connection/litellm message: the reflection path is failing — check that `/v1/models` shows the model and that `root_endpoint`'s scheme is right (the fix uses `api_base` + `ssl_verify=False`; plain-`http` servers need the endpoint changed in `config.py`).

## 4. The overnight run

### 4.1 Timing estimate
From the smoke test, note how long one `completion()` takes on your box (roughly: the smoke test's duration ÷ ~3). Expected total ≈ 150 × that. If the estimate exceeds ~12 h, reduce to `max_metric_calls=80` (still meaningful) — better a completed 80 than an abandoned 150.

### 4.2 Launch (from the repo root)
The CLI doesn't expose `log_dir`/profile, so use the snippet for full control:
```bash
cd ~/Documents/Misc/rlm_for_local
.venv/Scripts/python.exe -c "
import sys, json; sys.path.insert(0, 'src')
from pathlib import Path
from rlm_kernel.optimize import run_optimization
from rlm_kernel.vault import LocalVault
v = LocalVault(Path.home()/'.local/share/rlm-kernel/vault')
r = run_optimization(v, target='how-to-work', suite_name='needle_search',
                     profile='tiny', max_metric_calls=150, max_turns_per_task=6,
                     log_dir=Path('logs/k4-first-run'))
print(json.dumps(r, indent=2))
" 2>&1 | tee k4-acceptance-run.txt
```
Then leave the machine alone: no heavy jobs, don't touch the vault, don't stop llama-server, disable sleep/hibernate for the night. Note: interrupting (Ctrl+C, power loss) means **restarting from zero** — resume is not implemented.

What happens during the run: for each candidate, the evaluator swaps the candidate text into `contract/how-to-work.md`, runs the eval tasks through the real harness (REPL + sub-calls against your server), then restores the original text. At the end, promotion happens only through the gate: train improvement **and** held-out ≥ baseline **and** validation → version bump + lineage comment + git commit in the vault.

## 5. Morning after

1. **Read the result** at the end of `k4-acceptance-run.txt`: `status` will be `promoted` / `no_improvement` / `gate_error` / `error`, with `baseline_score`, `best_score`, `held_out_score`, `metric_calls`.
2. **If promoted:** inspect what changed — `cd ~/.local/share/rlm-kernel/vault && git log --oneline -3 && git diff HEAD~1 -- contract/how-to-work.md`. Read the evolved text yourself: does it make sense? The lineage comment `<!-- optimized_by: gepa-run-… -->` should be at the top. If you dislike it: `git revert HEAD` inside the vault.
3. **Write the report** to `rlm_for_local/docs/k4-first-run-report.md`:
   ```markdown
   # K4 First Run Report — <date>
   - Target: how-to-work | Suite: needle_search | Profile: tiny | Budget: 150 metric calls
   - Result: <status> | baseline: X% | best: Y% | held-out: Z% | metric calls used: N
   - Duration: ~H hours | Promoted page: contract/how-to-work.md (vN, gepa-run-<id>) or n/a
   - Notes: <what the evolved text changed; any anomalies; next target to try>
   ```
   An honest `no_improvement` still completes the milestone — it proves the pipeline end-to-end.

## 6. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `error` with ConnectError | llama-server down or wrong scheme/port → §2.2 |
| Baseline 0% with exceptions in feedback | same as above, or model id mismatch → `curl -k …/v1/models` |
| Baseline 100% | saturation → use `multi_hop` suite |
| `gate_error` on a good candidate | validation failed (usually slot variables) → inspect the candidate in `logs/k4-first-run/*.jsonl`; the page is untouched, safe to rerun |
| Everything extremely slow | lower `max_metric_calls` to 80 and/or `max_turns_per_task` to 4; close other model servers |
| REPL timeouts in feedback | `cell_timeout` too low for the model's speed → profile `tiny` has 60 s; raise in `config.py` if your box is slow |
| `UnicodeEncodeError` anywhere | stray non-UTF8 write — all harness writes are UTF-8 now; report it |

## 7. After the milestone

If the run promotes an improvement: consider a second run on `helper-docs`, and start thinking about the `fewshots` bootstrap path. If `no_improvement`: the honest next lever is eval-task difficulty (the 30–70% band) — add 5–10 harder verifiable tasks to `tests/evals/` and rerun. Either way, the K4 milestone closes with your `k4-first-run-report.md`, and the project moves to production soak per the owner's amendment.
