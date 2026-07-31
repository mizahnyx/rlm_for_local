# Implementation Instructions: Actionable Issues from the Frontends Validation

**Date:** 2026-07-30 (21:41)
**For:** the implementation agent working on `~/Documents/Misc/rlm_for_local/`
**From:** project owner, via Kimi Code CLI
**Source:** `docs/20260730-1843-frontends-validation.md` §2 (D2-a…D2-d), §3, §5 — every item below traces to it. **TDD per repo convention: each fix lands with its failing-then-passing test.**

---

## P1 — Path traversal in the docs route (security, must fix)

**Defect:** `src/rlm_web/app.py` (`@app.get("/docs/{name:path}")`, ~line 322) builds `doc_path = docs_dir / name` and only checks `exists()/is_file()`. `..%2F..%2F…` segments escape `docs/` and expose arbitrary filesystem reads to any authenticated caller.

**Fix:** resolve and jail:
```python
resolved = (docs_dir / name).resolve()
if not resolved.is_relative_to(docs_dir.resolve()) or not resolved.is_file():
    raise HTTPException(status_code=404)
```
**Test:** `/docs/..%2F..%2Fpyproject.toml` and `/docs/..%2F..%2F..%2FWindows/win.ini` return 404; a legitimate nested doc path still serves.

## P2 — Upload size caps missing (security, must fix)

**Defect:** the console's Markdown upload accepts unbounded files — spec requires **≤20 files, ≤8 MB total** (instruction doc D2).

**Fix:** enforce both caps in the `/jobs` submit handler (and the `/vault/ingest` handler): reject with a 413-style page naming the cap; count files before processing; sum bytes as read.
**Test:** 21 small files → rejected; one 9 MB file → rejected; 3 small files → accepted.

## P3 — Auth is fail-open when `RLM_WEB_TOKEN` is unset (must fix)

**Defect:** `app.py` (`_check_auth`, ~line 47): `if not token: return  # No token configured — allow all`. The spec's intent is fail-closed for a Tailscale-exposed service.

**Fix (choose one, default A):**
- **A (preferred):** startup refuses to serve non-local requests without a token — log a prominent warning at boot *and* add a visible banner on the login page ("no token configured — set RLM_WEB_TOKEN"). Loopback-only exemption is acceptable for desktop dev.
- **B:** hard-fail boot when token is unset unless `--allow-no-token` is passed explicitly.
**Test:** app with no env token still allows loopback per the chosen policy; an external-origin request without token is rejected; with token set, normal login flow unchanged.

## P4 — `answer["ready"] = True` missing from the promoted prompt (functional, must fix)

**Defect (from live check, §5):** `rlm check Qwen3.5-4B --quick` scored **P4 = 0/15** — the model never voluntarily sets `answer["ready"] = True`. Contributing cause: the promoted `contract/how-to-work.md` tells the model to "concatenate … and submit" but **never names the answer-dict mechanism**. The REPL contract page explains it, but the operative how-to-work text the model follows does not.

**Fix (two parts):**

1. **Gate-routed prompt edit.** Through the gate (propose → validate → promote — do **not** hand-edit the page directly; the vault's git history must show the provenance): add an explicit submission rule to `contract/how-to-work.md`'s SUBMIT step, e.g.:
   > 4. **SUBMIT** — In a ```repl block, set `answer["content"]` to your final answer and then `answer["ready"] = True`. Do this **only** after you have printed your candidate answer. Never submit in prose or JSON — only via the `answer` dict in code.
   Keep every `{slot}` variable intact (validation must pass) and keep the evolved extraction procedure unchanged otherwise.
2. **Prompt-regression guard test (new, permanent).** A contract test asserting that *live prompt pages* (contract kind, active status) explicitly contain the submission mechanism — assert `"ready" in body and "answer" in body` for `contract/repl-contract.md` and `contract/how-to-work.md`. This fails today on the promoted page and passes after the edit. It is the standing tripwire for any future GEPA promotion that weakens the protocol.
3. **Verification:** after the edit, re-run `rlm check Qwen3.5-4B-Abliterated --quick`. Expected: P4 > 0 (ideally full marks). Report the before/after scores in the commit message. If P4 does not improve, the cause is model behavior rather than prompt text — report that honestly instead of forcing the edit through.

## P5 — Test coverage gaps in `test_web.py` (should fix)

`test_web.py` (52 lines) covers login + basic routes only. Add: SSE event flow from a stub-backed completion, upload cap enforcement (from P2), traversal rejection (from P1), auth-policy behavior (from P3). Each P1–P3 fix above ships with its test here.

## P6 — Optional polish (take or leave, document the choice)

- **Jobs persistence:** job state is memory-only (lost on server restart); trajectory files are on disk. Either persist a job-record JSON under `data/jobs/<id>/` (resumable listing after restart) or leave as-is with the limitation already documented in the operator guide. Choose deliberately and record it in the guide.
- **`scripts/make_selfsigned_cert.sh`:** the spec named this script; the guide carries the openssl command inline instead. Either ship the script (one wrapper around the documented command) or edit the spec-reference in the guide to say "inline command (no script)". Choose one — no dangling reference.

---

## Acceptance

- Suite green with the new tests (P1, P2, P3, P4-guard, P5).
- `grep -rn "resolve()" src/rlm_web/app.py` shows the jail; manual traversal probe 404s.
- `rlm check Qwen3.5-4B-Abliterated --quick` after the P4 edit: P4 improved (attach before/after to the commit message).
- Vault git log shows the prompt edit came through the gate (no direct file edits).
- One commit or one-per-P as you prefer; messages follow the repo convention.
