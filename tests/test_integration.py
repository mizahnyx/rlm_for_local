"""Integration tests using the real llama-server.

These tests require a running llama-server (or any OpenAI-compatible server)
and are marked `slow`, so the fast suite deselects them. They are skipped when
the endpoint is unreachable.

Endpoint resolution (R25 — model-era drift): the default is the **configured
production model and endpoint** from `rlm_local.config`, not a hardcoded model
that the project's own model check scored FAIL. Override per host:

    RLM_TEST_ENDPOINT=https://lunacode:9010/v1
    RLM_TEST_MODEL=Qwen3.5-4B-Abliterated
"""

from __future__ import annotations

import os

import pytest

from rlm_local.config import PROFILES
from rlm_local.model_backend import HTTPModelBackend

_DEFAULT_ENDPOINT = PROFILES["laptop"].root_endpoint
_DEFAULT_MODEL = PROFILES["laptop"].root_model


@pytest.fixture(scope="module")
def endpoint() -> str:
    return os.environ.get("RLM_TEST_ENDPOINT", _DEFAULT_ENDPOINT)


@pytest.fixture(scope="module")
def model() -> str:
    return os.environ.get("RLM_TEST_MODEL", _DEFAULT_MODEL)


@pytest.fixture(scope="module")
def backend(endpoint, model):
    """Create a backend, skipping if the server is unreachable."""
    import httpx

    server_root = endpoint.rstrip("/").removesuffix("/v1")
    try:
        probe = httpx.Client(verify=False, timeout=3.0)
        # llama.cpp exposes /health; anything answering there is enough.
        probe.get(f"{server_root}/health")
        probe.close()
    except Exception:
        pytest.skip(f"llama-server not available at {endpoint}")

    be = HTTPModelBackend(
        root_endpoint=endpoint,
        root_model=model,
        verify=False,
        timeout=600.0,
    )
    yield be
    be.close()


@pytest.mark.slow
class TestModelBackendIntegration:

    def test_simple_chat(self, backend):
        resp = backend.chat(
            [{"role": "user", "content": "Say hello."}],
            tier="root",
            max_tokens=50,
            temperature=0.0,
        )
        assert len(resp) > 0

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
