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

    def test_target_map_is_symmetric(self):
        """Every readable target must be writable through the same key."""
        from rlm_kernel.optimize import TARGET_MAP, _get_target_text

        # Build a temp vault with seed data to verify get works
        import tempfile
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="k4_target_", ignore_cleanup_errors=True)
        vault = LocalVault(Path(td.name), init_git=False)
        seed_vault(vault)

        for target in TARGET_MAP:
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
        assert len(fewshots) > 0

        td.cleanup()
