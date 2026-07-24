"""JSONL trajectory logger (§10.3, R4.2).

Records every root message, every sub-call prompt/response, every REPL result,
and budget usage to a JSONL file. This is the data source for:
- Equivalence-class measurement (R4.2)
- Distillation data export (§11)
- Debugging and ablation analysis
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class TrajectoryLogger:
    """Thread-safe JSONL logger for harness trajectories."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            import tempfile
            ts = int(time.time() * 1_000_000)
            path = Path(tempfile.gettempdir()) / f"rlm_trajectory_{ts}.jsonl"
        self._path = Path(path)
        self._started_at = time.time()
        self._turn_count = 0
        self._subcall_count = 0

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
                        final_answer: str | None, warnings: list[str]) -> None:
        self._write({
            "event": "repl_result",
            "timestamp": time.time(),
            "turn": turn,
            "stdout": stdout,
            "stderr": stderr,
            "final_answer": final_answer,
            "warnings": warnings,
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
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
