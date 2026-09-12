"""REPL sandbox — subprocess-isolated Python worker (§5.3).

Uses a bidirectional TCP socket protocol:
- Harness sends: init, exec, shutdown
- Worker sends back: result, subcall_request (which the harness proxies)

This enables llm_query/llm_query_batched as ordinary Python functions in the
worker while keeping the worker killable and memory-bounded.

Design contracts honoured here
------------------------------
* **Disk spill reaches the worker (R1).** When the context handle is
  disk-backed, `init` sends a small *file descriptor* (`{"kind": "file", ...}`)
  rather than the text; the worker binds a lazy reader over the same path. The
  store keeps ownership of the file — the worker only ever reads it.
* **Cells are correlated (R4).** Every `exec` carries a monotonically
  increasing `cell_id` and the worker echoes it. A result belonging to a cell
  the harness already timed out on is discarded instead of being handed to the
  model as if it were the current cell's output; two timeouts in a row restart
  the worker.
* **The output cap is real (R10).** `stdout_cap` is what the prompt advertises
  as `{repl_cap}`; stdout is head-truncated with a marker and stderr keeps its
  head *and* tail (the traceback's last line is the useful one).
* **Every harness-emitted string is templated (R4.1).** See `templates.py`.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rlm_local.context_store import Context, _InMemoryContext
from rlm_local.templates import (
    CELL_STDERR_TRUNCATED,
    CELL_STDOUT_TRUNCATED,
    CELL_TIMEOUT_ERROR,
    REPL_WORKER_RESTARTED,
)

# ── Result envelope ────────────────────────────────────────────────────────


@dataclass
class REPLResult:
    stdout: str = ""
    stderr: str = ""
    final_answer: str | None = None
    warnings: list[str] = field(default_factory=list)
    answer_state: dict[str, Any] | None = None
    """The scaffold `answer` as it stood when the cell ended (VD2).

    None when the harness has no state to report — a timed-out cell, or a result
    that did not come from executing a cell. Deliberately data rather than a
    verdict: `model_check` decides what it means when a model claimed to submit
    and did not.
    """


# ── Wire protocol ─────────────────────────────────────────────────────────

def _send_msg(sock: socket.socket, msg: dict) -> None:
    payload = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recv_msg(sock: socket.socket, timeout: float | None = None) -> dict | None:
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        length = struct.unpack(">I", header)[0]
        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                return None
            data += chunk
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, OSError, json.JSONDecodeError, struct.error):
        return None
    finally:
        if timeout is not None:
            sock.settimeout(None)


def _truncate_middle(text: str, cap: int) -> str:
    """Keep the head and the tail of ``text``, eliding the middle.

    Tracebacks put the actionable line last, so a head-only cut throws away the
    most useful part of the error (§5.5).
    """
    if cap <= 0:
        return CELL_STDERR_TRUNCATED.format(elided=len(text))
    if len(text) <= cap:
        return text
    head = cap // 2
    tail = cap - head
    return text[:head] + CELL_STDERR_TRUNCATED.format(elided=len(text) - cap) + text[-tail:]


# ── REPL worker script (runs in subprocess) ────────────────────────────────

_WORKER_SCRIPT = r"""
import json, os, re, socket, struct, sys, traceback
from io import StringIO

# ── Connect back to harness ───────────────────────────────────────────────

_HOST = os.environ["RLM_REPL_HOST"]
_PORT = int(os.environ["RLM_REPL_PORT"])

_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_sock.connect((_HOST, _PORT))

def _send(msg):
    payload = json.dumps(msg).encode('utf-8')
    _sock.sendall(struct.pack('>I', len(payload)) + payload)

def _recv():
    header = b''
    while len(header) < 4:
        chunk = _sock.recv(4 - len(header))
        if not chunk: sys.exit(0)
        header += chunk
    length = struct.unpack('>I', header)[0]
    data = b''
    while len(data) < length:
        chunk = _sock.recv(length - len(data))
        if not chunk: sys.exit(0)
        data += chunk
    return json.loads(data.decode('utf-8'))

# Cell correlation (R4): every harness->worker message carries a cell_id and
# every worker->harness message echoes it, so the harness can tell a late
# result from the current one.
_cell_id = None

