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
            self._consecutive_nudges = 0
            self._consecutive_errors = 0
            return result

        # 2. Rescue parse: unclosed final fence
        unclosed = FENCE_UNCLOSED_RE.findall(text)
        if unclosed and unclosed[0].strip():
            result.blocks = [unclosed[0].strip()]
            result.warnings.append("Unclosed ```repl fence repaired.")
            self._consecutive_nudges = 0
            self._consecutive_errors = 0
            return result

        # 3. Rescue parse: ```python or bare ``` fences
        alt_blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if alt_blocks:
            result.blocks = [b.strip() for b in alt_blocks]
            result.warnings.append("Non-repl fence treated as ```repl.")
            self._consecutive_nudges = 0
            self._consecutive_errors = 0
            return result

        # 3b. Rescue parse: single obvious code block — model wrote code
        # without fences (narration pattern)
        alt_unclosed = re.findall(r"```(?:python)?\s*\n(.*?)$", text, re.DOTALL)
        if alt_unclosed:
            result.blocks = [alt_unclosed[0].strip()]
            result.warnings.append("Unclosed fence repaired (narration pattern).")
            self._consecutive_nudges = 0
            self._consecutive_errors = 0
            return result

        # 4. Courtesy FINAL: line
        final_match = FINAL_LINE_RE.search(text)
        if final_match:
            result.final_answer = final_match.group(1).strip()
            self._consecutive_nudges = 0
            self._consecutive_errors = 0
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

    def parse_stderr(self, text: str, stderr_text: str) -> ParseResult | None:
        """Check if the model fixed a previous error and extract the new block.

        Returns None if no fix was attempted.
        """
        result = self.parse(text)
        if result.blocks:
            self._consecutive_errors += 1
            return result
        return None

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
