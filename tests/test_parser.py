"""Tests for parser module (§5.6 guardrail layer)."""

from __future__ import annotations

from rlm_local.parser import Parser, repair_json


class TestParser:
    def test_extracts_repl_fenced_blocks(self):
        parser = Parser()
        text = """Some prose.

```repl
print("hello")
```

More prose.

```repl
x = 1 + 2
print(x)
```
"""
        result = parser.parse(text)
        assert len(result.blocks) == 2
        assert 'print("hello")' in result.blocks[0]
        assert "x = 1 + 2" in result.blocks[1]

    def test_extracts_python_fence_as_repl(self):
        parser = Parser()
        text = """```python
print("hello")
```
"""
        result = parser.parse(text)
        assert len(result.blocks) == 1
        assert 'print("hello")' in result.blocks[0]

    def test_extracts_bare_fence(self):
        parser = Parser()
        text = """```
print("hello")
```
"""
        result = parser.parse(text)
        assert len(result.blocks) == 1
        assert 'print("hello")' in result.blocks[0]

    def test_rescue_unclosed_fence(self):
        parser = Parser()
        text = """```repl
print("hello")
"""
        result = parser.parse(text)
        assert len(result.blocks) == 1
        assert 'print("hello")' in result.blocks[0]
        assert len(result.warnings) >= 1

    def test_courtesy_final_line(self):
        parser = Parser()
        text = """Here is my answer.

FINAL: The Treaty of Westphalia was signed in 1648.
"""
        result = parser.parse(text)
        assert result.final_answer == "The Treaty of Westphalia was signed in 1648."
        assert len(result.blocks) == 0

    def test_narration_nudge(self):
        parser = Parser()
        text = "I would run llm_query to find the answer."
        result = parser.parse(text)
        assert result.nudge is not None
        assert len(result.blocks) == 0

    def test_no_block_nudge(self):
        parser = Parser()
        text = "The answer is 42."
        result = parser.parse(text)
        assert result.nudge is not None
        assert len(result.blocks) == 0

    def test_nudge_budget_exhausted(self):
        parser = Parser(max_consecutive_nudges=2)
        # First nudge
        r1 = parser.parse("The answer is 42.")
        assert r1.nudge is not None
        # Second nudge
        r2 = parser.parse("Still no code.")
        assert r2.nudge is not None
        # Third — no more nudges
        r3 = parser.parse("I give up.")
        assert r3.nudge is None  # exhausted

    def test_check_answer_in_block(self):
        parser = Parser()
        block = '''
answer["content"] = "The year is 1648"
answer["ready"] = True
'''
        content, ready = parser.check_answer_in_block(block)
        assert ready is True
        assert content == "The year is 1648"

    def test_check_answer_not_ready(self):
        parser = Parser()
        block = '''
answer["content"] = "The year is 1648"
'''
        content, ready = parser.check_answer_in_block(block)
        assert ready is False
        assert content == "The year is 1648"

    def test_reset(self):
        parser = Parser(max_consecutive_nudges=2)
        parser.parse("bad")
        parser.parse("bad again")
        assert parser.consecutive_nudges == 2
        parser.reset()
        assert parser.consecutive_nudges == 0


class TestRepairJson:
    def test_strips_markdown_fence(self):
        result = repair_json('```json\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'

    def test_balances_braces(self):
        result = repair_json('Here is the result: {"name": "test", "value": 42} and more text')
        assert result == '{"name": "test", "value": 42}'

    def test_returns_as_is_non_json(self):
        result = repair_json("Just plain text")
        assert result == "Just plain text"

    def test_handles_nested_braces(self):
        result = repair_json('{"outer": {"inner": 1}}')
        assert result == '{"outer": {"inner": 1}}'

    def test_handles_unbalanced(self):
        result = repair_json('{"key": "value"')
        # Should return what it can
        assert result.startswith("{")
