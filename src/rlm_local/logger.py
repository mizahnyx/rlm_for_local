"""JSONL trajectory logger (§10.3, R4.2).

Records every root message, every sub-call prompt/response, every REPL result,
and budget usage to a JSONL file. This is the data source for:
- Equivalence-class measurement (R4.2)
- Distillation data export (§11)
- Debugging and ablation analysis

Thread-safety (R22)
-------------------
Trajectories are written from the root loop *and* from sub-call worker threads,
so `_write` holds a lock: a record is serialized into the file as one unit and
no record is lost to an interleaved write.

Retention (R22)
---------------
Records contain full prompts and responses, so they are written under
`./logs/trajectories/` inside the working directory (gitignored) rather than a
shared, world-readable temp directory. Nothing deletes them automatically:
retention is explicit — call :meth:`TrajectoryLogger.prune` (or point
`log_path` somewhere rotated) once the directory grows.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# Default location, relative to the working directory.
DEFAULT_LOG_DIR = Path("logs") / "trajectories"


class TrajectoryLogger:
    """Thread-safe JSONL logger for harness trajectories.

    Retention is manual: log files are never deleted implicitly, and
    `prune()` is provided for operators who want a bounded history.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.time_ns()
            path = DEFAULT_LOG_DIR / f"trajectory_{ts}.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._started_at = time.time()
        self._turn_count = 0
        self._subcall_count = 0
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """Where this trajectory is being written."""
        return self._path

    # ── Retention ─────────────────────────────────────────────────────────

    @staticmethod
    def prune(directory: str | Path = DEFAULT_LOG_DIR, keep: int = 50) -> int:
        """Delete all but the `keep` newest `*.jsonl` files in `directory`.

        Returns the number of files removed. Never raises on a missing
        directory — retention must not be able to break a run.
        """
        directory = Path(directory)
        if not directory.is_dir():
            return 0
        files = sorted(
            directory.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for stale in files[max(0, keep):]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    # ── Public log methods ────────────────────────────────────────────────

    def log_start(self, query: str, context_len: int, config: dict[str, Any]) -> None:
        self._write({
            "event": "start",
            "timestamp": time.time(),
            "query": query,
            "context_len": context_len,
            "config": config,
        })

    def log_root_message(self, role: str, content: str) -> None:
        """Log a message sent to or received from the root model."""
        self._write({
            "event": "root_message",
            "timestamp": time.time(),
            "turn": self._turn_count,
            "role": role,
            "content": content,
        })

    def log_turn_start(self, turn: int, max_turns: int) -> None:
        self._turn_count = turn
        self._write({
            "event": "turn_start",
            "timestamp": time.time(),
            "turn": turn,
            "max_turns": max_turns,
        })

    def log_repl_result(self, turn: int, stdout: str, stderr: str,
                        final_answer: str | None, warnings: list[str],
                        answer_state: dict[str, Any] | None = None) -> None:
        """Record one cell's outcome.

        `answer_state` is the scaffold `answer` as it stood when the cell ended
        (VD2). It is optional because not every caller has one — a timed-out cell
        has no reliable state — and a consumer that needs it must treat None as
        "unknown", never as "unchanged".
        """
        self._write({
            "event": "repl_result",
            "timestamp": time.time(),
            "turn": turn,
            "stdout": stdout,
            "stderr": stderr,
            "final_answer": final_answer,
            "warnings": warnings,
            "answer_state": answer_state,
        })

    def log_subcall(self, turn: int, index: int, prompt: str, response: str,
                    schema: dict[str, Any] | None = None,
                    cached: bool = False) -> None:
        self._subcall_count += 1
        self._write({
            "event": "subcall",
            "timestamp": time.time(),
            "turn": turn,
            "index": index,
            "subcall_num": self._subcall_count,
            "prompt": prompt,
            "response": response,
            "schema": schema,
            "cached": cached,
        })

    def log_guardrail(self, turn: int, guardrail: str, detail: str) -> None:
        self._write({
            "event": "guardrail",
            "timestamp": time.time(),
            "turn": turn,
            "guardrail": guardrail,
            "detail": detail,
        })

    def log_end(self, final_answer: str, turns_used: int, subcalls_used: int,
                forced: bool = False) -> None:
        self._write({
            "event": "end",
            "timestamp": time.time(),
            "elapsed_s": time.time() - self._started_at,
            "final_answer": final_answer,
            "turns_used": turns_used,
            "subcalls_used": subcalls_used,
            "forced": forced,
        })

    # ── Internals ─────────────────────────────────────────────────────────

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
