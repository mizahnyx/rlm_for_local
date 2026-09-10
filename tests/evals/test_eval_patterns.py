"""Guards for the eval suites' answer patterns (R24 / R25 item 5).

The evaluator matches with ``re.search(pattern, answer, re.IGNORECASE)``, so an
unanchored pattern silently accepts supersets: the ``count_fruits`` pattern
``"35"`` used to pass for the answer ``"135"``. These tests pin, per task:

1. the pattern is anchored (no bare substring patterns left),
2. a correct answer *does* match,
3. a plausible wrong answer (a numeric superset, a name prefix-extension, or a
   differently-spelled wrong value) does **not** match,
4. both the ``.py`` and the ``.json`` form of a suite agree.

The cases below are the ones the anchored patterns were written for; if a
pattern is loosened back to a bare substring, case 3 goes red.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.evals import load_suite

EVALS_DIR = Path(__file__).parent
SUITES = ["counting", "fact_extraction", "multi_hop", "needle_search"]


def _matches(suite: str, task_name: str, answer: str) -> bool:
    """Evaluate ``answer`` against the named task exactly as the evaluator does."""
    tasks = [t for t in load_suite(suite).tasks + load_suite(suite).held_out
             if t.name == task_name]
    assert len(tasks) == 1, f"{suite}/{task_name} not found"
    return bool(re.search(tasks[0].expected_pattern, answer, re.IGNORECASE))


# (suite, task, correct answer, wrong answer that must NOT match)
CASES = [
    # ── counting: numeric supersets must not match ────────────────────────
    ("counting", "count_fruits", "There are 35 fruits in total.", "I counted 135 items."),
    ("counting", "count_colors", "The list contains 24 color names.", "There are 240 entries."),
    ("counting", "count_errors", "I found 4 ERROR lines.", "There were 14 ERROR lines."),
    ("counting", "sum_prices", "The total is $42.50.", "The total is $142.50."),
    ("counting", "average_temperature", "The average is 22.86 degrees.", "The average is 122.86 degrees."),
    # ── fact extraction: hyphen/space tolerant, not superset tolerant ─────
    ("fact_extraction", "extract_isbn", "ISBN: 978-0-262-03384-8.", "ISBN: 978-0-262-03384-9."),
    ("fact_extraction", "extract_isbn", "ISBN 9780262033848.", "ISBN 97802620338480."),
    ("fact_extraction", "extract_phone", "Phone: (555) 123-4567.", "Phone: 555-123-45678."),
    ("fact_extraction", "extract_phone", "Phone: 555 123 4567.", "Phone: 555-999-4567."),
    ("fact_extraction", "extract_ip", "Primary: 192.168.1.100.", "Primary: 192.168.1.1000."),
    ("fact_extraction", "extract_currency", "Prices are in USD.", "Prices are in EUR."),
    ("fact_extraction", "extract_date", "Published March 15, 2024.", "Published March 15, 20240."),
    ("fact_extraction", "extract_date", "published march 15 2024", "published march 5, 2024"),
    # ── multi-hop: name prefix-extensions must not match ──────────────────
    ("multi_hop", "author_of_book", "Martin Fowler wrote it.", "Martina Fowler wrote it."),
    ("multi_hop", "author_of_book", "Martin-Fowler wrote it.", "Martin Van Buren wrote it."),
    ("multi_hop", "capital_of_country", "The capital is Tokyo.", "The capital is Tokyoite."),
    ("multi_hop", "ceo_of_company", "Sarah Chen is the CEO.", "Sarah Cheney is the CEO."),
    ("multi_hop", "ceo_of_company", "sarah-chen is the CEO.", "Sara Chen is the CEO."),
    ("multi_hop", "most_expensive_product", "Electronics has the highest average.",
     "Electronic goods have the highest average."),
    ("multi_hop", "oldest_employee", "Carol Davis is the oldest.", "Caroline Davis is the oldest."),
    # ── needle search ────────────────────────────────────────────────────
    ("needle_search", "color_needle_blue", "The color is blue.", "The color is bluish."),
    ("needle_search", "year_needle_1648", "The year is 1648.", "The year is 11648."),
    ("needle_search", "name_needle_alice", "Alice is named.", "Alicia is named."),
    ("needle_search", "price_needle", "It costs $42.99.", "It costs $42.999."),
    ("needle_search", "price_needle", "It costs forty-two dollars.", "It costs nineteen dollars."),
    ("needle_search", "email_needle", "Write to support@example.com.", "Write to sales@example.com."),
    ("needle_search", "temperature_needle", "The reading was 25.8C.", "The reading was 125.8C."),
    ("needle_search", "temperature_needle", "The reading was 25.8 degrees.", "The reading was 25.89 degrees."),
    ("needle_search", "version_needle", "Version 3.7.2 was released.", "Version 13.7.2 was released."),
]


@pytest.mark.parametrize("suite,task,good,bad", CASES)
def test_correct_answer_matches_and_wrong_answer_does_not(suite, task, good, bad):
    assert _matches(suite, task, good), f"{suite}/{task}: '{good}' must match"
    assert not _matches(suite, task, bad), f"{suite}/{task}: '{bad}' must NOT match"


@pytest.mark.parametrize("suite", SUITES)
def test_every_pattern_is_anchored(suite):
    """No task may keep a bare-substring pattern."""
    loaded = load_suite(suite)
    for task in loaded.tasks + loaded.held_out:
        pattern = task.expected_pattern
        assert "\\b" in pattern or "(?<!\\d)" in pattern or pattern.startswith("^"), (
            f"{suite}/{task.name}: pattern {pattern!r} is not anchored at a token "
            "boundary (see the convention in tests/evals/__init__.py)"
        )


@pytest.mark.parametrize("suite", SUITES)
def test_json_suite_mirrors_py_suite(suite):
    """The .json fallback must parse and agree with the .py source of truth.

    The JSON files are only used when the .py module is absent, but they used
    to be invalid JSON (Python expression syntax such as ``"x" * 15``) and had
    drifted away from the .py patterns — so the documented fallback was dead.
    """
    json_path = EVALS_DIR / f"{suite}.json"
    assert json_path.exists(), f"{suite}.json missing from the fallback set"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    loaded = load_suite(suite)
    by_name = {t.name: t for t in loaded.tasks + loaded.held_out}

    assert {item["name"] for item in data} == set(by_name)
    for item in data:
        task = by_name[item["name"]]
        assert item["expected_pattern"] == task.expected_pattern, (
            f"{suite}/{item['name']}: .json and .py patterns disagree"
        )
        assert item["context"] == task.context
        assert "tolerance" not in item, (
            f"{suite}/{item['name']}: 'tolerance' was removed from EvalTask; "
            "nothing ever read it"
        )
