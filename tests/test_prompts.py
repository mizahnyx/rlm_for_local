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


class TestPromptVarsDiscipline:
    """R16 — `prompt_vars` and the prompt template must agree exactly.

    An inert key is a capacity claim the model never sees; a placeholder with
    no key is a `KeyError` at run time. Both directions are checked.
    """

    def test_every_prompt_var_is_consumed_by_the_system_prompt(self):
        import re

        from rlm_local.config import load_config

        pv = load_config("laptop").prompt_vars()
        placeholders = set(re.findall(r"\{(\w+)\}", SYSTEM_PROMPT))
        assert set(pv) == placeholders, (
            f"inert: {sorted(set(pv) - placeholders)}; "
            f"unfilled: {sorted(placeholders - set(pv))}"
        )

    def test_prompt_vars_excludes_ctx_sizes(self):
        from rlm_local.config import load_config

        pv = load_config("laptop").prompt_vars()
        assert "root_ctx_size" not in pv
        assert "sub_ctx_size" not in pv


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

    def test_the_submission_example_is_not_a_quest(self):
        """The exemplar's content must not be imitable as a task (measured 2026-09-22).

        With the previous wording — "What is the vault access code?", answered `KX-2210` —
        two of six prose-derived questions stopped answering and searched the corpus for a
        vault access code instead. A 4B model given a broad question imitated the example's
        *content* because the example was the only concrete goal in the prompt. So the
        block now carries no memorisable quest, and says so in the harness's own voice.
        """
        all_text = " ".join(c for _, c in FEWSHOT_EXAMPLE).lower()
        for quest in ("vault", "access code", "secret", "kx-", "password", "server room"):
            assert quest not in all_text, (
                f"{quest!r} is in the few-shot block: a concrete quest in the example is "
                "what the model imitates instead of the question it was asked"
            )
        assert "example of the shape of a run, not a task" in all_text, (
            "the exemplar must say what it is, or a model can read it as the task"
        )

    def test_the_submission_example_keeps_the_lesson(self):
        """The structure it exists for survives the content change."""
        all_text = " ".join(c for _, c in FEWSHOT_EXAMPLE)
        assert "same turn" in all_text
        assert "did not ask for" in all_text, (
            "the scope discipline the vault failure lacked must be demonstrated, not just "
            "described in a comment"
        )


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


# ── R8 — vault few-shots must actually reach the prompt ────────────────────

class _FakeFrontmatter:
    def __init__(self, status="active", summary="A one-line summary."):
        self.status = type("S", (), {"value": status})()
        self.summary = summary


class _FakePage:
    def __init__(self, body, kind="fewshot", status="active", path="fewshot/x.md",
                 name=None):
        self.body = body
        self.kind = type("K", (), {"value": kind})()
        self.frontmatter = _FakeFrontmatter(status)
        self.path = path
        self.name = name or path.rsplit("/", 1)[-1].removesuffix(".md")


class _FakeVault:
    def __init__(self, pages):
        self._pages = pages

    def list(self, prefix="", kind=None):
        if kind is None:
            return list(self._pages)
        return [p for p in self._pages if p.kind.value == kind]

    def get(self, path):
        """No contract pages in these fixtures: the prompt falls back to the
        packaged SYSTEM_PROMPT and appends the helper section under test."""
        return None


#: The prompt_vars every prompt-assembly path needs (see Profile.prompt_vars).
_PROMPT_VARS = {
    "repl_cap": 2000,
    "sub_budget": 8000,
    "max_turns": 12,
    "example_chunking_idiom": "chunk()",
    "root_ctx_size": 8192,
    "sub_ctx_size": 8192,
}


QUERY_ANSWER_BODY = """\
# Few-Shot: needle-one

## Query
What year was the treaty signed?

## Answer
The Treaty of Westphalia was signed in 1648.
"""

EXAMPLE_SECTION_BODY = """\
# Few-Shot: chained

## Example

### User
Find the colour mentioned in the passage.

### Assistant
```repl
hits = grep('blue')
answer['content'] = hits[0]
answer['ready'] = True
```
"""


class TestHelperSectionSourceOfTruth:
    """DG9 — the prompt's helper section has one source.

    `KernelBridge.get_helper_summaries` had no production caller while
    `load_system_prompt_from_vault` built the same section itself (including the
    index-backed path), so the "which helpers does the model see" rule existed
    twice and only one copy was exercised.
    """

    def test_the_prompt_asks_the_bridge_for_helper_lines(self):
        from rlm_local.prompts import load_system_prompt_from_vault

        class _Bridge:
            index_path = None  # the bridge decides how to list; the caller does not

            def get_helper_summaries(self, limit: int = 30):
                return ["  sentinel: from the bridge"]

        prompt = load_system_prompt_from_vault(
            _PROMPT_VARS, vault=_FakeVault([]), bridge=_Bridge(),
        )

        assert "Available helpers:" in prompt
        assert "  sentinel: from the bridge" in prompt

    def test_without_a_bridge_the_vault_is_walked(self):
        from rlm_local.prompts import load_system_prompt_from_vault

        helper = _FakePage(
            "## Signature\n```python\ndef widget():\n```\n\n"
            "## Implementation\n```python\ndef widget():\n    return 1\n```\n",
            kind="helper",
            name="widget",
            path="helper/widget.md",
        )
        prompt = load_system_prompt_from_vault(_PROMPT_VARS, vault=_FakeVault([helper]))

        assert "  widget:" in prompt

    def test_the_vault_walk_keeps_the_cap(self):
        """The no-bridge path caps its own list, like the bridge path does."""
        from rlm_local.prompts import load_system_prompt_from_vault

        helpers = [
            _FakePage(
                "## Signature\n```python\ndef h():\n```\n\n"
                "## Implementation\n```python\ndef h():\n    return 1\n```\n",
                kind="helper",
                name=f"helper-{i:03d}",
                path=f"helper/helper-{i:03d}.md",
            )
            for i in range(40)
        ]
        prompt = load_system_prompt_from_vault(_PROMPT_VARS, vault=_FakeVault(helpers))

        assert prompt.count("  helper-") == 30


