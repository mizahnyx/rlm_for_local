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

### Timeline, continued (2026-07-26 evening → 2026-09-10)

| Date | Document | Role |
|---|---|---|
| 2026-07-26 18:09 | `20260726-1809-closeout-k4-validation.md` | Close-out validation (D-K4-1/D-K4-2, gate routing, migration, real F5) |
| 2026-07-26 19:05 | `20260726-1905-k4-wiring-fix-validation.md` | K4 wiring fix validation |
| 2026-07-26 19:40 | `20260726-1940-final-wiring-fix-validation.md` | Final wiring fix validation |
| 2026-07-26 19:54 | `20260726-1954-rk41-restoration-validation.md` | RK4.1 restoration validation |
| 2026-07-26 20:16 | `20260726-2016-gating-test-vacuity-fix-validation.md` | Gating-test vacuity fix (the incident the mutation rule comes from) |
| 2026-07-26 21:15 | `20260726-2115-k4-acceptance-run-guide.md` | K4 acceptance run guide |
| 2026-07-29 11:09 | `20260729-1109-k4-v5-health-check-protocol.md` | K4 v5 health-check protocol |
| 2026-07-30 10:02 | `20260730-1002-implementation-agent-instructions-frontends-modelcheck-docs.md` | Frontends, model-check and docs milestone instructions |
| 2026-07-30 18:43 | `20260730-1843-frontends-validation.md` | Frontends validation |
| 2026-07-30 21:41 | `20260730-2141-implementation-agent-actionable-issues.md` | Actionable issues raised against that milestone |
| 2026-07-30 22:21 | `20260730-2221-actionable-issues-fix-validation.md` | Those issues fixed and validated (271-test baseline) |
| 2026-07-30 | `k4-first-run-report.md` | K4 first run: five failed attempts, then promotion (honest attempt log) |
| standing | `load-test-report.md` | 100K-page load-gate report; extended 2026-09-10 with the post-remediation re-run |
| 2026-09-03 21:07 | `20260903-2107-rlm-for-local-full-analysis.md` | Full-repository analysis — findings F1–F16, S1–S6 |
| 2026-09-03 21:07 | `20260903-2107-remediation-plan.md` | The remediation plan (items R1–R25) those findings became |
| 2026-09-03 21:07 | `20260903-2107-agent-memory-landscape-report.md`, `20260903-2107-memanto-vs-rlm-behaviour-as-content.md` | Companion research and comparison reports |
| 2026-09-10 07:30 | `20260910-0730-remediation-validation.md` | **This cycle's validation** — waves 0–4 enacted, 641 tests green, 30/30 guards proven non-vacuous, LIVE needle + load gate + `rlm check` recorded, deviations stated |
| 2026-09-11 00:50 | `20260911-0050-router-model-assessment.md` | Router model sweep — stage-1 protocol screen, stage-2 suitability verdicts (five models scored 100/100 besides the baseline), the P1-saturation finding, host tuning note |
| 2026-09-11 12:19 | `20260911-1219-assessment-follow-ups-hardening.md` | That assessment's follow-ups enacted: cross-origin (CSRF) check on every state-changing route, a real P4 disagreement diagnostic, battery reweighting off the saturated probe |
| 2026-09-11 13:59 | `20260911-1359-p4-live-confirmation.md` | Live confirmation of that diagnostic on the model it came from: 46.7/NOT SUITABLE (predicted), the contradiction reproduced, and the finding that P4 is single-trial — one probe run swings this model's verdict a full band |

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

The **2026-09-03 analysis → remediation cycle is also closed**: every item
R1–R25 was enacted, with R23 (`schema` → `schema_version`) deferred by the plan
itself as a breaking vault-format change. Evidence — including the two claims
that could **not** be measured on the working host and why — is in
`20260910-0730-remediation-validation.md`. Deliberate residuals (the gate is a
quality gate rather than containment; `answer`/`context` are not restored after
each cell; P3's scoring weaknesses; `schema` shadowing a pydantic attribute) are
listed in §7 of `20260910-0730-remediation-validation.md` rather than left
implied.

The **model-suitability work that followed** is a separate, still-open thread:
the router sweep in `20260911-0050-router-model-assessment.md` answered which
models can drive the harness, and its follow-ups — the CSRF check, the P4
disagreement diagnostic, the battery reweight — are enacted in
`20260911-1219-assessment-follow-ups-hardening.md`. What remains open is
listed in §6 of that hardening report
(`20260911-1219-assessment-follow-ups-hardening.md`) rather than implied: P3's
scoring weaknesses, the `--screen` triage that is not a gate, the two models
skipped after failing it, and the host-side `max_instances` risk.

The one item that *was* verified live — the P4 diagnostic, on the model whose
contradiction motivated it — is recorded in
`20260911-1359-p4-live-confirmation.md`, including what it turned up beyond the
confirmation: the verdict moved as predicted, and P4's single-trial sampling
means one probe run can swing a model's verdict a full band.
