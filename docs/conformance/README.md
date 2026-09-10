# Conformance History — rlm-kernel

> **Layout note (2026-07-30):** all conformance documents were consolidated into
> `docs/` (this directory's parent) alongside the design specs and research
> reports; this folder retains only this index. Paths below are relative to
> `docs/`.

This directory contains the full conformance review and remediation trail
for the `rlm-kernel` implementation against its specification
(`docs/20260724-1736-rlm-kernel-evolvable-spec.md`).

## Timeline

| Date | Document | Role |
|---|---|---|
| 2026-07-24 | `20260724-1736-rlm-kernel-evolvable-spec.md` | Original specification (in `docs/`) |
| 2026-07-25 08:16 | `20260725-0816-rlm-kernel-conformity-review.md` | Initial conformity review — 13 defects + 8 spec deviations |
| 2026-07-25 08:38 | `20260725-0838-rlm-kernel-conformity-review-addendum.md` | Owner decisions on K3/K4 scope, load test, dependencies |
| 2026-07-25 09:28 | `20260725-0928-rlm-kernel-remediation-validation.md` | Remediation validation #1 — 5 gaps (G1-G5) |
| 2026-07-25 09:28 | `20260725-0928-rlm-kernel-remediation-validation-2.md` | Remediation validation #2 — G1-G5 follow-up |
| 2026-07-25 09:53 | `20260725-0953-load-gate-100k-runbook.md` | Load-gate runbook for Tier 1 (synthetic) + Tier 2 (organic) |
| 2026-07-25 19:51 | `20260725-1951-load-gate-tier1-diagnosis.md` | Load-gate diagnosis #1 — first 100K run analysis |
| 2026-07-26 14:12 | `20260726-1412-load-gate-diagnosis-2-fts-quadratic.md` | Diagnosis #2 — root cause (FTS DELETE quadratic) + fix protocol |
| 2026-07-26 15:27 | `20260726-1527-fts-quadratic-fix-validation.md` | Validation #3 — 100K gate PASSES (582 s / 117 ms / 38 ms) |
| 2026-07-26 16:36 | `20260726-1636-implementation-agent-instructions-closeout-and-k4.md` | Close-out instructions + K4-real milestone scope |

## Status

The conformance loop for review cycle July 24–26 is **closed**. All 13 defects
(D1–D13), 8 spec deviations (E1–E8), 5 gaps (G1–G5), and 2 defects (D-a, D-b)
are resolved. The 100K load gate passes all three acceptance metrics.

K4-real (GEPA offline optimizer) **completed on 2026-07-30** — it is no longer
the next milestone. The acceptance run promoted the evolved `how-to-work` page
through the gate with train 1.0 (5/5) and held-out 1.0 (2/2) against a baseline
of 0.8 / ~0.45; the first run took five failed overnight attempts (undeclared
dependencies, cp1252 crashes, parallel self-contention, a power cut) before a
checkpoint rescue succeeded. Results and the honest attempt log are in
`k4-first-run-report.md`; the close-out that unblocked it (D-K4-1/D-K4-2, gate
routing, migration, real F5) is validated in
`20260726-1809-closeout-k4-validation.md`.

Remaining work after K4 is tracked in the remediation plan
(`20260903-2107-remediation-plan.md`) and the analysis behind it
(`20260903-2107-rlm-for-local-full-analysis.md`); the conformance loop itself
(July 24–26 review cycle) remains closed.
