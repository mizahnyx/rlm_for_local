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
    DEFAULT_WEIGHT_PROFILE,
    DIAG_BLOCK_RAISED,
    DIAG_NOT_REACHED,
    DIAG_PROSE,
    DIAG_TEXT_NOT_CODE,
    DIAG_UNEXECUTABLE_TAG,
    PROBES,
    QUICK_PROBES,
    WEIGHTS,
    WEIGHT_PROFILES,
    _diagnose_missing_submission,
    _extract_call_text,
    _fence_regions,
    _normalize_for_match,
    _submission_sites,
    _text_kind_at,
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
    resolve_weights,
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


# ── P4 submission diagnostics (2026-09-11 assessment §3.1) ───────────────
#
# The recorded defect: `Qwen3.5-2B-Instruct` reported "answer['ready'] = True
# found in model output" *and* "No final answer detected" + forced finalization
# in the same P4 run, and scored 0 without the report saying why the two signals
# disagreed. These tests pin the explanation.


def _assistant(content: str, turn: int = 1) -> dict[str, Any]:
    """One `root_message` assistant entry, shaped as the logger writes it."""
    return {"event": "root_message", "role": "assistant",
            "content": content, "turn": turn}


def _repl(turn: int, stderr: str = "",
          final_answer: str | None = None) -> dict[str, Any]:
    """One `repl_result` entry, shaped as the logger writes it."""
    return {"event": "repl_result", "turn": turn, "stdout": "",
            "stderr": stderr, "final_answer": final_answer, "warnings": []}


class ProseSubmissionStub:
    """Emits the submission line as prose — the recorded 2026-09-11 shape.

    The model announces the submission without ever installing it, so P4 sees
    submission text in the transcript, no final answer, and forced finalization.
    """

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
        return (
            "I have determined the answer from the context.\n\n"
            "I will now set answer['ready'] = True so the loop receives it."
        )


class TestSubmissionSiteDetection:
    """Unit guards for the fence scanner the diagnostic is built on."""

    def test_fence_regions_span_the_block_body(self):
        text = "before\n```repl\nline one\nline two\n```\nafter"
        regions = _fence_regions(text)
        assert len(regions) == 1
        region = regions[0]
        assert region["tag"] == "repl"
        assert region["closed"] is True
        body = text[region["body_start"]:region["body_end"]]
        assert body.strip() == "line one\nline two"

    def test_unclosed_final_fence_is_reported_unclosed(self):
        region = _fence_regions("```repl\nx")[0]
        assert region["closed"] is False
        assert region["tag"] == "repl"

    def test_a_tag_with_trailing_words_uses_its_first_token(self):
        assert _fence_regions("```repl extra\nx\n```")[0]["tag"] == "repl"

    @pytest.mark.parametrize("tag", ["", "repl", "python"])
    def test_tags_the_parser_executes_are_marked_executable(self, tag):
        sites = _submission_sites(f"preamble\n```{tag}\nanswer['ready'] = True\n```")
        assert sites == [{
            "placement": "fence", "tag": tag, "executable": True, "in_text": None,
        }]

    def test_other_tags_are_marked_unexecutable(self):
        sites = _submission_sites("```bash\necho \"answer['ready'] = True\"\n```")
        assert sites[0]["placement"] == "fence"
        assert sites[0]["tag"] == "bash"
        assert sites[0]["executable"] is False

    def test_prose_and_fence_placement_are_both_reported(self):
        sites = _submission_sites(
            "I set answer['ready'] = True\n"
            "```repl\nanswer['ready'] = True\n```"
        )
        assert [s["placement"] for s in sites] == ["prose", "fence"]