# ── Lazy disk-backed context (R1) ─────────────────────────────────────────

class _FileContext:
    '''Read-only, lazy view over the context file the harness spilled.

    Byte-addressed exactly like rlm_local.context_store.Context: len() is a
    UTF-8 byte count, ctx[a:b] decodes byte offsets, ctx[i] is the character
    starting at byte offset i. The worker never writes and never deletes this
    file — ContextStore owns it.
    '''

    def __init__(self, path, total):
        self._path = path
        self._total = int(total)
        self._line_offsets = None

    def __len__(self):
        return self._total

    def __getitem__(self, key):
        if isinstance(key, int):
            if key < 0:
                key = self._total + key
            if key < 0 or key >= self._total:
                raise IndexError('context index %d out of range' % key)
            with open(self._path, 'rb') as f:
                f.seek(key)
                raw = f.read(4)
            return _decode_one(raw, key)
        if isinstance(key, slice):
            start, stop, step = key.indices(self._total)
            if step != 1:
                raise ValueError('context slicing only supports step=1')
            if stop - start <= 0:
                return ''
            with open(self._path, 'rb') as f:
                f.seek(start)
                raw = f.read(stop - start)
            return raw.decode('utf-8', errors='strict')
        raise TypeError('unsupported index type: %s' % type(key))

    def __iter__(self):
        return self.lines()

    def __str__(self):
        return self[:]

    def __repr__(self):
        return '<Context %d bytes at %s>' % (self._total, self._path)

    def _index(self):
        '''Byte offsets of line starts, built once by streaming the file.'''
        if self._line_offsets is None:
            offsets = [0]
            pos = 0
            with open(self._path, 'rb') as f:
                while True:
                    block = f.read(65536)
                    if not block:
                        break
                    start = 0
                    while True:
                        nl = block.find(b'\n', start)
                        if nl == -1:
                            break
                        offsets.append(pos + nl + 1)
                        start = nl + 1
                    pos += len(block)
            self._line_offsets = offsets
        return self._line_offsets

    def lines(self, start=0, count=None):
        offsets = self._index()
        with open(self._path, 'rb') as f:
            if start > 0 and start < len(offsets):
                f.seek(offsets[start])
            else:
                for _ in range(start):
                    if not f.readline():
                        return
            yielded = 0
            for raw in f:
                yield raw.decode('utf-8', errors='strict').rstrip('\r\n')
                yielded += 1
                if count is not None and yielded >= count:
                    break

    def grep(self, pattern, max_hits=50):
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ['Error: invalid regex pattern: %s' % e]
        hits = []
        with open(self._path, 'rb') as f:
            for raw in f:
                line = raw.decode('utf-8', errors='strict')
                if regex.search(line):
                    hits.append(line.rstrip('\r\n'))
                    if len(hits) >= max_hits:
                        break
        return hits

    def chunk(self, size=None, by=None):
        if by == 'paragraph':
            return list(self._paragraphs())
        chunk_size = size or 3000
        chunks = []
        with open(self._path, 'r', encoding='utf-8') as f:
            while True:
                piece = f.read(chunk_size)
                if not piece:
                    break
                chunks.append(piece)
        return chunks

    def _paragraphs(self, block=65536):
        buf = ''
        with open(self._path, 'r', encoding='utf-8') as f:
            while True:
                piece = f.read(block)
                if not piece:
                    break
                buf += piece
                parts = re.split(r'\n\s*\n', buf)
                buf = parts.pop()
                for p in parts:
                    p = p.strip()
                    if p:
                        yield p
        for p in re.split(r'\n\s*\n', buf):
            p = p.strip()
            if p:
                yield p


def _decode_one(raw, offset):
    for n in range(1, len(raw) + 1):
        try:
            ch = raw[:n].decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            continue
        if len(ch) == 1:
            return ch
        break
    raise UnicodeDecodeError('utf-8', raw or b'', 0, 1,
                             'byte offset %d is not a character boundary' % offset)

# ── Callback to harness for sub-calls ─────────────────────────────────────

def _harness_llm_query(prompt, schema=None):
    _send({"cmd": "subcall", "prompt": prompt, "schema": schema, "cell_id": _cell_id})
    resp = _recv()
    return resp.get("response", "Error: no response from harness")

