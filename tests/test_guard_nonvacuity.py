"""Tests for the guard-mutation runner itself (roadmap CL5).

The table is the project's instrument for "is this guard real?", so how it
*reports* matters as much as what it mutates. On 2026-09-15 the full table
reported `R23 the migration does not rewrite the key` as VACUOUS in two of three
full runs, while that entry is red in three isolated runs and in the third full
run. A green first result is therefore confirmed once before it becomes a
verdict, and these tests pin that behaviour — including the case that matters,
where the confirmation still comes back green and the entry is reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_guard_nonvacuity as cg  # noqa: E402


def _fake_entry(tmp_path: Path) -> tuple:
    """One mutation entry over a throwaway source file, plus that file."""
    target = tmp_path / "src" / "fake.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("GUARD = True\n", encoding="utf-8")
    entry = (
        "FAKE the guard is removed",
        "src/fake.py",
        "GUARD = True",
        "GUARD = False",
        ["tests/test_guard_nonvacuity.py::TestTheRunnerConfirmsAVacuousVerdict"],
    )
    return entry, target


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """Point the runner at a scratch root with one entry, and stub the tests."""
    entry, target = _fake_entry(tmp_path)
    monkeypatch.setattr(cg, "ROOT", tmp_path)
    monkeypatch.setattr(cg, "MUTATIONS", [entry])
    monkeypatch.setattr(sys, "argv", ["check_guard_nonvacuity.py"])
    return entry, target, monkeypatch


class TestTheRunnerConfirmsAVacuousVerdict:
    def test_a_green_first_run_is_confirmed_and_a_red_confirm_is_accepted(
        self, runner,
    ) -> None:
        """The flake this was written for: green, then red, means not vacuous."""
        _, target, monkeypatch = runner
        calls = []

        def fake_run_tests(nodes):
            calls.append(nodes)
            # First run green (the flake), the confirmation red (the truth).
            return 0 if len(calls) == 1 else 1

        monkeypatch.setattr(cg, "run_tests", fake_run_tests)
        assert cg.main() == 0
        assert len(calls) == 2, "a VACUOUS verdict must be confirmed once"
        assert target.read_text(encoding="utf-8") == "GUARD = True\n"

    def test_a_confirmed_green_is_reported_as_a_problem(self, runner) -> None:
        _, target, monkeypatch = runner
        calls = []
        monkeypatch.setattr(cg, "run_tests",
                            lambda nodes: calls.append(nodes) or 0)
        assert cg.main() == 1
        assert len(calls) == 2
        assert target.read_text(encoding="utf-8") == "GUARD = True\n"

    def test_a_red_first_run_is_not_re_run(self, runner) -> None:
        """The common case stays one test run: the table is slow enough."""
        _, _, monkeypatch = runner
        calls = []
        monkeypatch.setattr(cg, "run_tests",
                            lambda nodes: calls.append(nodes) or 1)
        assert cg.main() == 0
        assert len(calls) == 1

    def test_every_mutation_passes_its_named_tests_through(self, runner) -> None:
        """The runner hands pytest the node ids it was given, unmodified."""
        entry, _, monkeypatch = runner
        seen = []
        monkeypatch.setattr(cg, "run_tests",
                            lambda nodes: seen.append(list(nodes)) or 1)
        cg.main()
        assert seen == [entry[4]]

    def test_the_source_is_restored_even_when_the_mutation_applies(
        self, runner,
    ) -> None:
        """A leftover mutation would silently change the next test run."""
        _, target, monkeypatch = runner

        def explode(nodes):
            assert target.read_text(encoding="utf-8") == "GUARD = False\n"
            raise RuntimeError("pytest crashed")

        monkeypatch.setattr(cg, "run_tests", explode)
        with pytest.raises(RuntimeError):
            cg.main()
        assert target.read_text(encoding="utf-8") == "GUARD = True\n"
