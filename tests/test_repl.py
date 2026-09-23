"""Tests for REPL sandbox module."""

from __future__ import annotations

import socket
import threading
import time

from rlm_local.repl import (
    REPLSandbox,
    _recv_msg,
    _send_msg,
)
from rlm_local.templates import (
    CELL_HARD_TIMEOUT_ERROR,
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

    def test_a_citation_on_the_answer_dict_reaches_the_text(self):
        """The owner's finding, 2026-09-22: the scaffold is a dict and the contract was text.

        The model set `answer['citations']` while the harness looked only for a `Citations:`
        line in the content, so the citation was discarded in the worker and the answer was
        refused for being uncited — three refusals each, on exactly the three prose questions
        that wrote that key, and none on the three that did not.
        """
        for written, expected in (
            ("['KQM7-3', 'PQ2X-7']", "Citations: KQM7-3; PQ2X-7"),
            ("'KQM7-3; PQ2X-7'", "Citations: KQM7-3; PQ2X-7"),
            ("'KQM7-3, PQ2X-7'", "Citations: KQM7-3; PQ2X-7"),
            ("('KQM7-3',)", "Citations: KQM7-3"),
        ):
            repl = REPLSandbox(cell_timeout=10.0)
            repl.start("data", MockSubcallMgr())
            try:
                result = repl.execute(
                    "answer['content'] = 'the answer is in there';\n"
                    f"answer['citations'] = {written}\n"
                    "answer['ready'] = True"
                )
                assert result.final_answer is not None, written
                assert result.final_answer.rstrip().endswith(expected), (
                    f"{written} produced {result.final_answer!r}"
                )
                assert result.final_answer.startswith("the answer is in there"), (
                    "the answer itself must survive: " + repr(result.final_answer)
                )
            finally:
                repl.shutdown()

    def test_a_citation_already_in_the_text_is_not_doubled(self):
        repl = REPLSandbox(cell_timeout=10.0)
        repl.start("data", MockSubcallMgr())
        try:
            result = repl.execute(
                "answer['content'] = 'text\\n\\nCitations: KQM7-3';\n"
                "answer['citations'] = ['PQ2X-7']\n"
                "answer['ready'] = True"
            )
            assert result.final_answer is not None
            assert result.final_answer.count("Citations:") == 1, result.final_answer
            assert "PQ2X-7" not in result.final_answer, (
                "the text's own line is authoritative; a second would be the harness "
                "arguing with a model that already complied"
            )
        finally:
            repl.shutdown()

    def test_no_citations_key_changes_nothing(self):
        """The reconciliation must be invisible when the model did not use the key."""
        repl = REPLSandbox(cell_timeout=10.0)
        repl.start("data", MockSubcallMgr())
        try:
            result = repl.execute("answer['content'] = 'plain'; answer['ready'] = True")
            assert result.final_answer == "plain"
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


# ── RO16: the two-stage cell budget ───────────────────────────────────────
#
# The subject here is `REPLSandbox.execute()`'s clock, so the worker is a socket
# and a thread rather than a process. That is not a convenience: the first attempt
# at this feature drove the extension through the root loop with a short soft
# limit, which put the worker's own startup *inside* the first cell's window, and
# the gate correctly stopped a cell it believed was stuck
# (`docs/20260917-2059-blocked-two-stage-budget-and-a-gate.md`). No process
# startup is inside any measurement below.


class _StubCorpusBridge:
    """Just enough bridge for a cell to make one helper call that succeeds."""

    def handle_count(self, *, kind=None, under=""):
        return "[count: 7 files]"


class _StubWorker:
    """The worker's end of the socket, played by a thread.

    A script is a list of `(action, payload)` pairs:

    * `("request", {...})` — the cell asks the harness for something, which is
      what the extension gate counts as work;
    * `("sleep", seconds)` — the cell is busy and silent;
    * `("close", None)` — the worker goes away, as a crashed worker does;
    * `("result", {...})` — the cell finishes with this result message.
    """

    def __init__(self, script: list[tuple]) -> None:
        self.parent, self._worker = socket.socketpair()
        self._thread = threading.Thread(
            target=self._play, args=(self._worker, script), daemon=True,
        )
        self._thread.start()

    def _play(self, sock: socket.socket, script: list[tuple]) -> None:
        try:
            exec_msg = _recv_msg(sock, timeout=10)
            if exec_msg is None:
                return
            cell_id = exec_msg.get("cell_id")
            for action, payload in script:
                if action == "sleep":
                    time.sleep(payload)
                elif action == "close":
                    sock.close()
                    return
                elif action == "request":
                    _send_msg(sock, {**payload, "cell_id": cell_id})
                    # The harness answers every helper request; drain the answer
                    # so this end stays in step with the real protocol.
                    _recv_msg(sock, timeout=10)
                elif action == "result":
                    _send_msg(sock, {"type": "result", "cell_id": cell_id, **payload})
        except OSError:  # the test closed the socket; nothing to report
            pass

    def close(self) -> None:
        self.parent.close()
        self._worker.close()


class TestTheTwoStageCellBudget:
    """The soft limit signals, the hard limit stops, and only work buys the extension.

    Two limits per cell, both configurable (owner, 2026-09-17): a cell that reaches
    the soft limit while it is *demonstrably* working is allowed to continue up to
    the hard limit, and a cell that reaches it having asked for nothing is stopped
    there. `cell_timeout_hard == cell_timeout` switches the second limit off
    entirely, which is the behaviour that existed before the feature.
    """

    @staticmethod
    def _drive(soft: float, hard: float, script: list[tuple]):
        extensions: list[float] = []
        repl = REPLSandbox(cell_timeout=soft, cell_timeout_hard=hard)
        repl._corpus_bridge = _StubCorpusBridge()
        repl._extension_reporter = extensions.append
        stub = _StubWorker(script)
        repl._worker_sock = stub.parent
        return repl, stub, extensions

    def test_a_working_cell_is_granted_the_hard_limit_and_finishes(self):
        repl, stub, extensions = self._drive(
            soft=0.3, hard=5.0,
            script=[
                ("request", {"type": "corpus_count"}),
                ("sleep", 0.4),
                ("result", {"stdout": "DONE\n"}),
            ],
        )
        try:
            result = repl.execute("corpus_count()")
        finally:
            stub.close()
            repl._worker_sock = None

        assert result.timed_out is False, result
        assert result.hard_timeout is False, result
        assert "DONE" in result.stdout, result
        assert result.stderr == "", result
        # Announced exactly once, with the elapsed seconds the operator line needs.
        assert len(extensions) == 1, extensions
        assert 0.3 * 0.8 <= extensions[0] < 5.0, extensions
        assert repl.cell_activity == 1, repl.cell_activity

    def test_a_cell_that_asked_for_nothing_is_stopped_at_the_soft_limit(self):
        repl, stub, extensions = self._drive(
            soft=0.3, hard=2.0,
            script=[("sleep", 3.0)],
        )
        try:
            result = repl.execute("while True: pass")
        finally:
            stub.close()
            repl._worker_sock = None

        assert result.timed_out is True, result
        assert result.hard_timeout is False, result
        assert CELL_TIMEOUT_ERROR.format(timeout="0.3") in result.stderr, result.stderr
        assert CELL_HARD_TIMEOUT_ERROR.format(timeout="2") not in result.stderr
        assert extensions == [], extensions
        assert repl.cell_activity == 0, repl.cell_activity

    def test_the_hard_limit_stops_even_a_working_cell(self):
        repl, stub, extensions = self._drive(
            soft=0.2, hard=0.7,
            script=[("request", {"type": "corpus_count"})],
        )
        try:
            result = repl.execute("corpus_count()")
        finally:
            stub.close()
            repl._worker_sock = None

        assert result.timed_out is True, result
        assert result.hard_timeout is True, result
        # The message names the budget that actually fired: a cell granted 0.7 s
        # must not be told its 0.2 s window closed.
        assert CELL_HARD_TIMEOUT_ERROR.format(timeout="0.7") in result.stderr, result.stderr
        assert CELL_TIMEOUT_ERROR.format(timeout="0.2") not in result.stderr
        assert len(extensions) == 1, extensions

    def test_one_limit_means_no_extension_at_all(self):
        repl, stub, extensions = self._drive(
            soft=0.3, hard=0.3,
            script=[("request", {"type": "corpus_count"})],
        )
        try:
            result = repl.execute("corpus_count()")
        finally:
            stub.close()
            repl._worker_sock = None

        assert result.timed_out is True, result
        assert result.hard_timeout is False, result
        assert CELL_TIMEOUT_ERROR.format(timeout="0.3") in result.stderr, result.stderr
        assert extensions == [], extensions
        # The cell *was* working; a single limit still stops it, which is the
        # pre-RO16 behaviour kept as a supported configuration.
        assert repl.cell_activity == 1, repl.cell_activity

    def test_a_worker_that_went_away_is_not_credited_with_slow_work(self):
        """A closed socket is not a spent window.

        Both arrive as "no message", and crediting the second one as progress would
        hand a crashed worker's cell twenty minutes of a budget nothing is using.
        This is the case that a clock comparison got wrong (~1 run in 4) before the
        wait was made to say *why* it ended.
        """
        repl, stub, extensions = self._drive(
            soft=0.3, hard=5.0,
            script=[
                ("request", {"type": "corpus_count"}),
                ("close", None),
            ],
        )
        try:
            started = time.monotonic()
            result = repl.execute("corpus_count()")
            elapsed = time.monotonic() - started
        finally:
            stub.close()
            repl._worker_sock = None

        assert result.timed_out is True, result
        assert result.hard_timeout is False, result
        assert CELL_TIMEOUT_ERROR.format(timeout="0.3") in result.stderr, result.stderr
        assert extensions == [], extensions
        # The cell did ask for something, so only the closed socket kept the
        # extension from being granted — and it must not have waited for the hard
        # limit to find that out.
        assert repl.cell_activity == 1, repl.cell_activity
        assert elapsed < 1.0, elapsed