class TestVaultFewShots:
    """R8 — `load_fewshots_from_vault` used to ignore the vault entirely and
    return the hardcoded example, so K4's `bootstrap_fewshots` promoted pages
    that could never influence a prompt."""

    def test_no_vault_returns_builtin(self):
        from rlm_local.prompts import load_fewshots_from_vault

        assert load_fewshots_from_vault(None) == list(FEWSHOT_EXAMPLE)

    def test_empty_vault_returns_builtin_unchanged(self):
        from rlm_local.prompts import load_fewshots_from_vault

        assert load_fewshots_from_vault(_FakeVault([])) == list(FEWSHOT_EXAMPLE)

    def test_query_answer_page_yields_a_pair(self):
        from rlm_local.prompts import load_fewshots_from_vault

        pairs = load_fewshots_from_vault(
            _FakeVault([_FakePage(QUERY_ANSWER_BODY)]),
            prompt_char_budget=8000,
        )
        assert pairs[: len(FEWSHOT_EXAMPLE)] == list(FEWSHOT_EXAMPLE)
        added = pairs[len(FEWSHOT_EXAMPLE):]
        assert added == [
            ("user", "What year was the treaty signed?"),
            ("assistant", "The Treaty of Westphalia was signed in 1648."),
        ]

    def test_example_section_page_yields_a_pair(self):
        from rlm_local.prompts import load_fewshots_from_vault

        pairs = load_fewshots_from_vault(
            _FakeVault([_FakePage(EXAMPLE_SECTION_BODY)]),
            prompt_char_budget=8000,
        )
        added = pairs[len(FEWSHOT_EXAMPLE):]
        assert len(added) == 2
        assert added[0][0] == "user"
        assert "colour" in added[0][1]
        assert added[1][0] == "assistant"
        assert "grep('blue')" in added[1][1]

    def test_oversized_fewshot_is_skipped(self):
        from rlm_local.prompts import load_fewshots_from_vault

        huge = _FakePage(
            "## Query\n" + "q" * 5000 + "\n\n## Answer\n" + "a" * 5000 + "\n"
        )
        # budget/4 = 1000 chars, so a 10k-char pair cannot fit
        assert load_fewshots_from_vault(
            _FakeVault([huge]), prompt_char_budget=4000,
        ) == list(FEWSHOT_EXAMPLE)

    def test_at_most_one_vault_fewshot_is_appended(self):
        from rlm_local.prompts import load_fewshots_from_vault

        pages = [
            _FakePage(QUERY_ANSWER_BODY, path="fewshot/a.md"),
            _FakePage(QUERY_ANSWER_BODY, path="fewshot/b.md"),
        ]
        pairs = load_fewshots_from_vault(_FakeVault(pages), prompt_char_budget=8000)
        assert len(pairs) == len(FEWSHOT_EXAMPLE) + 2

    def test_unparseable_body_is_ignored(self):
        from rlm_local.prompts import load_fewshots_from_vault

        page = _FakePage("# Just a title\n\nNothing structured here.\n")
        assert load_fewshots_from_vault(
            _FakeVault([page]), prompt_char_budget=8000,
        ) == list(FEWSHOT_EXAMPLE)

    def test_inactive_page_is_ignored(self):
        from rlm_local.prompts import load_fewshots_from_vault

        page = _FakePage(QUERY_ANSWER_BODY, status="deprecated")
        assert load_fewshots_from_vault(
            _FakeVault([page]), prompt_char_budget=8000,
        ) == list(FEWSHOT_EXAMPLE)

    def test_build_messages_contains_the_vault_pair(self):
        from rlm_local.prompts import load_fewshots_from_vault

        pv = {
            "repl_cap": 2000,
            "sub_budget": 8000,
            "max_turns": 12,
            "example_chunking_idiom": "chunk()",
            "root_ctx_size": 8192,
            "sub_ctx_size": 8192,
        }
        pairs = load_fewshots_from_vault(
            _FakeVault([_FakePage(QUERY_ANSWER_BODY)]), prompt_char_budget=8000,
        )
        msgs = build_messages("q", 10, "str", pv, fewshots=pairs)
        assert {"role": "assistant", "content": "The Treaty of Westphalia was signed in 1648."} in msgs
        assert any(
            m["role"] == "user" and m["content"] == "What year was the treaty signed?"
            for m in msgs
        )

    def test_vault_exception_falls_back_to_builtin(self):
        from rlm_local.prompts import load_fewshots_from_vault

        class Exploding:
            def list(self, prefix="", kind=None):
                raise RuntimeError("vault is broken")

        assert load_fewshots_from_vault(
            Exploding(), prompt_char_budget=8000,
        ) == list(FEWSHOT_EXAMPLE)
