"""The hard cell limit is an hour, on the owner's call (2026-09-23).

Raised from 1 200 s after the load gate measured what a search costs on this corpus: p95 ≥ 60 s
over the queries a live run actually issued, seven of fifteen not finishing inside 60 s, and one
cell demonstrably stopped at the old limit *while running `corpus_search`*
(`docs/20260923-1400-ro5-the-load-gate-measured.md`). The owner's reasoning, which the numbers
support: old hardware, small models, and an hour of waiting is acceptable where a killed cell is
not.

This is a *default*, so it is worth a test: a default that drifts back silently would undo the
decision without anything going red, which is the shape of failure this project keeps catching.
"""

from __future__ import annotations

from rlm_local.config import load_config


def test_the_hard_cell_limit_is_an_hour() -> None:
    for profile in ("tiny", "laptop", "workstation"):
        assert load_config(profile).cell_timeout_hard == 3600.0, profile


def test_the_soft_limit_is_unchanged_by_that() -> None:
    """The point of two limits: a cell that asks for nothing is still stopped promptly."""
    assert load_config("laptop").cell_timeout == 60.0
    assert load_config("workstation").cell_timeout == 120.0
    assert load_config("laptop").cell_timeout_hard > load_config("laptop").cell_timeout, (
        "equal limits switch the second stage off, which would make the hour meaningless"
    )


def test_the_cli_help_states_the_hour() -> None:
    """The number an operator reads must be the number in force.

    It may *mention* the old value — the help says the limit was raised from 1 200 s, which is
    worth keeping — so this checks that the stated default is the hour, not that the string is
    absent. The first version forbade `1200 s` anywhere in the file and went red on the
    sentence documenting the raise.
    """
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "rlm_local" / "cli.py"
    text = source.read_text(encoding="utf-8")
    # Contiguous *source* fragments, not the rendered sentence: the help is written as adjacent
    # string literals, so "the profile's value — 3600 s" never appears as one run of text. The
    # second version of this test asserted the rendered phrase and went red for that reason.
    assert '"3600 s; env RLM_CELL_TIMEOUT_HARD' in text, (
        "the CLI help does not state the new default"
    )
    assert '"1200 s; env RLM_CELL_TIMEOUT_HARD' not in text, (
        "a stale default in the help is how an operator raises a limit that is already raised"
    )
    assert re.search(r"cell_timeout_hard: float = 3600\.0",
                     (source.parent / "config.py").read_text(encoding="utf-8"))