def _harness_llm_query_batched(prompts, schema=None):
    _send({"cmd": "subcall_batched", "prompts": prompts, "schema": schema, "cell_id": _cell_id})
    resp = _recv()
    return resp.get("responses", ["Error: no response from harness"] * len(prompts))

def _harness_search(query, k=5, kinds=None):
    _send({"cmd": "search", "query": query, "k": k, "kinds": kinds, "cell_id": _cell_id})
    resp = _recv()
    return resp.get("result", "(no results)")

def _harness_propose(kind, name, body, rationale=""):
    _send({"cmd": "propose", "kind": kind, "name": name, "body": body,
           "rationale": rationale, "cell_id": _cell_id})
    resp = _recv()
    return resp.get("result", "Error: propose failed")

# Inject into globals so exec'd code can use them
llm_query = _harness_llm_query
llm_query_batched = _harness_llm_query_batched
search = _harness_search
propose = _harness_propose

# ── Scaffold namespace ────────────────────────────────────────────────────

answer = {"content": "", "ready": False}
context = None


def _answer_state():
    '''The scaffold `answer` as it stands when a cell ends (VD2).

    Reported as data, never as a verdict: P4 needs it to tell "the submission
    line never ran" from "it ran and did not take effect", and only the harness
    knows what either means.

    Every read is defensive. Model code owns `answer` once a cell starts, so a
    pathological value (an object whose __eq__ or __bool__ raises) must not be
    able to stop the cell's result from being sent — that would turn a
    diagnostic into a timeout for the model.
    '''
    try:
        if not isinstance(answer, dict):
            return {"is_dict": False, "type": type(answer).__name__}
        content = answer.get("content")
        return {
            "is_dict": True,
            "keys": sorted(str(k) for k in list(answer.keys())[:20]),
            "ready": bool(answer.get("ready")),
            "content_set": content not in (None, ""),
            "content_len": len(content) if isinstance(content, str) else None,
        }
    except Exception as e:
        return {"is_dict": isinstance(answer, dict), "error": type(e).__name__}


_SHOW_VARS_IGNORE = frozenset({"answer", "context", "__builtins__", "llm_query", "llm_query_batched",
                                "search", "propose",
                                "_harness_llm_query", "_harness_llm_query_batched",
                                "_harness_search", "_harness_propose",
                                "_SHOW_VARS_IGNORE", "_sock", "_send", "_recv", "_cell_id",
                                "json", "os", "re", "socket", "struct", "sys", "traceback",
                                "StringIO", "_HOST", "_PORT", "_FileContext", "_decode_one"})

def peek(n=2000):
    s = str(context)[:n]
    print(s)
    return s

def grep(pattern, max_hits=50):
    try:
        regex = re.compile(pattern)
    except re.error as e:
        print(f"Error: invalid regex: {e}")
        return []
    hits = []
    if hasattr(context, 'grep'):
        hits = context.grep(pattern, max_hits)
    else:
        for line in str(context).splitlines():
            if regex.search(line):
                hits.append(line)
                if len(hits) >= max_hits:
                    break
    for h in hits:
        print(h)
    return hits

def chunk(size=None, by=None):
    if hasattr(context, 'chunk'):
        chunks = context.chunk(size=size, by=by)
    else:
        text = str(context)
        if by == 'paragraph':
            chunks = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
        else:
            sz = size or 3000
            chunks = [text[i:i+sz] for i in range(0, len(text), sz)]
    print(f"Produced {len(chunks)} chunks")
    return chunks

def map_query(items, template, batch=True):
    if callable(template):
        prompts = [template(item) for item in items]
    else:
        prompts = [template.replace('{text}', str(item)) for item in items]
    if batch:
        return _harness_llm_query_batched(prompts)
    return [_harness_llm_query(p) for p in prompts]

def show_vars():
    import inspect
    frame = inspect.currentframe()
    if frame and frame.f_back:
        g = frame.f_back.f_globals
        for name in sorted(g):
            if name.startswith('_') or name in _SHOW_VARS_IGNORE:
                continue
            val = g[name]
            t = type(val).__name__
            try:
                s = str(val)
                if len(s) > 80:
                    s = s[:80] + '...'
            except:
                s = '<unprintable>'
            print(f"{name}: {t} = {s}")