class TestQuotedSubmissionText:
    """A live trajectory (`Qwen3.5-2B-Instruct`, 2026-09-11) showed a small model
    writing the submission *as text*: the ready-line regex matches, the
    interpreter only prints it, and the class "the line was not reached" would be
    the wrong explanation. `_text_kind_at` tells the two apart."""

    @pytest.mark.parametrize(
        "block,kind",
        [
            # real statements
            ("answer['ready'] = True", None),
            ("answer['content'] = 'x'\nanswer['ready'] = True", None),
            ("answer['content'] = \"it's fine\"\nanswer['ready'] = True", None),
            # text, not code
            ("print(\"answer['ready'] = True\")", "string"),
            ("s = 'answer[\\'ready\\'] = True'", "string"),
            ("s = '''answer['ready'] = True'''", "string"),
            ("# answer['ready'] = True", "comment"),
            ("print('ok')  # answer['ready'] = True", "comment"),
        ],
    )
    def test_placement_within_a_block(self, block, kind):
        offset = block.index("answer[")
        assert _text_kind_at(block, offset) == kind

    def test_a_closed_string_does_not_poison_what_follows(self):
        block = "label = 'submission'\nanswer['ready'] = True"
        assert _text_kind_at(block, block.index("answer[")) is None

    def test_an_escaped_quote_does_not_end_the_string_early(self):
        block = "s = 'it\\'s answer[\\'ready\\'] = True'"
        assert _text_kind_at(block, block.index("answer[")) == "string"

    def test_a_fenced_print_of_the_line_is_diagnosed_as_text(self):
        message = _assistant(
            "```repl\nprint(\"answer['ready'] = True\")\n```", turn=5
        )
        diagnostic = _diagnose_missing_submission([message], [_repl(5)])
        assert diagnostic["code"] == DIAG_TEXT_NOT_CODE
        assert "not as a statement" in diagnostic["detail"]
        assert "turn 5" in diagnostic["detail"]

    def test_a_commented_out_line_is_diagnosed_as_text(self):
        message = _assistant(
            "```repl\nanswer['content'] = 'x'\n# answer['ready'] = True\n```",
            turn=6,
        )
        diagnostic = _diagnose_missing_submission([message], [_repl(6)])
        assert diagnostic["code"] == DIAG_TEXT_NOT_CODE
        assert "comment" in diagnostic["detail"]

    def test_text_outranks_a_traceback_in_the_same_cell(self):
        """The line never being a statement is the actionable fact.

        A raise elsewhere in the cell does not change that, and reporting the
        traceback would send the reader looking in the wrong place.
        """
        message = _assistant(
            "```repl\nprint(\"answer['ready'] = True\")\nraise RuntimeError('x')\n```",
            turn=7,
        )
        diagnostic = _diagnose_missing_submission(
            [message], [_repl(7, stderr="RuntimeError: x")]
        )
        assert diagnostic["code"] == DIAG_TEXT_NOT_CODE

    def test_a_real_statement_still_gets_the_runtime_diagnosis(self):
        """The new check must not swallow the ordinary cases."""
        message = _assistant("```repl\nanswer['ready'] = True\n```", turn=8)
        assert _diagnose_missing_submission(
            [message], [_repl(8, stderr="NameError: name 'answer' is not defined")]
        )["code"] == DIAG_BLOCK_RAISED
        assert _diagnose_missing_submission([message], [_repl(8)])["code"] == DIAG_NOT_REACHED

    def test_prose_apostrophes_cannot_poison_a_block_scan(self):
        """`_text_kind_at` runs over the block body, never the whole message.

        Scanning a whole message would open a string at the first prose
        apostrophe ("Earth's") and mislabel every later block as text.
        """
        message = _assistant(
            "The ocean is Earth's largest.\n\n```repl\nanswer['ready'] = True\n```",
            turn=9,
        )
        diagnostic = _diagnose_missing_submission([message], [_repl(9)])
        assert diagnostic["code"] == DIAG_NOT_REACHED


