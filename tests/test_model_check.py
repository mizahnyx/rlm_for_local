"""Tests for model_check.py — stub-driven probe evaluation."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from rlm_local.model_backend import ModelBackend
from rlm_local.model_check import (
    PROBES,
    WEIGHTS,
    _extract_call_text,
    _normalize_for_match,
    check_model,
    probe_p1_protocol_emission,
    probe_p2_helper_calls,
    probe_p3_stderr_recovery,
    probe_p4_answer_submission,
    probe_p5_format_discipline,
    probe_p6_needle_accuracy,
    probe_p7_smart_quotes,
    probe_p8_subcall_usage,
    probe_p9_speed,
    save_check_report,
)

# ── Extract query helper (mirrors model_check internals for stub use) ────

_QUERY_FROM_META_RE = re.compile(r"^Answer the following:\s*(.+)$", re.MULTILINE)


def _extract_query(messages: list[dict[str, str]]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and "Your context is a " in msg.get("content", ""):
            m = _QUERY_FROM_META_RE.search(msg["content"])
            if m:
                return m.group(1).strip()
    return ""


# ── Good model stub ─────────────────────────────────────────────────────

_GOOD_ANSWERS: dict[str, str] = {
    "2+2": "4",
    "sky": "blue",
    "capital of France": "Paris",
    "Eiffel Tower": "7 million",
    "largest ocean": "Pacific Ocean",
    "boiling point": "100 degrees Celsius",
    "rainbow": "red, orange, yellow, green, blue, indigo, violet",
    "secret access code": "ALPHA-42",
    "treaty signed": "1748",
    "blood type": "O-Negative",
    "climate change": "rising sea levels and extreme weather",
    "42 divided by 6": "7",
    "Find lines": "important",
}


class GoodModelStub:
    """Stub that returns well-formed ```repl blocks and correct answers."""

    def __init__(self) -> None:
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []
        # Close-tracking: lets TestBackendReuse prove that a caller-supplied
        # backend is never closed by the probe battery.
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def _find_answer(self, query: str) -> str:
        query_lower = query.lower()
        for key, val in _GOOD_ANSWERS.items():
            if key.lower() in query_lower:
                return val
        return "determined from context"

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({
            "tier": tier,
            "message_count": len(messages),
        })
        # Sub-call: return a simple answer without consuming root turn
        if tier == "sub":
            return "key information: the answer is in the context"

        self._call_count += 1

        query = _extract_query(messages)
        answer = self._find_answer(query)

        if self._call_count % 2 == 1:
            # First response: probe with helpers + llm_query
            return (
                "Let me examine the context and search for relevant information.\n\n"
                "```repl\n"
                "print(f'Context length: {len(context)}')\n"
                "print(f'First 200 chars: {context[:200]}')\n"
                "hits = grep(r'.', max_hits=3)\n"
                "for h in hits[:3]:\n"
                "    print(h[:100])\n"
                "result = llm_query('extract key information from this text')\n"
                "print(f'llm result: {result[:100]}')\n"
                "```"
            )
        else:
            # Second response: submit answer
            safe_answer = answer.replace("\\", "\\\\").replace("'", "\\'")
            return (
                f"The answer is {answer}.\n\n"
                "```repl\n"
                f"answer['content'] = '{safe_answer}'\n"
                "answer['ready'] = True\n"
                "```"
            )


# ── Escaped-quote stub (F15 regression) ─────────────────────────────────


class EscapedQuoteStub:
    """Emits a helper call whose string argument contains an escaped quote.

    The pattern is chosen to discriminate the F15 lexer bug: the parenthetical
    opens *before* the escape (``Paris (the tower``) and closes *after* it
    (``isn\\'t)``). A lexer that treats the escaped quote as the end of the
    string sees that ``)`` as code, closes the call there, and extracts the
    unbalanced fragment ``grep('Paris (the tower isn\\'t)``.
    """

    CALL = r"hits = grep('Paris (the tower isn\'t) far from London')"

    def __init__(self) -> None:
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        if tier == "sub":
            return "the tower is in Paris"

        self._call_count += 1
        self.calls.append({"tier": tier, "message_count": len(messages)})

        if self._call_count == 1:
            return (
                "Let me search the context for the pattern.\n\n"
                "```repl\n"
                f"{self.CALL}\n"
                "print(hits[:3])\n"
                "```"
            )
        return (
            "The tower is in Paris.\n\n"
            "```repl\n"
            "answer['content'] = 'Paris'\n"
            "answer['ready'] = True\n"
            "```"
        )


# ── Hyphen-variant stub (R25 item 5 regression) ─────────────────────────