# ── Main loop ─────────────────────────────────────────────────────────────

def main():
    global context, answer, _cell_id
    while True:
        msg = _recv()
        cmd = msg.get("cmd")

        if cmd == "exec":
            _cell_id = msg.get("cell_id")
            code = msg["code"]
            cap_buf = StringIO()
            err_buf = StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            try:
                sys.stdout = cap_buf
                sys.stderr = err_buf
                exec(code, globals())
            except Exception:
                traceback.print_exc(file=err_buf)
            finally:
                sys.stdout = old_out
                sys.stderr = old_err

            final_answer = None
            try:
                if isinstance(answer, dict) and bool(answer.get("ready")):
                    content = answer.get("content", "")
                    final_answer = content if isinstance(content, str) else str(content)
            except Exception:
                # Model code owns `answer` once a cell has run, and this read is
                # on the critical path for *sending the result at all*: an
                # unusable value here used to escape the loop and strand the
                # cell, which the harness could only report as a timeout (and
                # which cost the model its REPL state on restart). Treat an
                # uninspectable `answer` as "not ready" and report the state.
                final_answer = None

            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": cap_buf.getvalue(),
                "stderr": err_buf.getvalue(),
                "final_answer": final_answer,
                "answer_state": _answer_state(),
            })

        elif cmd == "init":
            raw_ctx = msg.get("context")
            if isinstance(raw_ctx, dict) and raw_ctx.get("kind") == "file":
                context = _FileContext(raw_ctx["path"], raw_ctx["total"])
            elif raw_ctx is None:
                context = ""
            else:
                context = raw_ctx
            helpers = msg.get("helpers", [])
            for h in helpers:
                try:
                    exec(h["code"], globals())
                except Exception:
                    pass
            _send({"type": "result", "cell_id": None, "status": "ok"})

        elif cmd == "search":
            query = msg.get("query", "")
            k = msg.get("k", 5)
            kinds = msg.get("kinds")
            _send({"cmd": "search", "query": query, "k": k, "kinds": kinds,
                   "cell_id": _cell_id})
            resp = _recv()
            result_text = resp.get("result", "(no results)")
            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": result_text,
                "stderr": "",
                "final_answer": None,
            })

        elif cmd == "propose":
            _send({"cmd": "propose", "kind": msg.get("kind", ""),
                    "name": msg.get("name", ""), "body": msg.get("body", ""),
                    "rationale": msg.get("rationale", ""), "cell_id": _cell_id})
            resp = _recv()
            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": resp.get("result", "Error: propose failed"),
                "stderr": "",
                "final_answer": None,
            })

        elif cmd == "shutdown":
            _sock.close()
            break

if __name__ == "__main__":
    main()
