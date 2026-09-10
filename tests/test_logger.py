"""Tests for the JSONL trajectory logger (§10.3, R4.2) — R22."""

from __future__ import annotations

import builtins
import json
import threading
import time
from pathlib import Path

import pytest

import rlm_local.logger as logger_mod
from rlm_local.logger import TrajectoryLogger


class TestDefaultLocation:
    """R22 — trajectories used to land in the shared system temp dir under a
    predictable, never-cleaned name."""

    def test_default_path_is_under_the_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = TrajectoryLogger()
        resolved = logger.path.resolve()
        assert resolved.is_relative_to((tmp_path / "logs" / "trajectories").resolve()), resolved

    def test_default_directory_is_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = TrajectoryLogger()
        logger.log_start("q", 1, {})
        assert logger.path.exists()

    def test_logger_does_not_use_the_system_temp_dir(self):
        """The old default was `gettempdir()/rlm_trajectory_<us>.jsonl`.

        (A path-comparison test would be vacuous here: pytest's own `tmp_path`
        lives under the system temp dir, so "not under temp" cannot be asserted
        about an absolute path. Assert the mechanism instead.)
        """
        src = Path(logger_mod.__file__).read_text(encoding="utf-8")
        assert "tempfile" not in src
        assert "gettempdir" not in src

    def test_default_path_is_not_collision_prone(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = TrajectoryLogger()
        b = TrajectoryLogger()
        assert a.path != b.path

    def test_retention_is_documented(self):
        doc = TrajectoryLogger.__doc__ or ""
        assert "retention" in doc.lower()


class TestWriterJsonl:
    def test_records_round_trip(self, tmp_path):
        path = tmp_path / "t.jsonl"
        logger = TrajectoryLogger(path)
        logger.log_start("query", 10, {"profile": "tiny"})
        logger.log_turn_start(1, 12)
        logger.log_root_message("user", "hello")
        logger.log_end("answer", 1, 0)

        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [r["event"] for r in records] == [
            "start", "turn_start", "root_message", "end",
        ]
        assert records[0]["query"] == "query"
        assert records[3]["final_answer"] == "answer"

    def test_unicode_is_preserved(self, tmp_path):
        path = tmp_path / "t.jsonl"
        logger = TrajectoryLogger(path)
        logger.log_root_message("user", "café 🎉 日本語")
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["content"] == "café 🎉 日本語"


class TestThreadSafety:
    """R22 — the docstring claimed "Thread-safe" but there was no lock.

    `_write` opens, writes and closes per record, so concurrent writers can
    overlap inside that window. The test below instruments the write itself and
    fails if two writers are ever inside it at once (the lock is what prevents
    that); it also proves no record is lost.
    """

    def _instrument(self, monkeypatch, state):
        class FakeFile:
            def __init__(self, *args, **kwargs):
                pass

            def write(self, text):
                with state["guard"]:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                try:
                    time.sleep(0.003)  # widen the window a real write would occupy
                    with state["guard"]:
                        state["lines"].append(text)
                finally:
                    with state["guard"]:
                        state["active"] -= 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(*args, **kwargs):
            return FakeFile(*args, **kwargs)

        monkeypatch.setattr(logger_mod, "open", fake_open, raising=False)

    def test_concurrent_writes_do_not_overlap(self, tmp_path, monkeypatch):
        state = {
            "guard": threading.Lock(),
            "active": 0,
            "max_active": 0,
            "lines": [],
        }
        self._instrument(monkeypatch, state)

        logger = TrajectoryLogger(tmp_path / "t.jsonl")
        threads = [
            threading.Thread(
                target=lambda i=i: [
                    logger.log_guardrail(1, "g", f"{i}-{n}") for n in range(20)
                ]
            )
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state["max_active"] == 1, (
            f"{state['max_active']} writers were inside _write at once"
        )
        assert len(state["lines"]) == 160, "a record was lost"

    def test_lock_exists(self, tmp_path):
        logger = TrajectoryLogger(tmp_path / "t.jsonl")
        assert hasattr(logger, "_lock")


class TestRetentionHelper:
    """R22 — trajectories accumulate; pruning is an explicit operation."""

    def test_prune_keeps_newest(self, tmp_path):
        import os

        dirpath = tmp_path / "trajectories"
        dirpath.mkdir()
        paths = []
        for i in range(5):
            p = dirpath / f"trajectory_{i}.jsonl"
            p.write_text("{}\n", encoding="utf-8")
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
            paths.append(p)

        removed = TrajectoryLogger.prune(directory=dirpath, keep=2)
        assert removed == 3
        remaining = sorted(p.name for p in dirpath.glob("*.jsonl"))
        assert remaining == ["trajectory_3.jsonl", "trajectory_4.jsonl"]

    def test_prune_is_a_noop_when_under_the_limit(self, tmp_path):
        dirpath = tmp_path / "trajectories"
        dirpath.mkdir()
        (dirpath / "only.jsonl").write_text("{}\n", encoding="utf-8")
        assert TrajectoryLogger.prune(directory=dirpath, keep=10) == 0

    def test_prune_missing_directory_is_safe(self, tmp_path):
        assert TrajectoryLogger.prune(directory=tmp_path / "nope", keep=1) == 0
