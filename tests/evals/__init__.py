"""Eval suite loader — loads verifiable task definitions from .py modules.

Each suite file is a Python module exporting a TASKS list of EvalTask objects.
Tasks are verifiable without an LLM judge: each carries a single regex that the
model's answer must match.

Pattern convention (R24 / R25 item 5 — enforced by
``tests/evals/test_eval_patterns.py``)
-------------------------------------------------------------------------
Patterns are consumed with ``re.search(pattern, answer, re.IGNORECASE)``, so an
unanchored pattern silently accepts supersets: ``"35"`` used to pass for the
answer ``"135"``. Every pattern must therefore anchor its match at a token
boundary:

* **Word tokens** use ``\\b`` — e.g. ``r"(?i)\\btokyo\\b"``, which still matches
  ``"Tokyo."`` and ``"Tokyo's"`` but not ``"Tokyoite"``.
* **Numeric tokens** use ``(?<!\\d)`` / ``(?!\\d)`` rather than ``\\b``, because
  ``\\b`` would reject the legitimate answer ``"25.8C"`` (no boundary between
  ``8`` and ``C``). ``r"(?<!\\d)25\\.?8(?!\\d)"`` rejects ``"125.8"`` and
  ``"25.89"`` while accepting ``"25.8C"``.

Hyphenation, spacing, and case must not decide correctness (the documented
``O-Negative`` / ``O Negative`` evaluator bug): write ``[\\s-]*`` where the
expected text may be hyphenated or spaced, and prefer ``\\s+`` over a literal
space. ``re.IGNORECASE`` already covers case; inline ``(?i)`` is harmless and
kept where it was already present.

Note: ``EvalTask`` deliberately has **no** ``tolerance`` field. It used to
carry one, but nothing ever read it — the matcher is a pure regex search — so
numeric tasks encode their tolerance in the pattern itself. Honouring a real
numeric tolerance would require changing the evaluator in
``src/rlm_kernel/optimize.py`` (out of scope here).
"""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalTask:
    """A single verifiable eval task."""

    name: str
    query: str
    context: str
    expected_pattern: str


@dataclass
class EvalSuite:
    """Collection of eval tasks with optional held-out split."""

    name: str
    tasks: list[EvalTask]
    held_out: list[EvalTask] = field(default_factory=list)


def _load_module(module_path: Path) -> Any:
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(
        module_path.stem, str(module_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_suite(name: str, evals_dir: Path | None = None) -> EvalSuite:
    """Load an eval suite by name.

    Looks for <name>.py in the evals directory (default: tests/evals/).
    Splits tasks 70/30 into train/held-out using deterministic seed.

    The .py form is preferred; the .json form is a fallback for suites that
    ship data only. Both carry the same fields.
    """
    if evals_dir is None:
        evals_dir = Path(__file__).parent

    # Prefer .py format, fall back to .json
    py_path = evals_dir / f"{name}.py"
    json_path = evals_dir / f"{name}.json"

    if py_path.exists():
        module = _load_module(py_path)
        all_tasks: list[EvalTask] = list(module.TASKS)
    elif json_path.exists():
        import json as _json
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        all_tasks = [
            EvalTask(
                name=item["name"],
                query=item["query"],
                context=item["context"],
                expected_pattern=item["expected_pattern"],
            )
            for item in data
        ]
    else:
        raise FileNotFoundError(f"Eval suite not found: {py_path} or {json_path}")

    # Deterministic 70/30 split
    train: list[EvalTask] = []
    held_out: list[EvalTask] = []

    for task in all_tasks:
        h = int(hashlib.md5(task.name.encode()).hexdigest(), 16)
        if h % 100 < 70:
            train.append(task)
        else:
            held_out.append(task)

    return EvalSuite(name=name, tasks=train, held_out=held_out)
