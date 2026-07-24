"""Integration tests using the real llama-server.

These tests require the llama-server running at https://localhost:9010
with the LFM2.5-VL-1.6B model loaded. They are slow and may be skipped
in CI by marking with @pytest.mark.slow.
"""

from __future__ import annotations

import pytest

from rlm_local.model_backend import HTTPModelBackend


@pytest.mark.slow
class TestModelBackendIntegration:
    @pytest.fixture(scope="class")
    def backend(self):
        be = HTTPModelBackend(
            root_endpoint="https://localhost:9010/v1",
            root_model="LFM2.5-VL-1.6B",
            verify=False,
        )
        yield be
        be.close()

    def test_simple_chat(self, backend):
        resp = backend.chat(
            [{"role": "user", "content": "Say hello."}],
            tier="root",
            max_tokens=50,
            temperature=0.0,
        )
        assert len(resp) > 0
        assert "hello" in resp.lower() or "hi" in resp.lower()

    def test_code_block_emission(self, backend):
        """Model should be able to emit a ```repl block when asked."""
        resp = backend.chat(
            [{"role": "user", "content": "Write a ```repl block that prints 'hello'."}],
            tier="root",
            max_tokens=200,
            temperature=0.0,
        )
        assert "print" in resp.lower()

    def test_sub_tier_routing(self, backend):
        """Sub tier should use the same model if no separate sub model configured."""
        resp = backend.chat(
            [{"role": "user", "content": "Say one word: yes"}],
            tier="sub",
            max_tokens=10,
            temperature=0.0,
        )
        assert len(resp) > 0
