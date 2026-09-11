# Assessment follow-ups: CSRF, the P4 disagreement, and the battery scale

**Date:** 2026-09-11 12:19
**Scope:** the three follow-ups recommended after
`20260911-0050-router-model-assessment.md` — plus one defect found while
verifying them.
**Status:** enacted; fast suite green (`744 passed, 12 deselected`, 294 s);
11 new guard mutations proven non-vacuous.

The router sweep answered "which models can drive this harness". Its §6 listed
what the sweep itself could not fix. Three of those items were small, closed and
testable, and they were taken first:

1. the only open security item — no CSRF control on state-changing routes;
2. P4 scoring a contradiction silently;
3. a battery scale dominated by a probe every model already passes.

---

## 1. Cross-origin (CSRF) protection — the open security item

`20260910-0730-remediation-validation.md` §8.1 closed the session-secret bypass
and left this stated rather than fixed: the console authenticated with a session
cookie and relied on `SameSite=Lax` alone. A cookie rides along on *any* request
the browser makes, so authentication does not authorise a `POST` by itself — a
page the operator visits could create a job, ingest a file, or clear a chat
session in a console they were logged into. `GET /logout` was the cheapest form
of the same problem: any cross-site `<img>` tag logged the operator out.

**Fix.** `require_same_origin` is a route dependency on every state-changing
route: `POST /login`, `/jobs`, `/vault/ingest`, `/chat/send`, `/chat/clear`,
`/chat/context`, and `GET /logout`. On routes that carry both, it is ordered
*before* `require_auth`, so a cross-site request is refused without the auth gate
having to disclose whether a session existed.

The decision is a pure function (`_origin_allowed`), so the policy is a table
rather than a set of branches inside a request handler:

| `RLM_WEB_ORIGIN_CHECK` | A request that claims an origin | A request that claims none |
|---|---|---|
| `same-origin` (default) | must claim this server's | allowed — `curl`, scripts, a typed URL |
| `strict` | must claim this server's | **rejected** |
| `off` | allowed | allowed |

Three details are deliberate, and each has a test:

- **`Origin: null` fails closed.** A sandboxed iframe or a `file://` page sends
  that literal, so "unparseable" is tracked as distinct from "absent"
  (`_request_origin` returns `""` versus `None`). Treating the two alike would
  have let the one browser context that most wants CSRF protection through.
- **`Referer` is the fallback.** Browsers that withhold `Origin` on a
  same-origin form post still send it, so the check degrades to the referrer's
  origin rather than to nothing.
- **Loopback aliases are one origin.** `localhost`, `127.0.0.1` and `[::1]` on
  the same port normalize to a single host. They are the same server, and a
  check that disagreed would lock the operator out of their own console — which
  is how this kind of fix usually gets reverted.

`RLM_WEB_ALLOWED_ORIGINS` (comma-separated) replaces the same-origin comparison
with an explicit list, and does **not** implicitly include this server. That is
the DNS-rebinding-resistant mode: when the attacker's page resolves to the
console, its `Origin` and `Host` agree, so only a list the operator wrote down
can decide. The 403 detail says so, because otherwise the operator's own console
stops working with no explanation.

**Fail-closed at startup.** An unrecognised mode is rejected with exit 2, and so
is an allowlist entry that is not an origin (a bare hostname, a missing scheme).
Both are silent at request time by design — the mode falls back to the default
rather than to `off`, and an unusable entry simply never matches — so the mistake
has to be caught at boot or it looks like the console rejecting legitimate
requests for no reason.

**Residual, stated rather than implied:** this is origin *policy*, not an
anti-forgery token, and `RLM_WEB_ORIGIN_CHECK=off` exists. The mode that resists
DNS rebinding requires the operator to maintain the allowlist. `SameSite=Lax`
remains on the cookie as defence in depth.

Guards: `tests/test_web.py::TestOriginNormalization`,
`::TestOriginDecisionTable` (the 11-case policy table plus "every refusal says
why"), `::TestCrossOriginRequests` (including the attack reproduction: a live
session plus `Origin: https://evil.example` → 403), and
`::TestOriginCoverageByConstruction`, which walks the route table so a new
state-changing route cannot be added without the dependency.

## 2. P4: the contradiction now has an explanation

The recorded defect (`20260911-0050-router-model-assessment.md` §3.1):
`Qwen3.5-2B-Instruct` produced, in one P4 run, both
`answer['ready'] = True found in model output` and `No final answer detected` +
forced finalization. The two signals disagreed and P4 scored 0 without saying
why. That is a harness-diagnostic gap, not a model verdict.

