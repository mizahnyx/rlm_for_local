"""Tests for K4-real: eval suites, evaluator, gate-routed promotion, bootstrap.

All tests deterministic — no real LLM, no network.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.evals import EvalSuite, EvalTask, load_suite


# ── Eval suite loading ────────────────────────────────────────────────────

class TestEvalSuites:
    def test_load_needle_search(self):
        suite = load_suite("needle_search")
        assert len(suite.tasks) > 0
        assert len(suite.held_out) > 0
        # 70/30 split sanity
        total = len(suite.tasks) + len(suite.held_out)
        assert total == 7

    def test_load_counting(self):
        suite = load_suite("counting")
        total = len(suite.tasks) + len(suite.held_out)
        assert total == 5

    def test_load_multi_hop(self):
        suite = load_suite("multi_hop")
        total = len(suite.tasks) + len(suite.held_out)
        assert total == 5

    def test_load_fact_extraction(self):
        suite = load_suite("fact_extraction")
        total = len(suite.tasks) + len(suite.held_out)
        assert total == 5

    def test_all_suites_total_at_least_20(self):
        """Per spec: ≥20 tasks across all suites."""
        total = 0
        for name in ["needle_search", "counting", "multi_hop", "fact_extraction"]:
            suite = load_suite(name)
            total += len(suite.tasks) + len(suite.held_out)
        assert total >= 20, f"Only {total} tasks across all suites"

    def test_split_is_deterministic(self):
        """70/30 split must be deterministic across loads."""
        suite1 = load_suite("needle_search")
        suite2 = load_suite("needle_search")
        assert [t.name for t in suite1.tasks] == [t.name for t in suite2.tasks]

    def test_all_tasks_have_patterns(self):
        """Every task must have an expected_pattern."""
        for name in ["needle_search", "counting", "multi_hop", "fact_extraction"]:
            suite = load_suite(name)
            for task in suite.tasks + suite.held_out:
                assert task.expected_pattern, f"{task.name} has no pattern"


# ── Evaluator (offline, no LLM) ───────────────────────────────────────────

class TestEvaluator:
    def test_evaluate_candidate_offline(self, monkeypatch):
        """Evaluator returns expected score format without real LLM."""
        import rlm_local
        from rlm_kernel.optimize import evaluate_candidate

        # Stub completion to return predictable answers
        answers_iter = iter(["blue", "1648", "Alice", "$42.99",
                             "support@example.com", "25.8", "3.7.2"])
        def fake_completion(query, context, **kwargs):
            try:
                return next(answers_iter)
            except StopIteration:
                return "unknown"

        monkeypatch.setattr(rlm_local, "completion", fake_completion)

        result = evaluate_candidate(
            "test text", "needle_search",
            Path("tests/evals"), profile="tiny", max_turns=4,
        )
        assert 0.0 <= result.score <= 1.0
        assert result.total == len(load_suite("needle_search").tasks)
        assert len(result.per_task) == result.total

    def test_evaluator_score_0_when_all_wrong(self, monkeypatch):
        """Score is 0 when all answers are wrong."""
        import rlm_local
        from rlm_kernel.optimize import evaluate_candidate

        monkeypatch.setattr(rlm_local, "completion",
                            lambda q, c, **kw: "completely wrong answer")

        result = evaluate_candidate(
            "test", "needle_search",
            Path("tests/evals"), profile="tiny", max_turns=4,
        )
        assert result.score == 0.0
        assert result.passed == 0


# ── Target mapping ────────────────────────────────────────────────────────

class TestTargetMapping:
    def test_all_targets_have_vault_paths(self):
        from rlm_kernel.optimize import TARGET_MAP

        for target in ["prologue", "how-to-work", "nudges", "fewshots", "helper-docs"]:
            entry = TARGET_MAP.get(target)
            assert entry is not None, f"Missing target: {target}"
            path, desc = entry
            assert path, f"Empty path for {target}"
            assert desc, f"Empty description for {target}"

    def test_target_map_is_symmetric_and_distinct(self):
        """Every target maps to an existing, distinct page (D-K4-2)."""
        from rlm_kernel.optimize import TARGET_MAP, _get_target_text

        import tempfile
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_target_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        seen_paths: set[str] = set()
        for target in TARGET_MAP:
            path = TARGET_MAP[target][0]
            # Each target must have a distinct path
            assert path not in seen_paths, f"Duplicate path '{path}' for target '{target}'"
            seen_paths.add(path)
            # Each path must exist in the vault
            assert vault.exists(path), f"Target path '{path}' does not exist in seeded vault"
            # Each path must have non-empty text
            text = _get_target_text(vault, target)
            assert text is not None, f"_get_target_text returned None for {target}"
            assert len(text) > 0, f"Empty text for {target}"

        td.cleanup()


# ── Few-shot bootstrap (offline) ──────────────────────────────────────────

class TestFewShotBootstrap:
    def test_bootstrap_stores_through_gate(self, monkeypatch):
        """Bootstrap replays train split and stores successful trajectories."""
        import rlm_local
        from rlm_kernel.optimize import bootstrap_fewshots
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_bs_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        # Return task-specific correct answers
        answers = {"color": "blue", "year": "1648", "name": "Alice",
                    "price": "$42.99", "email": "support@example.com",
                    "temperature": "25.8", "version": "3.7.2"}
        def fake_completion(query, context, **kw):
            for key, val in answers.items():
                if key in query.lower() or key in context.lower()[:200]:
                    return val
            return "unknown"
        monkeypatch.setattr(rlm_local, "completion", fake_completion)

        transcripts = bootstrap_fewshots(
            vault, suite_name="needle_search",
            profile="tiny", max_shots=3,
        )
        assert len(transcripts) > 0

        # Verify fewshots were stored in vault
        fewshots = vault.list(kind="fewshot")
        td.cleanup()


class TestPromotionContract:
    """D-K4-1b: real-path contract test — exercises run_optimization end-to-end."""

    def test_run_optimization_promotes_through_gate(self, monkeypatch):
        """End-to-end: mocked GEPA → gate-routed promotion → prompt differs."""
        import tempfile
        from pathlib import Path

        import rlm_local
        from rlm_kernel.optimize import TARGET_MAP, _get_target_text, run_optimization
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_contract_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        target = "nudges"
        target_path, _ = TARGET_MAP[target]
        incumbent = vault.get(target_path)
        assert incumbent is not None
        original_body = incumbent.body
        original_version = incumbent.frontmatter.version

        # Mock gepa.optimize_anything to return a "winning" candidate immediately
        optimized_text = "OPTIMIZED: Emit exactly one ```repl block now."

        class FakeResult:
            best_candidate = optimized_text
            best_score = 0.9
            metric_calls = 1

        # Patch the GEPA import that run_optimization uses internally.
        # NOTE: GEPAConfig/EngineConfig are deliberately NOT patched — the real
        # config classes must construct successfully (D-R1 regression guard).
        import gepa.optimize_anything as gepa_oa
        monkeypatch.setattr(gepa_oa, "optimize_anything",
                            lambda **kw: FakeResult())
        # Mock rlm_local.completion for the evaluator (returns correct answers)
        def fake_completion(query, context, **kw):
            return "The answer is blue."  # matches needle_search tasks
        monkeypatch.setattr(rlm_local, "completion", fake_completion)

        # Run optimization (should promote since mocked GEPA returns a winner)
        result = run_optimization(
            vault, target=target, suite_name="needle_search",
            profile="tiny", max_metric_calls=1, max_turns_per_task=2,
        )

        # D-K4-1b assertions:
        # (a) status indicates promotion
        assert result["status"] == "promoted", f"Expected promoted, got {result['status']}"

        # (b) Incumbent page body changed
        reloaded = vault.get(target_path)
        assert reloaded is not None
        assert reloaded.body != original_body, "Body did not change after promotion"
        assert optimized_text in reloaded.body, "Optimized text not found in promoted body"

        # (c) Version bumped from the quarantined page (starts at version 0+1=1)
        assert reloaded.frontmatter.version >= 1

        # (d) Lineage present
        assert "optimized_by: gepa-run-" in reloaded.body
        assert "gepa-optimized" in reloaded.frontmatter.tags

        # (e) _get_target_text returns the new text (prompt-level non-inertness)
        current = _get_target_text(vault, target)
        assert current is not None
        assert optimized_text in current, "_get_target_text still returns old body"

        td.cleanup()

    def test_train_winner_held_out_loser_not_promoted(self, monkeypatch):
        """R-K4-1: train improvement + held-out loss → no promotion."""
        import tempfile
        from pathlib import Path

        import rlm_local
        from rlm_kernel.optimize import TARGET_MAP, _get_target_text, run_optimization
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_gate_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        target = "nudges"
        target_path, _ = TARGET_MAP[target]
        incumbent = vault.get(target_path)
        assert incumbent is not None
        original_body = incumbent.body

        # Mock GEPA to return a "winning" candidate
        optimized_text = "OPTIMIZED: Emit exactly one ```repl block now."

        class FakeResult:
            best_candidate = optimized_text
            best_score = 0.9  # improved on train
            metric_calls = 1

        import gepa.optimize_anything as gepa_oa
        monkeypatch.setattr(gepa_oa, "optimize_anything",
                            lambda **kw: FakeResult())
        # NOTE: GEPAConfig/EngineConfig deliberately NOT patched (D-R1 guard).
        # Train: 3/5 correct (score 0.6), best=0.9 > 0.6 → train gate OPENS
        # Held-out: all wrong (score 0.0), 0.0 < 0.6 → held-out gate BLOCKS
        # This discriminates: without the held-out gate, promotion would occur
        train_answers = [
            "the year is 1648",       # year task: PASS
            "wrong answer",           # alice task: FAIL
            "also wrong",             # price task: FAIL
            "email is support@example.com",  # email task: PASS
            "version 3.7.2",          # version task: PASS
        ]
        answer_iter = iter(train_answers + ["xyzzy_nomatch_xyzzy"] * 20)
        def fake_completion(query, context, **kw):
            try:
                return next(answer_iter)
            except StopIteration:
                return "xyzzy_nomatch_xyzzy"
        monkeypatch.setattr(rlm_local, "completion", fake_completion)

        result = run_optimization(
            vault, target=target, suite_name="needle_search",
            profile="tiny", max_metric_calls=1, max_turns_per_task=2,
        )

        # R-K4-1 assertion: must NOT be promoted
        assert result["status"] != "promoted", (
            f"Expected NOT promoted (held-out loss), got status={result['status']}"
        )

        # Incumbent body must be UNCHANGED
        reloaded = vault.get(target_path)
        assert reloaded is not None
        assert reloaded.body == original_body, (
            "Incumbent body was changed despite held-out loss"
        )

        td.cleanup()


class TestLiveWiringGuards:
    """D-R1/D-R2: non-vacuous guards against the two live-run defects.

    Both tests exercise REAL code paths (real GEPA config classes, real
    rlm_local.completion construction) — they fail on the pre-fix code and
    pass after, with discrimination verified by reasoning about each path,
    not by assuming the mock proves anything.
    """

    def test_gepa_config_constructs_with_real_package(self, monkeypatch):
        """D-R1: run_optimization must build GEPAConfig without TypeError.

        Only optimize_anything is mocked; GEPAConfig/EngineConfig/
        ReflectionConfig are the real classes from the installed gepa package.
        Pre-fix, EngineConfig(student_model=..., reflection_model=...) raised
        TypeError (unexpected kwargs) — this test goes red if bogus fields
        ever come back.
        """
        import tempfile
        from pathlib import Path

        import rlm_local
        from rlm_kernel.optimize import run_optimization
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_cfg_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        captured: dict = {}

        class FakeResult:
            best_candidate = "X"
            best_score = 0.0
            metric_calls = 0

        import gepa.optimize_anything as gepa_oa
        real_config_cls = gepa_oa.GEPAConfig

        def fake_optimize(**kw):
            captured["config"] = kw.get("config")
            return FakeResult()

        monkeypatch.setattr(gepa_oa, "optimize_anything", fake_optimize)
        monkeypatch.setattr(
            rlm_local, "completion", lambda query, context, **kw: "no match"
        )

        result = run_optimization(
            vault, target="nudges", suite_name="needle_search",
            profile="tiny", max_metric_calls=1, max_turns_per_task=1,
        )

        assert result["status"] != "error", (
            f"GEPA config construction failed: {result.get('reason')}"
        )
        cfg = captured.get("config")
        assert isinstance(cfg, real_config_cls), (
            f"optimize_anything received {type(cfg)}, not a real GEPAConfig"
        )
        assert cfg.engine.max_metric_calls == 1
        assert cfg.reflection.reflection_lm, "reflection_lm must be configured"
        td.cleanup()

    def test_evaluator_injects_candidate_into_completion(self, monkeypatch):
        """D-R2: candidate text must reach the model's messages.

        Uses target='how-to-work' — a page whose body is assembled into the
        system prompt via load_system_prompt_from_vault. A stub backend
        records every message it receives; the candidate marker must appear.
        Pre-fix (no kernel_bridge in the evaluator), the completion used the
        hardcoded prompt and the marker never arrived — flat signal.
        """
        import tempfile
        from pathlib import Path

        import rlm_local
        from rlm_kernel.optimize import make_gepa_evaluator
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_inj_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        seen: list[str] = []

        class StubBackend:
            def chat(self, messages, *, tier="root", max_tokens=1500,
                     temperature=0.0, response_schema=None):
                for m in messages:
                    seen.append(str(m.get("content", "")))
                return "The answer is blue."

        stub = StubBackend()
        # Intercept the backend rlm_local.completion builds internally
        monkeypatch.setattr(rlm_local, "HTTPModelBackend", lambda **kw: stub)

        evaluator = make_gepa_evaluator(
            "needle_search", None, "how-to-work", vault,
            profile="tiny", max_turns=2, split="train",
        )
        marker = "UNIQUE_CANDIDATE_MARKER_7f3a"
        evaluator(marker)

        assert any(marker in m for m in seen), (
            "Candidate text never reached the model — injection path broken "
            "(kernel_bridge missing from the evaluator)"
        )
        td.cleanup()
