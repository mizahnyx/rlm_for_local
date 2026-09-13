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
    (
        "R26 the string/comment lexer stops distinguishing text from code",
        "src/rlm_local/model_check.py",
        "    if state == \"comment\":\n"
        "        return \"comment\"\n"
        "    if state in (\"string\", \"triple\"):\n"
        "        return \"string\"\n"
        "    return None",
        "    return None",
        [
            "tests/test_model_check.py::TestQuotedSubmissionText"
            "::test_placement_within_a_block",
            "tests/test_model_check.py::TestQuotedSubmissionText"
            "::test_a_fenced_print_of_the_line_is_diagnosed_as_text",
            "tests/test_model_check.py::TestQuotedSubmissionText"
            "::test_a_commented_out_line_is_diagnosed_as_text",
        ],
    ),
    (
        "R26 the text/code verdict is computed but never recorded on a site",
        "src/rlm_local/model_check.py",
        "                    \"in_text\": _text_kind_at(body, match.start() - region[\"body_start\"]),",
        "                    \"in_text\": None,",
        [
            "tests/test_model_check.py::TestQuotedSubmissionText"
            "::test_a_fenced_print_of_the_line_is_diagnosed_as_text",
            "tests/test_model_check.py::TestQuotedSubmissionText"
            "::test_text_outranks_a_traceback_in_the_same_cell",
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
    # ── R29: P4 multi-trial sampling (2026-09-11 live confirmation §6) ─────
    (
        "R29 P4 collapses back to a single trial",
        "src/rlm_local/model_check.py",
        "        for query, context in P4_QUERIES[:trials]",
        "        for query, context in P4_QUERIES[:1]",
        [
            "tests/test_model_check.py::TestProbeP4::test_runs_three_trials_by_default",
            "tests/test_model_check.py::TestP4SubmissionDiagnostics"
            "::test_every_contradictory_trial_gets_its_own_diagnostic",
        ],
    ),
    (
        "R29 one voluntary trial is enough to pass P4",
        "src/rlm_local/model_check.py",
        "    majority = len(outcomes) // 2 + 1",
        "    majority = 1",
        [
            "tests/test_model_check.py::TestP4TrialScoring::test_majority_is_more_than_half",
            "tests/test_model_check.py::TestP4TrialScoring::test_scale",
        ],
    ),
    (
        "R29 the documented partial credit for a forced submission is dropped",
        "src/rlm_local/model_check.py",
        "_FORCED_CREDIT = 8 / 15",
        "_FORCED_CREDIT = 0.0",
        [
            "tests/test_model_check.py::TestP4TrialScoring::test_scale",
        ],
    ),
    (
        "R29 an unusable trial count silently samples less",
        "src/rlm_local/model_check.py",
        "    if 1 <= requested <= DEFAULT_P4_TRIALS:\n        return requested\n    return DEFAULT_P4_TRIALS",
        "    if 1 <= requested <= DEFAULT_P4_TRIALS:\n        return requested\n    return 1",
        [
            "tests/test_model_check.py::TestP4TrialCount"
            "::test_an_unusable_value_falls_back_to_more_trials_not_fewer",
        ],
    ),
    # ── VD2: the cell-end `answer` state (2026-09-12 roadmap item 2) ────────
    (
        "VD2 the REPL stops reporting the cell-end answer state",
        "src/rlm_local/repl.py",
        "            answer_state = _answer_state()",
        "            answer_state = None",
        [
            "tests/test_model_check.py::TestCellEndAnswerState"
            "::test_a_real_cell_reports_its_final_answer_state",
            "tests/test_model_check.py::TestCellEndAnswerState"
            "::test_an_intact_scaffold_sharpens_the_not_reached_wording",
        ],
    ),
    (
        "VD2 a rebound answer is no longer diagnosed",
        "src/rlm_local/model_check.py",
        "            if not state.get(\"is_dict\"):",
        "            if False:",
        [
            "tests/test_model_check.py::TestCellEndAnswerState"
            "::test_a_rebound_answer_is_diagnosed_as_rebound",
        ],
    ),
    (
        "VD2 the worker's submission read is unguarded again",
        "src/rlm_local/repl.py",
        "            except Exception:\n"
        "                # Model code owns `answer` once a cell has run, and this read is",
        "            except ZeroDivisionError:\n"
        "                # Model code owns `answer` once a cell has run, and this read is",
        [
            "tests/test_model_check.py::TestCellEndAnswerState"
            "::test_a_pathological_answer_cannot_break_the_result",
        ],
    ),
    # ── BS1/BS2: P3 measures recovery, not absence of failure (roadmap 4) ──
    (
        "BS1 a model that never executes a cell gets full credit again",
        "src/rlm_local/model_check.py",
        "    if not repl_entries:\n"
        "        return {\n"
        "            \"score\": 0,\n"
        "            \"max_score\": 15,\n"
        "            \"evidence\": [\n"
        "                \"No cell executed — the model never ran code, so recovery was \"\n"
        "                \"never exercised. Emitting nothing is not recovering.\"\n"
        "            ],\n"
        "            \"passed\": False,\n"
        "        }",
        "    if not repl_entries:\n"
        "        return {\n"
        "            \"score\": 15,\n"
        "            \"max_score\": 15,\n"
        "            \"evidence\": [\"No stderr events — no recovery needed.\"],\n"
        "            \"passed\": True,\n"
        "        }",
        [
            "tests/test_model_check.py::TestProbeP3"
            "::test_a_model_that_never_executes_scores_zero",
        ],
    ),
    (
        "BS2 the recovery scan ignores turn order again",
        "src/rlm_local/model_check.py",
        "        if _turn_after(r.get(\"turn\"), first_failure_turn)\n"
        "        and not (r.get(\"stderr\") or \"\").strip()",
        "        if not (r.get(\"stderr\") or \"\").strip()",
        [
            "tests/test_model_check.py::TestProbeP3"
            "::test_a_pre_error_grep_no_longer_counts_as_recovery",
        ],
    ),
    (
        "BS2 a cell at the failing turn counts as coming after it",
        "src/rlm_local/model_check.py",
        "    return candidate > reference",
        "    return candidate >= reference",
        [
            "tests/test_model_check.py::TestTurnOrdering"
            "::test_turn_after_is_strictly_later",
        ],
    ),
    (
        "BS3 an unterminated helper call is scored valid again",
        "src/rlm_local/model_check.py",
        "                valid = bool(call_text.strip()) and _balanced_parens(call_text)",
        "                valid = _balanced_parens(call_text)",
        [
            "tests/test_model_check.py::TestProbeP2NowRejectsUnterminatedCalls"
            "::test_an_unterminated_call_is_scored_invalid",
        ],
    ),
    # ── DG1/DG4: design §5.3, implemented 2026-09-12 (roadmap item 6) ──────
    (
        "DG1 model code gets the full builtins again",
        "src/rlm_local/repl.py",
        "_BLOCKED_BUILTINS = frozenset(\n"
        "    {\"input\", \"eval\", \"exec\", \"compile\", \"globals\", \"locals\", \"breakpoint\"}\n"
        ")",
        "_BLOCKED_BUILTINS = frozenset()",
        [
            "tests/test_repl.py::TestDesignSection53Scaffold"
            "::test_model_code_cannot_use_the_dynamic_execution_family",
        ],
    ),
    (
        "DG4 the scaffold is never restored after a cell",
        "src/rlm_local/repl.py",
        "            scaffold_repaired = _restore_scaffold()",
        "            scaffold_repaired = []",
        [
            "tests/test_repl.py::TestDesignSection53Scaffold"
            "::test_a_rebound_answer_is_repaired_for_the_next_cell",
            "tests/test_repl.py::TestDesignSection53Scaffold"
            "::test_a_helper_overwritten_by_a_non_callable_is_repaired",
        ],
    ),
    (
        "DG4 the cell-end state is read after the scaffold is repaired",
        "src/rlm_local/repl.py",
        "                \"answer_state\": answer_state,",
        "                \"answer_state\": _answer_state(),",
        [
            "tests/test_repl.py::TestDesignSection53Scaffold"
            "::test_a_rebound_answer_is_repaired_for_the_next_cell",
        ],
    ),
    # ── R23: `schema` → `schema_version` (roadmap item 7) ──────────────────
    (
        "R23 the old frontmatter key stops parsing",
        "src/rlm_kernel/schema.py",
        "        validation_alias=AliasChoices(\"schema_version\", \"schema\"),",
        "        validation_alias=AliasChoices(\"schema_version\",),",
        [
            "tests/rlm_kernel/test_schema.py::TestSchemaVersionRename"
            "::test_both_key_names_parse_identically",
            "tests/rlm_kernel/test_vault.py::TestParsePage::test_parse_and_roundtrip",
        ],
    ),
    (
        "R23 serialization writes the old key again",
        "src/rlm_kernel/schema.py",
        "            \"schema_version\", \"id\", \"kind\", \"name\", \"title\", \"summary\",",
        "            \"schema\", \"id\", \"kind\", \"name\", \"title\", \"summary\",",
        [
            "tests/rlm_kernel/test_schema.py::TestSchemaVersionRename"
            "::test_serialization_writes_the_new_key",
        ],
    ),
    (
        "R23 the migration does not rewrite the key",
        "src/rlm_kernel/schema.py",
        "            + \"schema_version:\" + match.group(1) + match.group(2)",
        "            + \"schema:\" + match.group(1) + match.group(2)",
        [
            "tests/rlm_kernel/test_schema.py::TestVaultSchemaMigration"
            "::test_migration_rewrites_only_the_legacy_page",
            "tests/rlm_kernel/test_schema.py::TestSchemaKeyMigration"
            "::test_renames_the_key_and_leaves_everything_else_alone",
        ],
    ),
    # ── CL1: search stops returning retired pages (roadmap item 8) ─────────
    (
        "CL1 search returns every status again",
        "src/rlm_kernel/index.py",
        "        wanted_statuses = [\"active\"] if statuses is None else list(statuses)",
        "        wanted_statuses = [] if statuses is None else list(statuses)",
        [
            "tests/rlm_kernel/test_index.py::TestSearchStatusPredicate"
            "::test_a_deprecated_page_is_not_returned_by_default",
            "tests/rlm_kernel/test_kernel_cli.py::TestSearchStatusFilter"
            "::test_a_deprecated_page_is_hidden_from_its_own_query",
        ],
    ),
    (
        "CL1 a search card forgets the page's status",
        "src/rlm_kernel/search.py",
        "        \"status\": row.get(\"status\", \"active\"),",
        "        \"status\": \"active\",",
        [
            "tests/rlm_kernel/test_kernel_cli.py::TestSearchStatusFilter"
            "::test_status_widens_the_search_to_the_history",
        ],
    ),
    # ── DG8/DG9: inert and dead code (roadmap item 9) ─────────────────────
    (
        "DG9 the helper summaries ignore their cap",
        "src/rlm_kernel/repl_bridge.py",
        "        for page in pages[:limit]:",
        "        for page in pages:",
        [
            "tests/rlm_kernel/test_repl_bridge.py::TestHelperDefinitions"
            "::test_helper_summaries_are_capped",
        ],
    ),
    (
        "DG9 the helper summaries always walk the vault",
        "src/rlm_kernel/repl_bridge.py",
        "        lines: list[str] = []\n"
        "        try:\n"
        "            if self.index_path.exists():",
        "        lines: list[str] = []\n"
        "        try:\n"
        "            if False:",
        [
            "tests/rlm_kernel/test_repl_bridge.py::TestHelperDefinitions"
            "::test_helper_summaries_use_the_index_when_one_exists",
        ],
    ),
    (
        "DG9 the prompt builds the helper section itself again",
        "src/rlm_local/prompts.py",
        "        if bridge is not None:\n            helper_lines = bridge.get_helper_summaries()",
        "        if False:\n            helper_lines = bridge.get_helper_summaries()",
        [
            "tests/test_prompts.py::TestHelperSectionSourceOfTruth"
            "::test_the_prompt_asks_the_bridge_for_helper_lines",
        ],
    ),
    # ── CL3: the worker's messages come from templates.py (roadmap item 10) ─
    (
        "CL3 the worker carries its own copies of the harness messages",
        "src/rlm_local/repl.py",
        "    \"_MSG = \" + json.dumps(WORKER_MESSAGES) + \"\\n\\n\"",
        "    \"_MSG = \" + json.dumps({\n"
        "        \"invalid_regex\": \"Error: invalid regex: {error}\",\n"
        "        \"no_harness_response\": \"Error: no response from harness\",\n"
        "        \"search_no_results\": \"(no results)\",\n"
        "        \"propose_failed\": \"Error: propose failed\",\n"
        "    }) + \"\\n\\n\"",
        [
            "tests/test_templates.py::TestNoInlineHarnessStrings"
            "::test_pattern_absent_from_src",
            "tests/test_templates.py::TestTemplateDiscipline::test_no_dead_templates",
        ],
    ),
    (
        "CL3 the bridge stops using the shared no-results message",
        "src/rlm_kernel/repl_bridge.py",
        "        if not results:\n            return WORKER_SEARCH_NO_RESULTS",
        "        if not results:\n            return \"no results\"",
        [
            "tests/rlm_kernel/test_repl_bridge.py::TestSearchProxy"
            "::test_search_without_match_reports_no_results",
        ],
    ),
    # ── CL4: slot-subset validation at the gate (roadmap item 11) ─────────
    (
        "CL4 unknown slots are accepted again",
        "src/rlm_kernel/gate.py",
        "    unknown = sorted(set(slots) - known)",
        "    unknown = []",
        [
            "tests/rlm_kernel/test_gate.py::TestSlotSubsetValidation"
            "::test_a_formatted_contract_with_an_unknown_slot_is_rejected",
            "tests/rlm_kernel/test_gate.py::TestSlotSubsetValidation"
            "::test_promotion_refuses_the_page",
        ],
    ),
    (
        "CL4 every page is slot-checked, including the shipped templates",
        "src/rlm_kernel/gate.py",
        "    candidates = {page.path, f\"{page.frontmatter.kind.value}/{page.name}.md\"}\n"
        "    if not (candidates & _formatted_contract_paths()):\n"
        "        return",
        "    if False:\n        return",
        [
            "tests/rlm_kernel/test_gate.py::TestSlotSubsetValidation"
            "::test_a_template_page_with_its_own_slots_is_not_flagged",
            "tests/rlm_kernel/test_gate.py::TestSlotSubsetValidation"
            "::test_every_seeded_page_still_validates",
        ],
    ),
    # ── RO2: the corpus mount is read-only by construction (roadmap 7) ─────
    (
        "RO2 path containment is not enforced",
        "src/rlm_kernel/mounts.py",
        "        if not candidate.is_relative_to(self._root):",
        "        if False:",
        [
            "tests/rlm_kernel/test_mounts.py::TestContainment"
            "::test_escaping_paths_are_refused",
            "tests/rlm_kernel/test_mounts.py::TestContainment"
            "::test_the_outside_content_is_never_returned",
        ],
    ),
    (
        "RO2 reads are unbounded",
        "src/rlm_kernel/mounts.py",
        "        if max_bytes is not None:\n"
        "            return _BoundedReader(handle, max_bytes)\n"
        "        return handle",
        "        return handle",
        [
            "tests/rlm_kernel/test_mounts.py::TestStreaming"
            "::test_max_bytes_caps_the_read",
            "tests/rlm_kernel/test_mounts.py::TestStreaming"
            "::test_the_cap_holds_for_chunked_reads",
        ],
    ),
    (
        "RO2 derived state may live inside the corpus",
        "src/rlm_kernel/mounts.py",
        "    if derived == corpus or derived.is_relative_to(corpus):",
        "    if False:",
        [
            "tests/rlm_kernel/test_mounts.py::TestDerivedStatePlacement"
            "::test_a_vault_inside_the_corpus_is_refused",
        ],
    ),
    (
        "RO2 the mount grows a write method",
        "src/rlm_kernel/mounts.py",
        "    def exists(self, rel: str) -> bool:",
        "    def touch(self, rel: str) -> None:\n"
        "        (self._root / rel).touch()\n"
        "\n"
        "    def exists(self, rel: str) -> bool:",
        [
            "tests/rlm_kernel/test_mounts.py::TestReadOnlyByConstruction"
            "::test_the_mount_has_no_write_methods",
            "tests/rlm_kernel/test_mounts.py::TestReadOnlyByConstruction"
            "::test_the_module_has_no_write_shaped_api",
        ],
    ),
    # ── RO3/RO4: corpus access is read-only, bounded and index-backed ──────
    (
        "RO4 containment stops refusing paths that leave the corpus",
        "src/rlm_kernel/mounts.py",
        "            self._resolve(rel)\n"
        "            return True\n"
        "        except ReadOnlyViolation:\n"
        "            return False",
        "            return True\n"
        "        except ReadOnlyViolation:\n"
        "            return False",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeHandlerMessages"
            "::test_read_refuses_to_escape_the_corpus",
            "tests/test_corpus_repl.py::TestCorpusHelpersInALiveCell"
            "::test_a_cell_cannot_read_outside_the_corpus",
        ],
    ),
    (
        "RO4 corpus_list walks the subtree instead of one directory",
        "src/rlm_kernel/corpus.py",
        "            shown = self.mount.iter_children(\n"
        "                target, max_entries=_bounded(limit, LIST_LIMIT_MAX)\n"
        "            )",
        "            shown = self.mount.iter_entries(\n"
        "                target, max_entries=_bounded(limit, LIST_LIMIT_MAX)\n"
        "            )",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeWithoutAnIndex"
            "::test_list_still_works_from_the_mount",
            "tests/test_corpus_repl.py::TestCorpusHelpersInALiveCell"
            "::test_a_cell_can_search_and_read_the_corpus",
        ],
    ),
    (
        "RO4 corpus_find walks the tree when there is no index",
        "src/rlm_kernel/corpus.py",
        "        if self.index is None:\n"
        "            return CORPUS_NO_INDEX\n"
        "        hits = self.index.find(query, limit=limit, kind=kind, under=under)",
        "        hits = (\n"
        "            self.index.find(query, limit=limit, kind=kind, under=under)\n"
        "            if self.index is not None else list(self.mount.iter_entries(under))\n"
        "        )",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeWithoutAnIndex"
            "::test_find_never_walks_when_the_index_is_missing",
        ],
    ),
    (
        "RO4 find stops escaping LIKE wildcards",
        "src/rlm_kernel/corpus.py",
        '        params: list[Any] = [f"%{_escape_like(needle)}%"]',
        '        params: list[Any] = [f"%{needle}%"]',
        [
            "tests/rlm_kernel/test_corpus.py::TestQueries"
            "::test_find_escapes_like_wildcards",
        ],
    ),
    (
        "RO4 the result caps stop being applied",
        "src/rlm_kernel/corpus.py",
        "    return min(value, maximum)",
        "    return value",
        [
            "tests/rlm_kernel/test_corpus.py::TestQueries"
            "::test_bounded_helper_is_not_vacuous",
            "tests/rlm_kernel/test_corpus.py::TestBridgeHandlerMessages"
            "::test_read_cap_cannot_be_raised_past_the_maximum",
        ],
    ),
    (
        "RO4 an index inside the corpus is accepted",
        "src/rlm_kernel/corpus.py",
        "        assert_derived_outside_corpus(corpus_root, index_path)\n"
        "        return cls(index_path)",
        "        return cls(index_path)",
        [
            "tests/rlm_kernel/test_corpus.py::TestDerivedStateStaysOutside"
            "::test_an_index_inside_the_corpus_is_refused",
            "tests/test_cli_corpus.py::TestCorpusIndex"
            "::test_index_inside_the_corpus_is_refused",
        ],
    ),
    (
        "RO4 a bridge accepts an index inside the corpus",
        "src/rlm_kernel/corpus.py",
        "            assert_derived_outside_corpus(mount.root, candidate)",
        "            pass",
        [
            "tests/rlm_kernel/test_corpus.py::TestDerivedStateStaysOutside"
            "::test_a_bridge_refuses_an_index_inside_the_corpus",
        ],
    ),
    (
        "RO3 the index build starts reading file contents",
        "src/rlm_kernel/corpus.py",
        "            for entry in mount.iter_entries():\n"
        "                path = entry.rel",
        "            for entry in mount.iter_entries():\n"
        "                path = entry.rel\n"
        "                mount.read_bytes(path, max_bytes=1)",
        [
            "tests/rlm_kernel/test_corpus.py::TestIndexReadsNamesNotContents"
            "::test_build_never_opens_a_file",
        ],
    ),
    (
        "RO4 the REPL stops dispatching the corpus verbs",
        "src/rlm_local/repl.py",
        '        elif msg_type.startswith("corpus_"):',
        "        elif False:",
        [
            "tests/test_corpus_repl.py::TestParentDispatchesCorpusVerbs"
            "::test_find_reaches_the_bridge",
            "tests/test_corpus_repl.py::TestParentDispatchesCorpusVerbs"
            "::test_list_stat_read_and_count_reach_the_bridge",
        ],
    ),
    (
        "RO4 the worker stops exporting a corpus verb",
        "src/rlm_local/repl.py",
        "\ncorpus_read = _harness_corpus_read\n",
        "\ncorpus_read = None\n",
        [
            "tests/test_corpus_repl.py::TestWorkerDefinesTheCorpusVerbs"
            "::test_the_worker_defines_and_exports_the_verb[corpus_read]",
        ],
    ),
    (
        "RO4 a clobbered corpus verb stops being repaired",
        "src/rlm_local/repl.py",
        "                       'corpus_find', 'corpus_list', 'corpus_stat', 'corpus_read',\n"
        "                       'corpus_count'):",
        "                       'corpus_find'):",
        [
            "tests/test_corpus_repl.py::TestWorkerDefinesTheCorpusVerbs"
            "::test_a_clobbered_verb_is_repaired[corpus_list]",
        ],
    ),
    (
        "RO4 the run stops advertising the corpus to the model",
        "src/rlm_local/root_loop.py",
        "        if self._corpus_bridge is not None:\n"
        "            from rlm_local.prompts import build_system_prompt, corpus_helpers_section",
        "        if False:\n"
        "            from rlm_local.prompts import build_system_prompt, corpus_helpers_section",
        [
            "tests/test_corpus_repl.py::TestThePromptMatchesTheWorker"
            "::test_the_system_prompt_names_the_corpus_and_its_helpers",
        ],
    ),
    (
        "RO4 ask stops handing the corpus to the run",
        "src/rlm_local/cli.py",
        "            corpus_bridge=corpus_bridge,\n",
        "            corpus_bridge=None,\n",
        [
            "tests/test_cli_corpus.py::TestAskWithACorpus"
            "::test_ask_with_only_a_corpus_uses_the_stub_context",
        ],
    ),
    (
        "RO3 --progress-every goes inert again",
        "src/rlm_kernel/corpus.py",
        "            if not final and progress_every and count - reported < progress_every:\n"
        "                return",
        "            if False:\n"
        "                return",
        [
            "tests/rlm_kernel/test_corpus.py::TestIndexReadsNamesNotContents"
            "::test_progress_is_reported_by_interval_not_by_batch",
        ],
    ),
    (
        "RO3 stored path bytes become lossy",
        "src/rlm_kernel/corpus.py",
        "def path_bytes(rel: str) -> bytes:\n"
        "    \"\"\"The exact bytes of a path, whatever its encoding.\"\"\"\n"
        "    return rel.encode(\"utf-8\", \"surrogateescape\")",
        "def path_bytes(rel: str) -> bytes:\n"
        "    \"\"\"The exact bytes of a path, whatever its encoding.\"\"\"\n"
        "    return rel.encode(\"utf-8\", \"replace\")",
        [
            "tests/rlm_kernel/test_corpus.py::TestNamesThatAreNotUtf8"
            "::test_a_damaged_path_is_displayed_safely_and_stored_exactly",
        ],
    ),
    (
        "RO3 a not-UTF-8 path stops being recoverable for reading",
        "src/rlm_kernel/corpus.py",
        "        raw = self.index.raw_for(rel)\n"
        "        if raw is None:\n"
        "            return None",
        "        raw = self.index.raw_for(rel)\n"
        "        if raw is None or True:\n"
        "            return None",
        [
            "tests/rlm_kernel/test_corpus.py::TestNamesThatAreNotUtf8"
            "::test_a_damaged_file_can_still_be_read",
        ],
    ),
    (
        "RO3 the secondary indexes are not restored after a bulk load",
        "src/rlm_kernel/corpus.py",
        "            for statement in _SECONDARY_INDEXES:\n"
        "                cursor.execute(statement)\n"
        "            self._conn.commit()",
        "            pass",
        [
            "tests/rlm_kernel/test_corpus.py"
            "::TestProgressIsCheckpointedAndCompletenessIsRecorded"
            "::test_the_secondary_indexes_exist_after_a_bulk_load",
        ],
    ),
    (
        "RO3 batches stop being checkpointed during the walk",
        "src/rlm_kernel/corpus.py",
        "                    self._set_meta(\"entries\", str(written))\n"
        "                    self._conn.commit()\n"
        "                    report(written)",
        "                    report(written)",
        [
            "tests/rlm_kernel/test_corpus.py"
            "::TestProgressIsCheckpointedAndCompletenessIsRecorded"
            "::test_each_batch_is_checkpointed",
        ],
    ),
    (
        "RO3 an incomplete index is presented as complete",
        "src/rlm_kernel/corpus.py",
        "        return self.meta().get(\"complete\", \"1\") == \"1\"",
        "        return True",
        [
            "tests/rlm_kernel/test_corpus.py"
            "::TestProgressIsCheckpointedAndCompletenessIsRecorded"
            "::test_a_partial_index_says_so_in_find",
            "tests/rlm_kernel/test_corpus.py"
            "::TestProgressIsCheckpointedAndCompletenessIsRecorded"
            "::test_a_partial_index_says_so_even_when_nothing_matches",
        ],
    ),
    (
        "RO2 the read-only proof loses its run window",
        "src/rlm_kernel/corpus.py",
        "            if entry.mtime > ended:\n"
        "                future += 1\n"
        "            elif entry.mtime < started - slack:\n"
        "                stale += 1\n"
        "            else:\n"
        "                in_window += 1",
        "            if entry.mtime > ended:\n"
        "                stale += 1\n"
        "            elif entry.mtime < started - slack:\n"
        "                future += 1\n"
        "            else:\n"
        "                in_window += 1",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheReadOnlyProof"
            "::test_a_future_dated_file_is_not_reported_as_a_write",
            "tests/test_cli_corpus.py::TestCorpusVerify"
            "::test_a_future_dated_file_is_reported_as_not_ours",
        ],
    ),
    (
        "RO2 the read-only proof stops sampling, walking everything",
        "src/rlm_kernel/corpus.py",
        "            if sample is not None and newer >= sample:\n"
        "                complete = False\n"
        "                break",
        "            if False:\n"
        "                complete = False\n"
        "                break",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheReadOnlyProof"
            "::test_the_sample_bounds_the_cost_of_a_bad_answer",
        ],
    ),
]

# NOTE on a guard with no mutation entry: `_apply_memory_limit` (DG3) bounds the
# worker's address space with `resource.setrlimit`, which does not exist on
# Windows. Its effect is therefore unobservable on this development host, and a
# mutation entry for it would be reported as VACUOUS — a false alarm about the
# code rather than about the test. The guard is covered by
# `TestDesignSection53Scaffold::test_a_memory_limit_is_honoured_where_the_os_allows_it`,
# which skips the effect assertion on Windows and always asserts that a bogus
# value does not stop the worker. Recorded here so the gap is explicit rather
# than looked over.


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
        occurrences = original.count(old)
        if occurrences == 0:
            print(f"SKIP  {label}: mutation target not found in {rel}")
            failures.append(f"{label} (target not found)")
            continue
        if occurrences > 1:
            # An ambiguous target is worse than a missing one: `replace(..., 1)`
            # would edit the first occurrence, which may be a different function
            # entirely, and the guard would then look vacuous when it is fine.
            print(f"SKIP  {label}: mutation target appears {occurrences}x in {rel} "
                  "— add surrounding context to make it unique")
            failures.append(f"{label} (ambiguous target, {occurrences} occurrences)")
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
