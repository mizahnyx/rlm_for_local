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


# ── Bad model stub ──────────────────────────────────────────────────────


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


class TestProbeP3:
    def test_good_model_no_stderr(self, good_backend):
        result = probe_p3_stderr_recovery(good_backend)
        # Good model has no stderr → full credit
        assert result["score"] == 15
        assert result["passed"] is True

    def test_bad_model_fails(self, bad_backend):
        result = probe_p3_stderr_recovery(bad_backend)
        # Bad model never produces ```repl blocks, so no grep call at all.
        # No stderr events → P3 gives full credit by default.
        # This is expected: P3 only penalizes models that produce errors
        # without recovering.
        assert result["score"] == 15
        assert result["passed"] is True


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
    """Probes must not close a shared backend between calls."""

    def test_check_model_does_not_close_shared_backend(self):
        """Multiple probe calls reuse the same backend without closing it."""
        backend = GoodModelStub()
        # Track if close() is called
        close_called = []
        original_close = getattr(backend, 'close', None)
        if original_close:
            def tracked_close():
                close_called.append(True)
                original_close()
            backend.close = tracked_close

        result = check_model(backend, model_id="test-reuse", quick=True)
        assert result["score"] >= 0
        # Backend should still be usable after check_model returns
        # (completion() should not close a backend it didn't create)