"""Tests for the OpenAI-compatible HTTP model backend (R9 / R18)."""

from __future__ import annotations

import json
import warnings

import httpx
import pytest

from rlm_local.model_backend import (
    HTTPModelBackend,
    ModelBackendError,
    normalize_endpoint,
)


def _backend(handler, **kwargs) -> HTTPModelBackend:
    """Backend whose HTTP client is wired to a mock transport."""
    be = HTTPModelBackend(root_model="test-model", **kwargs)
    be._client.close()
    be._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        verify=False,
        timeout=httpx.Timeout(5.0),
    )
    return be


OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
}


class TestEndpointNormalization:
    """R9 — the comment promised a `/v1` suffix; the code only stripped `/`."""

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("http://h:9010", "http://h:9010/v1"),
            ("http://h:9010/", "http://h:9010/v1"),
            ("http://h:9010/v1", "http://h:9010/v1"),
            ("http://h:9010/v1/", "http://h:9010/v1"),
            ("https://localhost:9010", "https://localhost:9010/v1"),
            ("https://lunacode:9010/", "https://lunacode:9010/v1"),
            ("http://h:8080/openai", "http://h:8080/openai/v1"),
            ("http://h:8080/openai/v1", "http://h:8080/openai/v1"),
            ("  http://h:9010  ", "http://h:9010/v1"),
        ],
    )
    def test_normalize_endpoint(self, given, expected):
        assert normalize_endpoint(given) == expected

    def test_request_goes_to_the_normalized_path(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json=OK_BODY)

        be = _backend(handler, root_endpoint="http://test-host:9010")
        try:
            be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        assert seen == ["/v1/chat/completions"]

    def test_sub_endpoint_defaults_to_root(self):
        be = HTTPModelBackend(root_endpoint="http://h:9010", sub_endpoint="")
        try:
            assert be._sub_endpoint == "http://h:9010/v1"
        finally:
            be.close()


class TestResponseGuarding:
    """R9 — malformed payloads must raise a descriptive error, not KeyError."""

    @pytest.mark.parametrize(
        "body,expected_fragment",
        [
            ({}, "choices"),
            ({"choices": []}, "choices"),
            ({"choices": "nope"}, "choices"),
            ({"choices": [{}]}, "message"),
            ({"choices": [{"message": {}}]}, "content"),
            ({"choices": [{"message": {"content": 42}}]}, "content"),
        ],
    )
    def test_malformed_payload_raises_model_backend_error(self, body, expected_fragment):
        def handler(request):
            return httpx.Response(200, json=body)

        be = _backend(handler)
        try:
            with pytest.raises(ModelBackendError) as exc:
                be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        message = str(exc.value)
        assert expected_fragment in message
        assert "200" in message, "the HTTP status must be in the error"
        # A body snippet must be included so operators can see what came back.
        compact = json.dumps(body, separators=(",", ":"))
        assert compact[:20] in message, message

    def test_valid_payload_returns_content(self):
        def handler(request):
            return httpx.Response(200, json=OK_BODY)

        be = _backend(handler)
        try:
            assert be.chat([{"role": "user", "content": "hi"}]) == "hello"
        finally:
            be.close()

    def test_reasoning_content_is_used_when_content_is_absent(self):
        """Reasoning models may fill `reasoning_content` and leave content null."""

        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": None, "reasoning_content": "thought"}}],
            })

        be = _backend(handler)
        try:
            assert be.chat([{"role": "user", "content": "hi"}]) == "thought"
        finally:
            be.close()

    def test_empty_string_content_is_returned_as_is(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

        be = _backend(handler)
        try:
            assert be.chat([{"role": "user", "content": "hi"}]) == ""
        finally:
            be.close()

    def test_error_includes_status_and_snippet(self):
        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        be = _backend(handler)
        try:
            with pytest.raises(ModelBackendError) as exc:
                be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        assert "not json" in str(exc.value)


class TestRetry:
    """R9 — `tenacity` was a declared dependency that was never imported."""

    def test_retries_on_5xx_then_succeeds(self):
        attempts: list[int] = []

        def handler(request):
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, json=OK_BODY)

        be = _backend(handler)
        try:
            assert be.chat([{"role": "user", "content": "hi"}]) == "hello"
        finally:
            be.close()
        assert len(attempts) == 3

    def test_gives_up_after_two_retries(self):
        attempts: list[int] = []

        def handler(request):
            attempts.append(1)
            return httpx.Response(503, text="busy")

        be = _backend(handler)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        assert len(attempts) == 3, "2 retries means at most 3 attempts"

    def test_retries_on_transport_error(self):
        attempts: list[int] = []

        def handler(request):
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectError("nope", request=request)
            return httpx.Response(200, json=OK_BODY)

        be = _backend(handler)
        try:
            assert be.chat([{"role": "user", "content": "hi"}]) == "hello"
        finally:
            be.close()
        assert len(attempts) == 2

    def test_does_not_retry_4xx(self):
        attempts: list[int] = []

        def handler(request):
            attempts.append(1)
            return httpx.Response(404, text="no such model")

        be = _backend(handler)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        assert len(attempts) == 1, "a 4xx is a client error: retrying cannot help"


class TestAuthHeaders:
    def test_headers_are_sent(self):
        seen: list[str | None] = []

        def handler(request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json=OK_BODY)

        be = _backend(handler, headers={"Authorization": "Bearer tok"})
        try:
            be.chat([{"role": "user", "content": "hi"}])
        finally:
            be.close()
        assert seen == ["Bearer tok"]


class TestTLSVerificationWarning:
    """R18 — verification off is fine on loopback and must be loud elsewhere."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://localhost:9010/v1",
            "https://127.0.0.1:9010/v1",
            "https://127.5.5.5:9010/v1",
            "https://[::1]:9010/v1",
        ],
    )
    def test_no_warning_for_loopback(self, endpoint):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            be = HTTPModelBackend(root_endpoint=endpoint, verify=False)
            be.close()
        assert [w for w in caught if "verify" in str(w.message).lower()] == []

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://lunacode:9010/v1",
            "https://example.com/v1",
            "https://192.168.1.50:9010/v1",
        ],
    )
    def test_warns_for_non_loopback_with_verification_off(self, endpoint):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            be = HTTPModelBackend(root_endpoint=endpoint, verify=False)
            be.close()
        messages = [str(w.message) for w in caught]
        assert any("verif" in m.lower() for m in messages), messages

    def test_no_warning_when_verification_is_on(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            be = HTTPModelBackend(root_endpoint="https://example.com/v1", verify=True)
            be.close()
        assert [w for w in caught if "verif" in str(w.message).lower()] == []

    @pytest.mark.parametrize("endpoint", ["http://lunacode:9010", "http://192.168.1.5:9010"])
    def test_no_warning_for_plain_http(self, endpoint):
        """Plain http has no certificate to verify, so there is nothing to warn about."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            be = HTTPModelBackend(root_endpoint=endpoint, verify=False)
            be.close()
        assert [w for w in caught if "verif" in str(w.message).lower()] == []
