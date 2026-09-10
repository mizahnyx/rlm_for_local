"""Gate validation without execution by default (S3 / R19).

`rlm-kernel review` used to `exec` model-authored helper code in the host
process on every quarantined page. Restricted-builtin `exec` is escapable via
`().__class__.__bases__[0].__subclasses__()` chains and the substring blocklist
is trivially bypassed, so the gate was never containment — but a read-looking
command that executes code needs to be an explicit opt-in.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from rlm_kernel.gate import validate
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus

ESCAPE_CHAIN = """\
## Signature
```python
def sneaky(x):
    ...
```

## Implementation
```python
def sneaky(x):
    # A __subclasses__ escape chain: passes a substring blocklist untouched.
    for cls in ().__class__.__bases__[0].__subclasses__():
        if cls.__name__ == "catch_warnings":
            return cls
    return x
```
"""

CLEAN_HELPER = """\
## Signature
```python
def double(x):
    ...
```

## Implementation
```python
def double(x):
    return x * 2
```
"""


def _helper_page(body: str, name: str = "double") -> Page:
    """A quarantined helper page whose `name` matches the callable it defines."""
    return Page(
        Frontmatter(
            kind=PageKind.HELPER,
            name=name,
            title="Probe Helper",
            summary="Used by the R19 tests",
            status=PageStatus.PENDING,
        ),
        body,
        path=f"quarantine/{name}.md",
    )


@pytest.fixture
def planted_exec(monkeypatch):
    """Sentinel that records whether *the gate's* sandbox `exec` ever ran.

    Scoped to `rlm_kernel.gate` rather than `builtins`: pydantic compiles field
    validators with `exec` at first use, so a global patch reports false
    positives.
    """
    import rlm_kernel.gate as gate_mod

    calls: list[str] = []
    real_exec = builtins.exec

    def spy(source, *args, **kwargs):
        calls.append(source if isinstance(source, str) else "<code>")
        return real_exec(source, *args, **kwargs)

    monkeypatch.setattr(gate_mod, "exec", spy, raising=False)
    return calls


class TestValidateDoesNotExecuteByDefault:
    def test_default_call_never_execs_helper_code(self, planted_exec):
        report = validate(_helper_page(CLEAN_HELPER))
        assert planted_exec == [], "validate() executed code without execute=True"
        assert report.passed, report.errors

    def test_default_call_never_execs_even_a_hostile_helper(self, planted_exec):
        """A page that would blow up on exec must still validate statically."""
        hostile = CLEAN_HELPER.replace(
            "return x * 2", "raise RuntimeError('executed!')"
        )
        report = validate(_helper_page(hostile))
        assert planted_exec == [], "validate() executed code without execute=True"
        # Static rules alone cannot see a runtime raise, so this passes — that
        # is exactly the documented limitation.
        assert report.passed

    def test_execute_true_runs_the_sandbox(self, planted_exec):
        report = validate(_helper_page(CLEAN_HELPER), execute=True)
        assert planted_exec, "execute=True must run the sandbox check"
        assert report.passed, report.errors

    def test_execute_true_still_catches_a_definition_time_failure(self, planted_exec):
        broken = CLEAN_HELPER.replace(
            "def double(x):", "raise ValueError('boom at import time')\n\n\ndef double(x):"
        )
        report = validate(_helper_page(broken), execute=True)
        assert not report.passed
        assert any("Sandbox execution failed" in e for e in report.errors)

    def test_sandbox_does_not_call_the_helper_documented_limitation(self, planted_exec):
        """`exec` only *defines* the function; it never calls it.

        So a helper whose body raises is not caught even with `execute=True`.
        That is the real reach of the sandbox and is documented in the kernel
        manual's trust-model section.
        """
        broken_body = CLEAN_HELPER.replace("return x * 2", "raise ValueError('boom')")
        report = validate(_helper_page(broken_body), execute=True)
        assert planted_exec, "the sandbox did run"
        assert report.passed, (
            "an in-body raise is invisible to a definition-only sandbox"
        )

    def test_static_mode_reports_that_it_is_static(self, planted_exec):
        report = validate(_helper_page(CLEAN_HELPER))
        assert any("static" in w.lower() for w in report.warnings), report.warnings
        assert report.executed is False

    def test_execute_mode_marks_the_report_as_executed(self, planted_exec):
        report = validate(_helper_page(CLEAN_HELPER), execute=True)
        assert report.executed is True


class TestStaticChecksStillBite:
    """Turning execution off must not turn the gate off."""

    def test_syntax_error_is_still_an_error(self):
        bad = CLEAN_HELPER.replace("return x * 2", "return x *")
        report = validate(_helper_page(bad))
        assert not report.passed
        assert any("syntax" in e.lower() for e in report.errors)

    def test_disallowed_import_is_still_an_error(self):
        bad = CLEAN_HELPER.replace("def double(x):\n    return x * 2",
                                   "import subprocess\n\ndef double(x):\n    return x * 2")
        report = validate(_helper_page(bad))
        assert not report.passed
        assert any("subprocess" in e or "allowlist" in e.lower() for e in report.errors)

    def test_missing_callable_is_still_an_error(self):
        bad = CLEAN_HELPER.replace("def double(x):", "def other(x):")
        report = validate(_helper_page(bad))
        assert not report.passed
        assert any("callable" in e.lower() or "does not define" in e.lower()
                   for e in report.errors)

    def test_escape_chain_passes_static_validation_and_is_flagged(self):
        """The honest result: a `__subclasses__` chain is NOT blocked.

        The substring blocklist has no entry for it, so static validation
        passes. The report must say so — that is the trust-model disclosure,
        not a silent green.
        """
        report = validate(_helper_page(ESCAPE_CHAIN, name="sneaky"))
        assert report.passed, report.errors
        assert any("static" in w.lower() for w in report.warnings)
        # And running it with execute=True is what would actually touch it.
        assert report.executed is False


class TestCliReviewIsOptIn:
    def _vault_with_proposal(self, tmp_path: Path):
        from rlm_kernel.vault import LocalVault

        vault = LocalVault(tmp_path, init_git=False)
        page = _helper_page(CLEAN_HELPER)
        vault.put(page, "quarantine/double.md")
        return vault

    def test_review_without_execute_never_execs(self, tmp_path, planted_exec, capsys):
        from rlm_kernel.cli import _cmd_review

        vault = self._vault_with_proposal(tmp_path)

        class Args:
            pass

        args = Args()
        args.vault = tmp_path
        rc = _cmd_review(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert planted_exec == [], "review executed code without --execute"
        assert "static validation only" in out.lower()

    def test_review_with_execute_flag_execs(self, tmp_path, planted_exec, capsys):
        from rlm_kernel.cli import _cmd_review

        self._vault_with_proposal(tmp_path)

        class Args:
            pass

        args = Args()
        args.vault = tmp_path
        args.execute = True
        rc = _cmd_review(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert planted_exec, "--execute must run the sandbox check"
        assert "static validation only" not in out.lower()

    def test_review_empty_quarantine_still_reports_static_mode(self, tmp_path, capsys):
        from rlm_kernel.cli import _cmd_review
        from rlm_kernel.vault import LocalVault

        LocalVault(tmp_path, init_git=False)

        class Args:
            pass

        args = Args()
        args.vault = tmp_path
        rc = _cmd_review(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "static validation only" in out.lower()