class HyphenVariantStub:
    """Answers every P6 needle correctly, but with different punctuation.

    The blood-type needle is spelled ``O-Negative`` in the context; this stub
    answers ``O Negative``. Nothing about the answer is wrong — only its
    hyphenation — so P6 must score it 3/3.
    """

    _ANSWERS = {
        "secret access code": "The access code is ALPHA-42.",
        "treaty signed": "The treaty was signed in 1748.",
        "blood type": "The patient's blood type is O Negative.",
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({"tier": tier, "message_count": len(messages)})
        if tier == "sub":
            return "not used"

        query = _extract_query(messages).lower()
        answer = "determined from context"
        for key, value in self._ANSWERS.items():
            if key in query:
                answer = value
                break
        safe = answer.replace("\\", "\\\\").replace("'", "\\'")
        return (
            f"{answer}\n\n"
            "```repl\n"
            f"answer['content'] = '{safe}'\n"
            "answer['ready'] = True\n"
            "```"
        )


# ── Bad model stub ──────────────────────────────────────────────────────


class ErrorAfterGrepStub:
    """Greps once, then errors on every later turn and never recovers.

    Used to pin P3's second documented weakness: the recovery check is not
    time-ordered, so the *pre-error* grep call is counted as recovery.
    """

    def __init__(self) -> None:
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({"tier": tier, "message_count": len(messages)})
        if tier == "sub":
            return "not used"

        self._call_count += 1
        if self._call_count == 1:
            return (
                "Let me grep the context first.\n\n"
                "```repl\n"
                "hits = grep('important')\n"
                "print(len(hits))\n"
                "```"
            )
        # Every later turn raises; nothing is ever corrected.
        return (
            "Let me try that again.\n\n"
            "```repl\n"
            "raise RuntimeError('boom')\n"
            "```"
        )


class BadModelStub:
    """Stub that returns prose without ```repl blocks, smart quotes, wrong answers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({
            "tier": tier,
            "message_count": len(messages),
        })

        # Always return prose with smart quotes, no ```repl blocks.
        # No code keywords (avoids NUDGE_NARRATION; gets NO_BLOCK nudge).
        return (
            "I believe the result is \u201cUNKNOWN\u201d. "
            "There\u2019s no reliable way to determine this "
            "from the information I can access. "
            "I would recommend checking the original source."
        )


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def good_backend() -> GoodModelStub:
    return GoodModelStub()


@pytest.fixture
def bad_backend() -> BadModelStub:
    return BadModelStub()


# ── Individual probe unit tests ──────────────────────────────────────────

class TestProbeP1:
    def test_good_model_passes(self, good_backend):
        result = probe_p1_protocol_emission(good_backend)
        assert result["score"] >= 13  # at least 2/3 trials
        assert result["passed"] is True
        assert len(result["evidence"]) == 3

    def test_bad_model_fails(self, bad_backend):
        result = probe_p1_protocol_emission(bad_backend)
        assert result["score"] <= 6  # at most 1/3 trials
        assert result["passed"] is False


class TestProbeP2:
    def test_good_model_passes(self, good_backend):
        result = probe_p2_helper_calls(good_backend)
        assert result["score"] == 15
        assert result["passed"] is True

    def test_bad_model_fails(self, bad_backend):
        result = probe_p2_helper_calls(bad_backend)
        assert result["score"] == 0
        assert result["passed"] is False

    def test_escaped_quote_in_helper_call_is_valid(self):
        """A call containing an escaped quote must not be scored INVALID.

        Regression guard for F15: the paren lexer used to skip the backslash
        but *not* the character it escapes, so ``'...isn\\'t...'`` flipped the
        string state to "outside a string". The text after the escaped quote —
        including the ``)`` that closes the string's own parenthetical — was
        then lexed as code, the call was truncated at that ``)``, and
        ``_balanced_parens`` reported the truncated fragment as unbalanced.

        The stub's pattern deliberately contains a parenthetical *before* the
        escaped quote (``(the tower``) closed by a ``)`` *after* it, which is
        what makes the broken lexer extract an unbalanced fragment rather than
        the empty string.
        """
        backend = EscapedQuoteStub()
        result = probe_p2_helper_calls(backend)
        assert result["passed"] is True, result["evidence"]
        assert result["score"] == 15, result["evidence"]


class TestParenLexerEscape:
    """Unit guards for `_extract_call_text` (F15 / R15)."""

    @pytest.mark.parametrize(
        "text",
        [
            # Escaped single quote inside a single-quoted pattern.
            r"grep('it\'s (a) test')",
            # Escaped double quote inside a double-quoted pattern.
            r'grep("say \"hi\" (now)")',
            # Escaped quote plus a parenthetical spanning the escape point —
            # the exact shape that produced a false "unbalanced" verdict.
            r"grep('Paris (the tower isn\'t) far')",
            r"print('it\'s (fine)')",
        ],
    )
    def test_escaped_characters_do_not_break_call_extraction(self, text):
        assert _extract_call_text(text, 0) == text

    @pytest.mark.parametrize(
        "text",
        [
            "peek(0, 100)",
            "chunk(context, size=3000)",
            "grep('plain (balanced) pattern')",
            "grep('quote inside \"double\" string')",
        ],
    )
    def test_unescaped_calls_still_extract_fully(self, text):
        assert _extract_call_text(text, 0) == text

    def test_extraction_starts_at_the_offset(self):
        cell = "hits = grep('a (b) c')\nprint(hits)"
        assert _extract_call_text(cell, cell.index("grep")) == "grep('a (b) c')"

    def test_unterminated_call_yields_empty_string(self):
        """Documented current behaviour: an unclosed call extracts as "".

        NOTE: `_balanced_parens("")` is True, so an unterminated call is
        currently scored *valid* by P2. That is a second, separate weakness of
        the probe's scoring (not the escape bug fixed here); it is recorded
        here so it cannot regress silently, and left as-is because tightening
        probe scoring is an owner decision (see P3 note in the module).
        """
        assert _extract_call_text("grep('never closed", 0) == ""


class TestProbeP3:
    def test_good_model_no_stderr(self, good_backend):
        result = probe_p3_stderr_recovery(good_backend)
        # Good model has no stderr → full credit
        assert result["score"] == 15
        assert result["passed"] is True

    def test_bad_model_gets_full_credit_documented_weakness(self, bad_backend):
        """P3 scores 15/15 for a model that never executes any code — ON PURPOSE.

        This pins the *documented* P3 weakness (F15; probe docstring
        "Known weakness", manual §16), it does not bless a bug: a model that
        emits no ```repl block produces no stderr event at all, and P3's first
        branch grants full credit whenever there was nothing to recover from.
        Emitting nothing is therefore scored exactly like recovering.

        Tightening this scoring changes score semantics and is an owner
        decision, deferred (R15). The assertions below make that deferral
        explicit: if someone tightens P3, this test goes red and the change has
        to be deliberate.
        """
        result = probe_p3_stderr_recovery(bad_backend)
        assert result["score"] == 15
        assert result["max_score"] == 15
        assert result["passed"] is True
        assert any(
            "No stderr events" in line for line in result["evidence"]
        ), result["evidence"]

    def test_pre_error_grep_counts_as_recovery_documented_weakness(self):
        """Second documented P3 weakness: the recovery scan is not time-ordered.

        P3 searches *all* assistant messages for a balanced ``grep(`` call, so a
        call made *before* the failing turn is credited as "recovery" the model
        never performed (F15). This stub greps once, then errors on every
        subsequent turn and never corrects anything — under the current
        (documented, deferred) scoring it still reports recovery and 15/15.
        """
        result = probe_p3_stderr_recovery(ErrorAfterGrepStub())
        assert result["score"] == 15, result["evidence"]
        assert result["passed"] is True
        assert any(
            "Recovery" in line for line in result["evidence"]
        ), result["evidence"]


class TestProbeP4:
    def test_good_model_passes(self, good_backend):
        result = probe_p4_answer_submission(good_backend)
        assert result["score"] == 15
        assert result["passed"] is True

    def test_bad_model_fails(self, bad_backend):
        result = probe_p4_answer_submission(bad_backend)
        # Bad model never submits answer properly → forced finalization
        assert result["score"] < 15
        assert result["passed"] is False


class TestProbeP5:
    def test_good_model_passes(self, good_backend):
        result = probe_p5_format_discipline(good_backend)
        assert result["score"] == 10
        assert result["passed"] is True

    def test_bad_model_fails(self, bad_backend):
        result = probe_p5_format_discipline(bad_backend)
        # Bad model has format violations (prose without ```repl blocks)
        assert result["score"] < 10