A submission counts only when the interpreter runs the line, so "where did the
model put it?" has a mechanical answer. `_diagnose_missing_submission` computes
it from the trajectory, with no model in the loop:

| Code | Meaning |
|---|---|
| `submission_block_raised` | The line is in an executed block whose cell raised; the traceback's last line is quoted. |
| `submission_not_reached_at_runtime` | The block ran clean and never reached the line — a conditional, a loop, or `answer` rebound to a non-dict. |
| `submission_text_in_unexecutable_fence` | The line is inside a fence tag the parser does not execute. Only `repl`, `python` and untagged fences run (`parser.FENCE_RE`), so a ```json or ```bash fence is discarded. |
| `submission_text_outside_fence` | The line is prose; the parser extracts only fenced blocks. |

**Precedence is by evidence quality, not document order.** A block the
interpreter actually ran outranks a stray prose mention of the same line: if the
model *did* put the line in a good block and that block ran clean without
submitting, the prose mention is not the explanation. The first draft of this
code had that backwards, and the test
`test_an_executed_block_outranks_a_stray_prose_mention` pins the corrected order.

The scan is line-based (`_fence_regions`) rather than a regex over the whole
message, because spans are what the question needs, and an unclosed final fence
is reported as `closed=False` — the parser's stage 2 rescues it, so it still
executes and must not be blamed on placement.

**The score did not change.** A model that did not submit still scores 0; it now
comes with a reason. Any change to P4's score semantics would be an owner
decision (the same posture as P3's documented weaknesses).

**Evidence.** `ProseSubmissionStub` is not a mock: it drives the real completion
loop, the real parser, a real REPL subprocess and the real trajectory logger,
and the assertion is on the diagnostic the probe attaches. What is *not* claimed:
this has not been re-observed on `Qwen3.5-2B-Instruct` itself against the router,
so the code that model will produce is the one thing still unverified (§5).

## 3. The battery scale moved off the saturated probe

P1 ("emits a valid `repl` block") scored 20/20 for all ten models on the router,
down to 0.8B, so it separated nothing; the entire SUITABLE/MARGINAL spread came
from P4 (0 or 15) and P6 (0–15). Its weight compressed differences instead of
measuring them.

Weights are now a **named profile** rather than a constant, and every result and
saved report records the profile and the resolved weights — a score is
meaningless without the scale it came from:

| Profile | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 |
|---|---|---|---|---|---|---|---|---|---|
| `default` | 10 | 15 | 15 | 20 | 10 | 20 | 5 | 5 | 0 |
| `p1-heavy` | 20 | 15 | 15 | 15 | 10 | 15 | 5 | 5 | 0 |

`p1-heavy` is the pre-change scale, kept so the verdicts recorded in
`20260911-0050-router-model-assessment.md` stay reproducible; select it with
`rlm check --weights p1-heavy` or `RLM_CHECK_WEIGHTS`. Both profiles still total
100, and the quick battery (P1+P4+P6) still totals 50, so the `/100` reporting is
unchanged. An unknown profile name raises instead of falling back — a typo must
not produce a comparable-looking score on a different scale — and the CLI maps
that to exit 2 with the valid names.

What the change does to a verdict, on the quick battery (`_score_model`
normalises by total weight, so only the ratios matter):

| Model shape | `p1-heavy` | `default` |
|---|---|---|
| Protocol only (P1 full, P4/P6 fail) | 40 | 20 |
| Submits and retrieves (P1 fail, P4/P6 full) | 60 | 80 |
| Recorded baseline (P4 full, P6 10/15) | 90 | 86.7 |
| `MiniCPM5-2B` (all three full) | 100 | 100 |

Both directions move as intended: the probe that every model passes is worth
less, and the two that decide usability are worth more. The models already at
100 stay at 100.

Surface: `--weights` on `rlm check` and on `scripts/assess_router_models.py`
(whose JSON lines now also carry the profile and any P4 diagnostic), plus
`resolve_weights()` and `WEIGHT_PROFILES` in `model_check.py`.

## 4. Found while verifying: the documented fast command ran 20 fewer tests

Not one of the three items, and worth recording because it is the same defect
class this repository has an antidote for — a test that never runs.

`pytest -k` matches a substring of the node id, so the documented
`-k "not slow and not load"` did not mean "the 12 tests marked `slow`". It also
deselected 20 tests whose *names* happen to contain "load":
`test_ingest_loads_file`, `TestUploadCaps` (two), `test_base_template_loads_no_scripts`,
the four `test_load_*` eval-suite tests, `test_load_config_*` (three), and so on.
`20260910-0730-remediation-validation.md` §4 recorded the count as 12 because
that run used `-k "not slow"`; the "and not load" half was never measured.

Measured now, on 756 collected tests:

```
-m "not slow and not load"   744 passed,  12 deselected, 294 s   <- documented now
-k "not slow and not load"   724 passed,  32 deselected, 307 s   <- 20 never ran
```

The living docs (README, local manual §16.2) now say `-m`, and one new
parametrized test was renamed because its parameter was called `payload` — the
node id `[...-/vault/ingest-payload3]` contains "load" and would have been
silently skipped by the `-k` form as well.

## 5. Guard non-vacuity

Eleven mutations were added to `scripts/check_guard_nonvacuity.py`; all eleven
went red as required (`41 mutations total`, every target present exactly once):

| Mutation | Guard it proves |
|---|---|
| R26 the P4 diagnostic is never produced | The probe test asserts on the attached diagnostic. |
| R26 every fence tag counts as executable | Placement detection is not decorative. |
| R26 an executed block stops outranking a stray prose mention | The corrected precedence. |
| R27 the default battery reverts to the P1-heavy weights | The reweight is pinned. |
| R27 a score no longer records the scale it came from | A score carries its scale. |
| R28 a state-changing route loses its origin dependency | Route-table coverage by construction. |
| R28 logout loses its origin dependency | The state-changing `GET`. |
| R28 a mismatched origin is accepted (fail open) | The same-origin comparison. |
| R28 `Origin: null` is treated as claiming no origin | The fail-closed distinction. |
| R28 the allowlist implicitly trusts this server | The DNS-rebinding property. |
| R28 strict mode accepts a request that claims no origin | The `strict` mode's whole purpose. |

## 6. What remains open

- **No live confirmation of the P4 diagnostic.** It is proven end-to-end
  through the real loop with a stub backend, and unit-proven per code, but the
  run that motivated it (`Qwen3.5-2B-Instruct`, ~30–60 min on this host) has not
  been repeated. That run would also produce the model's first verdict on the new
  scale — 60/100 under `p1-heavy` becomes ≈47 under `default`, i.e. NOT
  SUITABLE rather than MARGINAL, which is a verdict change worth seeing on the
  record rather than calculating.
- **P3's scoring weaknesses** stay documented, not fixed (validation §7,
  local manual §16.4): "no stderr ⇒ full credit" and a non-time-ordered recovery
  scan. Tightening them is an owner decision.
