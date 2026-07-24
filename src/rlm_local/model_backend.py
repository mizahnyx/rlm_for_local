"""OpenAI-compatible HTTP model backend (§5.1).

Talks to llama-server, Ollama, LM Studio, or any /v1/chat/completions endpoint.
Handles self-signed certs, two-tier routing, and prefix-cache discipline.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class ModelBackend(Protocol):
    """Protocol for model inference backends (§5.1)."""

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str: ...


class HTTPModelBackend:
    """OpenAI-compatible HTTP backend for llama-server and friends."""

    def __init__(
        self,
        root_endpoint: str = "https://localhost:9010/v1",
        sub_endpoint: str = "",
        root_model: str = "",
        sub_model: str = "",
        verify: bool = False,
        timeout: float = 300.0,
    ) -> None:
        # Normalize: strip trailing slashes, ensure /v1 suffix
        self._root_endpoint = root_endpoint.rstrip("/")
        self._sub_endpoint = (sub_endpoint or root_endpoint).rstrip("/")
        self._root_model = root_model
        self._sub_model = sub_model or root_model
        self._verify = verify
        self._timeout = timeout

        # Shared client with connection pooling
        limits = httpx.Limits(max_keepalive_connections=8, max_connections=16)
        self._client = httpx.Client(
            verify=verify,
            timeout=httpx.Timeout(timeout),
            limits=limits,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Send a chat completion request.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            tier: "root" or "sub" — selects endpoint/model.
            max_tokens: Max completion tokens.
            temperature: Sampling temperature (0 = deterministic).
            response_schema: Optional JSON schema for constrained decoding.
        """
        endpoint = self._root_endpoint if tier == "root" else self._sub_endpoint
        model = self._root_model if tier == "root" else self._sub_model
        url = f"{endpoint}/chat/completions"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                    "strict": False,
                },
            }

        # The reference target blocks extraneous fields. Include only what
        # the OpenAI spec and llama-server accept.
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HTTPModelBackend:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