class TestProbeP6:
    def test_good_model_passes(self, good_backend):
        result = probe_p6_needle_accuracy(good_backend)
        assert result["score"] >= 10  # at least 2/3
        assert result["passed"] is True

    def test_bad_model_fails(self, bad_backend):
        result = probe_p6_needle_accuracy(bad_backend)
        assert result["score"] <= 5  # at most 1/3
        assert result["passed"] is False

    def test_hyphen_and_case_variants_count_as_correct(self):
        """R25 item 5 / F15: 'O-Negative' vs 'O Negative' must both score.

        The documented evaluator bug: the context spells the needle
        ``O-Negative``, and a model answering ``O Negative`` was scored wrong,
        costing 5 points. Case, hyphens, and whitespace must not decide
        correctness.
        """
        result = probe_p6_needle_accuracy(HyphenVariantStub())
        assert result["score"] == 15, result["evidence"]
        assert result["passed"] is True


class TestNeedleNormalization:
    """Unit guards for the needle-comparison normalizer (R25 item 5)."""

    @pytest.mark.parametrize(
        "left,right",
        [
            ("O-Negative", "O Negative"),
            ("O-Negative", "o  negative"),
            ("O\u2011Negative", "O Negative"),  # non-breaking hyphen
            ("ALPHA-42", "alpha 42"),
            ("  O   Negative  ", "o-negative"),
            ("it\u2019s", "it's"),  # smart apostrophe
        ],
    )
    def test_variants_normalize_identically(self, left, right):
        assert _normalize_for_match(left) == _normalize_for_match(right)
        assert _normalize_for_match(left) in _normalize_for_match(
            f"The answer is {right}."
        )

    def test_distinct_needles_stay_distinct(self):
        assert _normalize_for_match("O-Negative") != _normalize_for_match("O-Positive")
        assert _normalize_for_match("1748") != _normalize_for_match("1749")


