"""Model suitability checker (§D3).

Probe battery (P1-P9) evaluating whether a model is suitable as an RLM root
model. Each probe runs small completions and inspects the trajectory log.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rlm_local import completion
from rlm_local.config import Config, load_config
from rlm_local.model_backend import ModelBackend

# ── Unicode smart-quote detection ────────────────────────────────────────

_SMART_QUOTE_CHARS: set[str] = {
    "\u2018",  # LEFT SINGLE QUOTATION MARK
    "\u2019",  # RIGHT SINGLE QUOTATION MARK
    "\u201a",  # SINGLE LOW-9 QUOTATION MARK
    "\u201b",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u201c",  # LEFT DOUBLE QUOTATION MARK
    "\u201d",  # RIGHT DOUBLE QUOTATION MARK
    "\u201e",  # DOUBLE LOW-9 QUOTATION MARK
    "\u201f",  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    "\u2032",  # PRIME (used as smart single quote)
    "\u2033",  # DOUBLE PRIME (used as smart double quote)
}

# ── Helpers ────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:repl|python)?\s*\n", re.DOTALL)
_GREP_CALL_RE = re.compile(r"\bgrep\s*\(")
_PEEK_CALL_RE = re.compile(r"\bpeek\s*\(")
_CHUNK_CALL_RE = re.compile(r"\bchunk\s*\(")
_LLM_QUERY_RE = re.compile(r"\bllm_query\s*\(")
_ANSWER_READY_RE = re.compile(
    r"""answer\[(?:"ready"|'ready'|`ready`)\]\s*=\s*True"""
)
_ANSWER_CONTENT_RE = re.compile(
    r"""answer\[(?:"content"|'content'|`content`)\]\s*=\s*(.+)$""",
    re.MULTILINE,
)
_FINAL_LINE_RE = re.compile(r"^FINAL:\s*(.+)$", re.MULTILINE)
_QUERY_FROM_META_RE = re.compile(r"^Answer the following:\s*(.+)$", re.MULTILINE)


def _run_with_trajectory(
    query: str,
    context: str,
    backend: ModelBackend,
    profile: str = "tiny",
    config: Config | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a completion with trajectory logging and return (answer, log_lines)."""
    with tempfile.NamedTemporaryFile(
        suffix=".jsonl", delete=False, mode="w", encoding="utf-8"
    ) as f:
        log_path = f.name

    try:
        result = completion(
            query,
            context,
            backend=backend,
            profile=profile,
            config=config,
            log_path=log_path,
        )
        with open(log_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        return result, lines
    finally:
        Path(log_path).unlink(missing_ok=True)


def _extract_query_from_messages(messages: list[dict[str, str]]) -> str:
    """Pull the user query out of the metadata message."""
    for msg in messages:
        if msg.get("role") == "user" and "Your context is a " in msg.get("content", ""):
            m = _QUERY_FROM_META_RE.search(msg["content"])
            if m:
                return m.group(1).strip()
    return ""


def _assistant_messages(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return all log_root_message entries with role='assistant'."""
    return [
        line
        for line in lines
        if line.get("event") == "root_message"
        and line.get("role") == "assistant"
    ]


def _repl_results(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return all log_repl_result entries."""
    return [line for line in lines if line.get("event") == "repl_result"]


def _has_repl_block(text: str) -> bool:
    """Check if text contains a fenced ```repl block."""
    return bool(_FENCE_RE.search(text))


def _smart_quote_count(text: str) -> int:
    """Count smart-quote characters in text."""
    return sum(1 for ch in text if ch in _SMART_QUOTE_CHARS)


def _balanced_parens(text: str) -> bool:
    """Crude check that parentheses, brackets, and braces are balanced."""
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for ch in text:
        if ch in pairs:
            stack.append(ch)
        elif ch in (")", "]", "}"):
            if not stack or pairs.get(stack.pop()) != ch:
                return False
    return len(stack) == 0

def _count_tokens_approx(char_count: int) -> int:
    """Very rough token count: chars / 4."""
    return max(1, char_count // 4)


# ── Probe P1: Protocol emission ──────────────────────────────────────────

P1_QUERIES = [
    ("What is 2+2?", "Basic arithmetic: two plus two equals four."),
    (
        "What color is the sky on a clear day?",
        "On a clear day, the sky appears blue due to Rayleigh scattering.",
    ),
    (
        "What is the capital of France?",
        "France is a country in Europe. Its capital city is Paris.",
    ),
]


def probe_p1_protocol_emission(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P1: Protocol emission — 3 trials, checks for valid ```repl block on turn 1.

    Score: 20 pts (max).
    """
    passed = 0
    evidence: list[str] = []

    for i, (query, ctx) in enumerate(P1_QUERIES):
        _, lines = _run_with_trajectory(query, ctx, backend, profile)
        assistants = _assistant_messages(lines)
        first_responses = [
            a["content"] for a in assistants if a.get("content")
        ]

        has_block = any(_has_repl_block(r) for r in first_responses)
        if has_block:
            passed += 1
            evidence.append(
                f"Trial {i + 1}: PASS — valid ```repl block observed"
            )
        else:
            evidence.append(
                f"Trial {i + 1}: FAIL — no ```repl block in any response"
            )

    score = round(20 * passed / 3)
    return {
        "score": score,
        "max_score": 20,
        "evidence": evidence,
        "passed": passed >= 2,
    }


# ── Probe P2: Helper-call correctness ────────────────────────────────────


def probe_p2_helper_calls(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P2: Helper-call correctness — checks grep/peek/chunk are called with
    valid (balanced) arguments.

    Score: 15 pts (max).
    """
    query = (
        "Search the context for information about the Eiffel Tower and "
        "report how many visitors it gets annually."
    )
    context = (
        "The Eiffel Tower is a wrought-iron lattice tower in Paris, France. "
        "It was constructed from 1887 to 1889. It is 330 meters tall. "
        "The tower has three levels for visitors. "
        "Annual visitors: approximately 7 million people visit the Eiffel Tower each year."
    )

    _, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)

    helper_calls: list[tuple[str, str, bool]] = []
    for a in assistants:
        content = a.get("content", "")
        for pattern, name in [
            (_GREP_CALL_RE, "grep"),
            (_PEEK_CALL_RE, "peek"),
            (_CHUNK_CALL_RE, "chunk"),
        ]:
            for m in pattern.finditer(content):
                # Extract the call from match start to closing paren
                start = m.start()
                snippet = content[start : start + 200]
                paren_depth = 0
                call_end = start
                in_str = False
                str_char = ""
                for j, ch in enumerate(content[start:], start=start):
                    if in_str:
                        if ch == "\\":
                            continue
                        if ch == str_char:
                            in_str = False
                        continue
                    if ch in ('"', "'"):
                        in_str = True
                        str_char = ch
                        continue
                    if ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth -= 1
                        if paren_depth == 0:
                            call_end = j + 1
                            break
                call_text = content[start:call_end]
                valid = _balanced_parens(call_text)
                helper_calls.append((name, call_text[:80], valid))

    if not helper_calls:
        evidence = ["No grep/peek/chunk calls observed — model did not use helpers."]
        return {"score": 0, "max_score": 15, "evidence": evidence, "passed": False}

    all_valid = all(v for _, _, v in helper_calls)
    invalid = [(n, t) for n, t, v in helper_calls if not v]

    evidence: list[str] = []
    for name, call_text, valid_flag in helper_calls:
        status = "valid" if valid_flag else "INVALID (unbalanced)"
        evidence.append(f"  {name}: {status} — {call_text}")

    score = 15 if all_valid else max(0, 15 - len(invalid) * 5)
    return {
        "score": score,
        "max_score": 15,
        "evidence": evidence,
        "passed": all_valid,
    }


# ── Probe P3: stderr recovery ────────────────────────────────────────────


def probe_p3_stderr_recovery(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P3: stderr recovery — model encounters a REPL error and recovers.

    Score: 15 pts (max).  If no stderr events occur at all, model gets full
    credit (no recovery was necessary).  If stderr occurs and the next response
    corrects the issue, full credit.  If stderr occurs with no recovery, zero.
    """
    query = (
        "Find lines in the context that mention 'important'. "
        "Use grep with a pattern that finds the word."
    )
    context = (
        "Line 1: This is an important document.\n"
        "Line 2: Nothing special here.\n"
        "Line 3: Another important point.\n"
        "Line 4: End of file.\n"
    )

    _, lines = _run_with_trajectory(query, context, backend, profile)
    repl_entries = _repl_results(lines)
    assistants = _assistant_messages(lines)

    stderr_events = [r for r in repl_entries if r.get("stderr", "").strip()]

    if not stderr_events:
        return {
            "score": 15,
            "max_score": 15,
            "evidence": ["No stderr events — no recovery needed."],
            "passed": True,
        }

    # Check recovery: after stderr, does the next assistant response contain
    # a corrected call (grep with a different pattern, or fixed syntax)?
    recovered = False
    evidence_lines: list[str] = []

    for se in stderr_events:
        stderr_text = se.get("stderr", "")
        turn = se.get("turn", "?")
        evidence_lines.append(
            f"Turn {turn} stderr: {stderr_text[:120]}"
        )

    # Check if any assistant message after a stderr event contains a corrected
    # grep call
    stderr_indices = [
        i for i, r in enumerate(repl_entries) if r.get("stderr", "").strip()
    ]
    if stderr_indices:
        last_stderr_idx = stderr_indices[-1]
        for a in assistants:
            content = a.get("content", "")
            if _GREP_CALL_RE.search(content) and _balanced_parens(content):
                recovered = True
                evidence_lines.append(
                    "Recovery: corrected grep call found in subsequent response."
                )
                break

    if not recovered:
        evidence_lines.append(
            "No recovery observed — model did not correct after stderr."
        )

    score = 15 if recovered else 0
    return {
        "score": score,
        "max_score": 15,
        "evidence": evidence_lines,
        "passed": recovered,
    }


# ── Probe P4: answer-dict submission ─────────────────────────────────────


def probe_p4_answer_submission(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P4: answer-dict submission — model sets answer['content'] and
    answer['ready'] = True within turn budget.

    Score: 15 pts (max).
    """
    query = "What is the name of the largest ocean?"
    context = (
        "The Pacific Ocean is the largest and deepest of Earth's five oceanic "
        "divisions. It extends from the Arctic Ocean in the north to the "
        "Southern Ocean in the south."
    )

    final_answer, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)
    repl_entries = _repl_results(lines)

    # Check for answer submission in trajectory
    has_answer_ready = any(
        _ANSWER_READY_RE.search(a.get("content", "")) for a in assistants
    )
    has_final_answer = any(
        r.get("final_answer") is not None for r in repl_entries
    )
    end_entry = next(
        (line for line in lines if line.get("event") == "end"), None
    )
    forced = end_entry.get("forced", True) if end_entry else True

    evidence: list[str] = []
    if has_answer_ready:
        evidence.append("answer['ready'] = True found in model output.")
    else:
        evidence.append("answer['ready'] = True NOT found in model output.")

    if has_final_answer:
        evidence.append(
            f"Final answer submitted: {final_answer[:100]}"
        )
    else:
        evidence.append("No final answer detected.")

    if forced:
        evidence.append("WARNING: forced finalization — model did not submit voluntarily.")

    score = 15 if (has_final_answer and not forced) else (8 if has_final_answer else 0)
    return {
        "score": score,
        "max_score": 15,
        "evidence": evidence,
        "passed": has_final_answer and not forced,
    }


# ── Probe P5: Format discipline ──────────────────────────────────────────


def probe_p5_format_discipline(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P5: Format discipline — no bare JSON or prose without ```repl blocks.

    Score: 10 pts (max). Deductions for format violations.
    """
    query = "What is the boiling point of water in Celsius?"
    context = (
        "Water boils at 100 degrees Celsius at standard atmospheric pressure. "
        "This is a fundamental physical property of water."
    )

    _, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)

    violations = 0
    evidence: list[str] = []

    for a in assistants:
        content = a.get("content", "").strip()
        if not content:
            continue

        has_block = _has_repl_block(content)
        # A model is expected to always emit ```repl blocks.
        # Any assistant message without one is a format violation.
        if not has_block and len(content) > 5:
            violations += 1
            snippet = content[:100].replace("\n", " ")
            evidence.append(f"Violation: no ```repl block in response: {snippet}")

    if not violations:
        evidence.append("No format violations detected.")

    score = max(0, 10 - violations * 5)
    return {
        "score": score,
        "max_score": 10,
        "evidence": evidence,
        "passed": violations == 0,
    }


# ── Probe P6: Needle accuracy ────────────────────────────────────────────

NEEDLE_TASKS = [
    {
        "query": "What is the secret access code?",
        "context": (
            "Document: Internal Security Memo\n"
            "Date: 2024-03-15\n"
            "Subject: Access Protocol Update\n\n"
            "The new access code for the server room is ALPHA-42. "
            "This replaces the previous code BETA-17. All personnel must "
            "use ALPHA-42 starting Monday. The code must not be shared "
            "externally.\n\nAdditional notes: The backup generator key is "
            "GAMMA-99 and should be used only during power outages."
        ),
        "needle": "ALPHA-42",
    },
    {
        "query": "What year was the treaty signed?",
        "context": (
            "Historical Record: Treaty of Concord\n"
            "Signed: 1748\n"
            "Signatories: Kingdom of Eldoria, Republic of Valdris\n"
            "Terms: Mutual defense and trade agreement.\n\n"
            "After years of negotiations that began in 1745, the treaty "
            "was finally signed in 1748. The ratification process completed "
            "in early 1749. The treaty remained in effect until 1802."
        ),
        "needle": "1748",
    },
    {
        "query": "What is the patient's blood type?",
        "context": (
            "PATIENT MEDICAL RECORD\n"
            "Name: John Doe\n"
            "DOB: 1985-06-12\n"
            "Blood Type: O-Negative\n"
            "Allergies: Penicillin, Latex\n\n"
            "Visit History:\n"
            "2024-01-15: Routine checkup, blood type confirmed as O-Negative.\n"
            "2024-03-22: Allergic reaction to penicillin confirmed.\n"
            "2024-06-10: Annual physical, all vitals normal."
        ),
        "needle": "O-Negative",
    },
]


def probe_p6_needle_accuracy(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P6: Needle accuracy — 3 needle-in-haystack tasks.

    Score: 15 pts (max, 5 per correct).
    """
    correct = 0
    evidence: list[str] = []

    for i, task in enumerate(NEEDLE_TASKS):
        answer, _ = _run_with_trajectory(
            task["query"], task["context"], backend, profile
        )
        needle = task["needle"]
        # Normalize comparison
        answer_lower = answer.lower().strip()
        needle_lower = needle.lower().strip()

        if needle_lower in answer_lower:
            correct += 1
            evidence.append(
                f"Needle {i + 1}: PASS — '{needle}' found in answer '{answer[:80]}'"
            )
        else:
            evidence.append(
                f"Needle {i + 1}: FAIL — expected '{needle}', got '{answer[:80]}'"
            )

    score = correct * 5
    return {
        "score": score,
        "max_score": 15,
        "evidence": evidence,
        "passed": correct >= 2,
    }


# ── Probe P7: Smart-quote rate ───────────────────────────────────────────


def probe_p7_smart_quotes(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P7: Smart-quote rate — informational deduction.

    Score: 5 pts max, deduct if smart quotes found.
    """
    query = "List the colors of the rainbow in order."
    context = (
        "Rainbow colors: red, orange, yellow, green, blue, indigo, violet. "
        "This is commonly remembered with the acronym ROYGBIV."
    )

    _, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)

    total_smart_quotes = 0
    total_chars = 0
    for a in assistants:
        content = a.get("content", "")
        total_smart_quotes += _smart_quote_count(content)
        total_chars += len(content)

    rate = total_smart_quotes / max(total_chars, 1) if total_chars else 0
    deduction = 5 if total_smart_quotes > 0 else 0

    evidence = [
        f"Smart quotes found: {total_smart_quotes} in {total_chars} chars "
        f"(rate: {rate:.4f})"
    ]
    if deduction:
        evidence.append("5 pt deduction applied.")
    else:
        evidence.append("No smart quotes — full credit.")

    return {
        "score": 5 - deduction,
        "max_score": 5,
        "evidence": evidence,
        "passed": deduction == 0,
    }


# ── Probe P8: Sub-call usage ─────────────────────────────────────────────


def probe_p8_subcall_usage(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P8: Sub-call usage — model uses llm_query appropriately.

    Score: 5 pts (max).
    """
    query = (
        "Summarize the key points about climate change from this long document."
    )
    context = (
        "Climate change refers to long-term shifts in temperatures and weather "
        "patterns. Human activities have been the main driver of climate change, "
        "primarily due to the burning of fossil fuels like coal, oil and gas. "
        "The consequences include rising sea levels, more intense storms, and "
        "changes in precipitation patterns."
    )

    _, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)

    has_llm_query = any(
        _LLM_QUERY_RE.search(a.get("content", "")) for a in assistants
    )

    evidence: list[str] = []
    if has_llm_query:
        evidence.append("llm_query() usage detected in model responses.")
        score = 5
    else:
        evidence.append("llm_query() NOT used — model handled context directly.")
        score = 3  # partial credit if task still completed

    return {
        "score": score,
        "max_score": 5,
        "evidence": evidence,
        "passed": has_llm_query,
    }


# ── Probe P9: Speed ──────────────────────────────────────────────────────


def probe_p9_speed(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P9: Speed — wall time and approximate tok/s (report only, no score).

    Score: 0 pts (informational only).
    """
    query = "What is 42 divided by 6?"
    context = "The answer is that 42 divided by 6 equals 7."

    t0 = time.perf_counter()
    answer, lines = _run_with_trajectory(query, context, backend, profile)
    wall_time = time.perf_counter() - t0

    # Estimate tokens from assistant messages
    assistants = _assistant_messages(lines)
    total_chars = sum(len(a.get("content", "")) for a in assistants)
    approx_tokens = _count_tokens_approx(total_chars)
    tok_per_sec = approx_tokens / wall_time if wall_time > 0 else 0

    turns = sum(1 for line in lines if line.get("event") == "turn_start")

    evidence = [
        f"Wall time: {wall_time:.2f}s",
        f"Approx tokens: {approx_tokens}",
        f"Tok/s: {tok_per_sec:.1f}",
        f"Turns used: {turns}",
        f"Final answer: {answer[:100]}",
    ]

    return {
        "score": 0,
        "max_score": 0,
        "evidence": evidence,
        "passed": True,  # always passes — informational
    }


# ── Probe registry ────────────────────────────────────────────────────────

PROBES: dict[str, Any] = {
    "P1": probe_p1_protocol_emission,
    "P2": probe_p2_helper_calls,
    "P3": probe_p3_stderr_recovery,
    "P4": probe_p4_answer_submission,
    "P5": probe_p5_format_discipline,
    "P6": probe_p6_needle_accuracy,
    "P7": probe_p7_smart_quotes,
    "P8": probe_p8_subcall_usage,
    "P9": probe_p9_speed,
}

WEIGHTS: dict[str, int] = {
    "P1": 20,
    "P2": 15,
    "P3": 15,
    "P4": 15,
    "P5": 10,
    "P6": 15,
    "P7": 5,
    "P8": 5,
    "P9": 0,
}

QUICK_PROBES = ["P1", "P4", "P6"]


# ── Scoring ───────────────────────────────────────────────────────────────


def _score_model(
    per_probe: dict[str, dict[str, Any]],
    weights: dict[str, int],
) -> float:
    """Compute weighted score from per-probe results.

    Returns 0-100 scaled score.
    """
    total_weight = sum(
        weights[p] for p in per_probe if p in weights
    )
    if total_weight == 0:
        return 0.0

    weighted_sum = 0.0
    for probe_id, result in per_probe.items():
        w = weights.get(probe_id, 0)
        if w == 0 or result["max_score"] == 0:
            continue
        weighted_sum += (result["score"] / result["max_score"]) * w

    return round(weighted_sum * 100 / total_weight, 1)


def _verdict(score: float) -> str:
    """Map numeric score to suitability verdict."""
    if score >= 75:
        return "SUITABLE"
    elif score >= 50:
        return "MARGINAL"
    else:
        return "NOT SUITABLE"


# ── Main entry point ─────────────────────────────────────────────────────


def check_model(
    backend: ModelBackend,
    model_id: str = "",
    *,
    quick: bool = False,
    profile: str = "tiny",
) -> dict[str, Any]:
    """Run the full probe battery against a model backend.

    Args:
        backend: ModelBackend instance to evaluate.
        model_id: Human-readable model identifier for reporting.
        quick: If True, run P1+P4+P6 only (50-pt scale).
        profile: Hardware profile for completion() calls.

    Returns:
        Dict with keys: model_id, score, verdict, per_probe, evidence_lines.
    """
    probe_ids = QUICK_PROBES if quick else list(PROBES.keys())
    weights = {k: WEIGHTS[k] for k in probe_ids}

    per_probe: dict[str, dict[str, Any]] = {}
    all_evidence: list[str] = []

    for pid in probe_ids:
        probe_fn = PROBES[pid]
        result = probe_fn(backend, model_id=model_id, profile=profile)
        per_probe[pid] = result
        for line in result["evidence"]:
            all_evidence.append(f"[{pid}] {line}")

    score = _score_model(per_probe, weights)

    return {
        "model_id": model_id or "unknown",
        "score": score,
        "verdict": _verdict(score),
        "per_probe": per_probe,
        "evidence_lines": all_evidence,
    }


# ── Report writer ────────────────────────────────────────────────────────


def save_check_report(
    result: dict[str, Any],
    output_dir: str | Path,
) -> Path:
    """Write a Markdown report with YAML frontmatter.

    Args:
        result: Dict from check_model().
        output_dir: Directory to write the report.

    Returns:
        Path to the written report file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    filename = f"model-check-{result['model_id'].replace('/', '-')}-{timestamp}.md"
    filepath = output_dir / filename

    # YAML frontmatter
    frontmatter_lines = [
        "---",
        f"model: {result['model_id']}",
        f"score: {result['score']}",
        f"verdict: {result['verdict']}",
        f"date: {date_str}",
        "---",
        "",
    ]

    # Body
    body_lines: list[str] = []
    body_lines.append(f"# Model Suitability Report: {result['model_id']}\n")
    body_lines.append(f"**Score:** {result['score']}/100")
    body_lines.append(f"**Verdict:** {result['verdict']}\n")

    body_lines.append("## Per-Probe Results\n")
    for pid, probe_result in result["per_probe"].items():
        status = "PASS" if probe_result["passed"] else "FAIL"
        body_lines.append(
            f"### {pid} — {probe_result['score']}/{probe_result['max_score']} "
            f"({status})\n"
        )
        for line in probe_result["evidence"]:
            body_lines.append(f"- {line}")
        body_lines.append("")

    body_lines.append("## Evidence Log\n")
    for line in result.get("evidence_lines", []):
        body_lines.append(f"- {line}")

    content = "\n".join(frontmatter_lines) + "\n".join(body_lines) + "\n"

    filepath.write_text(content, encoding="utf-8")
    return filepath
