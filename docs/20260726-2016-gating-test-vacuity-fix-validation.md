# Validation: Gating-Test Vacuity Fix (`9bb5d3e`)

**Date:** 2026-07-26 (20:16)
**Reviewer:** Kimi Code CLI
**Validates:** `~/Documents/Misc/rlm_for_local/` @ `9bb5d3e` ("fix: make held-out gating test non-vacuous — baseline=0.6 so train gate opens") against `docs/20260726-1954-rk41-restoration-validation.md` §3.
**Suite:** `pytest -q -k "not slow"` → **180 passed, 0 failed, 12 deselected (slow)**.

---

## Verdict: validated — the held-out gate now has real regression protection.

The fix matches the prescription exactly (two of five train answers made wrong, with an explanatory comment encoding the scenario in the test itself). More importantly, **discrimination is proven, not assumed**. Re-running the simulation through the project's own loader with the new answer stream:

```
baseline = 0.600    best = 0.9    held_out = 0.000
with gate    → promoted: False   (held-out 0.0 < 0.6 blocks)
without gate → promoted: True    (0.9 > 0.6 — train gate opens)
```

The test now fails if the held-out condition is ever removed again and passes while it stands — precisely the property that was missing. `tests/rlm_kernel/test_optimize.py` alone: 14/14 green; full suite: 180/180.

## Milestone state after this commit

- **Held-out promotion gate:** restored in code (`optimize.py:337`) **and** protected by a discriminating test. The R-K4-1 incident is fully closed.
- **All K4 prerequisites green:** R1–R6, close-out W1–W5, D-K4-1 (gate-routed incumbent promotion + real contract test), D-K4-2 (real target pages), G-K4-1 (honest resume removal), G-K4-3 (documented lineage), load gate passed at 100K (582 s / 116.9 ms / 38 ms).
- **Non-blocking residuals on record:** decorative `demote` archive (git is the real rollback); warnings-only slot validation (evaluator self-selects crashy candidates out).

## The K4 acceptance run is now clear to schedule

This is the last item in the milestone, and it's owner/agent ops, not code:

1. llama-server up with the configured models.
2. `rlm-kernel optimize --target nudges` (cheapest target), `tiny` profile, `max_metric_calls` 150 — expect an overnight run at one full `completion()` per metric call.
3. Commit `docs/k4-first-run-report.md`: baseline vs best on held-out, metric calls used, lineage log — honest result either way; no improvement is still a successful pipeline validation.

After it, the K4 milestone — and the whole arc from harness spec to evolvable kernel to verified optimizer — is complete.