class TestProbeP7:
    def test_good_model_no_smart_quotes(self, good_backend):
        result = probe_p7_smart_quotes(good_backend)
        assert result["score"] == 5
        assert result["passed"] is True

    def test_bad_model_has_smart_quotes(self, bad_backend):
        result = probe_p7_smart_quotes(bad_backend)
        assert result["score"] == 0  # 5 pt deduction
        assert result["passed"] is False


class TestProbeP8:
    def test_good_model_uses_llm_query(self, good_backend):
        result = probe_p8_subcall_usage(good_backend)
        assert result["score"] == 5
        assert result["passed"] is True

    def test_bad_model_no_llm_query(self, bad_backend):
        result = probe_p8_subcall_usage(bad_backend)
        assert result["score"] < 5
        assert result["passed"] is False


class TestProbeP9:
    def test_returns_info(self, good_backend):
        result = probe_p9_speed(good_backend)
        assert result["max_score"] == 0
        assert result["passed"] is True
        assert len(result["evidence"]) >= 3


# ── Integration: check_model ────────────────────────────────────────────

class TestCheckModel:
    def test_good_model_scores_high(self):
        backend = GoodModelStub()
        result = check_model(backend, model_id="test-good")
        assert result["score"] >= 75, f"Good model scored {result['score']}, expected >=75"
        assert result["verdict"] == "SUITABLE"
        assert len(result["per_probe"]) == len(PROBES)
        assert len(result["evidence_lines"]) > 0

    def test_bad_model_scores_low(self):
        backend = BadModelStub()
        result = check_model(backend, model_id="test-bad")
        assert result["score"] < 50, f"Bad model scored {result['score']}, expected <50"
        assert result["verdict"] == "NOT SUITABLE"
        assert len(result["per_probe"]) == len(PROBES)
        assert len(result["evidence_lines"]) > 0

    def test_quick_mode(self):
        backend = GoodModelStub()
        result = check_model(backend, model_id="test-quick", quick=True)
        assert set(result["per_probe"].keys()) == {"P1", "P4", "P6"}
        assert result["score"] >= 75
        assert result["verdict"] == "SUITABLE"


class TestSaveReport:
    def test_writes_report_file(self):
        backend = GoodModelStub()
        result = check_model(backend, model_id="test-report-model")

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_check_report(result, tmpdir)
            assert filepath.exists()
            content = filepath.read_text(encoding="utf-8")

            # Check frontmatter
            assert "model: test-report-model" in content
            assert "score: " in content
            assert "verdict: SUITABLE" in content
            assert "date: " in content

            # Check body sections
            assert "# Model Suitability Report:" in content
            assert "## Per-Probe Results" in content
            assert "## Evidence Log" in content



class TestBackendReuse:
    """Probes must not close a backend they did not create.

    `completion()` tracks `own_backend` and closes only a backend it built
    itself (`__init__.py`), because a caller may share one backend across many
    completions (and across a whole probe battery). This test used to be
    vacuous — the tracker was never armed because `GoodModelStub` had no
    `close()`, and the only assertion was the tautology `score >= 0`. It now
    arms the tracker and asserts the backend is still usable afterwards.
    """

    def test_check_model_does_not_close_shared_backend(self):
        backend = GoodModelStub()
        result = check_model(backend, model_id="test-reuse", quick=True)
        assert backend.close_calls == 0, (
            "check_model closed a caller-supplied backend; the caller owns it "
            "(completion() must only close a backend it created itself)"
        )

        # The backend must still be usable for a second battery.
        second = check_model(backend, model_id="test-reuse", quick=True)
        assert second["verdict"] == "SUITABLE"
        assert second["score"] == result["score"]
        assert backend.close_calls == 0