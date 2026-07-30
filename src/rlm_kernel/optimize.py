"""K4-real: GEPA offline optimizer for harness prompt evolution (§8, Addendum §3).

Replaces the scaffold with a real GEPA-based optimizer that:
1. Loads eval suites (verifiable tasks with train/held-out splits)
2. Wraps rlm_local.completion as the evaluator
3. Uses gepa.optimize_anything for LLM-guided text evolution
4. Routes candidates through the gate (propose → validate → held-out eval → promote)
5. Bootstraps few-shots from verified-correct trajectories

Lineage: promoted pages carry `<!-- optimized_by: gepa-run-<id> -->` as a
body comment and the `gepa-optimized` tag in frontmatter. Version is bumped
on each promotion. Rollback via git.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── Target mapping — complete and symmetric (D4) ───────────────────────────

TARGET_MAP: dict[str, tuple[str, str]] = {
    "prologue": ("contract/templates/prologue.md", "Decomposition prologue"),
    "how-to-work": ("contract/how-to-work.md", "How to work orchestrator addendum"),
    "nudges": ("contract/templates/nudge-no-block.md", "Nudge template for missing code blocks"),
    "fewshots": ("fewshots/example.md", "Few-shot transcript example"),
    "helper-docs": ("helpers/grep.md", "Helper documentation template"),
}


def _get_target_text(vault: Any, target: str) -> str | None:
    """Read current text for a target from the vault (read-only)."""
    entry = TARGET_MAP.get(target)
    if entry is None:
        return None
    path, _ = entry
    try:
        page = vault.get(path)
        if page:
            return page.body
    except Exception:
        pass
    return None


# ── Evaluator wrapper (D2) ─────────────────────────────────────────────────

@dataclass
class EvalResult:
    """Result of evaluating one candidate against an eval suite."""

    score: float
    passed: int
    total: int
    feedback: str
    per_task: list[dict[str, Any]] = field(default_factory=list)


def evaluate_candidate(
    candidate_text: str,
    suite_name: str,
    eval_dir: Path,
    profile: str = "tiny",
    max_turns: int = 6,
    split: str = "train",
    kernel_bridge: Any | None = None,
) -> EvalResult:
    """Evaluate a candidate text artifact against an eval suite.

    This is the GEPA evaluator — it wraps rlm_local.completion() and returns
    (score, feedback_dict) with harness warnings as actionable side information.

    Args:
        candidate_text: The text being evaluated (prompt section body).
        suite_name: Name of the eval suite (needle_search, counting, etc.).
        eval_dir: Path to tests/evals/ directory.
        profile: Hardware profile for completion().
        max_turns: Turn budget per eval task.
        split: "train" or "held_out" — which split to evaluate.
        kernel_bridge: Optional KernelBridge into the vault (D-R2). REQUIRED
            for the candidate to actually influence the completion: without it,
            rlm_local falls back to the hardcoded prompts and the injected
            candidate text is never read.

    Returns:
        EvalResult with score (0.0–1.0), feedback text from harness warnings,
        and per-task breakdown.
    """
    import rlm_local
    from tests.evals import load_suite

    suite = load_suite(suite_name, eval_dir)
    tasks = suite.tasks if split == "train" else suite.held_out

    if not tasks:
        return EvalResult(score=1.0, passed=0, total=0,
                          feedback="(no tasks in split)")

    passed = 0
    per_task: list[dict[str, Any]] = []
    feedback_lines: list[str] = []

    for task in tasks:
        try:
            answer = rlm_local.completion(
                task.query, task.context,
                profile=profile, max_turns=max_turns,
                kernel_bridge=kernel_bridge,
            )
        except Exception as e:
            per_task.append({"task": task.name, "passed": False,
                             "answer": f"Error: {e}"})
            feedback_lines.append(f"FAIL {task.name}: exception {e}")
            continue

        ok = bool(re.search(task.expected_pattern, answer, re.IGNORECASE))

        per_task.append({
            "task": task.name,
            "passed": ok,
            "answer": answer[:300],
        })

        status = "PASS" if ok else "FAIL"
        snippet = answer[:120].replace("\n", " ")
        feedback_lines.append(f"{status} {task.name}: {snippet}")

        if ok:
            passed += 1

    score = passed / len(tasks)
    feedback = "\n".join(feedback_lines)

    return EvalResult(
        score=score,
        passed=passed,
        total=len(tasks),
        feedback=feedback,
        per_task=per_task,
    )


def _make_bridge(vault: Any) -> Any:
    """Construct a KernelBridge into a vault for evaluation (D-R2).

    The bridge lets rlm_local.completion() assemble prompts from vault pages
    (load_system_prompt_from_vault) and inject vault-defined helpers into the
    REPL. Without it, completions run on the hardcoded package prompts and
    candidate text written into the vault is never read.
    """
    from rlm_kernel.repl_bridge import KernelBridge
    from rlm_kernel.vault import LocalVault

    if isinstance(vault, LocalVault):
        index_path = vault.root / ".index" / "meta.sqlite"
    else:
        index_path = Path("__no_index__")
    return KernelBridge(vault=vault, index_path=index_path)


def make_gepa_evaluator(
    suite_name: str,
    eval_dir: Path,
    target: str,
    vault: Any,
    profile: str = "tiny",
    max_turns: int = 6,
    split: str = "train",
) -> Callable[[str], tuple[float, dict[str, Any]]]:
    """Create a GEPA-compatible evaluator function.

    Returns a callable that takes a candidate text string and returns
    (score, side_info_dict) as GEPA expects.

    The evaluator writes the candidate into the vault temporarily (via the
    target mapping) so that rlm_local picks it up for evaluation. After
    evaluation, the original text is restored.

    Args:
        suite_name: Eval suite to evaluate against.
        eval_dir: Path to tests/evals/.
        target: Optimization target key (e.g. "how-to-work").
        vault: VaultStore for injecting the candidate text.
        profile: Hardware profile for completion().
        max_turns: Turn budget per task.
        split: "train" or "held_out".
    """
    # Capture original text so we can restore it
    entry = TARGET_MAP.get(target)
    if entry is None:
        raise ValueError(f"Unknown target: {target}")
    target_path, _ = entry
    original = _get_target_text(vault, target)

    # D-R2: the candidate must flow into the completion via a kernel bridge,
    # otherwise rlm_local uses its hardcoded prompts and the injection below
    # is inert (flat optimization signal).
    bridge = _make_bridge(vault)

    def evaluator(candidate: str) -> tuple[float, dict[str, Any]]:
        # Inject candidate into vault for evaluation
        try:
            page = vault.get(target_path)
            if page is not None:
                page.body = candidate
                vault.put(page, target_path)
        except Exception:
            pass

        # Evaluate
        result = evaluate_candidate(
            candidate, suite_name, eval_dir,
            profile=profile, max_turns=max_turns, split=split,
            kernel_bridge=bridge,
        )

        # Restore original
        if original is not None:
            try:
                page = vault.get(target_path)
                if page is not None:
                    page.body = original
                    vault.put(page, target_path)
            except Exception:
                pass

        side_info = {
            "passed": result.passed,
            "total": result.total,
            "feedback": result.feedback,
            "per_task": result.per_task,
        }
        return (result.score, side_info)

    return evaluator


# ── GEPA runner (D3) ───────────────────────────────────────────────────────

def run_optimization(
    vault: Any,
    target: str = "how-to-work",
    suite_name: str = "needle_search",
    eval_dir: Path | None = None,
    profile: str = "tiny",
    max_metric_calls: int = 150,
    max_turns_per_task: int = 6,
    log_dir: Path | None = None,
    max_workers: int | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Run GEPA optimization on a target text artifact.

    Args:
        vault: VaultStore for page access and gate-routed promotion.
        target: Which text artifact to optimize (key into TARGET_MAP).
        max_metric_calls: GEPA budget (metric calls = evaluator invocations;
            NOTE: counts individual task evals, not iterations, and is only
            checked between iterations — verified against gepa source).
        max_turns_per_task: Turn budget per eval task.
        log_dir: Directory for run-state JSONL summary log. Checkpoint/resume
            from within a run is not yet implemented — an interrupted run
            restarts from zero. Future work.
        max_workers: GEPA parallel-evaluation workers. Default None = gepa
            default (cpu_count) — on small/shared boxes pass 2 to avoid
            self-contention (learned the hard way: 6 workers × slow CPU
            turned one iteration into 2–3 hours).
        run_dir: GEPA checkpoint directory — the engine writes per-iteration
            JSON state here. This is the authoritative live-progress channel;
            never rely on piped stdout for observability.

    Returns:
        Dict with baseline, best, status, and lineage info.
    """
    # Windows cp1252 consoles crash on Unicode in GEPA's own print() logs —
    # a proposed candidate containing '\u2011' killed a 3.7h acceptance run
    # at "Proposed new text" (the optimization itself had WORKED). Force
    # UTF-8 with backslash-replace so logging can never kill a run.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass

    from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig  # type: ignore
    from gepa.optimize_anything import optimize_anything  # type: ignore

    if eval_dir is None:
        eval_dir = Path(__file__).parent.parent.parent / "tests" / "evals"

    # Get current text as seed candidate
    seed = _get_target_text(vault, target)
    if seed is None:
        return {"status": "error", "reason": f"No text found for target '{target}'"}

    # Get target description for GEPA objective/background
    _, target_desc = TARGET_MAP.get(target, ("unknown", "Unknown target"))

    # Load suite to get splits and compute baseline
    from tests.evals import load_suite
    suite = load_suite(suite_name, eval_dir)

    # Convert eval tasks to GEPA DataInst format
    train_data = [
        {"query": t.query, "context": t.context,
         "expected_pattern": t.expected_pattern}
        for t in suite.tasks
    ]
    held_out_data = [
        {"query": t.query, "context": t.context,
         "expected_pattern": t.expected_pattern}
        for t in suite.held_out
    ] if suite.held_out else None

    # Create evaluator for train split
    train_evaluator = make_gepa_evaluator(
        suite_name, eval_dir, target, vault,
        profile=profile, max_turns=max_turns_per_task, split="train",
    )

    # Create evaluator for held-out split (used by GEPA as valset evaluator)
    held_out_evaluator = None
    if suite.held_out:
        held_out_evaluator = make_gepa_evaluator(
            suite_name, eval_dir, target, vault,
            profile=profile, max_turns=max_turns_per_task, split="held_out",
        )

    # D-R2: bridge so the vault (and thus the injected candidates) is actually
    # used by completions — baseline and held-out must use the same prompt path.
    bridge = _make_bridge(vault)

    # Baseline on train
    baseline_result = evaluate_candidate(
        seed, suite_name, eval_dir,
        profile=profile, max_turns=max_turns_per_task, split="train",
        kernel_bridge=bridge,
    )

    # Configure GEPA against the installed package's real schema (D-R1).
    # Reflection model = the harness's root tier, reached via litellm's
    # openai/ provider (any OpenAI-compatible local server).
    import litellm  # noqa: F401 — required at runtime by gepa.lm.LM
    litellm.ssl_verify = False  # local self-signed certs (module-level setting)

    from rlm_local.config import load_config
    harness_cfg = load_config(profile)
    engine_kwargs: dict[str, Any] = {"max_metric_calls": max_metric_calls}
    if max_workers is not None:
        engine_kwargs["max_workers"] = max_workers
    if run_dir is not None:
        engine_kwargs["run_dir"] = str(run_dir)
        engine_kwargs["display_progress_bar"] = True  # live rollout counter
    config = GEPAConfig(
        engine=EngineConfig(**engine_kwargs),
        reflection=ReflectionConfig(
            reflection_lm=f"openai/{harness_cfg.root_model}",
            reflection_lm_kwargs={
                "api_base": harness_cfg.root_endpoint,
                "api_key": "local",
            },
        ),
    )

    try:
        result = optimize_anything(
            seed_candidate=seed,
            evaluator=train_evaluator,
            dataset=train_data,
            valset=held_out_data,
            objective=f"Optimize the {target_desc} text to maximize accuracy on verifiable tasks.",
            background=(
                f"The candidate is the body text of a vault page at '{TARGET_MAP[target][0]}'. "
                "It will be used as part of an RLM harness system prompt. "
                "The evaluator runs rlm_local.completion() with this text injected "
                "and checks answers against regex patterns. "
                "Higher score is better. Score is fraction of tasks passed."
            ),
            config=config,
        )
    except Exception as e:
        return {
            "status": "error",
            "reason": f"GEPA optimization failed: {e}",
            "baseline_score": baseline_result.score,
        }

    # Get best candidate
    best_text = result.best_candidate if hasattr(result, 'best_candidate') else seed
    if isinstance(best_text, dict):
        best_text = best_text.get("text", seed)

    # Held-out evaluation of best candidate
    held_out_result = None
    if suite.held_out:
        held_out_result = evaluate_candidate(
            str(best_text), suite_name, eval_dir,
            profile=profile, max_turns=max_turns_per_task, split="held_out",
            kernel_bridge=bridge,
        )

    # D-K4-1a: Gate-routed promotion with slot validation + superseded_by archive
    best_score = result.best_score if hasattr(result, 'best_score') else baseline_result.score
    status = "no_improvement"

    if best_score > baseline_result.score and held_out_result is not None and held_out_result.score >= baseline_result.score:
        try:
            from rlm_kernel.gate import propose, validate, promote, demote
            from rlm_kernel.schema import PageStatus
            run_id = f"gepa-run-{int(time.time())}"
            target_path, _ = TARGET_MAP[target]

            # Archive the incumbent via demote (superseded_by the optimized version)
            incumbent = vault.get(target_path)
            if incumbent is not None and incumbent.frontmatter.status == PageStatus.ACTIVE:
                demote(vault, incumbent, superseded_by=target_path)

            # Propose the candidate through quarantine
            qpath = propose(
                vault, "contract", f"optimized-{target}",
                str(best_text),
                f"GEPA optimization of {target} — {best_score:.1%} vs baseline {baseline_result.score:.1%}",
            )
            qpage = vault.get(qpath)
            if qpage:
                # Validate — slot variables must be intact (D-K4-1a guard)
                report = validate(qpage, vault=vault)
                if report.passed:
                    # Add lineage to body before promotion
                    qpage.body = f"<!-- optimized_by: {run_id} -->\n{qpage.body}"
                    qpage.frontmatter.tags = list(qpage.frontmatter.tags) + ["gepa-optimized"]
                    # Promote directly into the target path (replaces incumbent)
                    promote(vault, qpage, target_path=target_path)
                    vault.git_commit(
                        f"kernel: GEPA optimize {target} ({run_id}) — "
                        f"{best_score:.1%} vs baseline {baseline_result.score:.1%}"
                    )
                    status = "promoted"
        except Exception:
            status = "gate_error"
    # G-K4-1: Write run-state to JSONL log for checkpoint/resume
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            run_log_path = log_dir / f"gepa-run-{target}-{int(time.time())}.jsonl"
            log_entry = {
                "timestamp": time.time(),
                "target": target,
                "suite": suite_name,
                "baseline_score": baseline_result.score,
                "best_score": best_score,
                "held_out_score": held_out_result.score if held_out_result else None,
                "metric_calls": getattr(result, 'metric_calls', 0),
                "status": status,
                "best_candidate_preview": str(best_text)[:500],
            }
            with open(run_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass

    return {
        "status": status,
        "target": target,
        "suite": suite_name,
        "baseline_score": baseline_result.score,
        "baseline_passed": baseline_result.passed,
        "baseline_total": baseline_result.total,
        "best_score": best_score,
        "held_out_score": held_out_result.score if held_out_result else None,
        "metric_calls": getattr(result, 'metric_calls', 0),
        "best_candidate": str(best_text)[:500],
    }


# ── Few-shot bootstrap (D6) ────────────────────────────────────────────────

def bootstrap_fewshots(
    vault: Any,
    suite_name: str = "needle_search",
    eval_dir: Path | None = None,
    profile: str = "tiny",
    max_shots: int = 3,
) -> list[str]:
    """Replay train split, keep verified trajectories, select canonical few-shots.

    Args:
        vault: VaultStore for gate-routed storage of selected few-shots.
        suite_name: Eval suite to replay.
        eval_dir: Path to tests/evals/.
        profile: Hardware profile.
        max_shots: Maximum number of few-shot transcripts to select.

    Returns:
        List of selected few-shot transcript text bodies.
    """
    import rlm_local
    from tests.evals import load_suite

    if eval_dir is None:
        eval_dir = Path(__file__).parent.parent.parent / "tests" / "evals"

    suite = load_suite(suite_name, eval_dir)
    successful: list[dict[str, Any]] = []

    for task in suite.tasks:
        try:
            answer = rlm_local.completion(
                task.query, task.context,
                profile=profile, max_turns=8,
            )
            if re.search(task.expected_pattern, answer, re.IGNORECASE):
                successful.append({
                    "query": task.query,
                    "answer": answer,
                    "task_name": task.name,
                })
        except Exception:
            continue

        if len(successful) >= max_shots:
            break

    # Select canonical transcripts (shortest successful ones for diversity)
    successful.sort(key=lambda x: len(x["answer"]))
    selected = successful[:max_shots]

    # Store through the gate
    stored: list[str] = []
    for i, shot in enumerate(selected):
        try:
            from rlm_kernel.gate import propose, validate, promote
            body = (
                f"# Few-Shot: {shot['task_name']}\n\n"
                f"## Query\n{shot['query']}\n\n"
                f"## Answer\n{shot['answer']}\n"
            )
            path = propose(vault, "fewshot", f"bootstrap-{shot['task_name']}",
                          body, f"Bootstrapped from {suite_name}")
            page = vault.get(path)
            if page:
                report = validate(page, vault=vault)
                if report.passed:
                    promote(vault, page)
                    stored.append(body)
        except Exception:
            continue

    return stored
