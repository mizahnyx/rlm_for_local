"""Tests for REPL sandbox module."""

from __future__ import annotations

from rlm_local.repl import REPLSandbox
from rlm_local.templates import (
    CELL_STDERR_TRUNCATED,
    CELL_STDOUT_TRUNCATED,
    CELL_TIMEOUT_ERROR,
    REPL_WORKER_RESTARTED,
)


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


class TestCellTimeoutCorrelation:
    """R4 — a timed-out cell must not have its late output misattributed.

    Before the fix there were no cell ids: `execute()` sent `exec`, and if it
    timed out the worker kept running. The *next* `execute()` then read the
    previous cell's `result` message and reported it as the current cell's
    output — silent, wrong evidence handed to the model.
    """

    def test_late_result_is_not_misattributed(self):
        repl = REPLSandbox(cell_timeout=1.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            # The cell outlives its window by ~0.3s, so its late result lands
            # inside the *next* cell's window — exactly the race that used to
            # hand cell 1's output to cell 2.
            first = repl.execute("import time; time.sleep(1.3); print('CELL_ONE')")
            assert CELL_TIMEOUT_ERROR.format(timeout=1.0) in first.stderr

            second = repl.execute("print('CELL_TWO')")
            assert REPL_WORKER_RESTARTED not in second.stderr, second
            assert "CELL_TWO" in second.stdout, second
            assert "CELL_ONE" not in second.stdout, (
                "the previous cell's output leaked into this cell's result"
            )
        finally:
            repl.shutdown()

    def test_second_consecutive_timeout_restarts_the_worker(self):
        repl = REPLSandbox(cell_timeout=1.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            # 1st: times out; worker is still busy.
            r1 = repl.execute("import time; time.sleep(4.0); print('SLOW_A')")
            assert CELL_TIMEOUT_ERROR.format(timeout=1.0) in r1.stderr
            assert REPL_WORKER_RESTARTED not in r1.stderr

            # 2nd: drains the stale result, then times out again → restart.
            r2 = repl.execute("import time; time.sleep(4.0); print('SLOW_B')")
            assert REPL_WORKER_RESTARTED in r2.stderr, r2

            # The loop continues on a fresh worker.
            r3 = repl.execute("print('ALIVE')")
            assert "ALIVE" in r3.stdout, r3
            assert r3.stderr == ""
        finally:
            repl.shutdown()

    def test_worker_can_be_restarted_explicitly(self):
        repl = REPLSandbox(cell_timeout=5.0)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            repl.execute("carried = 7")
            assert "7" in repl.execute("print(carried)").stdout
            repl.restart_worker()
            result = repl.execute("print(carried)")
            assert "NameError" in result.stderr
        finally:
            repl.shutdown()


class TestOutputCapHonesty:
    """R10 — the cap the prompt advertises is the cap that is enforced."""

    def test_stdout_is_capped_at_the_configured_value(self):
        cap = 64
        repl = REPLSandbox(cell_timeout=10.0, stdout_cap=cap)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            result = repl.execute("print('x' * 10000)")
            marker = CELL_STDOUT_TRUNCATED.format(cap=cap)
            assert result.stdout == "x" * cap + marker, len(result.stdout)
        finally:
            repl.shutdown()

    def test_stdout_under_the_cap_is_untouched(self):
        repl = REPLSandbox(cell_timeout=10.0, stdout_cap=4096)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            result = repl.execute("print('short')")
            assert result.stdout == "short\n"
        finally:
            repl.shutdown()

    def test_stderr_truncation_preserves_head_and_tail(self):
        """§5.5 — a traceback's last line is the useful one."""
        cap = 200
        repl = REPLSandbox(cell_timeout=10.0, stdout_cap=cap)
        mgr = MockSubcallMgr()
        repl.start("data", mgr)
        try:
            # Bulk in the *middle* of the traceback (a chained cause), short
            # and useful last line — the shape real failures have.
            code = "\n".join([
                "try:",
                "    raise RuntimeError('y' * 4000)",
                "except RuntimeError as e:",
                "    raise ValueError('SHORT_TAIL') from e",
            ])
            result = repl.execute(code)

            # Head survives: the first traceback preamble is intact.
            assert result.stderr.startswith("Traceback (most recent call last):")
            # Tail survives: with head-only truncation the exception line — the
            # entire point of showing stderr — would have been thrown away.
            assert "ValueError: SHORT_TAIL" in result.stderr, "tail must survive"
            assert "stderr elided" in result.stderr
            # The bulk really was elided rather than silently kept.
            assert "y" * 500 not in result.stderr
            assert len(result.stderr) < 1000
        finally:
            repl.shutdown()

    def test_stdout_cap_property_is_exposed(self):
        assert REPLSandbox(stdout_cap=1234).stdout_cap == 1234


class TestDesignSection53Scaffold:
    """Design §5.3, implemented 2026-09-12 (roadmap DG1, DG3, DG4).

    The design promised three things the code never did: restricted builtins for
    model code, a memory bound on the worker, and scaffold names restored after
    every cell so model code cannot brick the environment.
    """

    def _sandbox(self):
        return REPLSandbox(cell_timeout=20.0)

    def test_model_code_cannot_use_the_dynamic_execution_family(self):
        repl = self._sandbox()
        repl.start(None, None)
        try:
            for expression in ("eval('1+1')", "exec('x = 1')", "globals()",
                               "locals()", "compile('1', '<s>', 'eval')"):
                result = repl.execute(f"print({expression})" if "(" in expression
                                      else expression)
                assert "NameError" in result.stderr or "RuntimeError" in result.stderr, (
                    f"{expression} was allowed: stderr={result.stderr!r}"
                )
        finally:
            repl.shutdown()

    def test_ordinary_code_still_works(self):
        """The restriction must not break what cells actually do."""
        repl = self._sandbox()
        repl.start(None, None)
        try:
            result = repl.execute(
                "import json\n"
                "print(len([1, 2, 3]))\n"
                "print(json.dumps({'a': 1}))\n"
                "answer['content'] = 'ok'\n"
                "answer['ready'] = True"
            )
        finally:
            repl.shutdown()

        assert result.stderr == "", result.stderr
        assert result.final_answer == "ok"
        assert "3" in result.stdout

    def test_the_escape_hatch_restores_full_builtins(self, monkeypatch):
        monkeypatch.setenv("RLM_REPL_ALLOW_DYNAMIC", "1")
        repl = self._sandbox()
        repl.start(None, None)
        try:
            result = repl.execute("print(eval('2+2'))")
        finally:
            repl.shutdown()

        assert result.stderr == "", result.stderr
        assert "4" in result.stdout

    def test_a_rebound_answer_is_repaired_for_the_next_cell(self):
        """The design's "model code can't brick the environment", measured."""
        repl = self._sandbox()
        repl.start(None, None)
        try:
            first = repl.execute("answer = 'not a dict'")
            assert first.scaffold_repaired == ["answer"]
            assert first.answer_state == {"is_dict": False, "type": "str"}

            # The next cell must be able to submit normally.
            second = repl.execute(
                "answer['content'] = 'recovered'\nanswer['ready'] = True"
            )
        finally:
            repl.shutdown()

        assert second.final_answer == "recovered"
        assert second.scaffold_repaired == []

    def test_a_helper_overwritten_by_a_non_callable_is_repaired(self):
        repl = self._sandbox()
        repl.start(None, None)
        try:
            first = repl.execute("grep = 42")
            assert "grep" in first.scaffold_repaired

            second = repl.execute("hits = grep('important')\nprint(len(hits))")
        finally:
            repl.shutdown()

        assert second.stderr == "", second.stderr

    def test_a_deliberate_helper_override_is_left_alone(self):
        """Restoring is about usability, not about freezing the namespace."""
        repl = self._sandbox()
        repl.start(None, None)
        try:
            first = repl.execute("def grep(pattern, max_hits=50):\n    return ['custom']")
            assert first.scaffold_repaired == []
            second = repl.execute("print(grep('x'))")
        finally:
            repl.shutdown()

        assert "custom" in second.stdout

    def test_deleting_the_context_is_repaired(self):
        repl = self._sandbox()
        repl.start("hello context", None)
        try:
            first = repl.execute("del context")
            assert "context" in first.scaffold_repaired
            second = repl.execute("print(len(context))")
        finally:
            repl.shutdown()

        assert second.stderr == "", second.stderr

    def test_a_memory_limit_is_honoured_where_the_os_allows_it(self, monkeypatch):
        """POSIX: the worker's address space is bounded. Windows: a no-op."""
        import sys

        monkeypatch.setenv("RLM_REPL_MEMORY_MB", "512")
        repl = self._sandbox()
        repl.start(None, None)
        try:
            result = repl.execute(
                "import resource\n"
                "print(resource.getrlimit(resource.RLIMIT_AS)[0])"
                if sys.platform != "win32" else
                "print('windows: no rlimit')"
            )
        finally:
            repl.shutdown()

        assert result.stderr == "", result.stderr
        if sys.platform != "win32":
            assert str(512 * 1024 * 1024) in result.stdout
        else:
            assert "no rlimit" in result.stdout

    def test_a_bogus_memory_limit_does_not_stop_the_worker(self, monkeypatch):
        monkeypatch.setenv("RLM_REPL_MEMORY_MB", "not-a-number")
        repl = self._sandbox()
        repl.start(None, None)
        try:
            result = repl.execute("print('still alive')")
        finally:
            repl.shutdown()

        assert "still alive" in result.stdout


class TestDiskBackedContextInWorker:
    """R1 — the spill contract must survive the socket boundary.

    Before the fix `start()` did `str(context)`, shipping the entire blob to the
    worker and leaving it a plain `str`, so `hasattr(context, 'grep')` was
    always False and RAM was O(context) in both processes.
    """

    def _disk_context(self, text: str):
        from rlm_local.context_store import ContextStore

        store = ContextStore(spill_threshold=1)
        return store, store.ingest(text)

    def test_init_payload_carries_a_reference_not_the_text(self):
        import rlm_local.repl as repl_mod

        big = "abcdefghij" * 200_000  # 2M chars — well past any spill threshold
        store, ctx = self._disk_context(big)
        sent: list[dict] = []
        real_send = repl_mod._send_msg

        def spy(sock, msg):
            sent.append(msg)
            return real_send(sock, msg)

        repl = REPLSandbox(cell_timeout=20.0)
        mgr = MockSubcallMgr()
        repl_mod._send_msg = spy
        try:
            repl.start(ctx, mgr)
            init = sent[0]
            assert init["cmd"] == "init"
            spec = init["context"]
            assert isinstance(spec, dict) and spec["kind"] == "file", spec
            assert spec["total"] == len(ctx)
            # The whole init message must be tiny compared to the context.
            import json
            assert len(json.dumps(init)) < 2000
        finally:
            repl_mod._send_msg = real_send
            repl.shutdown()
            store.cleanup()

    def test_worker_reads_the_spilled_file_lazily(self):
        text = ("日本語のテキスト\n" * 5000) + "needle: café 🎉\n"
        store, ctx = self._disk_context(text)
        repl = REPLSandbox(cell_timeout=20.0)
        mgr = MockSubcallMgr()
        try:
            repl.start(ctx, mgr)
            # len() is a byte count, and the worker agrees with the handle.
            out = repl.execute("print(len(context))").stdout.strip()
            assert int(out) == len(text.encode("utf-8"))

            # Lazy methods exist on the worker-side handle.
            assert repl.execute("print(hasattr(context, 'grep'))").stdout.strip() == "True"
            assert repl.execute("print(hasattr(context, 'chunk'))").stdout.strip() == "True"

            # grep and chunk work on non-ASCII content, streamed from disk.
            hits = repl.execute("hits = grep('café'); print(len(hits)); print(hits[0])")
            assert "1" in hits.stdout
            assert "needle: café 🎉" in hits.stdout

            chunks = repl.execute("cs = chunk(size=100); print(len(cs))")
            assert int(chunks.stdout.strip().splitlines()[-1]) > 100

            # lines(start) uses the worker's own byte index.
            lines = repl.execute("print(list(context.lines(start=2, count=1)))")
            assert "日本語のテキスト" in lines.stdout
        finally:
            repl.shutdown()

    def test_worker_does_not_delete_or_modify_the_context_file(self):
        store, ctx = self._disk_context("x" * 100_000)
        path = ctx._path
        before = path.stat()
        repl = REPLSandbox(cell_timeout=20.0)
        mgr = MockSubcallMgr()
        try:
            repl.start(ctx, mgr)
            repl.execute("print(len(context)); grep('x', max_hits=1); chunk(size=50)")
            repl.shutdown()
            # ContextStore.cleanup() keeps ownership: the file survives shutdown.
            assert path.exists()
            after = path.stat()
            assert after.st_size == before.st_size
            assert after.st_mtime_ns == before.st_mtime_ns
        finally:
            store.cleanup()
        assert not path.exists(), "the store still owns cleanup"

    def test_in_memory_context_still_works(self):
        repl = REPLSandbox(cell_timeout=10.0)
        mgr = MockSubcallMgr()
        repl.start("plain text", mgr)
        try:
            assert repl.execute("print(context)").stdout.strip() == "plain text"
            assert repl.execute("print(len(context))").stdout.strip() == "10"
        finally:
            repl.shutdown()

    def test_needle_in_the_middle_of_a_large_spilled_context(self):
        """The realistic shape: a 600K-char spilled context, needle mid-file.

        Exercises the whole lazy path at scale — the byte-offset addressing, the
        streaming `grep`, and `str()` — rather than a handful of characters at
        the end of a small file.
        """
        filler = ("The committee reviewed the quarterly logistics report and "
                  "noted that no anomalies were observed.\n")
        needle = "The archive access code is ZQ-7741."
        parts: list[str] = []
        total = 0
        i = 0
        placed = False
        target = 300_000
        while total < target:
            i += 1
            if not placed and total >= target // 2:
                placed = True
                line = f"[{i:06d}] {needle}\n"
            else:
                line = f"[{i:06d}] {filler}"
            parts.append(line)
            total += len(line)
        text = "".join(parts)
        assert needle in text, "fixture bug: needle not inserted"

        store, ctx = self._disk_context(text)
        assert len(ctx) > 300_000
        repl = REPLSandbox(cell_timeout=30.0, stdout_cap=200_000)
        mgr = MockSubcallMgr()
        try:
            repl.start(ctx, mgr)
            hits = repl.execute("hits = grep('ZQ-7741'); print(len(hits))")
            assert hits.stdout.strip().splitlines()[-1] == "1", hits.stdout
            assert needle in hits.stdout
            assert repl.execute("print('ZQ-7741' in str(context))").stdout.strip() == "True"
        finally:
            repl.shutdown()
            store.cleanup()
