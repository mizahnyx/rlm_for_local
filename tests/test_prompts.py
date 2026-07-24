"""Tests for prompts and templates modules."""

from __future__ import annotations

from rlm_local.prompts import (
    FEWSHOT_EXAMPLE,
    SYSTEM_PROMPT,
    build_messages,
    build_system_prompt,
)
from rlm_local.templates import (
    METADATA_TEMPLATE,
    NUDGE_NO_BLOCK,
    PROLOGUE,
    TURN_HEADER,
    TURN_ZERO_SAFEGUARD,
)


class TestSystemPrompt:
    def test_build_with_prompt_vars(self):
        pv = {
            "repl_cap": 2000,
            "sub_budget": 8000,
            "max_turns": 12,
            "example_chunking_idiom": "example code here",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        prompt = build_system_prompt(pv)
        assert "2000" in prompt
        assert "8000" in prompt
        assert "12" in prompt
        assert "example code here" in prompt

    def test_system_prompt_has_key_sections(self):
        pv = {
            "repl_cap": 2000,
            "sub_budget": 8000,
            "max_turns": 12,
            "example_chunking_idiom": "example",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        prompt = build_system_prompt(pv)
        assert "REPL contract" in prompt
        assert "How to work" in prompt
        assert "PROBE" in prompt
        assert "PLAN" in prompt
        assert "EXECUTE" in prompt

    def test_system_prompt_no_placeholders_remain(self):
        pv = {
            "repl_cap": 2000,
            "sub_budget": 8000,
            "max_turns": 12,
            "example_chunking_idiom": "chunk(context)",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        prompt = build_system_prompt(pv)
        # All {placeholders} should be filled
        assert "{repl_cap}" not in prompt
        assert "{sub_budget}" not in prompt
        assert "{max_turns}" not in prompt


class TestFewShots:
    def test_example_fewshot_has_blocks(self):
        """The few-shot should contain at least one ```repl block."""
        all_text = " ".join(c for _, c in FEWSHOT_EXAMPLE)
        assert "```repl" in all_text

    def test_example_fewshot_shows_answer_submission(self):
        """The few-shot should demonstrate answer['ready'] = True."""
        all_text = " ".join(c for _, c in FEWSHOT_EXAMPLE)
        assert "answer['ready']" in all_text or 'answer["ready"]' in all_text

    def test_example_fewshot_uses_grep(self):
        """The few-shot should demonstrate grep usage."""
        all_text = " ".join(c for _, c in FEWSHOT_EXAMPLE)
        assert "grep" in all_text


class TestBuildMessages:
    def test_builds_correct_structure(self):
        pv = {
            "repl_cap": 2000,
            "sub_budget": 8000,
            "max_turns": 12,
            "example_chunking_idiom": "chunk()",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        msgs = build_messages("test query", 1000, "str", pv)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "test query" in msgs[1]["content"]
        # Prologue
        assert msgs[2]["role"] == "user"
        assert "code" in msgs[2]["content"].lower()

    def test_metadata_contains_query_not_context(self):
        pv = {
            "sub_budget": 8000,
            "max_turns": 12,
            "repl_cap": 2000,
            "example_chunking_idiom": "chunk()",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        msgs = build_messages("find the answer", 50000, "str", pv)
        metadata = msgs[1]["content"]
        assert "find the answer" in metadata
        assert "50000" in metadata
        # Context itself should NOT be in the metadata
        # (R2.1: the context is not in the root prompt)


class TestTemplates:
    def test_metadata_template(self):
        msg = METADATA_TEMPLATE.format(
            query="test query",
            context_type="str",
            context_len=1000,
            sub_budget=8000,
            max_turns=12,
        )
        assert "test query" in msg
        assert "1000" in msg

    def test_turn_header(self):
        msg = TURN_HEADER.format(turn=1, max_turns=12)
        assert "1" in msg
        assert "12" in msg

    def test_turn_zero_safeguard(self):
        assert "not inspected" in TURN_ZERO_SAFEGUARD.lower()
        assert "peek" in TURN_ZERO_SAFEGUARD.lower()

    def test_prologue(self):
        assert "probe" in PROLOGUE.lower()
        assert "decomposes" in PROLOGUE.lower()

    def test_nudge_no_block(self):
        assert "```repl" in NUDGE_NO_BLOCK
