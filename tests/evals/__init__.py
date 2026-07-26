"""Eval suite loader — loads verifiable task definitions from .py modules.

Each suite file is a Python module exporting a TASKS list of EvalTask objects.
Tasks are verifiable without an LLM judge: regex match or numeric tolerance.
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
    tolerance: float = 0.0


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
                tolerance=item.get("tolerance", 0.0),
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
