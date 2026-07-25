"""Offline optimization — scaffold only, not yet implemented (§8, K4).

STATUS: This module is a placeholder. The real K4 implementation (pending
R1–R5 green gate) will:
- Wrap rlm_local.completion as the evaluator via gepa.optimize_anything
- Use the sub-tier 4B model as student and root-tier 8B as reflection LM
- Feed templated harness warnings back as GEPA-style actionable feedback
- Route candidates through the gate (propose → validate → held-out eval → promote)
- Record optimized_by: gepa-run-<id> lineage in frontmatter
- Run few-shot bootstrap (replay trainset, select canonical transcripts)
- Enforce max_metric_calls 150–300 budget with tiny-profile caps

The current code is a hand-rolled mutation skeleton for development use only.
It MUST NOT write to live contract pages or bypass the gate. See the K4-real
section of docs/20260725-0838-rlm-kernel-conformity-review-addendum.md §3.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ── Eval suite ─────────────────────────────────────────────────────────────

@dataclass
class EvalTask:
    """A single verifiable eval task for prompt optimization."""

    name: str
    query: str
    context: str
    expected_pattern: str  # regex that must match the answer
    tolerance: float = 0.0  # for numeric tasks


@dataclass
class EvalSuite:
    """Collection of eval tasks with train/held-out splits."""

    name: str
    tasks: list[EvalTask]
    held_out: list[EvalTask] = field(default_factory=list)


# ── Built-in eval suites (§8) ──────────────────────────────────────────────

def _make_needle_suite() -> EvalSuite:
    """Simple needle-in-haystack eval suite."""
    tasks = [
        EvalTask(
            name="color_needle",
            query="What color is mentioned in the context?",
            context="The sky was overcast and gray. Later, a brilliant blue emerged from behind the clouds. "
                     "The observers noted the deep blue hue with satisfaction." * 20,
            expected_pattern=r"(?i)blue",
        ),
        EvalTask(
            name="year_needle",
            query="What year is mentioned?",
            context="Historical records indicate various dates. The treaty was signed in 1648. "
                     "Many other events followed in subsequent centuries." * 20,
            expected_pattern=r"1648",
        ),
    ]
    return EvalSuite(name="needle_search", tasks=tasks)


def _make_aggregation_suite() -> EvalSuite:
    """Simple aggregation eval suite."""
    tasks = [
        EvalTask(
            name="count_items",
            query="How many fruits are listed?",
            context="apple\n" * 5 + "banana\n" * 3 + "cherry\n" * 7 + "unrelated text\n" * 100,
            expected_pattern=r"15",
        ),
    ]
    held_out = [
        EvalTask(
            name="count_colors",
            query="How many color items are listed?",
            context="red\n" * 6 + "blue\n" * 4 + "green\n" * 8 + "padding\n" * 100,
            expected_pattern=r"18",
        ),
    ]
    return EvalSuite(name="aggregation", tasks=tasks, held_out=held_out)


BUILTIN_SUITES: dict[str, EvalSuite] = {
    "needle_search": _make_needle_suite(),
    "aggregation": _make_aggregation_suite(),
}


# ── Metrics ────────────────────────────────────────────────────────────────

def evaluate_task(task: EvalTask, completer: Callable[..., str]) -> tuple[bool, str]:
    """Evaluate one task against a completion function.

    Returns (passed, answer_text).
    """
    try:
        answer = completer(task.query, task.context)
    except Exception as e:
        return False, f"Error: {e}"

    passed = bool(re.search(task.expected_pattern, answer, re.IGNORECASE))
    return passed, answer


def evaluate_suite(suite: EvalSuite, completer: Callable[..., str]) -> dict[str, Any]:
    """Evaluate all tasks in a suite. Returns {score, passed, total, feedback}."""
    passed = 0
    results = []
    for task in suite.tasks:
        ok, answer = evaluate_task(task, completer)
        results.append({"task": task.name, "passed": ok, "answer": answer[:200]})
        if ok:
            passed += 1

    score = passed / len(suite.tasks) if suite.tasks else 0.0
    feedback_lines = [f"Score: {passed}/{len(suite.tasks)} ({score:.1%})"]
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        feedback_lines.append(f"  {status}: {r['task']}")

    return {
        "score": score,
        "passed": passed,
        "total": len(suite.tasks),
        "feedback": "\n".join(feedback_lines),
        "results": results,
    }


# ── Optimization runner ────────────────────────────────────────────────────

def run_optimization(
    vault: Any,
    target: str = "how-to-work",
    max_iterations: int = 20,
    profile: str = "laptop",
) -> dict[str, Any]:
    """Run GEPA-style optimization on a target text artifact.

    Args:
        vault: VaultStore for page access.
        target: Which text artifact to optimize.
        max_iterations: Max mutation passes.
        profile: Hardware profile for completion().

    Returns:
        Dict with before/after scores and optimization summary.
    """
    import rlm_local

    # Select eval suite based on target
    suite = BUILTIN_SUITES.get("needle_search", _make_needle_suite())

    # Get current prompt text
    current_text = _get_target_text(vault, target)
    if not current_text:
        return {"status": "skipped", "reason": f"No text found for target: {target}"}

    # Baseline evaluation
    baseline = evaluate_suite(suite, lambda q, c: rlm_local.completion(
        q, c, profile=profile, max_turns=6,
    ))

    # Simple mutation: try variations
    best_score = baseline["score"]
    best_text = current_text
    mutations = _generate_mutations(current_text, target)

    for i, mutant in enumerate(mutations[:max_iterations]):
        _set_target_text(vault, target, mutant)
        result = evaluate_suite(suite, lambda q, c: rlm_local.completion(
            q, c, profile=profile, max_turns=6,
        ))

        if result["score"] > best_score:
            best_score = result["score"]
            best_text = mutant

    # If no improvement, restore original
    if best_score <= baseline["score"]:
        _set_target_text(vault, target, current_text)
        status = "no_improvement"
    else:
        _set_target_text(vault, target, best_text)
        status = "improved"

    # Held-out evaluation
    held_out_result = None
    if suite.held_out:
        held_out_result = evaluate_suite(
            EvalSuite(name="held_out", tasks=suite.held_out),
            lambda q, c: rlm_local.completion(q, c, profile=profile, max_turns=6),
        )

    return {
        "status": status,
        "target": target,
        "baseline_score": baseline["score"],
        "best_score": best_score,
        "iterations": min(len(mutations), max_iterations),
        "held_out": held_out_result,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_target_text(vault: Any, target: str) -> str | None:
    """Get the current text for a target artifact from the vault."""
    page_map = {
        "prologue": "contract/templates/prologue.md",
        "how-to-work": "contract/how-to-work.md",
        "nudges": "contract/templates/nudge-no-block.md",
        "fewshots": "fewshots/example.md",
        "helper-docs": "helpers/grep.md",
    }
    path = page_map.get(target)
    if path is None:
        return None
    try:
        page = vault.get(path)
        if page:
            return page.body
    except Exception:
        pass
    return None


def _set_target_text(vault: Any, target: str, text: str) -> None:
    """No-op: scaffold does not write to live pages (D13).

    Real K4 will route candidates through the gate: propose → validate →
    held-out eval → promote with lineage.
    """
    import warnings
    warnings.warn(
        f"optimize._set_target_text is a no-op in scaffold mode. "
        f"Target '{target}' would be written through the gate in K4-real."
    )


def _generate_mutations(text: str, target: str) -> list[str]:
    """Generate simple text mutations for optimization.

    In a full implementation, this would use a reflection LM via GEPA.
    For now, applies cheap structural variants.
    """
    mutations = [text]  # original first

    # Add emphasis markers
    mutations.append(text.replace("PROBE", "**PROBE**").replace("PLAN", "**PLAN**"))

    # Add explicit "Do NOT" warnings
    mutations.append(
        text + "\n\nCRITICAL: Do NOT finalize until you have verified your answer with a small test."
    )

    # Simplify
    lines = text.split("\n")
    if len(lines) > 5:
        mutations.append("\n".join(lines[: len(lines) // 2]))

    # Expand with more detail
    mutations.append(
        text + "\n\nRemember: each turn should do exactly ONE thing. "
        "Probe first, then plan, then execute one step at a time."
    )

    return mutations