class TestP4SubmissionDiagnostics:
    def test_prose_submission_text_is_located_outside_every_fence(self):
        message = _assistant("I will set answer['ready'] = True now.", turn=2)
        diagnostic = _diagnose_missing_submission([message], [])
        assert diagnostic["code"] == DIAG_PROSE
        assert "turn 2" in diagnostic["detail"]
        assert "never executed" in diagnostic["detail"]

    def test_unexecutable_fence_tag_is_named(self):
        message = _assistant("```bash\necho \"answer['ready'] = True\"\n```", turn=1)
        diagnostic = _diagnose_missing_submission([message], [_repl(1)])
        assert diagnostic["code"] == DIAG_UNEXECUTABLE_TAG
        assert "```bash" in diagnostic["detail"]

    def test_raised_block_is_reported_with_the_exception(self):
        message = _assistant(
            "```repl\nanswer['content'] = 'x'\nanswer['ready'] = True\n```",
            turn=3,
        )
        entries = [_repl(3, stderr=(
            "Traceback (most recent call last):\n"
            "  File \"<cell>\", line 1, in <module>\n"
            "NameError: name 'answer' is not defined"
        ))]
        diagnostic = _diagnose_missing_submission([message], entries)
        assert diagnostic["code"] == DIAG_BLOCK_RAISED
        assert "NameError: name 'answer' is not defined" in diagnostic["detail"]

    def test_clean_block_that_never_submits_says_the_line_was_not_reached(self):
        message = _assistant("```repl\nanswer['ready'] = True\n```", turn=4)
        diagnostic = _diagnose_missing_submission([message], [_repl(4)])
        assert diagnostic["code"] == DIAG_NOT_REACHED
        assert "not reached at runtime" in diagnostic["detail"]
        assert "turn 4" in diagnostic["detail"]

    @pytest.mark.parametrize("tag", ["", "repl", "python"])
    def test_a_block_the_parser_executed_is_never_blamed_on_its_tag(self, tag):
        """``` / ```repl / ```python all execute, so the tag is not the excuse."""
        message = _assistant(f"```{tag}\nanswer['ready'] = True\n```", turn=1)
        diagnostic = _diagnose_missing_submission([message], [_repl(1)])
        assert diagnostic["code"] == DIAG_NOT_REACHED

    def test_unclosed_fence_counts_as_executed(self):
        """Parser stage 2 rescues an unclosed fence, so it still runs."""
        message = _assistant("```repl\nanswer['ready'] = True", turn=1)
        diagnostic = _diagnose_missing_submission([message], [_repl(1)])
        assert diagnostic["code"] == DIAG_NOT_REACHED

    def test_an_executed_block_outranks_a_stray_prose_mention(self):
        """Evidence quality decides the order, not document order.

        A block the interpreter ran is authoritative: if it ran clean and still
        did not submit, the prose mention of the same line is not the reason.
        """
        message = _assistant(
            "Setting answer['ready'] = True.\n\n"
            "```repl\nanswer['ready'] = True\n```",
            turn=1,
        )
        diagnostic = _diagnose_missing_submission([message], [_repl(1)])
        assert diagnostic["code"] == DIAG_NOT_REACHED

    def test_no_submission_text_is_not_a_contradiction(self):
        assert _diagnose_missing_submission([_assistant("plain prose")], []) is None
        assert _diagnose_missing_submission([], []) is None

    def test_probe_scores_zero_and_attaches_the_diagnostic(self):
        result = probe_p4_answer_submission(ProseSubmissionStub())
        assert result["score"] == 0
        assert result["passed"] is False
        assert result["diagnostic"]["code"] == DIAG_PROSE
        assert any(
            line.startswith("DIAGNOSTIC [") for line in result["evidence"]
        ), result["evidence"]

    def test_a_real_submission_carries_no_diagnostic(self, good_backend):
        result = probe_p4_answer_submission(good_backend)
        assert result["passed"] is True
        assert result["diagnostic"] is None

    def test_a_model_that_never_mentions_the_line_carries_no_diagnostic(
        self, bad_backend
    ):
        result = probe_p4_answer_submission(bad_backend)
        assert result["diagnostic"] is None


# ── Battery weighting (2026-09-11 assessment §3.1, §6) ───────────────────
#
# P1 is saturated on the router — every model scored 20/20, down to 0.8B — so
# its weight moved onto P4 and P6, which produce the entire verdict spread.


