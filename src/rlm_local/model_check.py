"""Model suitability checker (§D3).

Probe battery (P1-P9) evaluating whether a model is suitable as an RLM root
model. Each probe runs small completions and inspects the trajectory log.
"""

from __future__ import annotations

import json
import os
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


# Characters that read as "hyphen" in model output, including the Unicode
# forms a chat model emits when it is trying to be typographically correct.
_DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u00ad-"

# Smart quotes/apostrophes and the no-break space → ASCII, matching the
# normalization the parser already applies to model output in production.
_SMART_QUOTE_FOLD = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2032": "'", "\u2033": '"',
    "\u00a0": " ",
}


def _normalize_for_match(text: str) -> str:
    """Normalize text before needle comparison (R25 item 5, F15).

    Case, hyphenation, and whitespace must not decide whether a *correct*
    answer is scored correct. The documented evaluator bug: the medical-record
    needle is spelled ``O-Negative`` in the context, so a model answering
    ``O Negative`` lost 5 points for a right answer.

    Applies: lowercase → fold smart quotes and no-break spaces to ASCII →
    every dash form to a space → collapse whitespace → strip.
    """
    folded = "".join(_SMART_QUOTE_FOLD.get(ch, ch) for ch in text.lower())
    for ch in _DASH_CHARS:
        folded = folded.replace(ch, " ")
    return " ".join(folded.split())


def _extract_call_text(content: str, start: int) -> str:
    """Extract the text of a helper call that begins at ``start``.

    Scans forward from ``start`` tracking Python string state and parenthesis
    depth, and returns the substring up to *and including* the parenthesis that
    closes the call. If the call never closes, returns the empty string.

    Escaped characters inside a string literal are skipped as a unit: a
    backslash consumes the character that follows it. Without that, an escaped
    quote (``'it\\'s'``) would be read as the end of the string, the string's
    contents would be lexed as code, and the paren depth would be mis-tracked —
    so a perfectly valid call would be reported as "INVALID (unbalanced)"
    (F15 / R15).
    """
    paren_depth = 0
    in_str = False
    str_char = ""
    call_end = start
    i = start
    n = len(content)
    while i < n:
        ch = content[i]
        if in_str:
            if ch == "\\":
                # Skip the backslash *and* the escaped character.
                i += 2
                continue
            if ch == str_char:
                in_str = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = True
            str_char = ch
            i += 1
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth == 0:
                call_end = i + 1
                break
        i += 1
    return content[start:call_end]


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
                # Extract the call from the match start to its closing paren
                call_text = _extract_call_text(content, m.start())
                # BS3 (tightened 2026-09-12): an unterminated call extracts as ""
                # and `_balanced_parens("")` is True, which used to score it
                # *valid* — so `grep('never closed` counted as a correct helper
                # call. Emptiness is the caller's business: `_balanced_parens` is
                # a predicate about parens, and a call the lexer cannot delimit is
                # not a call.
                valid = bool(call_text.strip()) and _balanced_parens(call_text)
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


def _turn_after(candidate: Any, reference: Any) -> bool:
    """Is ``candidate`` a turn strictly after ``reference``?

    Turns are 1-based display turns. A missing or non-integer turn cannot be
    shown to come after anything, so it counts as *not* after — the conservative
    direction when the question is "did the model recover?" (BS2).
    """
    if not isinstance(candidate, int) or not isinstance(reference, int):
        return False
    return candidate > reference


