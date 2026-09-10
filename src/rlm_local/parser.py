"""Guardrail parser (§5.6).

Ordered pipeline: extract ```repl blocks → rescue parse → templated retry nudges
→ stderr self-correction → finalization repair → sub-call output repair.

Each rule is cheap; the pipeline never asks the model to re-do work the harness
can do mechanically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rlm_local.templates import NUDGE_STDERR_ERROR


@dataclass
class ParseResult:
    """Result of parsing a root model response."""

    blocks: list[str] = field(default_factory=list)
    """Extracted code blocks to execute (in order)."""

    final_answer: str | None = None
    """Non-None if the model signaled completion via answer dict or FINAL: line."""

    warnings: list[str] = field(default_factory=list)
    """Templated harness warnings for this result."""

    nudge: str | None = None
    """If set, a templated retry nudge to append as a user message."""

    raw_text: str = ""
    """The original model response text (for logging)."""


FENCE_RE = re.compile(r"```(?:repl|python)?\s*\n(.*?)```", re.DOTALL)
FENCE_UNCLOSED_RE = re.compile(r"```(?:repl|python)?\s*\n(.*?)$", re.DOTALL)
FINAL_LINE_RE = re.compile(r"^FINAL:\s*(.+)$", re.MULTILINE)
ANSWER_READY_RE = re.compile(r"""answer\[(?:"ready"|'ready'|`ready`)\]\s*=\s*True""")
ANSWER_CONTENT_RE = re.compile(r"""answer\[(?:"content"|'content'|`content`)\]\s*=\s*(.+)$""", re.MULTILINE)

# Small models frequently emit Unicode "smart quotes" when writing code,
# which is a syntax error in Python (observed live with Qwen3.5-4B:
# answer[’content’] = ’…’). Normalize to ASCII before execution.
SMART_QUOTES: dict[str, str] = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
}


def normalize_code(text: str) -> tuple[str, bool]:
    """Replace smart quotes with ASCII quotes in a code block.

    Returns (normalized_text, changed) — changed=True if any replacement
    was made (callers should record a templated warning, R4.1).
    """
    changed = False
    for smart, ascii_q in SMART_QUOTES.items():
        if smart in text:
            text = text.replace(smart, ascii_q)
            changed = True
    return text, changed


class Parser:
    """Parses root model responses into executable blocks and termination signals."""

    def __init__(
        self,
        max_consecutive_nudges: int = 2,
        max_consecutive_errors: int = 3,
    ) -> None:
        self._consecutive_nudges = 0
        self._consecutive_errors = 0
        self._max_nudges = max_consecutive_nudges
        self._max_errors = max_consecutive_errors
        self._turn: int = 0

    def parse(self, text: str, *, turn: int = 0) -> ParseResult:
        """Parse a root model response. Returns executable blocks and/or termination.

        Args:
            text: Raw model response text.
            turn: Current turn number (0-indexed). Turn 0 gets extra safeguards.

        Returns:
            ParseResult with blocks, final_answer, warnings, and optional nudge.
        """
        self._turn = turn
        result = ParseResult(raw_text=text)

        # 1. Extract fenced ```repl blocks
        blocks = FENCE_RE.findall(text)
        if blocks:
            result.blocks = [b.strip() for b in blocks]
            self._normalize(result)
            self._consecutive_nudges = 0
            return result

        # 2. Rescue parse: unclosed final fence
        unclosed = FENCE_UNCLOSED_RE.findall(text)
        if unclosed and unclosed[0].strip():
            result.blocks = [unclosed[0].strip()]
            result.warnings.append("Unclosed ```repl fence repaired.")
            self._normalize(result)
            self._consecutive_nudges = 0
            return result

        # Stages 3/3b of the original pipeline ("```python or bare ```" and the
        # narration variant) were removed in R5: their regexes accepted a bare
        # or `python`-only language tag where stages 1/2 also accept `repl`, so
        # every text they matched had already been consumed above — they were
        # unreachable. The behaviour they were meant to provide is covered by
        # stages 1/2 (see tests/test_parser.py::TestUnreachableStagesRemoved).

        # 4. Courtesy FINAL: line
        final_match = FINAL_LINE_RE.search(text)
        if final_match:
            result.final_answer = final_match.group(1).strip()
            self._consecutive_nudges = 0
            return result

        # 5. Prose narration with no code at all: nudge
        from rlm_local.templates import NUDGE_NO_BLOCK, NUDGE_NARRATION

        # If the model seems to be describing code but didn't fence it
        code_keywords = ["llm_query", "peek(", "grep(", "chunk(", "print(",
                         "answer[", "map_query", "context["]
        if any(kw in text for kw in code_keywords):
            self._consecutive_nudges += 1
            if self._consecutive_nudges <= self._max_nudges:
                result.nudge = NUDGE_NARRATION
            else:
                result.nudge = None  # counted as error, forced finalization
            return result

        # 6. Nothing parseable at all
        self._consecutive_nudges += 1
        if self._consecutive_nudges <= self._max_nudges:
            result.nudge = NUDGE_NO_BLOCK
        return result

    def _normalize(self, result: ParseResult) -> None:
        """Normalize smart quotes in extracted blocks; warn once if any changed."""
        changed_any = False
        normalized: list[str] = []
        for block in result.blocks:
            nb, changed = normalize_code(block)
            normalized.append(nb)
            changed_any = changed_any or changed
        result.blocks = normalized
        if changed_any:
            result.warnings.append("Smart quotes normalized to ASCII.")

    def parse_stderr(self, text: str, stderr_text: str) -> ParseResult | None:
        """§5.6 stage 4 — stderr self-correction.

        Called after a cell produced a traceback. A cell that errored counts
        against the consecutive-error budget, so a model that keeps emitting
        broken code reaches forced finalization instead of spinning until the
        turn budget runs out; while the budget holds, the returned result
        carries a templated nudge naming the exception class.

        Returns None if the response contained no code (nothing to correct).
        Deliberately side-effect free apart from the error counter: it must not
        touch the nudge counter or re-run the main pipeline.
        """
        result = ParseResult(raw_text=text)

        blocks = FENCE_RE.findall(text)
        if not blocks:
            blocks = [b for b in FENCE_UNCLOSED_RE.findall(text) if b.strip()]
        if not blocks:
            return None

        result.blocks = [b.strip() for b in blocks]
        self._normalize(result)

        self._consecutive_errors += 1
        if self._consecutive_errors <= self._max_errors:
            result.nudge = NUDGE_STDERR_ERROR.format(
                error_kind=_error_kind(stderr_text),
                errors=self._consecutive_errors,
                max_errors=self._max_errors,
            )
        return result

    def check_answer_in_block(self, block: str) -> tuple[str | None, bool]:
        """Check if a code block sets answer['content'] and answer['ready']=True.

        Returns (content, ready). content is None if not set.
        """
        ready = ANSWER_READY_RE.search(block) is not None
        content_match = ANSWER_CONTENT_RE.search(block)
        content = content_match.group(1).strip().strip("'\"") if content_match else None
        return content, ready

    @property
    def consecutive_nudges(self) -> int:
        return self._consecutive_nudges

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    def reset(self) -> None:
        self._consecutive_nudges = 0
        self._consecutive_errors = 0
        self._turn = 0

    def reset_errors(self) -> None:
        """A cell executed without a traceback — the error streak is over.

        Called by the root loop after a clean REPL result. `parse()` must NOT
        do this: it runs *before* execution and so cannot know whether the code
        worked.
        """
        self._consecutive_errors = 0


_ERROR_KIND_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning|Interrupt))\b")


def _error_kind(stderr_text: str) -> str:
    """Best-effort exception class name from a traceback ('' → generic label)."""
    if not stderr_text:
        return "error"
    matches = _ERROR_KIND_RE.findall(stderr_text)
    if matches:
        return matches[-1]
    tail = [ln for ln in stderr_text.strip().splitlines() if ln.strip()]
    return tail[-1].strip() if tail else "error"


def repair_json(text: str, schema: dict[str, Any] | None = None) -> str:
    """Repair malformed JSON from sub-call responses (§5.6 item 6).

    Strips fences, balances braces, extracts the first plausible JSON object.
    Returns the original text if repair fails.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Try to find and balance a JSON object
    start = cleaned.find("{")
    if start == -1:
        cleaned = cleaned.strip().strip("'\"")
        return cleaned  # Not JSON; return as-is

    # Balance braces
    depth = 0
    end = start
    for i, ch in enumerate(cleaned[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if depth != 0:
        # Unbalanced — return what we have
        return cleaned[start:]

    return cleaned[start:end]
