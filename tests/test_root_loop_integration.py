"""Integration test — calls RootLoop.run() and rlm_local.completion() end-to-end.

Per conformity review R1: this test must exercise the full harness pipeline
against a stub model backend, covering:
  (a) completion without kernel bridge
  (b) completion with KernelBridge backed by a temp seeded vault
  (c) turn-0 probe → code → answer-dict flow
  (d) forced-finalization path

This test SHOULD fail on the current commit (D1–D3) and pass after R2 fixes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from rlm_local import completion
from rlm_local.config import Config, load_config
from rlm_local.model_backend import ModelBackend
from rlm_local.root_loop import RootLoop


# ── Stub model backend ─────────────────────────────────────────────────────

class StubBackend:
    """A ModelBackend that returns scripted responses in sequence."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({
            "tier": tier,
            "message_count": len(messages),
            "last_role": messages[-1]["role"] if messages else None,
        })
        if self.responses:
            return self.responses.pop(0)
        # Default: answer-dict response
        return "\n".join([
            "I'll submit the answer now.",
            "```repl",
            "answer['content'] = '42'",
            "answer['ready'] = True",
            "```",
        ])


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tiny_cfg() -> Config:
    return load_config("tiny")


@pytest.fixture
def seeded_vault():
    """Create a temp vault with seed pages + core memory for bridge tests."""
    from rlm_kernel.seed import seed_vault
    from rlm_kernel.vault import LocalVault

    td = tempfile.TemporaryDirectory(prefix="rlm_int_vault_", ignore_cleanup_errors=True)
    vault = LocalVault(Path(td.name), init_git=False)
    seed_vault(vault)
    # Write core memory so the bridge has something to inject
    from rlm_kernel.memory import MemoryManager
    mgr = MemoryManager()
    mgr.write_core(vault, "Test instance — integration test.")
    yield vault
    td.cleanup()


@pytest.fixture
def bridge(seeded_vault):
    """KernelBridge over a seeded temp vault with rebuilt index."""
    from rlm_kernel.index import rebuild_index
    from rlm_kernel.repl_bridge import KernelBridge

    idx_path = seeded_vault.root / ".index" / "meta.sqlite"
    rebuild_index(seeded_vault, idx_path)
    return KernelBridge(seeded_vault, idx_path)


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestRootLoopIntegration:
    """End-to-end tests through RootLoop.run()."""

    def test_basic_completion_without_kernel(self, tiny_cfg):
        """(a) A full completion without kernel bridge."""
        backend = StubBackend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("What is the answer?", "Some context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_completion_with_kernel_bridge(self, tiny_cfg, bridge):
        """(b) A completion with KernelBridge backed by a seeded vault."""
        backend = StubBackend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=bridge)
        try:
            answer = loop.run("What is the answer?", "Some context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_turn_zero_probe_then_answer(self, tiny_cfg):
        """(c) Turn-0 probe, turn-1 answer-dict flow."""
        backend = StubBackend(responses=[
            # Turn 0 — probe
            "\n".join([
                "I'll probe the context first.",
                "```repl",
                "print(f'Context length: {len(context)}')",
                "```",
            ]),
            # Turn 1 — answer
            "\n".join([
                "Got it. Submitting.",
                "```repl",
                "answer['content'] = 'probed-and-answered'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Probe test", "Test context data")
        finally:
            loop.shutdown()

        assert answer == "probed-and-answered"

    def test_forced_finalization_path(self, tiny_cfg):
        """(d) Model never sets answer-dict → forced finalization."""
        # Return responses with no ```repl blocks and no answer-dict
        # After nudges are exhausted, forced finalization triggers
        backend = StubBackend(responses=[
            "The sky is blue.",   # no code block → nudge
            "I think 42.",        # no code block → nudge
            "Maybe 43.",          # nudge budget exhausted → error
            "Let me think...",    # consecutive errors → forced finalize
            "FINAL: best effort 42",
        ] * 3)  # enough for all turns
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Forced test", "Context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0


class TestCompletionAPI:
    """End-to-end through the public completion() API."""

    def test_completion_without_kernel(self):
        """(a) completion() without bridge returns a string."""
        backend = StubBackend()
        answer = completion(
            "Test query", "Test context",
            config=load_config("tiny"), backend=backend,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_completion_with_kernel_bridge(self, bridge):
        """(b) completion() with bridge returns a string."""
        backend = StubBackend()
        answer = completion(
            "Test query", "Test context",
            config=load_config("tiny"), backend=backend,
            kernel_bridge=bridge,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0
