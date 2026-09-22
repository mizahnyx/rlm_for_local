"""Tests for drafting questions from passages, locally.

The properties that matter are all about what is *not* shipped: a reply that is commentary
is not a question, a question the probe cannot parse is not written, and the addresses that
pair a question with its passage never go anywhere but beside the corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rlm_local.question_draft import (
    Draft, as_questions, clean_question, draft_question, write_sources,
)
from rlm_local.templates import DRAFT_QUESTION_PROMPT, DRAFT_QUESTION_UNUSABLE


class FakeBackend:
    """A model that returns whatever it is told to, and records what it was asked."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(messages)
        return self.reply


class TestCleaningAReply:
    def test_a_plain_question_survives(self) -> None:
        assert clean_question("¿Cuántos hermanos tenía el autor?") == \
            "¿Cuántos hermanos tenía el autor?"
        assert clean_question("What year did the family leave?") == \
            "What year did the family leave?"

    def test_lead_ins_and_decoration_are_stripped(self) -> None:
        assert clean_question("Question: What happened in 1974?") == \
            "What happened in 1974?"
        assert clean_question("1. **What happened in 1974?**") == "What happened in 1974?"
        assert clean_question("- Pregunta: ¿Qué pasó en 1974?") == "¿Qué pasó en 1974?"

    def test_a_preamble_line_before_the_question_is_skipped(self) -> None:
        reply = "Sure, here is a question:\nWhat happened in 1974?"
        assert clean_question(reply) == "What happened in 1974?"

    def test_commentary_is_not_a_question(self) -> None:
        for reply in ("I cannot answer that from the passage.",
                      "As an AI, I need more context.",
                      "Lo siento, no puedo responder eso.",
                      "The passage does not contain an answer.",
                      ""):
            assert clean_question(reply) == "", reply

    def test_a_request_for_context_is_not_a_question_even_with_a_question_mark(self) -> None:
        """The case that makes the commentary filter load-bearing rather than decorative.

        Without it these would be *shipped*: each ends in a question mark, so the shape
        test accepts it, and a draft that asks the operator for more context is a wasted
        run rather than a probe.
        """
        for reply in ("Lo siento, ¿puedes darme más contexto?",
                      "I need more context to answer?",
                      "I cannot answer that?"):
            assert clean_question(reply) == "", reply

    def test_a_statement_is_not_turned_into_a_question(self) -> None:
        """No invented question marks: a statement is not a probe."""
        assert clean_question("The family left in 1974.") == ""

    def test_a_reply_with_no_usable_line_returns_empty(self) -> None:
        assert clean_question("###\n\n   \n*") == ""


class TestDrafting:
    def test_the_passage_reaches_the_prompt(self) -> None:
        backend = FakeBackend("What did they find inside?")
        draft = draft_question(backend, "PASSAGE-TEXT-HERE", draft_model="m",
                               identifier="prose-001", address="a/b.txt#L0-9")
        assert draft.usable and draft.identifier == "prose-001"
        sent = backend.calls[0][-1]["content"]
        assert "PASSAGE-TEXT-HERE" in sent
        assert "{passage}" not in sent, "the placeholder must be substituted, not shipped"
        assert sent.startswith(DRAFT_QUESTION_PROMPT.split("{passage}")[0][:20])

    def test_an_unusable_reply_is_marked_and_says_so(self) -> None:
        backend = FakeBackend("I cannot answer that.")
        draft = draft_question(backend, "text", identifier="prose-002")
        assert not draft.usable
        assert draft.question == DRAFT_QUESTION_UNUSABLE

    def test_only_usable_drafts_become_questions(self) -> None:
        drafts = [
            Draft("prose-001", "What happened?", "a#L0-9", "m", True),
            Draft("prose-002", DRAFT_QUESTION_UNUSABLE, "b#L0-9", "m", False),
        ]
        questions = as_questions(drafts)
        assert [q.id for q in questions] == ["prose-001"]
        assert questions[0].question == "What happened?"


class TestTheAddressesStayBesideTheCorpus:
    def test_sources_are_written_with_the_usable_flag(self, tmp_path: Path) -> None:
        drafts = [
            Draft("prose-001", "What happened?", "dir/file.txt#L0-9", "m", True),
            Draft("prose-002", DRAFT_QUESTION_UNUSABLE, "dir/other.txt#L9-20", "m", False),
        ]
        path = tmp_path / "set.sources.tsv"
        write_sources(drafts, path)
        text = path.read_text(encoding="utf-8")
        assert "prose-001\tdir/file.txt#L0-9\tyes" in text
        assert "prose-002\tdir/other.txt#L9-20\tno" in text
        assert text.startswith("# id\taddress\tusable")

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="Windows does not implement POSIX file modes")
    def test_the_sources_file_is_not_world_readable(self, tmp_path: Path) -> None:
        import stat

        path = tmp_path / "set.sources.tsv"
        write_sources([Draft("prose-001", "Q?", "a#L0-9", "m", True)], path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, oct(mode)


class TestThePromptIsShipped:
    def test_the_prompt_carries_the_rules_that_make_a_usable_probe(self) -> None:
        assert "{passage}" in DRAFT_QUESTION_PROMPT, (
            "without the placeholder the passage is never shown and every question is a "
            "question about nothing"
        )
        for rule in ("Do not name the file", "Do not include the answer", "question mark"):
            assert rule in DRAFT_QUESTION_PROMPT, rule