- **`--screen` is still not a gate** (assessment §6). It passes 0.8B models
  whose battery verdict is MARGINAL.
- **`Qwen2.5-VL-3B` and `LFM2.5-2.6B-Heretic`** are still un-battery-tested
  after failing the screen.
- **Host-side:** `max_instances=4` on a 15 GiB machine remains an OOM risk; the
  sweep's `--before-each` restart is a workaround, and the operator's router
  configuration was deliberately not changed.
- **CSRF is origin policy, not tokens;** `off` exists, and the rebinding-safe
  mode needs the allowlist maintained (§1).

## 7. How to reproduce

```bash
# the fast suite, on markers rather than names
uv run pytest -m "not slow and not load" -q

# every guard mutation, including the eleven added here
uv run python scripts/check_guard_nonvacuity.py
uv run python scripts/check_guard_nonvacuity.py --only R26
uv run python scripts/check_guard_nonvacuity.py --only R27
uv run python scripts/check_guard_nonvacuity.py --only R28

# the living-doc lint
uv run python scripts/check_docs.py

# the reweighted battery, and the old scale for comparison
export RLM_ENDPOINT="https://lunacode:9010/v1"
uv run python -m rlm_local.cli check <model-id> --quick
uv run python -m rlm_local.cli check <model-id> --quick --weights p1-heavy
```

The origin policy is exercised in-process by `tests/test_web.py`; to try it by
hand, start the console and send a request that claims another origin:

```bash
curl -i -X POST http://127.0.0.1:8778/jobs \
     -H 'Origin: https://evil.example' \
     --data 'query=hi&context='
# 403 Cross-origin request rejected: origin https://evil.example:443 does not
# match this server (http://127.0.0.1:8778).
```
