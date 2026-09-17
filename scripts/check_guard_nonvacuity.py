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
import os
import shutil
import subprocess
import sys
import tempfile
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
        "                       'corpus_count', 'corpus_search', 'corpus_coverage'):",
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
    (
        "RO2 the digest stops covering entry sizes",
        "src/rlm_kernel/corpus.py",
        "    h.update(struct.pack(\">Qd\", entry.size & 0xFFFFFFFFFFFFFFFF, stamp))",
        "    h.update(struct.pack(\">Qd\", 0, stamp))",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_changed_size_with_the_same_mtime_is_caught",
        ],
    ),
    (
        "RO2 the digest stops covering file timestamps",
        "src/rlm_kernel/corpus.py",
        "    stamp = 0.0 if entry.kind == \"dir\" else entry.mtime",
        "    stamp = 0",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_changed_mtime_is_caught",
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_directory_timestamps_are_not_part_of_the_digest",
        ],
    ),
    (
        "RO2 the digest starts covering directory timestamps",
        "src/rlm_kernel/corpus.py",
        "    stamp = 0.0 if entry.kind == \"dir\" else entry.mtime",
        "    stamp = int(round(entry.mtime * 1_000_000_000))",
        [
            # The first target is the deterministic one: it moves a directory's
            # mtime and requires the digest not to move, so the mutation is red
            # every time. The walk-vs-index comparison below is only *usually*
            # sensitive to this mutation (it depends on whether the two reads saw
            # the same directory timestamp), which is why the verdict no longer
            # rests on it — it made the table report VACUOUS about once in four
            # runs until this target was added.
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_directory_mtime_change_does_not_move_the_digest",
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_file_mtime_change_moves_the_digest",
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_walk_and_an_index_of_the_same_corpus_agree",
        ],
    ),
    (
        "RO2 the digest goes back to a nanosecond integer timestamp",
        "src/rlm_kernel/corpus.py",
        "    h.update(struct.pack(\">Qd\", entry.size & 0xFFFFFFFFFFFFFFFF, stamp))",
        "    h.update(struct.pack(\">Qq\", entry.size, int(round(stamp * 1_000_000_000))))",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_timestamp_that_does_not_fit_in_nanoseconds_is_handled",
        ],
    ),
    (
        "RO2 the index digest reads the display path, not the exact bytes",
        "src/rlm_kernel/corpus.py",
        "        \"SELECT raw, kind, size, mtime FROM entries\"",
        "        \"SELECT path, kind, size, mtime FROM entries\"",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_walk_and_an_index_of_the_same_corpus_agree",
        ],
    ),
    (
        "RO1 the sniff reads the whole file instead of the head",
        "src/rlm_kernel/classify.py",
        "    want = sniff_bytes\n"
        "    if hash_mode == \"head\":\n"
        "        want = max(sniff_bytes, hash_bytes)",
        "    want = None\n"
        "    if hash_mode == \"head\":\n"
        "        want = None",
        [
            "tests/rlm_kernel/test_classify.py::TestTheClassifyPass"
            "::test_the_read_is_bounded_to_the_head",
        ],
    ),
    (
        "RO1 the classify pass stops being resumable",
        "src/rlm_kernel/classify.py",
        "        if not redo:\n"
        "            sql += \" AND c.raw IS NULL\"",
        "        if False:\n"
        "            sql += \" AND c.raw IS NULL\"",
        [
            "tests/rlm_kernel/test_classify.py::TestTheClassifyPass"
            "::test_a_second_run_classifies_nothing_new",
        ],
    ),
    (
        "RO1 an unreadable file aborts the pass instead of being recorded",
        "src/rlm_kernel/classify.py",
        "        except ReadOnlyViolation as e:\n"
        "            result, digest, mode, read = SniffResult(UNREADABLE, None), \"\", \"none\", 0\n"
        "            note = type(e).__name__\n"
        "            stats.unreadable += 1",
        "        except ReadOnlyViolation:\n"
        "            raise",
        [
            "tests/rlm_kernel/test_classify.py::TestTheClassifyPass"
            "::test_it_reports_unreadable_paths_without_aborting",
        ],
    ),
    (
        "RO1 the report starts carrying paths",
        "src/rlm_kernel/classify.py",
        "        unreadable = by_kind.get(UNREADABLE, {}).get(\"files\", 0)\n"
        "        return {\n"
        "            \"classified\": sum(v[\"files\"] for v in by_kind.values()),",
        "        unreadable = by_kind.get(UNREADABLE, {}).get(\"files\", 0)\n"
        "        sample = [row[0] for row in self._conn.execute(\n"
        "            \"SELECT path FROM entries LIMIT 1\")]\n"
        "        return {\n"
        "            \"sample\": sample,\n"
        "            \"classified\": sum(v[\"files\"] for v in by_kind.values()),",
        [
            "tests/rlm_kernel/test_classify.py::TestTheReport"
            "::test_it_contains_no_paths",
        ],
    ),
    (
        "RO1 NUL bytes stop meaning binary",
        "src/rlm_kernel/classify.py",
        "    if b\"\\x00\" not in data:\n"
        "        try:\n"
        "            data.decode(\"utf-8\")",
        "    if True:\n"
        "        try:\n"
        "            data.decode(\"utf-8\", \"ignore\")",
        [
            "tests/rlm_kernel/test_classify.py::TestTheSniffItself"
            "::test_binary_with_nul_bytes",
        ],
    ),
    (
        "RO1 the classification table truncates the path index",
        "src/rlm_kernel/classify.py",
        "    def ensure(self) -> None:\n"
        "        self._conn.executescript(",
        "    def ensure(self) -> None:\n"
        "        self._conn.executescript(\"DELETE FROM entries;\")\n"
        "        self._conn.executescript(",
        [
            "tests/rlm_kernel/test_classify.py"
            "::TestOpeningTheIndexLeavesThePathIndexAlone"
            "::test_classifying_does_not_truncate_the_path_index",
        ],
    ),
    (
        "RO2 two different snapshots compare as equal",
        "src/rlm_kernel/corpus.py",
        "    same = (\n"
        "        before[\"digest\"] == after[\"digest\"]\n"
        "        and before[\"entries\"] == after[\"entries\"]\n"
        "        and before[\"file_bytes\"] == after[\"file_bytes\"]\n"
        "    )",
        "    same = True",
        [
            "tests/rlm_kernel/test_corpus.py::TestTheCorpusDigest"
            "::test_a_changed_size_is_caught",
            "tests/test_cli_corpus.py::TestCorpusDigestCommand"
            "::test_compare_reports_a_change_and_exits_one",
        ],
    ),
    # ── Mining: the queue, its caches and its windows (RO3) ───────────────
    (
        # The incident this project must never repeat: `os.kill(pid, 0)` is a
        # harmless liveness probe on POSIX, but on Windows it calls
        # TerminateProcess, so it *kills the process it asks about*. It killed a
        # test run and the harness running it. The named test scans the module's
        # source, so applying this mutation cannot cause a kill — nothing executes
        # the inserted call.
        "RO3 mining: the lock probes a process instead of reading its heartbeat",
        "src/rlm_kernel/mine.py",
        "        if age is not None and age < stale_after:",
        "        os.kill(int(lock_file.read_text().split()[0]), 0)\n"
        "        if age is not None and age < stale_after:",
        [
            "tests/rlm_kernel/test_mine.py::TestReadOnlyByConstruction"
            "::test_the_module_never_probes_a_process",
        ],
    ),
    (
        "RO3 mining: the cache key stops being content-addressed",
        "src/rlm_kernel/mine.py",
        "        digest.update(source_hash.encode(\"ascii\", \"replace\"))",
        "        digest.update(b\"constant\")",
        [
            "tests/rlm_kernel/test_mine.py::TestTheDerivationCache"
            "::test_key_is_content_addressed",
        ],
    ),
    (
        "RO3 mining: an archive listing ignores its member cap",
        "src/rlm_kernel/mine.py",
        "        for info in archive.infolist():\n"
        "            if len(members) >= MAX_MEMBERS:\n"
        "                break",
        "        for info in archive.infolist():\n"
        "            if False:\n"
        "                break",
        [
            "tests/rlm_kernel/test_mine.py::TestArchiveListing"
            "::test_the_listing_is_bounded",
        ],
    ),
    (
        "RO3 mining: a damaged container aborts the run",
        "src/rlm_kernel/mine.py",
        "    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError,\n"
        "            ReadOnlyViolation, ValueError) as e:\n"
        "        return TaskOutcome(FAILED, type(e).__name__)",
        "    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError,\n"
        "            ReadOnlyViolation, ValueError) as e:\n"
        "        raise",
        [
            "tests/rlm_kernel/test_mine.py::TestArchiveListing"
            "::test_the_handler_reports_rather_than_raises",
            "tests/rlm_kernel/test_mine.py::TestArchiveListing"
            "::test_a_damaged_container_is_a_row_not_a_crash",
        ],
    ),
    (
        "RO3 mining: the pause file is ignored",
        "src/rlm_kernel/mine.py",
        "        if pause_file is not None and Path(pause_file).exists():",
        "        if False:",
        [
            "tests/rlm_kernel/test_mine.py::TestWindows"
            "::test_a_pause_file_stops_the_run",
        ],
    ),
    (
        "RO3 mining: text files get queued for extraction they do not need",
        "src/rlm_kernel/mine.py",
        "        f\"c.kind IN ('document', 'archive') AND ({doc_like})\", patterns,",
        "        f\"c.kind IN ('document', 'archive', 'text') AND ({doc_like})\", patterns,",
        [
            "tests/rlm_kernel/test_mine.py::TestPlanningFromTheMap"
            "::test_it_queues_documents_and_archives_only",
        ],
    ),
    (
        "RO3 mining: the CLI ignores the worker lock",
        "src/rlm_local/cli.py",
        "            holder = acquire_lock(lock_path)\n"
        "            if holder is None:",
        "            holder = acquire_lock(lock_path)\n"
        "            if False:",
        [
            "tests/test_cli_mine.py::TestRun::test_a_second_worker_is_refused",
        ],
    ),
    (
        "RO3 mining: the CLI never releases the lock",
        "src/rlm_local/cli.py",
        "            finally:\n"
        "                release_lock(lock_path)",
        "            finally:\n"
        "                pass",
        [
            "tests/test_cli_mine.py::TestRun::test_the_lock_is_released_after_a_run",
        ],
    ),
    (
        "RO3 mining: --for ignores its unit",
        "src/rlm_local/cli.py",
        "    units = {\"s\": 1, \"m\": 60, \"h\": 3600}\n"
        "    if text[-1] in units:\n"
        "        number, factor = text[:-1], units[text[-1]]",
        "    units = {\"s\": 1, \"m\": 60, \"h\": 3600}\n"
        "    if False:\n"
        "        number, factor = text[:-1], units[text[-1]]",
        [
            "tests/test_cli_mine.py::TestDurations::test_seconds_minutes_hours",
        ],
    ),
    # ── The text index: words, addresses and coverage (RO3) ───────────────
    (
        "RO3 mining: the claim stops being an index seek",
        "src/rlm_kernel/mine.py",
        "            CREATE INDEX IF NOT EXISTS mine_queue_claim\n"
        "                ON mine_queue(task, state, priority, raw);",
        "            CREATE INDEX IF NOT EXISTS mine_queue_claim\n"
        "                ON mine_queue(task, state, priority);",
        [
            "tests/rlm_kernel/test_mine.py::TestTheClaimQueryStaysACheapSeek"
            "::test_the_claim_is_an_index_seek_not_a_sort",
            "tests/rlm_kernel/test_mine.py::TestTheClaimQueryStaysACheapSeek"
            "::test_the_claim_index_covers_raw",
        ],
    ),
    (
        "RO3 text: the FTS table starts storing the text it must not store",
        "src/rlm_kernel/textindex.py",
        "            CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(\n"
        "                body,\n"
        "                content='',",
        "            CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(\n"
        "                body,",
        [
            "tests/rlm_kernel/test_textindex.py::TestStorage"
            "::test_the_fts_table_stores_no_text",
        ],
    ),
    (
        "RO3 text: vendored matches stop being filtered",
        "src/rlm_kernel/textindex.py",
        "        if not include_vendored:\n"
        "            clauses.append(\"c.vendored = 0\")",
        "        if False:\n"
        "            clauses.append(\"c.vendored = 0\")",
        [
            "tests/rlm_kernel/test_textindex.py::TestVendoredRanking"
            "::test_vendored_matches_are_filtered_and_counted",
        ],
    ),
    (
        "RO3 text: vendored matches stop being counted",
        "src/rlm_kernel/textindex.py",
        "        if not include_vendored:\n"
        "            row = self._conn.execute(",
        "        if False:\n"
        "            row = self._conn.execute(",
        [
            "tests/rlm_kernel/test_textindex.py::TestVendoredRanking"
            "::test_vendored_matches_are_filtered_and_counted",
        ],
    ),
    (
        "RO3 text: an incomplete index stops saying so",
        "src/rlm_kernel/textindex.py",
        "    if pct >= 99.5:\n        return \"\"",
        "    if True:\n        return \"\"",
        [
            "tests/rlm_kernel/test_textindex.py::TestCoverage"
            "::test_an_incomplete_search_says_so",
            "tests/test_cli_corpus.py::TestCorpusSearchCommand"
            "::test_an_empty_text_index_says_so",
        ],
    ),
    (
        "RO3 text: query terms stop being quoted for FTS5",
        "src/rlm_kernel/textindex.py",
        "        return \" AND \".join(f'\"{term}\"' for term in terms[:16])",
        "        return \" AND \".join(terms[:16])",
        [
            "tests/rlm_kernel/test_textindex.py::TestSearchAndRead"
            "::test_fts_syntax_cannot_be_injected",
        ],
    ),
    # ── The corpus helpers: search, members, coverage (RO4) ───────────────
    (
        "RO4 corpus_search stops reporting its coverage",
        "src/rlm_kernel/corpus.py",
        "            snapshot = text_index.published_coverage()\n"
        "            if snapshot is None:\n"
        "                lines.append(CORPUS_COVERAGE_UNKNOWN)\n"
        "            else:\n"
        "                note = coverage_note(snapshot)\n"
        "                if note:\n"
        "                    lines.append(note)",
        "            snapshot = text_index.published_coverage()\n"
        "            if snapshot is None:\n"
        "                pass\n"
        "            else:\n"
        "                note = coverage_note(snapshot)\n"
        "                if False:\n"
        "                    lines.append(note)",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_a_missing_word_carries_the_coverage_in_its_only_element",
        ],
    ),
    (
        "RO4 corpus_search returns one string instead of a list of hits",
        "src/rlm_local/repl.py",
        "    result = resp.get('result')\n"
        "    if isinstance(result, list):\n"
        "        return result",
        "    result = resp.get('result')\n"
        "    if isinstance(result, list):\n"
        "        return \"\\n\".join(result)",
        [
            "tests/test_corpus_repl.py::TestCorpusHelpersInALiveCell"
            "::test_a_cell_can_search_the_words_inside_the_corpus",
        ],
    ),
    (
        "RO4 corpus_search stops labelling derived text",
        "src/rlm_kernel/corpus.py",
        "            if hit.derived:\n"
        "                labels.append(f\"derived:{hit.engine or 'unknown'}\")",
        "            if False:\n"
        "                labels.append(f\"derived:{hit.engine or 'unknown'}\")",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_derived_text_is_labelled_with_its_engine",
        ],
    ),
    (
        "RO4 corpus_find stops looking inside archives",
        "src/rlm_kernel/corpus.py",
        "        member_lines = self._find_members(query, limit=limit)",
        "        member_lines = []",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_archives_listed_for_search_are_searched_by_find",
        ],
    ),
    (
        "RO4 the worker stops exporting corpus_search",
        "src/rlm_local/repl.py",
        "\ncorpus_search = _harness_corpus_search\n",
        "\ncorpus_search = None\n",
        [
            "tests/test_corpus_repl.py::TestWorkerDefinesTheCorpusVerbs"
            "::test_every_advertised_helper_exists_in_the_worker",
        ],
    ),
    (
        "RO4 corpus_read stops accepting a printed address",
        "src/rlm_kernel/corpus.py",
        "        via_address = self._read_address(rel)\n"
        "        if via_address is not None:\n"
        "            return via_address",
        "        via_address = None\n"
        "        if via_address is not None:\n"
        "            return via_address",
        [
            "tests/rlm_kernel/test_read_address.py::TestAddressParsing"
            "::test_a_file_address_reads_verbatim",
            "tests/rlm_kernel/test_read_address.py::TestAddressParsing"
            "::test_a_derived_address_reads_from_the_cache",
        ],
    ),
    (
        "RO4 an address that names no chunk is misread as a file",
        "src/rlm_kernel/textindex.py",
        "        if row is None:\n            return None",
        "        if row is None:\n            return Hit(\n"
        "                chunk_id=0, source=source, origin=ORIGIN_FILE,\n"
        "                source_hash=\"\", byte_start=start, byte_end=end,\n"
        "                derived=False, engine=None, vendored=False, score=0.0)",
        [
            "tests/rlm_kernel/test_read_address.py::TestAddressParsing"
            "::test_an_unindexed_address_is_refused_not_misread",
        ],
    ),
    # ── The unsearched-submission nudge (RO4, live run 3) ─────────────────
    # Live run 3 submitted "not mentioned in the corpus" after a single
    # `print(len(context))`. The nudge is driven by the fact that the parent
    # process serves every corpus helper request, so the counter and the nudge
    # both have to be real for the guard to mean anything.
    (
        "RO4 the sandbox stops counting served corpus calls",
        "src/rlm_local/repl.py",
        "        self.corpus_calls += 1\n        bridge = self._corpus_bridge",
        "        bridge = self._corpus_bridge",
        [
            "tests/test_corpus_repl.py::TestTheSandboxCountsCorpusCalls"
            "::test_every_served_verb_counts[corpus_search]",
            "tests/test_corpus_repl.py::TestTheSandboxCountsCorpusCalls"
            "::test_calls_accumulate_across_verb_kinds",
            "tests/test_root_loop_integration.py::TestCorpusUnsearchedNudge"
            "::test_a_search_before_submitting_is_accepted_without_a_nudge",
        ],
    ),
    (
        "RO4 an unsearched submission stops being nudged",
        "src/rlm_local/root_loop.py",
        "                    if (self._corpus_bridge is not None\n"
        "                            and self._repl is not None\n"
        "                            and not self._repl.corpus_calls):",
        "                    if (False\n"
        "                            and self._repl is not None\n"
        "                            and not self._repl.corpus_calls):",
        [
            "tests/test_root_loop_integration.py::TestCorpusUnsearchedNudge"
            "::test_a_submission_with_no_helper_call_is_refused_once",
            "tests/test_root_loop_integration.py::TestCorpusUnsearchedNudge"
            "::test_an_unsupported_claim_is_not_accepted_the_first_time",
        ],
    ),
    (
        "RO4 the unsearched submission is nudged but the turn does not restart",
        "src/rlm_local/root_loop.py",
        "                        self._logger.log_root_message(\"user\",\n"
        "                                                      NUDGE_CORPUS_UNSEARCHED)\n"
        "                    turn += 1\n"
        "                    continue\n"
        "                break",
        "                        self._logger.log_root_message(\"user\",\n"
        "                                                      NUDGE_CORPUS_UNSEARCHED)\n"
        "                break",
        [
            "tests/test_root_loop_integration.py::TestCorpusUnsearchedNudge"
            "::test_a_submission_with_no_helper_call_is_refused_once",
        ],
    ),
    # ── RO4 provenance: the citation requirement, and the measurement of it ──
    (
        "RO4 the prompt stops requiring the answer to cite its evidence",
        "src/rlm_local/prompts.py",
        "    \"- **Cite your evidence.** Every claim you make about the corpus must carry\"",
        "    \"Mention your sources only if you feel like it.\"",
        [
            "tests/test_corpus_repl.py::TestWorkerDefinesTheCorpusVerbs"
            "::test_the_section_requires_the_answer_to_cite_its_evidence",
        ],
    ),
    (
        "RO4 the citation check stops looking at the answer",
        "src/rlm_local/root_loop.py",
        "        cited = bool(ADDRESS_TOKEN_RE.search(text))",
        "        cited = True",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationTelemetry"
            "::test_an_answer_with_no_address_is_recorded_as_uncited",
            "tests/test_root_loop_integration.py::TestCorpusCitationTelemetry"
            "::test_the_forced_finalization_answer_is_measured_too",
        ],
    ),
    (
        "RO4 a forced corpus answer stops being measured",
        "src/rlm_local/root_loop.py",
        "            self._record_citations(turn + 1, final_answer)",
        "            pass",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationTelemetry"
            "::test_the_forced_finalization_answer_is_measured_too",
        ],
    ),
    # ── RO4 provenance: the refusal, and its escape hatch ─────────────────
    (
        "RO4 an answer that cites nothing stops being refused",
        "src/rlm_local/root_loop.py",
        "        addresses = set(ADDRESS_TOKEN_RE.findall(text))\n"
        "        if not addresses:\n"
        "            return None if COVERAGE_MARKER in text.lower() else \"uncited\"",
        "        addresses = set(ADDRESS_TOKEN_RE.findall(text))\n"
        "        if not addresses:\n"
        "            return None",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationGuard"
            "::test_an_uncited_answer_is_refused_and_then_a_cited_one_wins",
        ],
    ),
    (
        "RO4 the coverage escape hatch stops counting as an answer",
        "src/rlm_local/root_loop.py",
        "        if not addresses:\n"
        "            return None if COVERAGE_MARKER in text.lower() else \"uncited\"\n"
        "        if (self._cites_only_unanswering_evidence(addresses)\n"
        "                and COVERAGE_MARKER not in text.lower()):\n"
        "            return \"weak\"\n"
        "        return None",
        "        if not addresses:\n"
        "            return \"uncited\"\n"
        "        if (self._cites_only_unanswering_evidence(addresses)\n"
        "                and COVERAGE_MARKER not in text.lower()):\n"
        "            return \"weak\"\n"
        "        return None",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationGuard"
            "::test_an_answer_that_names_coverage_is_accepted_without_a_nudge",
        ],
    ),
    (
        "RO4 a FINAL: line escapes the citation rule",
        "src/rlm_local/root_loop.py",
        "                refusal = self._refusal_reason(result.final_answer)\n"
        "                if refusal and corpus_uncited_nudges < cfg.max_consecutive_nudges:",
        "                refusal = self._refusal_reason(result.final_answer)\n"
        "                if False:",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationGuard"
            "::test_an_uncited_final_line_after_a_search_is_refused",
        ],
    ),
    (
        "RO4 a FINAL: line escapes the unsearched rule",
        "src/rlm_local/root_loop.py",
        "                if (self._corpus_bridge is not None\n"
        "                        and self._repl is not None\n"
        "                        and not self._repl.corpus_calls\n"
        "                        and corpus_nudges < cfg.max_consecutive_nudges):",
        "                if (False\n"
        "                        and self._repl is not None\n"
        "                        and not self._repl.corpus_calls\n"
        "                        and corpus_nudges < cfg.max_consecutive_nudges):",
        [
            "tests/test_root_loop_integration.py::TestCorpusCitationGuard"
            "::test_a_courtesy_final_line_is_guarded_too",
        ],
    ),
    # ── RO4: a search must not count the index it searches (2026-09-15) ───
    (
        "RO4 a search goes back to counting the chunk table for its coverage",
        "src/rlm_kernel/corpus.py",
        "            snapshot = text_index.published_coverage()",
        "            snapshot = text_index.coverage()",
        [
            "tests/rlm_kernel/test_corpus.py::TestSearchCoverageIsPublishedNotCounted"
            "::test_a_miss_without_a_snapshot_says_unknown_and_does_not_scan",
            "tests/rlm_kernel/test_corpus.py::TestSearchCoverageIsPublishedNotCounted"
            "::test_a_miss_quotes_the_published_snapshot",
        ],
    ),
    (
        "RO4 the published snapshot stops being readable",
        "src/rlm_kernel/textindex.py",
        "        raw = self._get_meta(COVERAGE_SNAPSHOT_KEY)\n"
        "        if raw is None:\n"
        "            return None",
        "        raw = None\n"
        "        if raw is None:\n"
        "            return None",
        [
            "tests/rlm_kernel/test_textindex.py::TestCoverageSnapshot"
            "::test_a_published_snapshot_round_trips",
            "tests/rlm_kernel/test_corpus.py::TestSearchCoverageIsPublishedNotCounted"
            "::test_a_miss_quotes_the_published_snapshot",
        ],
    ),
    (
        "RO4 a mining window stops publishing coverage",
        "src/rlm_kernel/mine.py",
        "    publish_coverage_snapshot(conn)\n",
        "    pass\n",
        [
            "tests/rlm_kernel/test_mine.py::TestWindows"
            "::test_a_window_publishes_the_coverage_snapshot",
        ],
    ),
    (
        "RO4 the terminal answer stops being asked for its evidence",
        "src/rlm_local/root_loop.py",
        "                FORCED_FINALIZATION_CORPUS_PROMPT\n"
        "                if self._corpus_bridge is not None\n"
        "                else FORCED_FINALIZATION_PROMPT",
        "                FORCED_FINALIZATION_CORPUS_PROMPT\n"
        "                if False\n"
        "                else FORCED_FINALIZATION_PROMPT",
        [
            "tests/test_root_loop_integration.py::TestForcedFinalizationCarriesProvenance"
            "::test_a_corpus_run_is_asked_for_its_evidence",
        ],
    ),
    (
        "RO4 the last turn stops being the turn for answering",
        "src/rlm_local/root_loop.py",
        "                    and self._repl.corpus_calls\n"
        "                    and turn == max_turns - 1):",
        "                    and self._repl.corpus_calls\n"
        "                    and False):",
        [
            "tests/test_root_loop_integration.py::TestCorpusLastTurnNudge"
            "::test_a_corpus_run_is_told_its_last_turn_is_for_answering",
        ],
    ),
    # ── RO4: a citation must be an address the harness served (2026-09-16) ──
    (
        "RO4 any address-shaped citation is accepted again",
        "src/rlm_local/root_loop.py",
        "        served = set(getattr(self._repl, \"corpus_addresses_served\", set()) or set())\n"
        "        return addresses - served",
        "        return set()",
        [
            "tests/test_root_loop_integration.py::TestCitationsMustBeServed"
            "::test_a_fabricated_citation_is_refused_then_a_served_one_wins",
            "tests/test_root_loop_integration.py::TestCitationsMustBeServed"
            "::test_naming_coverage_does_not_excuse_an_invented_address",
        ],
    ),
    (
        "RO4 the sandbox stops remembering what it served",
        "src/rlm_local/repl.py",
        "        self.corpus_addresses_served.update(served)",
        "        self.corpus_addresses_served.update(set())",
        [
            "tests/test_root_loop_integration.py::TestCitationsMustBeServed"
            "::test_a_served_citation_is_accepted_first_time",
            "tests/test_corpus_repl.py::TestTheSandboxRemembersWhichAddressesItServed"
            "::test_a_search_serves_the_addresses_it_returns",
        ],
    ),
    # ── RO4: match quality, measured against the question (2026-09-16) ────
    (
        "RO4 the match label measures the search instead of the question",
        "src/rlm_kernel/corpus.py",
        "        terms = content_terms(self.question or \"\")",
        "        terms = content_terms(query)",
        [
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_every_hit_carries_how_much_of_the_question_it_covers",
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_without_a_question_no_coverage_is_claimed",
        ],
    ),
    (
        "RO4 every match is called strong",
        "src/rlm_kernel/textindex.py",
        "    if covered >= total or covered >= max(2, MATCH_STRONG_RATIO * total):\n"
        "        return \"strong\"",
        "    if True:\n"
        "        return \"strong\"",
        [
            "tests/rlm_kernel/test_textindex.py::TestMatchQuality"
            "::test_quality_bands[1-6-weak]",
            "tests/rlm_kernel/test_corpus.py::TestBridgeContentSearch"
            "::test_a_hit_sharing_no_question_word_is_none_not_weak",
        ],
    ),
    (
        "RO4 a served search stops reporting what it served",
        "src/rlm_local/repl.py",
        "            self._report_search_quality(text)",
        "            pass",
        [
            "tests/test_root_loop_integration.py::TestSearchQualityIsLogged"
            "::test_a_search_records_what_it_served",
        ],
    ),
    (
        "a model transport failure discards the run again",
        "src/rlm_local/root_loop.py",
        "                if not self._model_ok:\n"
        "                    raise\n"
        "                break",
        "                if not self._model_ok:\n"
        "                    raise\n"
        "                raise",
        [
            "tests/test_root_loop_integration.py::TestAModelFailureEndsTheRunRatherThanDiscardingIt"
            "::test_a_failing_turn_degrades_to_forced_finalization",
        ],
    ),
    # ── RO4: the label is read back at submission (2026-09-17) ─────────────
    (
        "RO4 an answer may rest on a served weak hit again",
        "src/rlm_local/root_loop.py",
        "        return all(band in (\"weak\", \"none\") for band in seen)",
        "        return False",
        [
            "tests/test_root_loop_integration.py::TestAnAnswerMustRestOnAnAnsweringMatch"
            "::test_an_answer_resting_on_a_non_answering_hit_is_refused",
            "tests/test_root_loop_integration.py::TestAnAnswerMustRestOnAnAnsweringMatch"
            "::test_the_refusal_records_the_band_it_refused_on",
        ],
    ),
    (
        "RO4 an unlabelled address counts as a weak one",
        "src/rlm_local/root_loop.py",
        "        seen = [bands.get(address) for address in addresses]\n"
        "        if not seen or any(band is None for band in seen):\n"
        "            return False\n"
        "        return all(band in (\"weak\", \"none\") for band in seen)",
        "        seen = [bands.get(address) for address in addresses]\n"
        "        if not seen:\n"
        "            return False\n"
        "        return all(band in (\"weak\", \"none\", None) for band in seen)",
        [
            "tests/test_root_loop_integration.py::TestAnAnswerMustRestOnAnAnsweringMatch"
            "::test_a_question_with_no_content_words_cannot_label_a_citation",
        ],
    ),
    (
        "RO4 the sandbox stops remembering the band a hit was served with",
        "src/rlm_local/repl.py",
        "            bands = _served_bands(result)\n"
        "            for address, band in bands.items():\n"
        "                current = self.corpus_address_bands.get(address)\n"
        "                if current is None or BAND_ORDER.index(band) < BAND_ORDER.index(current):\n"
        "                    self.corpus_address_bands[address] = band",
        "            bands = _served_bands(result)\n"
        "            for address, band in bands.items():\n"
        "                pass",
        [
            "tests/test_corpus_repl.py::TestTheSandboxRemembersWhichBandAServedHitHad"
            "::test_a_search_remembers_the_band_its_hit_was_served_with",
            "tests/test_root_loop_integration.py::TestAnAnswerMustRestOnAnAnsweringMatch"
            "::test_an_answer_resting_on_a_non_answering_hit_is_refused",
        ],
    ),
    (
        "RO4 a passage's own text is allowed to label its hit",
        "src/rlm_local/repl.py",
        "        header = str(element).split(\"\\n\", 1)[0]",
        "        header = str(element)",
        [
            "tests/test_corpus_repl.py::TestTheSandboxRemembersWhichBandAServedHitHad"
            "::test_a_passage_cannot_label_itself",
        ],
    ),
    (
        "RO4 a later search demotes an address already judged good",
        "src/rlm_local/repl.py",
        "                current = self.corpus_address_bands.get(address)\n"
        "                if current is None or BAND_ORDER.index(band) < BAND_ORDER.index(current):\n"
        "                    self.corpus_address_bands[address] = band",
        "                self.corpus_address_bands[address] = band",
        [
            "tests/test_corpus_repl.py::TestTheSandboxRemembersWhichBandAServedHitHad"
            "::test_the_strongest_band_for_an_address_is_the_one_kept",
        ],
    ),
    (
        "RO4 a weak refusal sends the uncited nudge",
        "src/rlm_local/root_loop.py",
        "                    nudge = (NUDGE_CORPUS_WEAK_EVIDENCE\n"
        "                             if corpus_uncited_reason == \"weak\"\n"
        "                             else NUDGE_CORPUS_UNCITED)",
        "                    nudge = NUDGE_CORPUS_UNCITED",
        [
            "tests/test_root_loop_integration.py::TestAnAnswerMustRestOnAnAnsweringMatch"
            "::test_the_refusal_records_the_band_it_refused_on",
        ],
    ),
    # ── RO10: the trace viewer, and the records it audits (2026-09-17) ─────
    (
        "RO10 the run stops recording which addresses a helper served",
        "src/rlm_local/root_loop.py",
        "        self._repl._corpus_serve_logger = self._log_corpus_served",
        "        self._repl._corpus_serve_logger = None",
        [
            "tests/test_root_loop_integration.py::TestARunRecordsWhatEachHelperServed"
            "::test_a_search_leaves_the_addresses_and_their_bands",
        ],
    ),
    (
        "RO10 a failed helper call is recorded as a success that served an address",
        "src/rlm_local/repl.py",
        "            self._report_served(msg_type, msg, [], chars=len(text), ok=False)",
        "            self._report_served(msg_type, msg, [str(msg.get(\"rel\") or \"\")],\n"
        "                                chars=len(text), ok=True)",
        [
            "tests/test_corpus_repl.py::TestTheSandboxReportsWhatItServed"
            "::test_a_failed_call_is_reported_as_a_failure_that_served_nothing",
        ],
    ),
    (
        "RO10 the band stops travelling with the address",
        "src/rlm_local/repl.py",
        "        payload = [{\"address\": address, \"band\": bands.get(address)}\n"
        "                   for address in addresses]",
        "        payload = [{\"address\": address, \"band\": None}\n"
        "                   for address in addresses]",
        [
            "tests/test_corpus_repl.py::TestTheSandboxReportsWhatItServed"
            "::test_a_search_reports_every_address_with_its_band",
            "tests/test_root_loop_integration.py::TestATraceOfARealRunIsAuditable"
            "::test_the_page_carries_the_band_and_the_passage_behind_it",
        ],
    ),
    (
        "RO10 the rendered page stops embedding the passage",
        "src/rlm_local/traceview.py",
        "        run_passages = (passages or {}).get(str(path)) or (passages or {}).get(run.path.name) or {}",
        "        run_passages = {}",
        [
            "tests/test_cli_trace.py::TestTraceRenderWithACorpus"
            "::test_the_page_embeds_the_passage",
        ],
    ),
    (
        "RO10 a trace directory inside the corpus is accepted",
        "src/rlm_local/traceview.py",
        "    if corpus_root is not None:\n"
        "        assert_derived_outside_corpus(corpus_root, out_dir)",
        "    if False:\n"
        "        assert_derived_outside_corpus(corpus_root, out_dir)",
        [
            "tests/test_traceview.py"
            "::test_a_trace_directory_inside_the_corpus_is_refused",
            "tests/test_cli_trace.py::TestTraceRender"
            "::test_a_write_target_inside_the_corpus_is_refused",
        ],
    ),
    (
        "RO10 the summary starts carrying the question",
        "src/rlm_local/traceview.py",
        "        f\"{run.path.name}: turns={run.turns_used if run.turns_used is not None else '?'}\"",
        "        f\"{run.path.name} question={run.query}:\"\n"
        "        f\" turns={run.turns_used if run.turns_used is not None else '?'}\"",
        [
            "tests/test_traceview.py"
            "::test_the_summary_carries_no_question_no_address_and_no_quote",
            "tests/test_cli_trace.py::TestTraceRender"
            "::test_the_terminal_gets_aggregates_and_never_corpus_text",
        ],
    ),
    (
        "RO10 an unserved citation is audited as if it had answered",
        "src/rlm_local/traceview.py",
        "            if address not in served:\n"
        "                audit.cited_unserved.append(address)",
        "            if False:\n"
        "                audit.cited_unserved.append(address)",
        [
            "tests/test_traceview.py"
            "::test_a_citation_no_helper_served_is_a_fabrication_when_serves_were_recorded",
        ],
    ),
    (
        "RO10 an unverifiable citation is reported as a fabrication",
        "src/rlm_local/traceview.py",
        "        if not self.served:",
        "        if False:",
        [
            "tests/test_traceview.py"
            "::test_a_run_without_served_events_says_the_audit_is_partial",
        ],
    ),
    (
        "RO10 a torn trajectory line crashes the viewer",
        "src/rlm_local/traceview.py",
        "        except json.JSONDecodeError:\n"
        "            torn += 1\n"
        "            continue",
        "        except json.JSONDecodeError:\n"
        "            raise",
        [
            "tests/test_traceview.py"
            "::test_a_truncated_last_line_is_reported_rather_than_fatal",
        ],
    ),
    # ── RO11: one passage must not scan the chunk table (2026-09-17) ───────
    (
        "RO11 an address read goes back to scanning on display",
        "src/rlm_kernel/textindex.py",
        "        if raw_source is None:\n"
        "            row = self._conn.execute(",
        "        if True:\n"
        "            row = self._conn.execute(",
        [
            "tests/rlm_kernel/test_corpus.py::TestReadingOneAddressDoesNotScanEveryChunk"
            "::test_the_lookup_filters_on_the_indexed_column",
        ],
    ),
    (
        "RO11 the bridge stops resolving display to exact bytes",
        "src/rlm_kernel/corpus.py",
        "                raw_source = self.index.raw_for(match.group(\"source\"))",
        "                raw_source = None",
        [
            "tests/rlm_kernel/test_corpus.py::TestReadingOneAddressDoesNotScanEveryChunk"
            "::test_the_lookup_filters_on_the_indexed_column",
        ],
    ),
    (
        "RO11 the viewer reads a container member anyway",
        "src/rlm_local/traceview.py",
        "        if resolvable is None:\n"
        "            passages[address] = (",
        "        if False:\n"
        "            passages[address] = (",
        [
            "tests/test_traceview.py"
            "::test_a_container_member_is_marked_rather_than_read",
        ],
    ),
    # ── A cell that does not compile costs nothing (2026-09-17) ────────────
    (
        "a cell that does not compile is charged a turn again",
        "src/rlm_local/root_loop.py",
        "                if repl_result.syntax_error:\n"
        "                    if syntax_retries >= cfg.max_syntax_retries:",
        "                if False:\n"
        "                    if syntax_retries >= cfg.max_syntax_retries:",
        [
            "tests/test_root_loop_integration.py"
            "::TestInvalidPythonIsRetriedWithoutPenalty::test_a_syntax_error_costs_no_turn",
        ],
    ),
    (
        "a syntax error is charged to the error budget again",
        "src/rlm_local/repl.py",
        "                _syntax_error = True",
        "                _syntax_error = False",
        [
            "tests/test_root_loop_integration.py"
            "::TestInvalidPythonIsRetriedWithoutPenalty"
            "::test_the_syntax_retry_does_not_touch_the_error_budget",
        ],
    ),
    (
        "the syntax retry budget stops being honoured",
        "src/rlm_local/root_loop.py",
        "                    if syntax_retries >= cfg.max_syntax_retries:",
        "                    if False:",
        [
            "tests/test_root_loop_integration.py"
            "::TestInvalidPythonIsRetriedWithoutPenalty"
            "::test_a_model_that_never_writes_valid_python_still_terminates",
        ],
    ),
    # ── A cell budget is a harness limit, and says so (2026-09-17) ─────────
    (
        "a cell timeout goes back to looking like a code error",
        "src/rlm_local/root_loop.py",
        "                if repl_result.timed_out:",
        "                if False:",
        [
            "tests/test_root_loop_integration.py::TestTheCellBudgetIsVisibleAndConfigurable"
            "::test_a_cell_that_dies_on_its_budget_is_recorded_with_its_budget_and_verb",
        ],
    ),
    (
        "the timeout stops naming the helper that was running",
        "src/rlm_local/root_loop.py",
        "            f\"last_helper={self._last_corpus_helper or 'none'} \"",
        "            f\"last_helper=none \"",
        [
            "tests/test_root_loop_integration.py::TestTheCellBudgetIsVisibleAndConfigurable"
            "::test_a_cell_that_dies_on_its_budget_is_recorded_with_its_budget_and_verb",
        ],
    ),
    (
        "the cell budget stops being configurable",
        "src/rlm_local/cli.py",
        "        overrides[\"cell_timeout\"] = float(args.cell_timeout)",
        "        pass",
        [
            "tests/test_root_loop_integration.py::TestTheCellBudgetIsVisibleAndConfigurable"
            "::test_the_budget_is_a_cli_flag",
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
    """Run one mutation's named tests, in a process with its own bytecode cache.

    The separate cache is not hygiene, it is correctness. CPython validates a
    cached `.pyc` against the source's size and its mtime **truncated to whole
    seconds**, so a mutation that leaves the file the same length and lands in the
    same second as an earlier compile is imported from the *previous* bytecode and
    the guard looks vacuous. That is the shape of the 2026-09-14/15 false alarms
    (roadmap CL5): `R23 the migration does not rewrite the key` shortens
    `schema_version` to `schema`, and so does the entry before it, and in a full
    run the two land close enough together for the second run to reuse the first
    one's bytecode. A per-run cache prefix makes every run compile from the source
    it was handed.
    """
    cache = Path(tempfile.mkdtemp(prefix="rlm-pycache-"))
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = str(cache)
    cmd = [
        str(PY), "-m", "pytest", *node_ids,
        "-p", "no:cacheprovider", "-q", "--no-header", "-x", "--timeout=180",
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              env=env)
        return proc.returncode
    finally:
        # Best effort: a locked cache directory in the temp area is not worth
        # failing a run over, and it is outside the repository either way.
        shutil.rmtree(cache, ignore_errors=True)


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
            if rc == 0:
                # A VACUOUS verdict is a claim that a guard does not work, and on
                # 2026-09-15 this table made that claim about an entry whose
                # isolated runs are red — twice, in three full runs (roadmap
                # CL5). A false alarm about a dead guard costs more than a second
                # test run, and an instrument that cries wolf stops being read,
                # so a green first result is confirmed once before it is
                # reported. The confirmation's verdict is the one recorded.
                print(f"RERUN    {label}: green on the first run; confirming")
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
