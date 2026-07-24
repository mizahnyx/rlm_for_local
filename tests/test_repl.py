"""Tests for REPL sandbox module."""

from __future__ import annotations

from rlm_local.repl import REPLSandbox


class MockSubcallMgr:
    def llm_query(self, prompt, schema=None):
        return f"llm: {prompt[:40]}"
    def llm_query_batched(self, prompts, schema=None):
        return [f"batched: {p[:30]}" for p in prompts]


class TestREPLSandbox:
    def test_start_and_execute(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("hello world", mgr)
        try:
            result = repl.execute("print('hello from repl')")
            assert "hello from repl" in result.stdout
            assert result.stderr == ""
            assert result.final_answer is None
        finally:
            repl.shutdown()

    def test_context_available(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("test context data", mgr)
        try:
            result = repl.execute("print(len(context))")
            assert "17" in result.stdout  # len("test context data") == 18, wait let me count: t-e-s-t- -c-o-n-t-e-x-t- -d-a-t-a = 17
        finally:
            repl.shutdown()

    def test_answer_mechanism(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            result = repl.execute(
                "answer['content'] = 'final result'; answer['ready'] = True"
            )
            assert result.final_answer == "final result"
        finally:
            repl.shutdown()

    def test_stderr_capture(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            result = repl.execute("raise ValueError('test error')")
            assert "ValueError" in result.stderr
            assert "test error" in result.stderr
        finally:
            repl.shutdown()

    def test_helpers_available(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("line one\nline two\nline three", mgr)
        try:
            result = repl.execute("show_vars()")
            # Should not crash
            assert result.stderr == ""
        finally:
            repl.shutdown()

    def test_grep_helper(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("apple\nbanana\napple pie\ncherry", mgr)
        try:
            result = repl.execute("hits = grep('apple'); print(len(hits))")
            assert "2" in result.stdout
        finally:
            repl.shutdown()

    def test_llm_query_from_repl(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("context data", mgr)
        try:
            result = repl.execute(
                "response = llm_query('test prompt'); print(response)"
            )
            assert "llm:" in result.stdout
        finally:
            repl.shutdown()

    def test_llm_query_batched_from_repl(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("context data", mgr)
        try:
            result = repl.execute(
                "responses = llm_query_batched(['a', 'b']); print(len(responses))"
            )
            assert "2" in result.stdout
        finally:
            repl.shutdown()

    def test_chunk_helper(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("a" * 100, mgr)
        try:
            result = repl.execute("chunks = chunk(size=30); print(len(chunks))")
            assert "4" in result.stdout  # ceil(100/30) = 4
        finally:
            repl.shutdown()

    def test_multiple_cells_persist_state(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            repl.execute("x = 42")
            result = repl.execute("print(x)")
            assert "42" in result.stdout
        finally:
            repl.shutdown()

    def test_shutdown_cleans_up(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        repl.shutdown()
        # Should not raise if called again
        repl.shutdown()
