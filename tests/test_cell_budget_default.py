"""The cell budgets, and the numbers they are set from.

Two limits, deliberately different sizes. The *soft* one signals and extends a cell that has asked
the harness for something; the *hard* one stops it. The soft limit also stops a cell that asked for
nothing, so it cannot simply be made huge — the pair is what makes a long wait acceptable where a
killed cell is not.

Both values come from measurements rather than taste:

* **1 200 s → 3 600 s** (owner, 2026-09-23), after the load gate measured p95 ≥ 60 s over the
  queries a live run actually issued, seven of fifteen not finishing inside 60 s, and one cell
  stopped at the old limit *while running `corpus_search`*
  (`docs/20260923-1400-ro5-the-load-gate-measured.md`).
* **3 600 s → 5 400 s, with the soft limits raised too** (owner, 2026-09-24), because the recorded
  cost of a question on this corpus is **10–65 minutes**
  (`docs/20260923-1500-the-search-latency-decision-brief.md`): the only limit that actually stops
  anything sat *below* the slowest legitimate question, so "accept the latency" was not something
  the harness could honour. The instruction was to resize the budgets rather than reshape what
  search returns — and to leave the reshaping options (affordable terms, rare-term prefilter,
  cheaper ranking) untried, which is what they remain.

These are *defaults*, so they are worth tests: a default that drifts back silently would undo the
decision without anything going red, which is the shape of failure this project keeps catching.
"""

from __future__ import annotations

import re
from pathlib import Path

from rlm_local.config import load_config

#: Recorded, not re-measured: the queries a live run issued (`20260923-1400`).
P95_SEARCH_SECONDS = 60.0
#: Recorded, not re-measured: the cheapest and dearest question in the recorded set (`-1500`).
SLOWEST_QUESTION_SECONDS = 65.0 * 60.0


def test_the_hard_cell_limit_is_ninety_minutes() -> None:
    for profile in ("tiny", "laptop", "workstation"):
        assert load_config(profile).cell_timeout_hard == 5400.0, profile


def test_the_hard_limit_covers_the_slowest_recorded_question() -> None:
    """The only real stop must not sit below a legitimate question's cost."""
    for profile in ("tiny", "laptop", "workstation"):
        assert load_config(profile).cell_timeout_hard >= SLOWEST_QUESTION_SECONDS, profile


def test_the_soft_limit_no_longer_flags_an_ordinary_search() -> None:
    """Half the recorded searches crossed the old 60 s soft limit.

    The soft limit is a *signal* — "this cell is doing something expensive" — so one that a routine
    search always crosses is a signal that is always on, which is the same as no signal. It is
    raised above the recorded p95 but stays far below the hard limit, so a cell that asked for
    nothing is still stopped promptly.
    """
    for profile, expected in (("tiny", 180.0), ("laptop", 300.0), ("workstation", 600.0)):
        config = load_config(profile)
        assert config.cell_timeout == expected, profile
        assert config.cell_timeout > P95_SEARCH_SECONDS, profile
        assert config.cell_timeout < config.cell_timeout_hard, profile


def test_the_two_limits_stay_distinct() -> None:
    """Equal limits switch the second stage off, which would make the hard limit meaningless."""
    for profile in ("tiny", "laptop", "workstation"):
        config = load_config(profile)
        assert config.cell_timeout_hard > config.cell_timeout, profile


def test_the_cli_help_states_the_default_in_force() -> None:
    """The number an operator reads must be the number in force.

    It may *mention* the older values — the help says the limit was raised from 1 200 s, and then
    from 3 600 s, which is worth keeping — so this checks the stated default, not the absence of
    the string. Two earlier versions of this test failed for the opposite reason: one forbade
    `1200 s` anywhere and went red on the sentence documenting the raise, and one asserted the
    *rendered* phrase when the help is written as adjacent string literals.
    """
    source = Path(__file__).resolve().parents[1] / "src" / "rlm_local" / "cli.py"
    text = source.read_text(encoding="utf-8")
    assert '"5400 s; env RLM_CELL_TIMEOUT_HARD' in text, (
        "the CLI help does not state the default in force"
    )
    assert '"3600 s; env RLM_CELL_TIMEOUT_HARD' not in text, (
        "a stale default in the help is how an operator raises a limit that is already raised"
    )
    assert re.search(r"cell_timeout_hard: float = 5400\.0",
                     (source.parent / "config.py").read_text(encoding="utf-8"))
