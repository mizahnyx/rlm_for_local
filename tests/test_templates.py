"""Template discipline tests (R4.1 / R7).

`templates.py` claims that *every* string the harness emits is frozen there.
That claim is only true if each frozen string can actually be emitted — this
module enforces it, and enforces that no model-facing message is written inline
somewhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import rlm_local.templates as templates_mod

SRC = Path(templates_mod.__file__).parent
TEMPLATES_SRC = SRC / "templates.py"


def _template_constants() -> dict[str, str]:
    """Top-level UPPER_CASE string constants defined in templates.py."""
    tree_src = TEMPLATES_SRC.read_text(encoding="utf-8")
    names: dict[str, str] = {}
    for match in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=", tree_src, re.MULTILINE):
        name = match.group(1)
        value = getattr(templates_mod, name, None)
        if isinstance(value, str):
            names[name] = value
    return names


def _all_src_text_except_templates() -> str:
    parts: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "templates.py":
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class TestTemplateDiscipline:
    def test_there_are_templates_to_check(self):
        """Guard against the checker silently finding nothing (vacuity)."""
        constants = _template_constants()
        assert len(constants) >= 15, f"only found {sorted(constants)}"

    def test_no_dead_templates(self):
        """Every frozen template must be referenced by shipping code (R7).

        `NUDGE_EMPTY_ANSWER`, `CELL_TIMEOUT_ERROR`, `REPL_READY` and
        `REPL_FINAL_ANSWER` were previously defined-but-never-emitted; the
        first two are now wired and the last two deleted.
        """
        src = _all_src_text_except_templates()
        dead = [name for name in _template_constants() if name not in src]
        assert dead == [], (
            f"templates defined but never emitted by src/rlm_local: {dead}. "
            "Wire them or delete them."
        )

    def test_dead_boot_templates_removed(self):
        for name in ("REPL_READY", "REPL_FINAL_ANSWER"):
            assert not hasattr(templates_mod, name), (
                f"{name} was never emitted; R7 says delete or wire it"
            )

    def test_cell_timeout_error_is_used_by_the_repl(self):
        repl_src = (SRC / "repl.py").read_text(encoding="utf-8")
        assert "CELL_TIMEOUT_ERROR" in repl_src

    def test_cell_timeout_error_formats(self):
        rendered = templates_mod.CELL_TIMEOUT_ERROR.format(timeout=12.5)
        assert "12.5" in rendered

    def test_truncation_markers_format(self):
        assert "2048" in templates_mod.CELL_STDOUT_TRUNCATED.format(cap=2048)
        assert "17" in templates_mod.CELL_STDERR_TRUNCATED.format(elided=17)

    def test_no_answer_placeholders_are_non_empty(self):
        assert templates_mod.NO_ANSWER_PRODUCED.strip()
        assert templates_mod.FINALIZATION_FAILED.strip()

    def test_root_loop_uses_the_terminal_placeholders(self):
        root_src = (SRC / "root_loop.py").read_text(encoding="utf-8")
        assert "NO_ANSWER_PRODUCED" in root_src
        assert "FINALIZATION_FAILED" in root_src


class TestNoInlineHarnessStrings:
    """The R4.1 inverse check: no inline model-facing message duplicates."""

    # Message templates that were previously inline in root_loop.py / repl.py.
    INLINE_PATTERNS = [
        r'"[^"]*No answer produced[^"]*"',
        r'"[^"]*stderr truncated[^"]*"',
        r'"Error: REPL timed out',
        r'"Error: unexpected REPL response type',
    ]

    @pytest.mark.parametrize("pattern", INLINE_PATTERNS)
    def test_pattern_absent_from_src(self, pattern):
        src = _all_src_text_except_templates()
        hits = re.findall(pattern, src)
        assert hits == [], f"inline harness string(s) found: {hits}"
