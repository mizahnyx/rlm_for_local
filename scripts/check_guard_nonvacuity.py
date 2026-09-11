"""Guard non-vacuity check (the project's documented antidote, R24).

For each entry below: apply a literal source mutation that restores the pre-fix
behaviour (or a plausible regression), run **only** the named test, and require
it to go RED. A guard test that stays green when its guard is removed is vacuous
and must not be merged — this repository has had three such incidents, and the
antidote is written down in its conformance history.

This is the recorded evidence for the 2026-09-10 remediation wave
(`docs/20260910-0730-remediation-validation.md`): the mutation table is
deliberately literal and point-in-time. If a mutation target is no longer found,
the entry is reported as a problem rather than skipped silently — re-derive the
mutation from the current source instead of deleting the entry.

Usage:
    .venv/Scripts/python.exe scripts/check_guard_nonvacuity.py [--only SUBSTRING]

Exit code is 0 only when every guard went red as required.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"

# (label, file, old_source, mutated_source, [pytest node ids])
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # ── R2: byte offsets ───────────────────────────────────────────────────
    (
        "R2 len() reverts to character count",
        "src/rlm_local/context_store.py",
        "    def __len__(self) -> int:\n        return self._total_bytes",
        "    def __len__(self) -> int:\n        return len(self[:])",
        ["tests/test_context_store.py::TestByteOffsetSemantics::test_disk_len_is_bytes"],
    ),
    (
        "R2 ingest stops building the byte line index",
        "src/rlm_local/context_store.py",
        "        return Context(fname, total_bytes, line_offsets)",
        "        return Context(fname, total_bytes, None)",
        [
            "tests/test_context_store.py::TestByteOffsetSemantics"
            "::test_disk_lines_start_uses_byte_index"
        ],
    ),
    (
        "R2 chunk() materializes via str(self)",
        "src/rlm_local/context_store.py",
        "        chunks: list[str] = []\n"
        "        with open(self._path, \"r\", encoding=\"utf-8\") as f:\n"
        "            while True:\n"
        "                piece = f.read(chunk_size)\n"
        "                if not piece:\n"
        "                    break\n"
        "                chunks.append(piece)\n"
        "        return chunks",
        "        text = str(self)\n"
        "        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]",
        [
            "tests/test_context_store.py::TestByteOffsetSemantics"
            "::test_disk_chunk_is_lazy_not_materializing"
        ],
    ),
    (
        "R2 mid-codepoint addressing becomes lenient",
        "src/rlm_local/context_store.py",
        "    raise UnicodeDecodeError(\n"
        "        \"utf-8\", raw or b\"\", 0, 1,\n"
        "        f\"byte offset {offset} is not a character boundary\",\n"
        "    )",
        "    return raw[:1].decode(\"utf-8\", errors=\"replace\")",
        [
            "tests/test_context_store.py::TestByteOffsetSemantics"
            "::test_mid_codepoint_index_raises"
        ],
    ),
    # ── R1: spill reaches the worker ───────────────────────────────────────
    (
        "R1 start() ships the whole context over the socket again",
        "src/rlm_local/repl.py",
        "        if isinstance(context, Context):",
        "        if False:",
        [
            "tests/test_repl.py::TestDiskBackedContextInWorker"
            "::test_init_payload_carries_a_reference_not_the_text",
            "tests/test_repl.py::TestDiskBackedContextInWorker"
            "::test_worker_reads_the_spilled_file_lazily",
        ],
    ),
    # ── R3: budget / memoization order ─────────────────────────────────────
    (
        "R3 budget charged before the cache check",
        "src/rlm_local/subcall_manager.py",
        "        # Memoization check — free, and must precede the budget charge.\n"
        "        cache_key = hashlib.sha256(prompt.encode()).hexdigest()\n"
        "        with self._cache_lock:\n"
        "            if cache_key in self._cache:\n"
        "                self._cache_hits += 1\n"
        "                return self._cache[cache_key]\n"
        "\n"
        "        # Budget check\n"
        "        with self._lock:\n"
        "            if self._calls_used >= self._max_calls:\n"
        "                return SUBCALL_COUNT_EXHAUSTED.format(\n"
        "                    used=self._calls_used, max_subcalls=self._max_calls,\n"
        "                )\n"
        "            if self._chars_used + prompt_len > self._max_chars:\n"
        "                return SUBCALL_CHAR_EXHAUSTED\n"
        "            self._calls_used += 1\n"
        "            self._chars_used += prompt_len\n",
        "        # Budget check\n"
        "        with self._lock:\n"
        "            if self._calls_used >= self._max_calls:\n"
        "                return SUBCALL_COUNT_EXHAUSTED.format(\n"
        "                    used=self._calls_used, max_subcalls=self._max_calls,\n"
        "                )\n"
        "            if self._chars_used + prompt_len > self._max_chars:\n"
        "                return SUBCALL_CHAR_EXHAUSTED\n"
        "            self._calls_used += 1\n"
        "            self._chars_used += prompt_len\n"
        "\n"
        "        # Memoization check\n"
        "        cache_key = hashlib.sha256(prompt.encode()).hexdigest()\n"
        "        with self._cache_lock:\n"
        "            if cache_key in self._cache:\n"
        "                self._cache_hits += 1\n"
        "                return self._cache[cache_key]\n",
        [
            "tests/test_subcall_manager.py::TestBudgetAndMemoizationOrder"
            "::test_cache_hit_works_even_when_budget_is_exhausted"
        ],
    ),
    (
        "R3 cache_hits reverts to cache size",
        "src/rlm_local/subcall_manager.py",
        "        with self._cache_lock:\n            return self._cache_hits",
        "        with self._cache_lock:\n            return len(self._cache)",
        [
            "tests/test_subcall_manager.py::TestBudgetAndMemoizationOrder"
            "::test_cache_hits_counts_hits_not_cache_size"
        ],
    ),
    # ── R4: cell correlation ───────────────────────────────────────────────
    (
        "R4 stale results are accepted as the current cell's",
        "src/rlm_local/repl.py",
        "                    if msg_cell != cell_id:",
        "                    if False:",
        [
            "tests/test_repl.py::TestCellTimeoutCorrelation"
            "::test_late_result_is_not_misattributed"
        ],
    ),
    (
        "R4 second timeout does not restart the worker",
        "src/rlm_local/repl.py",
        "        if self._consecutive_timeouts >= self._restart_threshold:",
        "        if False:",
        [
            "tests/test_repl.py::TestCellTimeoutCorrelation"
            "::test_second_consecutive_timeout_restarts_the_worker"
        ],
    ),
    # ── R5: guardrail wiring ───────────────────────────────────────────────
    (
        "R5 repair_json no longer runs on schema'd sub-calls",
        "src/rlm_local/subcall_manager.py",
        "        if use_schema is not None and response:\n"
        "            response = repair_json(response, use_schema)",
        "        if False:\n            response = repair_json(response, use_schema)",
        [
            "tests/test_root_loop_integration.py::TestSubcallRepairJsonWiring"
            "::test_schema_response_is_repair_parsed"
        ],
    ),
    (
        "R5 parse_stderr is not called by the root loop",
        "src/rlm_local/root_loop.py",
        "                    err_result = self._parser.parse_stderr(root_text, repl_result.stderr)",
        "                    err_result = None",
        [
            "tests/test_root_loop_integration.py::TestStderrSelfCorrectionWiring"
            "::test_error_then_recovery_finishes_with_the_corrected_answer"
        ],
    ),
    (
        "R5 parse() resets the error streak again",
        "src/rlm_local/parser.py",
        "            result.blocks = [b.strip() for b in blocks]\n"
        "            self._normalize(result)\n"
        "            self._consecutive_nudges = 0\n"
        "            return result",
        "            result.blocks = [b.strip() for b in blocks]\n"
        "            self._normalize(result)\n"
        "            self._consecutive_nudges = 0\n"
        "            self._consecutive_errors = 0\n"
        "            return result",
        [
            "tests/test_parser.py::TestParseStderr"
            "::test_parse_does_not_reset_the_error_streak"
        ],
    ),
    # ── R6: empty answers ──────────────────────────────────────────────────
    (
        "R6 empty submissions fall back to truthiness",
        "src/rlm_local/root_loop.py",
        "                    if repl_result.final_answer.strip() == \"\":",
        "                    if False:",
        [
            "tests/test_root_loop_integration.py::TestAnswerFinalizationGuards"
            "::test_empty_submission_is_nudged_then_real_answer_wins"
        ],
    ),
    (
        "R6 empty-answer nudges are unbounded",
        "src/rlm_local/root_loop.py",
        "                if empty_answer_nudges <= cfg.max_consecutive_nudges:",
        "                if True:",
        [
            "tests/test_root_loop_integration.py::TestAnswerFinalizationGuards"
            "::test_empty_answer_nudge_budget_is_respected"
        ],
    ),
    # ── R7: template discipline ────────────────────────────────────────────
    (
        "R7 a template constant becomes unreferenced",
        "src/rlm_local/templates.py",
        "NUDGE_STDERR_ERROR = (",
        "NEVER_EMITTED_TEMPLATE = \"this string is not referenced anywhere\"\n\n"
        "NUDGE_STDERR_ERROR = (",
        ["tests/test_templates.py::TestTemplateDiscipline::test_no_dead_templates"],
    ),
    (
        "R7 the REPL timeout error goes back to an inline f-string",
        "src/rlm_local/repl.py",
        "        message = CELL_TIMEOUT_ERROR.format(timeout=self._cell_timeout)",
        "        message = f\"Error: REPL timed out after {self._cell_timeout}s\"",
        [
            "tests/test_templates.py::TestNoInlineHarnessStrings"
            "::test_pattern_absent_from_src[\"Error: REPL timed out]"
        ],
    ),
    # ── R8: vault few-shots ────────────────────────────────────────────────
    (
        "R8 load_fewshots_from_vault ignores the vault again",
        "src/rlm_local/prompts.py",
        "        pairs = parse_fewshot_body(page.body or \"\")",
        "        pairs = []",
        [
            "tests/test_prompts.py::TestVaultFewShots"
            "::test_query_answer_page_yields_a_pair"
        ],
    ),
    (
        "R8 the few-shot prompt budget is not enforced",
        "src/rlm_local/prompts.py",
        "        if sum(len(content) for _, content in pairs) > max_pair_chars:\n            continue",
        "        if False:\n            continue",
        [
            "tests/test_prompts.py::TestVaultFewShots"
            "::test_oversized_fewshot_is_skipped"
        ],
    ),
    # ── R9: backend robustness ─────────────────────────────────────────────
    (
        "R9 endpoint normalization drops the /v1 append",
        "src/rlm_local/model_backend.py",
        "    if not path.endswith(\"/v1\"):\n        path = f\"{path}/v1\"",
        "    if False:\n        path = f\"{path}/v1\"",
        ["tests/test_model_backend.py::TestEndpointNormalization"],
    ),
    (
        "R9 extraction goes back to raw indexing",
        "src/rlm_local/model_backend.py",
        "        return _extract_content(data, status=resp.status_code, body=resp.text)",
        "        return data[\"choices\"][0][\"message\"][\"content\"]",
        ["tests/test_model_backend.py::TestResponseGuarding"],
    ),
    (
        "R9 retries are disabled",
        "src/rlm_local/model_backend.py",
        "        stop=stop_after_attempt(3),  # initial attempt + 2 retries",
        "        stop=stop_after_attempt(1),",
        ["tests/test_model_backend.py::TestRetry"],
    ),
    (
        "R18 the non-loopback TLS warning is removed",
        "src/rlm_local/model_backend.py",
        "                if urlsplit(endpoint).scheme == \"https\" and not _is_loopback(endpoint):",
        "                if False:",
        [
            "tests/test_model_backend.py::TestTLSVerificationWarning"
            "::test_warns_for_non_loopback_with_verification_off"
        ],
    ),
    # ── R10: the cap promise ───────────────────────────────────────────────
    (
        "R10 the REPL cap no longer comes from the profile",
        "src/rlm_local/root_loop.py",
        "            stdout_cap=cfg.repl_output_char_cap,",
        "            stdout_cap=256 * 1024,",
        [
            "tests/test_root_loop_integration.py::TestOutputCapPromise"
            "::test_prompt_cap_and_enforced_cap_agree"
        ],
    ),
    # ── R21 / R22: web ─────────────────────────────────────────────────────
    (
        "R21 a route loses its auth dependency",
        "src/rlm_web/app.py",
        "@app.get(\"/chat\", response_class=HTMLResponse, dependencies=[Depends(require_auth)])",
        "@app.get(\"/chat\", response_class=HTMLResponse)",
        [
            "tests/test_web.py::TestAuthCoverageByConstruction"
            "::test_every_api_route_declares_auth"
        ],
    ),
    (
        "R21 a missing peer address is trusted again",
        "src/rlm_web/app.py",
        "    if client_host is None:",
        "    if False:",
        [
            "tests/test_web.py::TestAuthFailClosed::test_authorize_helper_table",
            "tests/test_web.py::TestAuthFailClosed"
            "::test_testclient_host_is_rejected_without_the_env_optin",
        ],
    ),
    (
        "R21 login falls back to a non-constant-time compare",
        "src/rlm_web/app.py",
        "    if secrets.compare_digest(token, expected):",
        "    if token == expected:",
        ["tests/test_web.py::TestLoginPolicy::test_login_uses_constant_time_comparison"],
    ),
    (
        "R21 login accepts any token when none is configured",
        "src/rlm_web/app.py",
        "    if not expected:",
        "    if False:",
        ["tests/test_web.py::TestLoginPolicy::test_login_without_configured_token_is_400"],
    ),
    (
        "R22 the job store is unbounded again",
        "src/rlm_web/app.py",
        "_jobs: dict[str, dict[str, Any]] = _BoundedLRU(MAX_STORED_ENTRIES)",
        "_jobs: dict[str, dict[str, Any]] = {}",
        ["tests/test_web.py::TestBoundedStores::test_job_and_chat_stores_are_bounded"],
    ),
    (
        "R22 the trajectory logger loses its lock",
        "src/rlm_local/logger.py",
        "        line = json.dumps(record, ensure_ascii=False) + \"\\n\"\n"
        "        with self._lock:\n"
        "            with open(self._path, \"a\", encoding=\"utf-8\") as f:\n"
        "                f.write(line)",
        "        line = json.dumps(record, ensure_ascii=False) + \"\\n\"\n"
        "        with open(self._path, \"a\", encoding=\"utf-8\") as f:\n"
        "            f.write(line)",
        ["tests/test_logger.py::TestThreadSafety::test_concurrent_writes_do_not_overlap"],
    ),
    (
        "R22 the logger writes to the shared temp dir again",
        "src/rlm_local/logger.py",
        "            ts = time.time_ns()",
        "            import tempfile\n"
        "            path = Path(tempfile.gettempdir()) / \"rlm_trajectory.jsonl\"\n"
        "            ts = time.time_ns()",
        ["tests/test_logger.py::TestDefaultLocation::test_logger_does_not_use_the_system_temp_dir"],
    ),
    # ── R26: the P4 disagreement diagnostic (2026-09-11 assessment §3.1) ────
    (
        "R26 the P4 diagnostic is never produced",
        "src/rlm_local/model_check.py",
        "    if has_answer_ready and not has_final_answer:",
        "    if False:",
        [
            "tests/test_model_check.py::TestP4SubmissionDiagnostics"
            "::test_probe_scores_zero_and_attaches_the_diagnostic",
        ],
    ),
    (
        "R26 every fence tag counts as executable",
        "src/rlm_local/model_check.py",
        "                    \"executable\": region[\"tag\"] in _EXECUTABLE_FENCE_TAGS,",
        "                    \"executable\": True,",
        [
            "tests/test_model_check.py::TestSubmissionSiteDetection"
            "::test_other_tags_are_marked_unexecutable",
            "tests/test_model_check.py::TestP4SubmissionDiagnostics"
            "::test_unexecutable_fence_tag_is_named",
        ],
    ),
    (
        "R26 an executed block stops outranking a stray prose mention",
        "src/rlm_local/model_check.py",
        "    executed = [c for c in candidates if c[1][\"executable\"]]",
        "    executed = []",
        [
            "tests/test_model_check.py::TestP4SubmissionDiagnostics"
            "::test_an_executed_block_outranks_a_stray_prose_mention",
            "tests/test_model_check.py::TestP4SubmissionDiagnostics"
            "::test_raised_block_is_reported_with_the_exception",
        ],
    ),
    # ── R27: battery weighting (2026-09-11 assessment §3.1, §6) ────────────
    (
        "R27 the default battery reverts to the saturated P1-heavy weights",
        "src/rlm_local/model_check.py",
        "    \"default\": {\n"
        "        \"P1\": 10,\n"
        "        \"P2\": 15,\n"
        "        \"P3\": 15,\n"
        "        \"P4\": 20,\n"
        "        \"P5\": 10,\n"
        "        \"P6\": 20,",
        "    \"default\": {\n"
        "        \"P1\": 20,\n"
        "        \"P2\": 15,\n"
        "        \"P3\": 15,\n"
        "        \"P4\": 15,\n"
        "        \"P5\": 10,\n"
        "        \"P6\": 15,",
        [
            "tests/test_model_check.py::TestWeightProfiles"
            "::test_default_moves_weight_onto_the_discriminating_probes",
            "tests/test_model_check.py::TestWeightProfiles"
            "::test_protocol_only_model_is_penalised_harder_under_the_new_default",
            "tests/test_model_check.py::TestWeightProfiles"
            "::test_voluntary_submitter_separates_further_under_the_new_default",
        ],
    ),
    (
        "R27 a score no longer records the scale it came from",
        "src/rlm_local/model_check.py",
        "        \"weight_profile\": weight_profile,",
        "        \"weight_profile\": \"unknown\",",
        [
            "tests/test_model_check.py::TestCheckModelScaleIsRecorded"
            "::test_result_records_the_profile_and_the_resolved_weights",
        ],
    ),
    # ── R28: cross-origin (CSRF) protection (2026-09-10 validation §8.1) ───
    (
        "R28 a state-changing route loses its origin dependency",
        "src/rlm_web/app.py",
        "@app.post(\"/jobs\", response_class=HTMLResponse,\n"
        "          dependencies=[Depends(require_same_origin), Depends(require_auth)])",
        "@app.post(\"/jobs\", response_class=HTMLResponse,\n"
        "          dependencies=[Depends(require_auth)])",
        [
            "tests/test_web.py::TestOriginCoverageByConstruction"
            "::test_every_state_changing_route_is_checked",
            "tests/test_web.py::TestCrossOriginRequests"
            "::test_cross_origin_post_is_rejected_with_a_live_session",
        ],
    ),
    (
        "R28 logout loses its origin dependency",
        "src/rlm_web/app.py",
        "@app.get(\"/logout\", dependencies=[Depends(require_same_origin)])",
        "@app.get(\"/logout\")",
        [
            "tests/test_web.py::TestCrossOriginRequests"
            "::test_logout_is_not_a_free_for_all",
            "tests/test_web.py::TestOriginCoverageByConstruction"
            "::test_every_state_changing_route_is_checked",
        ],
    ),
    (
        "R28 a mismatched origin is accepted (fail open)",
        "src/rlm_web/app.py",
        "    if supplied == own_origin:\n"
        "        return True, \"\"\n"
        "    return False, f\"origin {supplied} does not match this server ({own_origin})\"",
        "    return True, \"\"",
        [
            "tests/test_web.py::TestOriginDecisionTable::test_policy",
            "tests/test_web.py::TestCrossOriginRequests"
            "::test_cross_origin_post_is_rejected_with_a_live_session",
        ],
    ),
    (
        "R28 Origin: null is treated as a client that claims no origin",
        "src/rlm_web/app.py",
        "    return _normalize_origin(raw)",
        "    return _normalize_origin(raw) or None",
        [
            "tests/test_web.py::TestCrossOriginRequests::test_origin_null_fails_closed",
        ],
    ),
    (
        "R28 the allowlist implicitly trusts this server again",
        "src/rlm_web/app.py",
        "    if allowlist:\n        if supplied in allowlist:\n            return True, \"\"",
        "    if allowlist:\n"
        "        if supplied in allowlist or supplied == own_origin:\n"
        "            return True, \"\"",
        [
            "tests/test_web.py::TestCrossOriginRequests"
            "::test_allowlist_admits_the_named_origin_only",
            "tests/test_web.py::TestOriginDecisionTable::test_policy",
        ],
    ),
    (
        "R28 strict mode accepts a request that claims no origin",
        "src/rlm_web/app.py",
        "    if supplied is None:\n"
        "        if mode == \"strict\":\n"
        "            return False, \"an Origin or Referer header is required in strict mode\"\n"
        "        return True, \"\"",
        "    if supplied is None:\n        return True, \"\"",
        [
            "tests/test_web.py::TestCrossOriginRequests::test_strict_mode_demands_an_origin",
            "tests/test_web.py::TestOriginDecisionTable::test_policy",
        ],
    ),
]


def run_tests(node_ids: list[str]) -> int:
    cmd = [
        str(PY), "-m", "pytest", *node_ids,
        "-p", "no:cacheprovider", "-q", "--no-header", "-x", "--timeout=180",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="substring filter on the label")
    args = ap.parse_args()

    failures: list[str] = []
    checked = 0
    for label, rel, old, new, nodes in MUTATIONS:
        if args.only and args.only.lower() not in label.lower():
            continue
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if old not in original:
            print(f"SKIP  {label}: mutation target not found in {rel}")
            failures.append(f"{label} (target not found)")
            continue

        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        try:
            rc = run_tests(nodes)
        finally:
            path.write_text(original, encoding="utf-8")

        checked += 1
        if rc == 0:
            print(f"VACUOUS  {label}: tests still passed with the guard removed")
            failures.append(label)
        else:
            print(f"ok       {label}: went red as required")

    print(f"\nchecked {checked} guards; {len(failures)} problem(s)")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
