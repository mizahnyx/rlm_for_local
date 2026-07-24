"""Tests for subcall manager module."""

from __future__ import annotations

from rlm_local.subcall_manager import SubcallManager
from rlm_local.templates import SUBCALL_COUNT_EXHAUSTED


class FakeBackend:
    """Fake backend for testing SubcallManager without a real server."""

    def chat(self, messages, *, tier="sub", max_tokens=1024, temperature=0.0,
             response_schema=None):
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
