"""REPL sandbox — subprocess-isolated Python worker (§5.3).

Uses a bidirectional TCP socket protocol:
- Harness sends: exec, init, shutdown
- Worker sends back: result, subcall_request (which the harness proxies)

This enables llm_query/llm_query_batched as ordinary Python functions in the
worker while keeping the worker killable and memory-bounded.
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

# ── Result envelope ────────────────────────────────────────────────────────


@dataclass
class REPLResult:
    stdout: str = ""
    stderr: str = ""
    final_answer: str | None = None
    warnings: list[str] = field(default_factory=list)


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

# ── Callback to harness for sub-calls ─────────────────────────────────────

def _harness_llm_query(prompt, schema=None):
    _send({"cmd": "subcall", "prompt": prompt, "schema": schema})
    resp = _recv()
    return resp.get("response", "Error: no response from harness")

def _harness_llm_query_batched(prompts, schema=None):
    _send({"cmd": "subcall_batched", "prompts": prompts, "schema": schema})
    resp = _recv()
    return resp.get("responses", ["Error: no response from harness"] * len(prompts))

# Inject into globals so exec'd code can use them
llm_query = _harness_llm_query
llm_query_batched = _harness_llm_query_batched

# ── Scaffold namespace ────────────────────────────────────────────────────

answer = {"content": "", "ready": False}
context = None
_SHOW_VARS_IGNORE = frozenset({"answer", "context", "__builtins__", "llm_query", "llm_query_batched",
                                "_harness_llm_query", "_harness_llm_query_batched",
                                "_SHOW_VARS_IGNORE", "_sock", "_send", "_recv",
                                "json", "os", "re", "socket", "struct", "sys", "traceback",
                                "StringIO", "_HOST", "_PORT"})

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
    global context, answer
    while True:
        msg = _recv()
        cmd = msg.get("cmd")

        if cmd == "exec":
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
            if isinstance(answer, dict) and answer.get("ready"):
                final_answer = answer.get("content", "")

            _send({
                "type": "result",
                "stdout": cap_buf.getvalue(),
                "stderr": err_buf.getvalue(),
                "final_answer": final_answer,
            })

        elif cmd == "init":
            # Receive context as a raw string (worker treats it as str)
            raw_ctx = msg.get("context", "")
            context = raw_ctx
            # Inject helpers if provided (K1: definitions from vault)
            helpers = msg.get("helpers", [])
            for h in helpers:
                try:
                    exec(h["code"], globals())
                    exec(f"{h['name']} = _harness_search", globals()) if h["name"] == "search" else None
                except Exception:
                    pass
            _send({"type": "result", "status": "ok"})

        elif cmd == "search":
            # Proxy search to harness (K1)
            query = msg.get("query", "")
            k = msg.get("k", 5)
            kinds = msg.get("kinds")
            _send({"cmd": "search", "query": query, "k": k, "kinds": kinds})
            resp = _recv()
            result_text = resp.get("result", "(no results)")
            _send({
                "type": "result",
                "stdout": result_text,
                "stderr": "",
                "final_answer": None,
            })

        elif cmd == "propose":
            # Proxy propose to harness (K1)
            _send({"cmd": "propose", "kind": msg.get("kind", ""),
                    "name": msg.get("name", ""), "body": msg.get("body", ""),
                    "rationale": msg.get("rationale", "")})
            resp = _recv()
            _send({
                "type": "result",
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
    ) -> None:
        self._cell_timeout = cell_timeout
        self._stdout_cap = stdout_cap
        self._proc: subprocess.Popen | None = None
        self._server_sock: socket.socket | None = None
        self._worker_sock: socket.socket | None = None
        self._worker_path: Path | None = None
        self._subcall_manager: Any = None
        self._lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._kernel_bridge: Any = None

    def start(self, context: Any, subcall_manager: Any,
              definitions: list[dict[str, str]] | None = None) -> None:
        """Start the REPL worker and inject context + subcall manager.

        Args:
            context: The user context data.
            subcall_manager: The SubcallManager for sub-LLM call proxying.
            definitions: Optional list of {"name": str, "code": str} helper
                         definitions to inject into the REPL namespace (K1).
        """
        self._subcall_manager = subcall_manager

        # Write worker script
        tmpdir = Path(tempfile.mkdtemp(prefix="rlm_repl_"))
        worker_path = tmpdir / "_worker.py"
        worker_path.write_text(_WORKER_SCRIPT)
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

        # Start a background thread to handle subcall requests from worker
        self._accept_thread = threading.Thread(
            target=self._subcall_loop, daemon=True,
        )
        self._accept_thread.start()

        # Initialize: send context + optional helpers (K1)
        ctx_str = str(context) if isinstance(context, (Context, _InMemoryContext, str)) else str(context)
        init_msg: dict = {"cmd": "init", "context": ctx_str}
        if definitions:
            init_msg["helpers"] = definitions
        _send_msg(self._worker_sock, init_msg)
        resp = _recv_msg(self._worker_sock, timeout=5.0)
        if resp is None or resp.get("status") != "ok":
            self.shutdown()
            raise RuntimeError(f"REPL init failed: {resp}")

    def _subcall_loop(self) -> None:
        """Background thread: listen for subcall requests from worker socket.

        The worker sends subcall/subcall_batched, we proxy to SubcallManager,
        and send the response back.
        """
        # We need a separate connection or multiplexing. Simpler: the worker
        # uses the same socket for subcall requests and the harness intercepts
        # them between exec commands.
        #
        # Actually, the subcall requests happen during exec() — the worker
        # sends a subcall cmd, the harness receives it on the same socket,
        # processes it, sends response back, then the worker continues.
        #
        # This means _recv_msg in execute() must handle interleaved subcall
        # messages. We implement this in execute() itself.
        pass  # Handled inline in execute()

    def execute(self, code: str) -> REPLResult:
        """Execute code in the worker, handling interleaved subcall requests."""
        if not self._worker_sock:
            raise RuntimeError("REPL not started")

        with self._lock:
            _send_msg(self._worker_sock, {"cmd": "exec", "code": code})

            # Read responses — may be subcall requests or the final result
            while True:
                msg = _recv_msg(self._worker_sock, timeout=self._cell_timeout)
                if msg is None:
                    return REPLResult(
                        stderr=f"Error: REPL timed out after {self._cell_timeout}s",
                    )

                msg_type = msg.get("type", msg.get("cmd", ""))

                if msg_type == "subcall":
                    # Worker is requesting a subcall
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

                elif msg_type == "result":
                    # Final execution result
                    stdout = msg.get("stdout", "")
                    stderr = msg.get("stderr", "")
                    final_answer = msg.get("final_answer")

                    if len(stdout) > self._stdout_cap:
                        from rlm_local.templates import CELL_STDOUT_TRUNCATED
                        stdout = stdout[:self._stdout_cap] + CELL_STDOUT_TRUNCATED.format(
                            cap=self._stdout_cap,
                        )
                    if len(stderr) > self._stdout_cap:
                        stderr = stderr[:self._stdout_cap] + "\n[... stderr truncated ...]"

                    return REPLResult(
                        stdout=stdout,
                        stderr=stderr,
                        final_answer=final_answer,
                    )

                else:
                    # Unknown message — shouldn't happen
                    return REPLResult(
                        stderr=f"Error: unexpected REPL response type: {msg_type}",
                    )

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

    @property
    def cell_timeout(self) -> float:
        return self._cell_timeout
