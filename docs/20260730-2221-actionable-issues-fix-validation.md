# Validation: Actionable-Issues Fixes (`f84656a`…`498abc3`)

**Date:** 2026-07-30 (22:21)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `498abc3` against `docs/20260730-2141-implementation-agent-actionable-issues.md` (P1–P6)
**Suite:** `pytest -q -k "not slow"` → **271 passed, 0 failed** (+20 over the previous 251).

---

## Verdict

**All six items are fixed or deliberately resolved — one pending verification remains: the live re-run of `rlm check` after the P4 prompt edit, which the agent deferred (CPU timeout). I have launched it myself (task `bash-d22nvde3`, ~35 min) and will append the result here; it is the interesting part, since it measures whether a one-line prompt change moves the submission-protocol score.**

## Per-item verification

- **P1 — path traversal: ✅ fixed.** `app.py:348-349` resolves and jails with `is_relative_to(docs_dir.resolve())`; tests probe `pyproject.toml` and `win.ini` escapes → 404 (`test_web.py:197`).
- **P2 — upload caps: ✅ fixed.** `MAX_UPLOAD_FILES = 20`, `MAX_UPLOAD_BYTES = 8 MB`, enforced with 413 rejections; cap tests present.
- **P3 — auth policy: ✅ fixed (the preferred variant).** Token unset → loopback-only (`127.0.0.1`, `::1`, `localhost`, `testclient`), everything else 401; token set → normal session flow. Commit message documents the policy.
- **P4 — `answer["ready"] = True` prompt fix: ✅ fixed in form, verification pending.** The promoted `contract/how-to-work.md` now names the answer-dict explicitly, edited **through the gate** (`propose → validate → promote`, slots preserved, vault commit `953d971` with lineage `gepa-run-edit-p4-fix`). The **prompt-regression guard test** exists (`test_web.py:250-263`): asserts `how-to-work.md` and `repl-contract.md` contain the answer-dict mechanism — trips on future GEPA promotions that weaken it. Commit message honestly records: *"Before: P4 0/15… After: regression guard passes; live check pending (CPU timeout)."* **That live check is what I am running now** (`bash-d22nvde3`); result appended below.
- **P5 — test coverage: ✅ fixed.** `test_web.py` grew to 16+ tests covering traversal, caps, ingest flows + dedup, chat routes, SSL arg passing, SSE stream-id flow.
- **P6 — optional polish: ✅ deliberately resolved.** Jobs remain memory-resident with the limitation documented in the operator guide (chosen tradeoff); self-signed cert documented inline (no script) — no dangling references.

## Extras shipped beyond the instructions (good work, in scope)

`f84656a` — job page SSE fixed (vanilla EventSource replacing a broken htmx extension); `86f5ff9` — `rlm tag` command + `--tag` search filter; `f4b09bc` — rlm-check crash fix (shared backend closed between probes); `30a9082` — rlm-check stderr evidence, timing, pass/fail summary. All consistent with the project's direction.

## Remaining item — RESOLVED (2026-07-30, ~22:50)

Live P4 verification after the prompt edit: **P4 = 0/15, unchanged** (total 60/100 MARGINAL; P1 3/3, P6 2/3).

**Verdict per the pre-registered escape hatch: the cause is model behavior, not prompt text.** The prompt edit was correct to make (protocol documentation should be explicit) and the regression guard test is the right tripwire — but Qwen3.5-4B does not reliably perform the voluntary submission step; it narrates, and the harness's **forced finalization is the structural mechanism** keeping this model productive. The MARGINAL band's "assisted" is literally the architecture, and that's an acceptable steady state: correctness is unaffected (P6 needles found regardless). If better P4 is ever wanted, the levers are: a few-shot transcript showing a voluntary answer-dict submission (few-shot bootstrap), or a stronger-adherence model — not more prompt wording.

**Second finding (eval-pattern brittleness):** P6 needle 3 scored FAIL on *"expected 'O-Negative', got 'Patient blood type: O Negative'"* — the model answered **correctly**; the regex demands the hyphenated form. A false negative that cost 5 points (70 → 60 vs the earlier run with identical true capability). Fix for the battery: normalize hyphens/whitespace/case when matching needle answers (and audit expected patterns for format variance). This echoes the recurring lesson of the whole project: the evaluator's text shapes scores more than the model does.

No further P4 action is required from the implementation agent; the two follow-ups (few-shot-with-submission option, needle-pattern normalization) are logged for the next planning round.