class TestWeightProfiles:
    def test_default_full_battery_is_a_100_point_scale(self):
        assert sum(WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE].values()) == 100

    def test_default_quick_battery_is_a_50_point_scale(self):
        quick = sum(WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE][p] for p in QUICK_PROBES)
        assert quick == 50

    def test_default_moves_weight_onto_the_discriminating_probes(self):
        default = WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE]
        assert default["P1"] == 10
        assert default["P4"] > WEIGHT_PROFILES["p1-heavy"]["P4"]
        assert default["P6"] > WEIGHT_PROFILES["p1-heavy"]["P6"]

    def test_p1_heavy_reproduces_the_recorded_assessment_scale(self):
        """The dated 2026-09-11 scores must stay reproducible as recorded."""
        assert WEIGHT_PROFILES["p1-heavy"] == {
            "P1": 20, "P2": 15, "P3": 15, "P4": 15, "P5": 10,
            "P6": 15, "P7": 5, "P8": 5, "P9": 0,
        }

    def test_weights_constant_is_the_default_profile(self):
        assert WEIGHTS == WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE]

    @pytest.mark.parametrize("name", sorted(WEIGHT_PROFILES))
    def test_every_profile_weights_every_probe(self, name):
        """Guard: registering a probe without weighting it must fail loudly."""
        assert set(WEIGHT_PROFILES[name]) == set(PROBES)

    def test_unknown_profile_raises_instead_of_falling_back(self):
        with pytest.raises(ValueError, match="unknown weight profile"):
            resolve_weights("p1-heavvy")

    def test_resolve_weights_returns_a_copy(self):
        weights = resolve_weights(DEFAULT_WEIGHT_PROFILE)
        weights["P1"] = 999
        assert WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE]["P1"] == 10

    def test_protocol_only_model_is_penalised_harder_under_the_new_default(self):
        from rlm_local.model_check import _score_model

        protocol_only = {
            "P1": {"score": 20, "max_score": 20},
            "P4": {"score": 0, "max_score": 15},
            "P6": {"score": 0, "max_score": 15},
        }
        old = _score_model(
            protocol_only,
            {p: WEIGHT_PROFILES["p1-heavy"][p] for p in QUICK_PROBES},
        )
        new = _score_model(
            protocol_only,
            {p: WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE][p] for p in QUICK_PROBES},
        )
        assert old == 40.0
        assert new == 20.0

    def test_voluntary_submitter_separates_further_under_the_new_default(self):
        from rlm_local.model_check import _score_model

        submits_but_no_protocol = {
            "P1": {"score": 0, "max_score": 20},
            "P4": {"score": 15, "max_score": 15},
            "P6": {"score": 15, "max_score": 15},
        }
        old = _score_model(
            submits_but_no_protocol,
            {p: WEIGHT_PROFILES["p1-heavy"][p] for p in QUICK_PROBES},
        )
        new = _score_model(
            submits_but_no_protocol,
            {p: WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE][p] for p in QUICK_PROBES},
        )
        assert old == 60.0
        assert new == 80.0


class TestCheckModelScaleIsRecorded:
    def test_result_records_the_profile_and_the_resolved_weights(self):
        result = check_model(GoodModelStub(), model_id="scale", quick=True)
        assert result["weight_profile"] == DEFAULT_WEIGHT_PROFILE
        assert result["weights"] == {
            p: WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE][p] for p in QUICK_PROBES
        }

    def test_p1_heavy_can_still_be_selected_explicitly(self):
        result = check_model(
            GoodModelStub(), model_id="scale-old", quick=True,
            weight_profile="p1-heavy",
        )
        assert result["weight_profile"] == "p1-heavy"
        assert result["weights"] == {
            p: WEIGHT_PROFILES["p1-heavy"][p] for p in QUICK_PROBES
        }

    def test_unknown_profile_fails_before_any_probe_runs(self):
        backend = GoodModelStub()
        with pytest.raises(ValueError):
            check_model(
                backend, model_id="scale-bad", quick=True,
                weight_profile="does-not-exist",
            )
        assert backend.calls == []

    def test_report_names_the_scale_it_was_scored_on(self):
        result = check_model(GoodModelStub(), model_id="scale-report", quick=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            content = save_check_report(result, tmpdir).read_text(encoding="utf-8")
        assert f"weights: {DEFAULT_WEIGHT_PROFILE}" in content
        assert f"**Weights:** {DEFAULT_WEIGHT_PROFILE}" in content
        assert "P4 20" in content