"""


class REPLSandbox:
    """Subprocess-isolated Python REPL with callbacks for sub-LLM calls."""

    def __init__(
        self,
        cell_timeout: float = 60.0,
        stdout_cap: int = 256 * 1024,
        restart_after_consecutive_timeouts: int = 2,
    ) -> None:
        self._cell_timeout = cell_timeout
        self._stdout_cap = stdout_cap
        self._restart_threshold = max(2, restart_after_consecutive_timeouts)
        self._proc: subprocess.Popen | None = None
        self._server_sock: socket.socket | None = None
        self._worker_sock: socket.socket | None = None
        self._worker_path: Path | None = None
        self._subcall_manager: Any = None
        self._lock = threading.RLock()
        self._kernel_bridge: Any = None
        # R4 protocol state
        self._cell_seq = 0
        self._init_payload: dict | None = None
        self._consecutive_timeouts = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, context: Any, subcall_manager: Any,
              definitions: list[dict[str, str]] | None = None) -> None:
        """Start the REPL worker and inject context + subcall manager.

        Args:
            context: The user context data. A disk-backed
                :class:`~rlm_local.context_store.Context` is passed by
                reference (path + byte length) so the text never crosses the
                socket and the worker reads it lazily (R1).
            subcall_manager: The SubcallManager for sub-LLM call proxying.
            definitions: Optional list of {"name": str, "code": str} helper
                         definitions to inject into the REPL namespace (K1).
        """
        self._subcall_manager = subcall_manager

        if isinstance(context, Context):
            init_ctx: Any = {
                "kind": "file",
                "path": str(context._path),
                "total": len(context),
            }
        else:
            init_ctx = str(context)

        self._init_payload = {"cmd": "init", "context": init_ctx}
        if definitions:
            self._init_payload["helpers"] = definitions

        self._spawn()

    def _spawn(self) -> None:
        """(Re)launch the worker process and replay the init payload."""
        if self._worker_sock is not None:
            try:
                self._worker_sock.close()
            except OSError:
                pass
            self._worker_sock = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._proc = None

        # Write worker script (kept on disk so tracebacks are readable)
        tmpdir = Path(tempfile.mkdtemp(prefix="rlm_repl_"))
        worker_path = tmpdir / "_worker.py"
        worker_path.write_text(_WORKER_SCRIPT, encoding="utf-8")
        self._worker_path = worker_path

        # Bind server socket
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(1)
        host, port = self._server_sock.getsockname()

        # Launch worker
        self._proc = subprocess.Popen(
            [sys.executable, str(worker_path)],
            env={**os.environ, "RLM_REPL_HOST": host, "RLM_REPL_PORT": str(port)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Accept worker connection (with timeout)
        self._server_sock.settimeout(10.0)
        try:
            self._worker_sock, addr = self._server_sock.accept()
        except socket.timeout:
            self._proc.kill()
            raise RuntimeError("REPL worker failed to connect within 10s")
        finally:
            self._server_sock.settimeout(None)

        # Initialize: send context reference + optional helpers (K1)
        _send_msg(self._worker_sock, self._init_payload or {"cmd": "init", "context": ""})
        resp = _recv_msg(self._worker_sock, timeout=5.0)
        if resp is None or resp.get("status") != "ok":
            self.shutdown()
            raise RuntimeError(f"REPL init failed: {resp}")

        self._cell_seq = 0
        self._consecutive_timeouts = 0

    def restart_worker(self) -> None:
        """Kill and relaunch the worker, replaying init (R4).

        REPL variables are lost; callers must tell the model.
        """
        with self._lock:
            self._spawn()

    def _restart_in_place(self) -> None:
        self._spawn()

    # ── Execution ─────────────────────────────────────────────────────────

    def execute(self, code: str) -> REPLResult:
        """Execute code in the worker, handling interleaved subcall requests.

        Every cell carries a `cell_id`; results carrying a different id belong
        to a cell that already timed out and are discarded rather than
        misattributed to this one (R4).
        """
        if not self._worker_sock:
            raise RuntimeError("REPL not started")

        with self._lock:
            self._cell_seq += 1
            cell_id = self._cell_seq
            _send_msg(self._worker_sock, {"cmd": "exec", "code": code, "cell_id": cell_id})

            deadline = time.monotonic() + self._cell_timeout

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    msg = None
                else:
                    msg = _recv_msg(self._worker_sock, timeout=remaining)

                if msg is None:
                    return self._on_timeout(cell_id)

                msg_type = msg.get("type", msg.get("cmd", ""))
                msg_cell = msg.get("cell_id")

                if msg_type == "result":
                    if msg_cell != cell_id:
                        # A late result for a cell we already gave up on. Drop
                        # it and let the current cell have a full window — the
                        # worker only starts it once it has finished the old
                        # one.
                        deadline = time.monotonic() + self._cell_timeout
                        continue
                    return self._build_result(msg)

                if msg_cell is not None and msg_cell != cell_id:
                    # Late request from a cell we already abandoned: answer it
                    # so the worker is not left blocked, then keep draining.
                    self._answer_stale_request(msg)
                    deadline = time.monotonic() + self._cell_timeout
                    continue

                self._handle_request(msg_type, msg)

    def _build_result(self, msg: dict) -> REPLResult:
        stdout = msg.get("stdout", "")
        stderr = msg.get("stderr", "")
        final_answer = msg.get("final_answer")
        answer_state = msg.get("answer_state")

        if len(stdout) > self._stdout_cap:
            stdout = stdout[: self._stdout_cap] + CELL_STDOUT_TRUNCATED.format(
                cap=self._stdout_cap,
            )
        if len(stderr) > self._stdout_cap:
            stderr = _truncate_middle(stderr, self._stdout_cap)

        self._consecutive_timeouts = 0
        return REPLResult(
            stdout=stdout, stderr=stderr, final_answer=final_answer,
            answer_state=answer_state if isinstance(answer_state, dict) else None,
        )

    def _on_timeout(self, cell_id: int) -> REPLResult:
        """Handle a cell that exceeded `cell_timeout` (R4)."""
        self._consecutive_timeouts += 1
        message = CELL_TIMEOUT_ERROR.format(timeout=self._cell_timeout)

        if self._consecutive_timeouts >= self._restart_threshold:
            # A stale result is still outstanding and we timed out again: the
            # worker is wedged. Restart it and start a clean cell sequence.
            try:
                self._restart_in_place()
            except Exception as e:  # pragma: no cover - environment failure
                return REPLResult(stderr=f"{message}\n{REPL_WORKER_RESTARTED}\n{e}")
            return REPLResult(stderr=f"{message}\n{REPL_WORKER_RESTARTED}")

        return REPLResult(stderr=message)

    def _answer_stale_request(self, msg: dict) -> None:
        """Unblock a worker that is asking about a cell we already abandoned."""
        if not self._worker_sock:
            return
        msg_type = msg.get("type", msg.get("cmd", ""))
        try:
            if msg_type == "subcall_batched":
                n = len(msg.get("prompts", []))
                _send_msg(self._worker_sock, {"responses": ["Error: cell timed out"] * n})
            elif msg_type in ("subcall", "search", "propose"):
                _send_msg(self._worker_sock, {"response": "Error: cell timed out",
                                              "result": "Error: cell timed out"})
        except OSError:
            pass

    def _handle_request(self, msg_type: str, msg: dict) -> None:
        """Serve a sub-call / search / propose request from the running cell."""
        if msg_type == "subcall":
            prompt = msg.get("prompt", "")
            schema = msg.get("schema")
            if self._subcall_manager:
                response = self._subcall_manager.llm_query(prompt, schema=schema)
            else:
                response = "Error: subcall manager not available"
            _send_msg(self._worker_sock, {"response": response})

        elif msg_type == "subcall_batched":
            prompts = msg.get("prompts", [])
            schema = msg.get("schema")
            if self._subcall_manager:
                responses = self._subcall_manager.llm_query_batched(prompts, schema=schema)
            else:
                responses = ["Error: subcall manager not available"] * len(prompts)
            _send_msg(self._worker_sock, {"responses": responses})

        elif msg_type == "search":
            # K1: worker requesting a vault search
            if self._kernel_bridge:
                result_text = self._kernel_bridge.handle_search(
                    msg.get("query", ""),
                    k=msg.get("k", 5),
                    kinds=msg.get("kinds"),
                )
            else:
                result_text = "Error: kernel bridge not available"
            _send_msg(self._worker_sock, {"result": result_text})

        elif msg_type == "propose":
            # K1: worker proposing a new page
            if self._kernel_bridge:
                result_text = self._kernel_bridge.handle_propose(
                    msg.get("kind", ""),
                    msg.get("name", ""),
                    msg.get("body", ""),
                    msg.get("rationale", ""),
                )
            else:
                result_text = "Error: kernel bridge not available"
            _send_msg(self._worker_sock, {"result": result_text})

        else:
            _send_msg(self._worker_sock, {
                "response": f"Error: unsupported REPL request: {msg_type}",
                "result": f"Error: unsupported REPL request: {msg_type}",
            })

    def shutdown(self) -> None:
        """Terminate the REPL worker and clean up."""
        if self._worker_sock:
            try:
                _send_msg(self._worker_sock, {"cmd": "shutdown"})
            except (OSError, BrokenPipeError, ConnectionResetError):
                pass
            try:
                self._worker_sock.close()
            except OSError:
                pass
            self._worker_sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        if self._proc:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
            self._proc = None

        if self._worker_path:
            import shutil
            shutil.rmtree(self._worker_path.parent, ignore_errors=True)
            self._worker_path = None

    # ── Introspection (used by the root loop / tests) ─────────────────────

    @property
    def cell_timeout(self) -> float:
        return self._cell_timeout

    @property
    def stdout_cap(self) -> int:
        return self._stdout_cap
