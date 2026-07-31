# Validation: Frontends + Model-Check + Operator Docs (`c8402b3`, `3ef2ead`)

**Date:** 2026-07-30 (18:43)
**Reviewer:** Kimi Code CLI (validation requested by user)
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `c8402b3` (M1–M3) + `3ef2ead` (D4) against `docs/20260730-1002-implementation-agent-instructions-frontends-modelcheck-docs.md` (D1–D4, owner-confirmed scope)
**Suite:** `pytest -q -k "not slow"` → **251 passed, 0 failed, 12 deselected (slow)** — 65 new tests across `test_cli.py`, `test_chat.py`, `test_model_check.py`, `test_web.py`.

---

## 1. Verdict

**The four deliverables substantially conform and the suite is fully green.** D1 (CLI) and D3 (model-check) are essentially spec-exact; D4 (docs) is complete with all three required procedures. D2 (web) covers the confirmed scope (console + vault search, SSE live jobs, dual HTTPS path documented, auth) but carries **one security defect (path traversal in the docs route), one missing security control (upload size caps), and two smaller deviations** (in-memory jobs, fail-open auth when no token is configured). The live D3 acceptance run (`rlm check Qwen3.5-4B --quick`) is in flight at this writing and noted in §6.

## 2. Deliverable-by-deliverable

### D1 — CLI frontend ✅
All commands present and correctly shaped: `ask` (with `--context-file/--context-dir/--stdin/--vault` context assembly), `chat` (interactive loop with a `_commands` dispatch dict covering `/ask /ingest /context /clear /search /get /note /check /quit`; non-slash input routes to `/ask`; Ctrl+C/Ctrl+D clean), `ingest` (Markdown → vault pages with **content-hash idempotency**), `search`, `get`, `check`, `vault` (pass-through), `optimize`. Tests cover argparse flows, assembly order, ingest dedup, and the scripted chat loop.

### D2 — Web frontend ⚠️ (defects below)
- **Conforms:** routes `/`, `/jobs`, `/jobs/{id}`, `/jobs/{id}/events` (SSE driven by `data/jobs/<id>/trajectory.jsonl`), `/vault`, `/vault/page`, `/vault/ingest` (ephemeral-context vs save-to-vault semantics preserved), `/check`, `/docs/{name}`, `/login`/`logout` (bearer token via `RLM_WEB_TOKEN` + session cookie). Bonus: a `/chat` web mode (out of scope but harmless). Operator guide documents **both** HTTPS paths — `tailscale cert` (preferred) and self-signed openssl with SAN for hostname+Tailscale IP, plus phone install instructions.
- **D2-a — Security defect (must fix): path traversal in `/docs/{name:path}`** (`app.py:322-328`). The handler does `docs_dir / name` and only checks `exists()/is_file()` — `..%2F..%2F` segments escape `docs/` and expose any file the process can read. Behind auth, but a token holder (or token thief) gets filesystem read. **Fix:** `resolved = (docs_dir / name).resolve(); if not resolved.is_relative_to(docs_dir.resolve()): 404`.
- **D2-b — Missing control (must fix): no upload caps.** The spec required ≤20 files / ≤8 MB total on Markdown upload; no size/file-count enforcement found in `app.py`. On a Tailscale-exposed service this is the natural abuse vector.
- **D2-c — Deviation (minor): jobs are memory-resident** (`_jobs: dict`), so job state dies on server restart; only the trajectory file is on disk. Spec asked for JSON-on-disk job records (resumable listing). The operator guide documents the limitation honestly — acceptable as a documented v1 tradeoff, flagged for the record.
- **D2-d — Deviation (minor): auth is fail-open when `RLM_WEB_TOKEN` is unset** (`app.py:47-48`: "No token configured — allow all"). Spec's intent was fail-closed (401 everywhere except `/login`). At minimum this should log a loud warning at startup; better, refuse non-local requests without a token.

### D3 — Model suitability tool ✅
Battery P1–P9 implemented per spec (`model_check.py`, 862 lines): protocol emission, helper-call correctness, stderr recovery, answer-dict submission, format discipline, needle accuracy ×3, smart-quote rate (deduction-only), sub-call usage, speed band (report-only). `WEIGHTS` dict, verdict bands exactly per spec (**≥75 SUITABLE / 50–74 MARGINAL / <50 NOT SUITABLE**), `--quick` (P1+P4+P6) mode, persisted reports at `docs/model-checks/<model>-<ts>.md` with frontmatter and per-probe evidence. Tests include scripted good/bad model fixtures reproducing the historical failure modes.

### D4 — Documentation ✅
`docs/operator-guide.md` (475 lines): all eight chapters — system intro, quickstart, CLI reference, Web UI reference (pages, job lifecycle, both HTTPS paths, phone setup), **Model Management with all three required procedures** (§5.1 initialize vault for new model, §5.2 repurpose existing vault incl. re-evaluation of evolved pages per the AppWorld lesson, §5.3 grade suitability), security notes, troubleshooting, FAQ. `README.md` gained a frontends section. `scripts/make_selfsigned_cert.sh` was **not** shipped — the guide carries the openssl command inline instead (acceptable variant; flagged as the one spec artifact not produced).

## 3. Test-suite note

`test_web.py` is thin (52 lines) relative to the others — covers login flow and basic routes. The SSE flow, upload caps, and traversal protection are not covered there; when D2-a/D2-b are fixed, tests must land with them (per repo TDD rule).

## 4. Required fixes (one small commit)

1. **D2-a:** resolve-and-jail the docs route (path traversal) + a traversal test.
2. **D2-b:** enforce 20-file / 8 MB caps on upload + a cap test.
3. **D2-d:** fail-closed or loud-startup-warning when `RLM_WEB_TOKEN` is unset.
4. Optional: ship `scripts/make_selfsigned_cert.sh` wrapping the guide's openssl command.

## 5. Live acceptance check (completed 2026-07-30 ~19:30)

`rlm check Qwen3.5-4B-Abliterated --quick` → **score 70.0/100, verdict MARGINAL** (2,123 s for 7 probes):

- **P1 protocol emission: 3/3 PASS** (valid ```repl blocks every trial)
- **P4 answer-dict submission: 0/15 FAIL** — no voluntary `answer['ready']=True`; forced finalization
- **P6 needle accuracy: 3/3 PASS** (all three needles found)

**Interpretation: the tool told the truth, and it's a good one.** The model is excellent at *getting* answers (P1/P6 perfect) but unreliable at the *voluntary submission protocol* — consistent with every trajectory we've reviewed (our eval suites score answer correctness regardless of forced-vs-voluntary submission, which masked this). The MARGINAL band's own definition ("sub-tier / assisted") fits precisely: this model works in production **with** harness assistance (the forced-finalization fallback). Notably, the promoted how-to-work's SUBMIT instruction ("concatenate all extracted snippets into a single answer and submit") does not explicitly name the `answer["ready"] = True` mechanism — a candidate refinement for the next optimization round or a one-line manual edit, with the gate's eval as the check. This also validates the battery's discriminating power: correctness-only evals said "great model"; the battery said "great at answers, weak at protocol — assisted." Both are true, and only the second is actionable.

The D3 verdict is confirmed as SUITABLE tool, honest battery. Nothing in §4 changes.
