"""Tests for subcall manager module."""

from __future__ import annotations

import inspect

from rlm_local.subcall_manager import SubcallManager
from rlm_local.templates import SUBCALL_COUNT_EXHAUSTED


class FakeBackend:
    """Fake backend for testing SubcallManager without a real server."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tier="sub", max_tokens=1024, temperature=0.0,
             response_schema=None):
        self.calls += 1
        prompt = messages[0]["content"]
        return f"Response to: {prompt[:30]}..."


class TestSubcallManager:
    def test_llm_query_basic(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        result = mgr.llm_query("What is the capital of France?")
        assert result.startswith("Response to:")

    def test_llm_query_batched(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        prompts = ["Q1", "Q2", "Q3"]
        results = mgr.llm_query_batched(prompts)
        assert len(results) == 3
        assert all(r.startswith("Response to:") for r in results)

    def test_call_budget_exhausted(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=1, max_chars=100000)
        mgr.llm_query("first")  # use the one call
        result = mgr.llm_query("second")  # exhausted
        assert "exhausted" in result.lower()

    def test_char_budget_exhausted(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=5)
        result = mgr.llm_query("123456")  # 6 chars > 5 budget
        assert "exhausted" in result.lower()

    def test_memoization(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        r1 = mgr.llm_query("same prompt")
        r2 = mgr.llm_query("same prompt")
        assert r1 == r2  # cached

    def test_calls_used(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        assert mgr.calls_used == 0
        mgr.llm_query("test")
        assert mgr.calls_used == 1
        mgr.llm_query_batched(["a", "b"])
        assert mgr.calls_used == 3

    def test_calls_remaining(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=5, max_chars=100000)
        assert mgr.calls_remaining == 5
        mgr.llm_query("test")
        assert mgr.calls_remaining == 4

    def test_shutdown(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend)
        mgr.shutdown()  # should not raise


class TestBudgetAndMemoizationOrder:
    """R3 — cache hits must not consume budget; `cache_hits` must count hits."""

    def test_identical_prompt_charges_budget_once(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        mgr.llm_query("same prompt")
        mgr.llm_query("same prompt")
        assert mgr.calls_used == 1, "a cache hit must not consume a call"
        assert mgr.chars_used == len("same prompt"), "nor characters"
        assert backend.calls == 1, "the backend must only be hit once"

    def test_cache_hits_counts_hits_not_cache_size(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        assert mgr.cache_hits == 0
        mgr.llm_query("a")
        assert mgr.cache_hits == 0, "a miss is not a hit"
        mgr.llm_query("a")
        assert mgr.cache_hits == 1
        mgr.llm_query("a")
        assert mgr.cache_hits == 2
        mgr.llm_query("b")
        # Two distinct prompts cached, but still only two hits.
        assert mgr.cache_hits == 2

    def test_distinct_prompts_charge_twice(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=100000)
        mgr.llm_query("one")
        mgr.llm_query("two")
        assert mgr.calls_used == 2
        assert mgr.chars_used == len("one") + len("two")
        assert mgr.cache_hits == 0

    def test_cache_hit_works_even_when_budget_is_exhausted(self):
        """Memoization exists to relieve budget pressure — it must still serve."""
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=1, max_chars=100000)
        first = mgr.llm_query("same")
        second = mgr.llm_query("same")
        assert first == second
        assert "exhausted" not in second.lower()
        assert mgr.calls_used == 1
        assert mgr.cache_hits == 1

    def test_char_budget_not_charged_for_cache_hit(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=10, max_chars=6)
        mgr.llm_query("abc")
        mgr.llm_query("abc")  # would overflow a naive char budget (3+3 > 6 is fine; check 4+4)
        assert mgr.chars_used == 3

    def test_batched_cache_hits_are_free(self):
        backend = FakeBackend()
        mgr = SubcallManager(backend, max_calls=2, max_chars=100000)
        first = mgr.llm_query_batched(["x", "y"])
        assert mgr.calls_used == 2
        results = mgr.llm_query_batched(["x", "y"])
        assert results == first, "batched hits must return the memoized responses"
        assert all("exhausted" not in r.lower() for r in results)
        assert mgr.calls_used == 2, "batched cache hits must not charge budget"
        assert mgr.cache_hits == 2
        assert backend.calls == 2

    def test_sub_model_parameter_is_gone(self):
        """R3/R16 — the unused `sub_model` constructor parameter was removed."""
        params = inspect.signature(SubcallManager.__init__).parameters
        assert "sub_model" not in params
