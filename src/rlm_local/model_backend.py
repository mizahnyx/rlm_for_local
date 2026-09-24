"""OpenAI-compatible HTTP model backend (§5.1).

Talks to llama-server, Ollama, LM Studio, or any /v1/chat/completions endpoint.
Handles self-signed certs, two-tier routing, and prefix-cache discipline.

Robustness contract (R9)
------------------------
* The configured endpoint is normalized to a `/v1` base — the comment always
  claimed this and the code only stripped trailing slashes, so an endpoint
  given as `http://host:9010` produced a 404 path.
* Response extraction is guarded: a payload without `choices`/`content` raises
  :class:`ModelBackendError` carrying the HTTP status and a body snippet,
  instead of leaking a bare `KeyError`/`IndexError` out of `completion()`.
* Transport errors and 5xx responses are retried twice with exponential
  backoff (the `tenacity` dependency was declared but never used). 4xx is not
  retried: retrying a client error cannot help.
* `headers` allows bearer-token auth against a proxied endpoint.
* Verification stays off by default for local self-signed servers, but a
  non-loopback endpoint with verification off emits a `UserWarning` (R18).
"""

from __future__ import annotations

import ipaddress
import json
import warnings
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# How much of the response body is quoted in error messages.
_BODY_SNIPPET = 300


class ModelBackendError(RuntimeError):
    """The backend could not extract a completion from the server's response.

    Carries the HTTP status and a body snippet so the failure is diagnosable
    from the error alone.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        detail = message
        if status is not None:
            detail = f"{detail} (HTTP {status})"
        if body:
            snippet = body[:_BODY_SNIPPET]
            if len(body) > _BODY_SNIPPET:
                snippet += "…"
            detail = f"{detail}: {snippet}"
        super().__init__(detail)
        self.status = status
        self.body = body


class ChatResult:
    """A completion as the server described it: the text, and its own measurements.

    `usage` and `timings` are `None` when the backend or the server does not report them. That is
    not an error, and it must never become a zero: a missing measurement that reads as "0 ms of
    prompt processing" is precisely the confident wrong answer this project keeps meeting
    (`AGENTS.md` §1.8 corollary — a check that cannot see the truth must say unknown).
    """

    __slots__ = ("content", "usage", "timings")

    def __init__(
        self,
        content: str,
        usage: dict[str, Any] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.usage = usage
        self.timings = timings

    def __repr__(self) -> str:  # pragma: no cover - a debugging aid
        return (f"ChatResult(content=<{len(self.content)} chars>, "
                f"usage={'yes' if self.usage else 'no'}, "
                f"timings={'yes' if self.timings else 'no'})")


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


def normalize_endpoint(url: str) -> str:
    """Return `url` as an OpenAI `/v1` base URL.

    Trailing slashes are stripped and `/v1` is appended when absent, matching
    what the class docstring has always promised.
    """
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _is_loopback(endpoint: str) -> bool:
    """True for localhost, 127.0.0.0/8, and ::1."""
    host = urlsplit(endpoint).hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_retryable(exc: BaseException) -> bool:
    """Transport failures and 5xx only (R9)."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


def _extract_content(data: Any, *, status: int, body: str) -> str:
    """Pull the assistant text out of an OpenAI-style payload, or explain why not."""
    if not isinstance(data, dict):
        raise ModelBackendError(
            "response was not a JSON object", status=status, body=body,
        )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelBackendError(
            "response has no 'choices' array", status=status, body=body,
        )

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelBackendError(
            "response 'choices[0]' is not an object", status=status, body=body,
        )

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelBackendError(
            "response 'choices[0]' has no 'message' object",
            status=status, body=body,
        )

    content = message.get("content")
    if content is None:
        # Reasoning models sometimes put the text elsewhere and leave
        # `content` null; accept the common fallback rather than failing.
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            return reasoning
        finish = choice.get("finish_reason")
        raise ModelBackendError(
            f"response message has no 'content' (finish_reason={finish!r})",
            status=status, body=body,
        )
    if not isinstance(content, str):
        raise ModelBackendError(
            f"response 'content' is {type(content).__name__}, not a string",
            status=status, body=body,
        )
    return content


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
        headers: dict[str, str] | None = None,
    ) -> None:
        self._root_endpoint = normalize_endpoint(root_endpoint)
        self._sub_endpoint = normalize_endpoint(sub_endpoint or root_endpoint)
        self._root_model = root_model
        self._sub_model = sub_model or root_model
        self._verify = verify
        self._timeout = timeout
        self._headers = dict(headers) if headers else {}

        # R18: verification off is a localhost convenience. Anywhere else it
        # silently accepts whatever certificate shows up — say so. Only https
        # endpoints have a certificate to verify, so plain http is exempt.
        if not verify:
            for endpoint in {self._root_endpoint, self._sub_endpoint}:
                if urlsplit(endpoint).scheme == "https" and not _is_loopback(endpoint):
                    warnings.warn(
                        f"TLS certificate verification is disabled for the "
                        f"non-loopback endpoint {endpoint!r}. Traffic to it can "
                        f"be intercepted. Pass verify=True, or point the "
                        f"endpoint at localhost.",
                        UserWarning,
                        stacklevel=2,
                    )

        # Shared client with connection pooling
        limits = httpx.Limits(max_keepalive_connections=8, max_connections=16)
        self._client = httpx.Client(
            verify=verify,
            timeout=httpx.Timeout(timeout),
            limits=limits,
        )

    @property
    def root_endpoint(self) -> str:
        return self._root_endpoint

    @property
    def sub_endpoint(self) -> str:
        return self._sub_endpoint

    @retry(
        stop=stop_after_attempt(3),  # initial attempt + 2 retries
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    def _post(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        resp = self._client.post(url, json=payload, headers=self._headers or None)
        resp.raise_for_status()
        return resp

    def chat_detailed(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Send a chat completion and return the content **plus what the server measured**.

        `chat` throws away everything but the text, which is right for a run and wrong for a
        measurement: llama-server reports `usage` (including `prompt_tokens_details.cached_tokens`)
        and `timings` (`prompt_ms`/`prompt_n`, `predicted_ms`/`predicted_n`, `cache_n`) per
        request. Those numbers are the only way to tell **prompt processing from decode**, and
        therefore the only way to explain why describing two documents of the same size can take
        29 s and 803 s — measured 2026-09-23, and unexplained until this was recorded.

        Nothing is added to the protocol: a caller that wants the metadata asks for it by name and
        falls back to `chat` when the backend does not offer it.
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
        resp = self._post(url, payload)
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ModelBackendError(
                f"response was not valid JSON ({e})",
                status=resp.status_code,
                body=resp.text,
            ) from e

        usage = data.get("usage") if isinstance(data, dict) else None
        timings = data.get("timings") if isinstance(data, dict) else None
        return ChatResult(
            content=_extract_content(data, status=resp.status_code, body=resp.text),
            usage=usage if isinstance(usage, dict) else None,
            timings=timings if isinstance(timings, dict) else None,
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
        """Send a chat completion request and return the assistant message content.

        See `chat_detailed` for the same call with the server's own measurements attached.
        """
        return self.chat_detailed(
            messages, tier=tier, max_tokens=max_tokens, temperature=temperature,
            response_schema=response_schema,
        ).content

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HTTPModelBackend:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