def probe_p3_stderr_recovery(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P3: stderr recovery — after a cell raises, does the model get past it?

    Score: 15 pts (max).

    Three outcomes, and the difference between the first two is the whole point:

    * **No cell executed at all → 0/15.** The model never ran code, so recovery
      was never exercised. Emitting nothing is *not* recovering.
    * **Cells executed, none raised → 15/15.** Nothing to recover from, and the
      model demonstrably executed working code.
    * **A cell raised → 15/15 only if a later cell ran clean.** That is the
      behavioural measurement of "recovered": the error streak ended. Every cell
      after the failure raising again (or no cell following it, e.g. forced
      finalization) scores 0/15.

    Tightened 2026-09-12 (roadmap BS1/BS2)
    --------------------------------------
    The previous scoring measured *absence of failure* rather than recovery, in
    two documented ways (F15), and both are now fixed:

    1. **No-stderr ⇒ 15/15** gave a model that emitted no code the same score as
       a model that recovered. A model with no cells now scores 0.
    2. **The recovery scan was not time-ordered.** It accepted a balanced
       ``grep(`` call from *any* assistant message, including one made *before*
       the failing turn, so a model that never corrected anything was credited
       with "recovery". Recovery is now a property of a *later* cell.

    The old behaviour and the reason it was deferred are recorded in
    ``docs/20260910-0730-remediation-validation.md`` §7 and §16.4 of
    ``docs/rlm-local-manual.md``; this change was authorised as roadmap item 4
    (``docs/20260912-1155-roadmap.md``), because it moves score semantics.

    A valid helper call appearing after the failure is still reported, as
    supporting evidence — it is a *signal* of a deliberate correction, while the
    clean-cell measurement is the behaviour itself.
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

    evidence_lines: list[str] = []

    if not repl_entries:
        return {
            "score": 0,
            "max_score": 15,
            "evidence": [
                "No cell executed — the model never ran code, so recovery was "
                "never exercised. Emitting nothing is not recovering."
            ],
            "passed": False,
        }

    failing = [r for r in repl_entries if (r.get("stderr") or "").strip()]
    if not failing:
        return {
            "score": 15,
            "max_score": 15,
            "evidence": [
                f"{len(repl_entries)} cell(s) ran without a traceback — nothing "
                "to recover from."
            ],
            "passed": True,
        }

    for entry in failing:
        turn = entry.get("turn", "?")
        for line in (entry.get("stderr") or "").split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("File "):
                evidence_lines.append(f"Turn {turn} stderr: {stripped[:200]}")
                break
        else:
            evidence_lines.append(f"Turn {turn} stderr: (empty)")

    # Recovery is a *later* cell that ran clean (BS2: time-ordered).
    first_failure_turn = failing[0].get("turn")
    later_clean = [
        r for r in repl_entries
        if _turn_after(r.get("turn"), first_failure_turn)
        and not (r.get("stderr") or "").strip()
    ]
    recovered = bool(later_clean)
    if recovered:
        evidence_lines.append(
            f"Recovery: turn {later_clean[0].get('turn')} ran clean after the "
            f"failure on turn {first_failure_turn}."
        )
    else:
        evidence_lines.append(
            f"No recovery: no cell after the failure on turn {first_failure_turn} "
            "ran without a traceback."
        )

    # Supporting evidence only: a valid helper call after the failure.
    correcting_call = None
    for message in assistants:
        if not _turn_after(message.get("turn"), first_failure_turn):
            continue
        content = message.get("content", "") or ""
        for match in _GREP_CALL_RE.finditer(content):
            call_text = _extract_call_text(content, match.start())
            if call_text and _balanced_parens(call_text):
                correcting_call = call_text[:80]
                break
        if correcting_call:
            break
    evidence_lines.append(
        f"Corrected helper call after the failure: {correcting_call}"
        if correcting_call
        else "No valid helper call after the failure (supporting signal only)."
    )

    return {
        "score": 15 if recovered else 0,
        "max_score": 15,
        "evidence": evidence_lines,
        "passed": recovered,
    }


# ── Submission diagnostics (P4) ───────────────────────────────────────────
#
# P4 used to answer a contradiction with a silent 0. On 2026-09-11
# `Qwen3.5-2B-Instruct` put `answer['ready'] = True` into its message and the
# loop still reported "No final answer detected" plus forced finalization — the
# two signals disagreed and the report said nothing about why (assessment §3.1).
#
# A submission only counts when the *interpreter* runs the line, so the question
# "where did the model put it?" has a mechanical answer: prose the parser never
# extracts, a fence tag the parser never executes, or an executed block that
# raised or never reached the line. These helpers compute that answer from the
# trajectory alone, with no model in the loop, so they are unit-testable.

#: Fence tags `parser.FENCE_RE` executes. An untagged fence counts: the regex
#: makes the language tag optional, so ``` and ```repl behave identically.
_EXECUTABLE_FENCE_TAGS = frozenset({"", "repl", "python"})

DIAG_PROSE = "submission_text_outside_fence"
DIAG_UNEXECUTABLE_TAG = "submission_text_in_unexecutable_fence"
DIAG_TEXT_NOT_CODE = "submission_text_is_quoted_or_commented"
DIAG_BLOCK_RAISED = "submission_block_raised"
DIAG_ANSWER_REBOUND = "submission_answer_rebound"
DIAG_STATE_INCONSISTENT = "submission_state_inconsistent"
DIAG_NOT_REACHED = "submission_not_reached_at_runtime"


def _text_kind_at(block: str, offset: int) -> str | None:
    """Is ``offset`` inside a Python string literal or a comment?

    Returns ``"string"``, ``"comment"``, or ``None`` for real code.

    Best-effort lexer, not a parser: it walks the cell tracking quote state
    (single, double, triple, with backslash escapes) and ``#`` comments. It
    exists because of what a live trajectory showed — a small model writing the
    submission *as text* (``print("answer['ready'] = True")``), which the
    ready-line regex matches while the interpreter only prints it. Nothing
    submits, no traceback appears, and "the line was not reached at runtime"
    would then be the wrong explanation for the right class.

    It must be run over the *block body*, never over a whole message: prose
    apostrophes would open a string that never closes and poison everything
    after them.
    """
    state = "code"
    quote = ""
    i = 0
    while i < offset and i < len(block):
        ch = block[i]
        if state == "comment":
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "triple":
            if block.startswith(quote * 3, i):
                state = "code"
                i += 3
                continue
            i += 1
            continue
        if state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                state = "code"
            i += 1
            continue
        # state == "code"
        if ch == "#":
            state = "comment"
            i += 1
            continue
        if ch in "\"'":
            if block.startswith(ch * 3, i):
                state, quote = "triple", ch
                i += 3
            else:
                state, quote = "string", ch
                i += 1
            continue
        i += 1

    if state == "comment":
        return "comment"
    if state in ("string", "triple"):
        return "string"
    return None


def _fence_regions(text: str) -> list[dict[str, Any]]:
    """Span of every fenced block in ``text``, in document order.

    Line-based rather than regex-based: a model that emits a fence puts it at the
    start of a line, and spans are what the diagnostic needs to answer "was the
    submission line inside a block, and which one?". An unclosed final fence is
    returned with ``closed=False`` — the parser's stage 2 rescues it, so it still
    executes.
    """
    regions: list[dict[str, Any]] = []
    tag: str | None = None
    body_start = 0
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if tag is None:
                rest = stripped[3:].strip()
                tag = rest.split()[0] if rest else ""
                body_start = offset + len(line) + 1
            else:
                # Inside a fence, any bare ``` line closes it. A closing fence
                # never carries a language tag.
                regions.append({
                    "tag": tag,
                    "body_start": body_start,
                    "body_end": offset,
                    "closed": True,
                })
                tag = None
        offset += len(line) + 1
    if tag is not None:
        regions.append({
            "tag": tag,
            "body_start": body_start,
            "body_end": len(text),
            "closed": False,
        })
    return regions


def _submission_sites(text: str) -> list[dict[str, Any]]:
    """Locate every ``answer['ready'] = True`` occurrence in one message.

    Each site records where the line sits: ``prose`` when no fence region
    contains it, otherwise ``fence`` with the tag, whether the parser would
    execute that tag, and — for a fenced site — whether the occurrence is real
    code or merely text inside a string/comment in that block.
    """
    regions = _fence_regions(text)
    sites: list[dict[str, Any]] = []
    for match in _ANSWER_READY_RE.finditer(text):
        site: dict[str, Any] = {
            "placement": "prose",
            "tag": None,
            "executable": False,
            "in_text": None,
        }
        for region in regions:
            if region["body_start"] <= match.start() < region["body_end"]:
                body = text[region["body_start"]:region["body_end"]]
                site = {
                    "placement": "fence",
                    "tag": region["tag"],
                    "executable": region["tag"] in _EXECUTABLE_FENCE_TAGS,
                    "in_text": _text_kind_at(body, match.start() - region["body_start"]),
                }
                break
        sites.append(site)
    return sites


def _diagnose_missing_submission(
    assistants: list[dict[str, Any]],
    repl_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Explain why submission text in the transcript never became an answer.

    Returns ``None`` when there is nothing to explain (no assistant message
    carries the submission text at all). Otherwise a dict with a stable ``code``
    and a human-readable ``detail``.

    Precedence is by evidence quality, not by the order the text appears in: a
    block the interpreter actually *ran* is authoritative, so it is diagnosed
    before a stray prose mention of the same line. Only when the model never put
    the line in an executable block does the placement itself become the
    explanation:

    1. an executed block in which the line is *text*, not code — inside a string
       literal or after a ``#`` (the interpreter never sees it as a statement);
    2. an executed block whose cell raised a traceback;
    3. an executed block whose cell ended with `answer` rebound to something that
       is not a dict, so no submission could have taken effect (VD2 — this needs
       the cell-end state the REPL now reports);
    4. a cell that ended with `answer` ready yet recorded no submission, or whose
       `answer` could not be inspected at all — a harness inconsistency rather
       than a model verdict;
    5. an executed block that ran clean and still did not submit — the line was
       not reached at runtime (a conditional, a loop, or an earlier block in the
       same turn that ended the turn first), stated with more confidence when the
       cell-end state proves the scaffold survived the cell;
    6. text inside a tag the parser never executes (``json``, ``bash``, …);
    7. text outside every fence, which the parser never extracts at all.

    (1) outranks (2) deliberately: a traceback elsewhere in the cell does not
    change the fact that *this* line was never a statement, and that fact is the
    actionable one. (3) and (4) sit after (2) because a traceback is an observed
    event with its own evidence, while the end state is an interpretation of what
    was left behind.
    """
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for message in assistants:
        content = message.get("content", "") or ""
        for site in _submission_sites(content):
            candidates.append((message, site))

    if not candidates:
        return None

    def _turns(messages: list[dict[str, Any]]) -> str:
        return ", ".join(
            str(t) for t in sorted({m.get("turn") for m in messages}, key=str)
        )

    executed = [c for c in candidates if c[1]["executable"]]

    quoted = [c for c in executed if c[1]["in_text"]]
    if quoted:
        kinds = sorted({str(c[1]["in_text"]) for c in quoted})
        rendered = " and ".join(kinds)
        return {
            "code": DIAG_TEXT_NOT_CODE,
            "detail": (
                f"submission line sits inside a {rendered} in an executed block "
                f"(turn {_turns([m for m, _ in quoted])}), not as a statement — "
                "the interpreter never runs it as code, so `answer['ready']` is "
                "still False when the cell ends and P4 sees submission text with "
                "no submission. The model is describing the submission instead "
                "of performing it."
            ),
        }

    for message, _site in executed:
        turn = message.get("turn")
        for entry in repl_entries:
            if entry.get("turn") != turn:
                continue
            stderr = (entry.get("stderr") or "").strip()
            if not stderr:
                continue
            tail = [ln for ln in stderr.splitlines() if ln.strip()]
            return {
                "code": DIAG_BLOCK_RAISED,
                "detail": (
                    f"submission line sits in an executed block (turn {turn}) "
                    "whose cell raised before the submission ran: "
                    f"{tail[-1] if tail else stderr}"
                ),
            }

    # VD2: the cell-end state separates the cases the trajectory alone could not.
    # A rebound `answer` or a ready-but-unrecorded submission is a fact about the
    # cell's final state; without it, "not reached at runtime" was the only thing
    # the harness could honestly say.
    for message, _site in executed:
        turn = message.get("turn")
        for entry in repl_entries:
            if entry.get("turn") != turn:
                continue
            state = entry.get("answer_state")
            if not isinstance(state, dict):
                continue
            if state.get("error"):
                return {
                    "code": DIAG_STATE_INCONSISTENT,
                    "detail": (
                        f"the scaffold `answer` could not be read at the end of "
                        f"the cell (turn {turn}): {state['error']} raised while "
                        "inspecting it. That is a model-side object the harness "
                        "cannot interpret — report it as a harness limitation, not "
                        "a model verdict."
                    ),
                }
            if not state.get("is_dict"):
                kind = state.get("type", "unknown")
                return {
                    "code": DIAG_ANSWER_REBOUND,
                    "detail": (
                        f"`answer` was a {kind}, not a dict, when the cell ended "
                        f"(turn {turn}) — model code rebound the scaffold, so even "
                        "a submission line that did run could not have taken "
                        "effect. The cell must leave `answer` a dict."
                    ),
                }
            if state.get("ready"):
                return {
                    "code": DIAG_STATE_INCONSISTENT,
                    "detail": (
                        f"the cell's `answer` was ready at the end (turn {turn}) "
                        "yet no submission was recorded — the REPL derives a "
                        "submission from exactly that state, so this is a harness "
                        "inconsistency, not a model verdict."
                    ),
                }

    if executed:
        turns = _turns([m for m, _ in executed])
        # Sharper wording when the state proves the scaffold survived the cell.
        intact = any(
            isinstance(entry.get("answer_state"), dict)
            and entry["answer_state"].get("is_dict")
            and not entry["answer_state"].get("ready")
            and not entry["answer_state"].get("error")
            for message, _site in executed
            for entry in repl_entries
            if entry.get("turn") == message.get("turn")
        )
        if intact:
            detail = (
                "submission line is a real statement in an executed block (turn "
                f"{turns}) that ran clean, and the scaffold `answer` was still "
                "intact with ready=False when the cell ended — so the line "
                "genuinely never ran. Usual causes: a conditional or loop body "
                "that did not execute, or an earlier block in the same turn that "
                "ended the turn first."
            )
        else:
            detail = (
                "submission line is a real statement in an executed block (turn "
                f"{turns}) that produced no traceback and no submission, and the "
                "cell reported no end state to interpret — the harness cannot "
                "tell which of these it was: a conditional or loop body that did "
                "not run, `answer` rebound to something that is not a dict, or an "
                "earlier block in the same turn that ended the turn first."
            )
        return {"code": DIAG_NOT_REACHED, "detail": detail}

    wrong_tag = [c for c in candidates if c[1]["placement"] == "fence"]
    if wrong_tag:
        tags = sorted({str(c[1]["tag"]) for c in wrong_tag})
        rendered = ", ".join(f"```{t}" for t in tags)
        return {
            "code": DIAG_UNEXECUTABLE_TAG,
            "detail": (
                f"submission line appears inside a {rendered} fence (turn "
                f"{_turns([m for m, _ in wrong_tag])}) — only ```repl, "
                "```python and untagged fences are executed, so the harness "
                "discarded it."
            ),
        }

    return {
        "code": DIAG_PROSE,
        "detail": (
            "submission line appears outside any ``` fence (turn "
            f"{_turns([m for m, _ in candidates])}) — the parser extracts only "
            "fenced blocks (parser.py stages 1-2), so the line was never "
            "executed and the turn was answered with a narration nudge instead."
        ),
    }


# ── Probe P4: answer-dict submission ─────────────────────────────────────
#
# Three trials, like P1, and for a recorded reason: on 2026-09-11 a live run of
# `Qwen3.5-2B-Instruct` scored 46.7 (NOT SUITABLE) and then, minutes later on the
# same prompt against the same server, behaved as a voluntary submitter — 86.7
# (SUITABLE). P4 is worth 20 of the quick battery's 50 points, so one sample of a
# flaky behaviour was deciding a verdict band.
#
# The trials use three *different* questions, so one lucky prompt cannot decide it
# either. The first is the original single-shot query, so earlier recorded runs
# remain comparable at the trial level.

P4_QUERIES: list[tuple[str, str]] = [
    (
        "What is the name of the largest ocean?",
        "The Pacific Ocean is the largest and deepest of Earth's five oceanic "
        "divisions. It extends from the Arctic Ocean in the north to the "
        "Southern Ocean in the south.",
    ),
    (
        "How many players from one team are on the field in a soccer match?",
        "A soccer match is played by two teams, each fielding eleven players at "
        "a time, including one goalkeeper. Substitutes wait off the field.",
    ),
    (
        "What is the chemical symbol for gold?",
        "Gold is a chemical element with the symbol Au and atomic number 79. It "
        "is a dense, soft, malleable, and ductile metal.",
    ),
]

DEFAULT_P4_TRIALS = len(P4_QUERIES)
P4_TRIALS_ENV = "RLM_CHECK_P4_TRIALS"

#: Credit per trial. A voluntary submission is the property under test; a
#: submission that only happened once the loop forced finalization keeps the
#: fraction the previous single-shot scoring gave it (8/15).
#:
#: That second case is currently unreachable — the loop logs `forced=True` only
#: when no REPL submission happened, and any submission sets `forced=False` — but
#: it is kept so the scale stays continuous if the loop ever changes, and its
#: unreachability is pinned by a test rather than assumed.
_VOLUNTARY_CREDIT = 1.0
_FORCED_CREDIT = 8 / 15

P4_MAX_SCORE = 15


def resolve_p4_trials() -> int:
    """Number of P4 trials, from `RLM_CHECK_P4_TRIALS` when it is usable.

    Fewer trials cost proportionally less wall time — which matters on a host
    that serves a 4B model at single-digit tokens per second — at the price of
    exactly the stability this multi-trial sampling exists to buy. So any
    unusable value falls back to the *full* set rather than to a smaller one, and
    a run that used fewer says so in its evidence.
    """
    raw = os.environ.get(P4_TRIALS_ENV, "").strip()
    if not raw:
        return DEFAULT_P4_TRIALS
    try:
        requested = int(raw)
    except ValueError:
        return DEFAULT_P4_TRIALS
    if 1 <= requested <= DEFAULT_P4_TRIALS:
        return requested
    return DEFAULT_P4_TRIALS


def _score_p4_trials(outcomes: list[str]) -> tuple[float, bool]:
    """Score a list of trial outcomes. Pure, so the scale is directly testable.

    Returns ``(score_out_of_15, passed)``. `passed` is a majority of trials
    submitting voluntarily — a model that does it in two of three runs *is* a
    voluntary submitter with a flaky habit, and the score records how flaky.
    """
    if not outcomes:
        return 0.0, False

    credit = sum(
        _VOLUNTARY_CREDIT if outcome == "voluntary"
        else _FORCED_CREDIT if outcome == "forced"
        else 0.0
        for outcome in outcomes
    )
    voluntary = sum(1 for outcome in outcomes if outcome == "voluntary")
    score = round(P4_MAX_SCORE * credit / len(outcomes), 1)
    majority = len(outcomes) // 2 + 1
    return score, voluntary >= majority


def _run_p4_trial(
    backend: ModelBackend,
    query: str,
    context: str,
    profile: str,
) -> dict[str, Any]:
    """Run one P4 trial and classify what the model did."""
    final_answer, lines = _run_with_trajectory(query, context, backend, profile)
    assistants = _assistant_messages(lines)
    repl_entries = _repl_results(lines)

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

    if not has_final_answer:
        outcome = "none"
    elif forced:
        outcome = "forced"
    else:
        outcome = "voluntary"

    # The contradiction that used to be scored silently: submission text is
    # present, yet nothing submitted. Say where the line went instead of
    # reporting two disagreeing booleans and a 0.
    diagnostic: dict[str, Any] | None = None
    unlocatable = False
    if has_answer_ready and not has_final_answer:
        diagnostic = _diagnose_missing_submission(assistants, repl_entries)
        unlocatable = diagnostic is None

    return {
        "outcome": outcome,
        "final_answer": final_answer,
        "has_answer_ready": has_answer_ready,
        "diagnostic": diagnostic,
        "unlocatable": unlocatable,
    }


def probe_p4_answer_submission(
    backend: ModelBackend,
    model_id: str = "",
    profile: str = "tiny",
) -> dict[str, Any]:
    """P4: answer-dict submission — does the model submit *on its own*?

    `answer['content']` plus `answer['ready'] = True`, executed by the REPL
    within the turn budget, with no forced finalization. Several trials (see
    `P4_QUERIES`), because a single sample of this behaviour is what the
    2026-09-11 live run showed to be untrustworthy.

    Score: 15 pts (max) — the mean per-trial credit, so a model that submits in
    two of three trials scores 10 and one that never does scores 0.
    """
    trials = resolve_p4_trials()
    results = [
        _run_p4_trial(backend, query, context, profile)
        for query, context in P4_QUERIES[:trials]
    ]

    evidence: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    voluntary = sum(1 for r in results if r["outcome"] == "voluntary")
    forced_submissions = sum(1 for r in results if r["outcome"] == "forced")

    for i, result in enumerate(results, start=1):
        outcome = result["outcome"]
        answer_preview = str(result["final_answer"])[:100]

        if outcome == "voluntary":
            evidence.append(
                f"Trial {i}: PASS — submitted voluntarily: {answer_preview}"
            )
        elif outcome == "forced":
            evidence.append(
                f"Trial {i}: PARTIAL — submitted only after forced finalization: "
                f"{answer_preview}"
            )
        else:
            evidence.append(
                f"Trial {i}: FAIL — no submission (forced finalization)"
            )

        if result["diagnostic"] is not None:
            diagnostics.append({"trial": i, **result["diagnostic"]})
            evidence.append(
                f"Trial {i}: DIAGNOSTIC [{result['diagnostic']['code']}]: "
                f"{result['diagnostic']['detail']}"
            )
        elif result["unlocatable"]:
            evidence.append(
                f"Trial {i}: DIAGNOSTIC [submission_text_unlocatable]: the "
                "ready-text match was seen in a message but no occurrence could "
                "be located — report this as a harness bug, not a model verdict."
            )
        elif outcome == "none" and not result["has_answer_ready"]:
            evidence.append(
                f"Trial {i}: answer['ready'] = True never appeared in the output."
            )

    score, passed = _score_p4_trials([r["outcome"] for r in results])
    evidence.append(
        f"Summary: {voluntary}/{trials} trials submitted voluntarily"
        + (
            f", {forced_submissions} only after forced finalization"
            if forced_submissions else ""
        )
        + (" — majority voluntary" if passed else " — no majority voluntary")
    )
    if trials < DEFAULT_P4_TRIALS:
        evidence.append(
            f"NOTE: only {trials} of {DEFAULT_P4_TRIALS} trials ran "
            f"({P4_TRIALS_ENV}={trials}); this sample is smaller than the "
            "default, so a verdict drawn from it is correspondingly less stable."
        )

    return {
        "score": score,
        "max_score": P4_MAX_SCORE,
        "evidence": evidence,
        "passed": passed,
        "diagnostic": diagnostics[0] if diagnostics else None,
        "diagnostics": diagnostics,
        "trials": trials,
        "trial_results": [r["outcome"] for r in results],
        "voluntary_trials": voluntary,
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

    Matching is case-, hyphen-, and whitespace-insensitive (R25 item 5): the
    comparison runs on ``_normalize_for_match`` of both the answer and the
    needle, so ``O Negative`` is accepted for the ``O-Negative`` needle.
    """
    correct = 0
    evidence: list[str] = []

    for i, task in enumerate(NEEDLE_TASKS):
        answer, _ = _run_with_trajectory(
            task["query"], task["context"], backend, profile
        )
        needle = task["needle"]
        # Normalize comparison (case/hyphen/whitespace-insensitive)
        answer_norm = _normalize_for_match(answer)
        needle_norm = _normalize_for_match(needle)

        if needle_norm in answer_norm:
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

WEIGHT_PROFILES: dict[str, dict[str, int]] = {
    # Since the 2026-09-11 router sweep, P1 ("emits a valid ```repl block") is
    # saturated: all ten models scored 20/20, down to the 0.8B ones. A probe
    # every candidate passes cannot rank candidates, and its 20 points only
    # compress the spread that P4 and P6 produce — the entire SUITABLE/MARGINAL
    # split comes from those two. The default profile moves 10 points from P1
    # onto them. `p1-heavy` is the pre-change weighting.
    "default": {
        "P1": 10,
        "P2": 15,
        "P3": 15,
        "P4": 20,
        "P5": 10,
        "P6": 20,
        "P7": 5,
        "P8": 5,
        "P9": 0,
    },
    # Kept so the scores recorded in
    # docs/20260911-0050-router-model-assessment.md stay reproducible.
    "p1-heavy": {
        "P1": 20,
        "P2": 15,
        "P3": 15,
        "P4": 15,
        "P5": 10,
        "P6": 15,
        "P7": 5,
        "P8": 5,
        "P9": 0,
    },
}

DEFAULT_WEIGHT_PROFILE = "default"

#: The default profile, for callers and tests that just want "the weights".
WEIGHTS: dict[str, int] = WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE]

QUICK_PROBES = ["P1", "P4", "P6"]


def resolve_weights(profile: str = DEFAULT_WEIGHT_PROFILE) -> dict[str, int]:
    """Return a copy of a named weight profile.

    Raises:
        ValueError: on an unknown profile name — a typo must not silently fall
            back to a different scale and produce comparable-looking scores.
    """
    try:
        return dict(WEIGHT_PROFILES[profile])
    except KeyError:
        raise ValueError(
            f"unknown weight profile {profile!r}; "
            f"choose from {', '.join(sorted(WEIGHT_PROFILES))}"
        ) from None


def _render_weights(weights: dict[str, int]) -> str:
    """Render a weight map for a report line, e.g. ``P1 10, P4 20``."""
    return ", ".join(f"{pid} {w}" for pid, w in weights.items()) or "none"


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
    weight_profile: str = DEFAULT_WEIGHT_PROFILE,
) -> dict[str, Any]:
    """Run the full probe battery against a model backend.

    Args:
        backend: ModelBackend instance to evaluate.
        model_id: Human-readable model identifier for reporting.
        quick: If True, run P1+P4+P6 only. Their weights sum to 50 in every
            profile; the score is normalised to 100 either way.
        profile: Hardware profile for completion() calls.
        weight_profile: Named weighting from `WEIGHT_PROFILES`. Recorded in the
            result, because a score means nothing without the scale it came
            from.

    Returns:
        Dict with keys: model_id, score, verdict, per_probe, evidence_lines,
        weight_profile, weights.

    Raises:
        ValueError: on an unknown `weight_profile`.
    """
    import time as _time
    t0 = _time.perf_counter()

    probe_ids = QUICK_PROBES if quick else list(PROBES.keys())
    weights = {
        k: v
        for k, v in resolve_weights(weight_profile).items()
        if k in probe_ids
    }

    per_probe: dict[str, dict[str, Any]] = {}
    all_evidence: list[str] = []
    passed_count = 0
    failed_count = 0

    for pid in probe_ids:
        probe_fn = PROBES[pid]
        result = probe_fn(backend, model_id=model_id, profile=profile)
        per_probe[pid] = result
        if result["passed"]:
            passed_count += 1
        else:
            failed_count += 1
        for line in result["evidence"]:
            all_evidence.append(f"[{pid}] {line}")

    score = _score_model(per_probe, weights)
    elapsed = _time.perf_counter() - t0

    return {
        "model_id": model_id or "unknown",
        "score": score,
        "verdict": _verdict(score),
        "elapsed_seconds": round(elapsed, 1),
        "probes_passed": passed_count,
        "probes_failed": failed_count,
        "probes_total": len(probe_ids),
        "per_probe": per_probe,
        "evidence_lines": all_evidence,
        "weight_profile": weight_profile,
        "weights": weights,
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
        f"weights: {result.get('weight_profile', DEFAULT_WEIGHT_PROFILE)}",
        f"date: {date_str}",
        "---",
        "",
    ]

    # Body
    body_lines: list[str] = []
    body_lines.append(f"# Model Suitability Report: {result['model_id']}\n")
    body_lines.append(f"**Score:** {result['score']}/100")
    body_lines.append(f"**Verdict:** {result['verdict']}")
    body_lines.append(
        f"**Weights:** {result.get('weight_profile', DEFAULT_WEIGHT_PROFILE)} "
        f"({_render_weights(result.get('weights', {}))})"
    )
    body_lines.append(f"**Probes:** {result['probes_passed']}/{result['probes_total']} passed, "
                      f"{result['probes_failed']} failed")
    body_lines.append(f"**Time:** {result['elapsed_seconds']:.0f}s\n")

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
