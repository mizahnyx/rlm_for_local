"""Tests for rlm_web frontend (D2)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rlm_web.app import (
    _normalize_origin,
    _origin_allowed,
    app,
)


_ENV_KEYS = (
    "RLM_WEB_TOKEN",
    "RLM_WEB_SECRET",
    "RLM_WEB_SSL_KEY",
    "RLM_WEB_SSL_CERT",
    "RLM_WEB_ALLOW_TESTCLIENT",
    "RLM_WEB_ORIGIN_CHECK",
    "RLM_WEB_ALLOWED_ORIGINS",
)


@pytest.fixture(autouse=True)
def _web_state():
    """Isolate every test from process-global web state (S5/R21).

    * The web app's dev-client allowance is explicit: Starlette's TestClient
      connects from the synthetic host "testclient", which is *not* loopback,
      so without the opt-in every request would be rejected once no
      `RLM_WEB_TOKEN` is configured (`TestAuthFailClosed` unsets it to prove
      the fail-closed default).
    * `main()` re-installs SessionMiddleware with `https_only=True` when SSL
      args are present. That is deliberate process state; tests must not leak
      it into each other, so the middleware stack and the relevant env vars are
      snapshotted and restored.
    """
    saved_middleware = list(app.user_middleware)
    saved_stack = app.middleware_stack
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}

    os.environ["RLM_WEB_ALLOW_TESTCLIENT"] = "1"
    yield

    app.user_middleware = saved_middleware
    app.middleware_stack = saved_stack
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestStatic:
    def test_static_dir_is_mounted_and_empty_by_default(self, client):
        """The static mount exists but no third-party JS is vendored.

        htmx was loaded on every page with zero `hx-` attributes in the
        templates; the job and chat pages use vanilla EventSource (R16).
        """
        resp = client.get("/static/htmx.min.js")
        assert resp.status_code == 404

    def test_no_vendored_htmx_file(self):
        static = Path(__file__).parent.parent / "src" / "rlm_web" / "static"
        assert not (static / "htmx.min.js").exists()

    def test_base_template_loads_no_scripts(self):
        base = Path(__file__).parent.parent / "src" / "rlm_web" / "templates" / "base.html"
        assert "<script" not in base.read_text(encoding="utf-8")


class TestAuth:
    def test_login_with_correct_token(self, client):
        os.environ["RLM_WEB_TOKEN"] = "correct-horse"
        try:
            resp = client.post("/login", data={"token": "correct-horse"},
                              follow_redirects=False)
            assert resp.status_code == 303
        finally:
            del os.environ["RLM_WEB_TOKEN"]

    def test_login_with_wrong_token(self, client):
        os.environ["RLM_WEB_TOKEN"] = "correct-horse"
        try:
            resp = client.post("/login", data={"token": "wrong-token"})
            assert resp.status_code == 401
        finally:
            del os.environ["RLM_WEB_TOKEN"]


class TestConsole:
    def test_console_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Console" in resp.text


class TestJobs:
    def test_job_creation_redirects(self, client):
        resp = client.post("/jobs", data={
            "query": "What is blue?",
            "context": "The sky is blue.",
            "profile": "tiny",
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert "/jobs/" in resp.headers["location"]


class TestChat:
    def test_chat_page_renders(self, client):
        resp = client.get("/chat")
        assert resp.status_code == 200
        assert "Chat" in resp.text
        assert "chat-input" in resp.text

    def test_chat_context_command(self, client):
        resp = client.post("/chat/send", json={
            "session_id": "test-session",
            "message": "/context",
            "profile": "tiny",
        })
        assert resp.status_code == 200
        assert "stream_id" in resp.json()

    def test_chat_clear_then_context(self, client):
        client.post("/chat/send", json={
            "session_id": "test-clear",
            "message": "hello",
            "profile": "tiny",
        })
        client.post("/chat/clear", json={"session_id": "test-clear"})
        resp = client.post("/chat/context", json={"session_id": "test-clear"})
        data = resp.json()
        assert data["total_chars"] == 0

    def test_chat_slash_commands_return_stream_id(self, client):
        for cmd in ["/context", "/clear", "/quit"]:
            resp = client.post("/chat/send", json={
                "session_id": "test-cmds",
                "message": cmd,
                "profile": "tiny",
            })
            assert resp.status_code == 200
            assert "stream_id" in resp.json()


class TestStartupArgs:
    def test_main_parses_host_port(self):
        from rlm_web.app import main as web_main
        import uvicorn
        _orig = uvicorn.run
        called = []
        def fake_run(app_str, **kw):
            called.append(kw)
        uvicorn.run = fake_run
        try:
            web_main(["--host", "0.0.0.0", "--port", "9999"])
            assert called[0]["host"] == "0.0.0.0"
            assert called[0]["port"] == 9999
        finally:
            uvicorn.run = _orig

    def test_main_passes_ssl_args(self):
        from rlm_web.app import main as web_main
        import uvicorn
        _orig = uvicorn.run
        called = []
        def fake_run(app_str, **kw):
            called.append(kw)
        uvicorn.run = fake_run
        try:
            web_main(["--ssl-keyfile", "key.pem", "--ssl-certfile", "cert.pem"])
            assert called[0].get("ssl_keyfile") == "key.pem"
            assert called[0].get("ssl_certfile") == "cert.pem"
        finally:
            uvicorn.run = _orig

    def test_ssl_args_make_the_session_cookie_secure(self):
        """S5/R21 — the session cookie must be Secure when TLS is served."""
        import uvicorn

        from rlm_web.app import _session_https_only, main as web_main

        assert _session_https_only() is False
        _orig = uvicorn.run
        uvicorn.run = lambda *a, **kw: None
        try:
            web_main(["--ssl-keyfile", "key.pem", "--ssl-certfile", "cert.pem"])
        finally:
            uvicorn.run = _orig
        assert _session_https_only() is True

        os.environ["RLM_WEB_TOKEN"] = "t"
        with TestClient(app, base_url="https://testserver") as c:
            resp = c.post("/login", data={"token": "t"}, follow_redirects=False)
            assert resp.status_code == 303
            cookie = resp.headers["set-cookie"].lower()
            assert "secure" in cookie, cookie


class TestVault:
    def test_vault_page_not_found(self, client):
        resp = client.get("/vault/page/nonexistent.md", follow_redirects=False)
        assert resp.status_code in (404, 401)


class TestVaultIngest:
    """POST /vault/ingest — permanent vault ingestion from web uploads."""

    @pytest.fixture
    def seeded_client(self):
        """Client with a temp seeded vault."""
        td = tempfile.TemporaryDirectory(prefix="web_ingest_", ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        from rlm_kernel.vault import LocalVault
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.index import rebuild_index
        vault = LocalVault(vault_path, init_git=False)
        seed_vault(vault)
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")
        os.environ["RLM_VAULT_ROOT"] = str(vault_path)
        with TestClient(app) as c:
            yield c, vault_path
        del os.environ["RLM_VAULT_ROOT"]
        td.cleanup()

    def test_ingest_creates_page(self, seeded_client):
        """Uploading a .md file creates a vault page."""
        client, vault_path = seeded_client
        test_content = b"# Test Document\n\nThis is a test for vault ingestion."
        resp = client.post("/vault/ingest", files={
            "files": ("test-doc.md", test_content, "text/markdown"),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] >= 1

    def test_ingest_detects_duplicate(self, seeded_client):
        """Re-uploading same content reports it as duplicate."""
        client, vault_path = seeded_client
        test_content = b"# Unique\n\nUnique content here."
        r1 = client.post("/vault/ingest", files={
            "files": ("a.md", test_content, "text/markdown"),
        })
        assert r1.json()["ingested"] == 1
        r2 = client.post("/vault/ingest", files={
            "files": ("b.md", test_content, "text/markdown"),
        })
        assert r2.json()["skipped"] >= 1

    def test_ingest_empty_upload(self, seeded_client):
        """Uploading no files returns 400."""
        client, vault_path = seeded_client
        resp = client.post("/vault/ingest")
        assert resp.status_code == 400


class TestPathTraversal:
    """P1: docs route must not escape docs/ directory."""

    def test_traversal_to_pyproject_rejected(self, client):
        """../.. traversal returns 404, not the file content."""
        resp = client.get("/docs/..%2F..%2Fpyproject.toml")
        assert resp.status_code == 404

    def test_traversal_to_windows_dir_rejected(self, client):
        """Deep traversal to system dir returns 404."""
        resp = client.get("/docs/..%2F..%2F..%2FWindows/win.ini")
        assert resp.status_code == 404

    def test_legitimate_doc_still_serves(self, client):
        """A real doc path still works."""
        resp = client.get("/docs/operator-guide.md")
        assert resp.status_code in (200, 401, 404)  # 404 if file DNE, 401 if auth, 200 if ok


class TestUploadCaps:
    """P2: file uploads must enforce count and size limits."""

    def test_too_many_files_rejected(self, client):
        """21 files rejected (cap is 20)."""
        files = [("files", (f"file{i}.md", io.BytesIO(b"# Test"), "text/markdown"))
                 for i in range(21)]
        resp = client.post("/vault/ingest", files=files)
        assert resp.status_code in (400, 413)

    def test_file_too_large_rejected(self, client):
        """A single large file rejected."""
        big = b"x" * (9 * 1024 * 1024)  # 9 MB
        resp = client.post("/vault/ingest", files={
            "files": ("big.md", big, "text/markdown"),
        })
        assert resp.status_code in (400, 413)


class TestAuthPolicy:
    """P3: auth must be fail-closed for non-local requests."""

    def test_localhost_allowed_without_token(self, client):
        """Loopback request works without RLM_WEB_TOKEN set."""
        old = os.environ.pop("RLM_WEB_TOKEN", None)
        try:
            resp = client.get("/")
            assert resp.status_code == 200
        finally:
            if old:
                os.environ["RLM_WEB_TOKEN"] = old


class TestPromptRegressionGuard:
    """P4: contract pages must contain the answer-dict submission mechanism."""

    def test_how_to_work_contains_answer_ready(self):
        """contract/how-to-work.md must explicitly mention answer['ready']."""
        from pathlib import Path
        from rlm_kernel.vault import LocalVault
        vault = LocalVault(
            Path.home() / ".local/share/rlm-kernel/vault", init_git=False,
        )
        page = vault.get("contract/how-to-work.md")
        assert page is not None, "how-to-work.md not found in vault"
        body = page.body
        assert "ready" in body.lower(), (
            "how-to-work.md does not mention answer-ready mechanism"
        )
        assert "answer" in body.lower(), (
            "how-to-work.md does not mention answer dict"
        )

    def test_repl_contract_contains_answer_ready(self):
        """contract/repl-contract.md must mention answer['ready']."""
        from pathlib import Path
        from rlm_kernel.vault import LocalVault
        vault = LocalVault(
            Path.home() / ".local/share/rlm-kernel/vault", init_git=False,
        )
        page = vault.get("contract/repl-contract.md")
        assert page is not None, "repl-contract.md not found in vault"
        body = page.body
        assert "ready" in body.lower(), (
            "repl-contract.md does not mention answer-ready mechanism"
        )


import io
from io import BytesIO


# ── S5 / R21 — auth completeness ───────────────────────────────────────────

class TestSSEAuth:
    """Both SSE endpoints used to skip `_check_auth` entirely, so job answers
    and chat responses were readable by anyone who could reach the port."""

    def test_job_events_requires_auth(self, client):
        os.environ["RLM_WEB_TOKEN"] = "sekrit"
        try:
            resp = client.get("/jobs/does-not-matter/events")
            assert resp.status_code == 401, resp.status_code
        finally:
            del os.environ["RLM_WEB_TOKEN"]

    def test_chat_events_requires_auth(self, client):
        os.environ["RLM_WEB_TOKEN"] = "sekrit"
        try:
            resp = client.get("/chat/events/whatever")
            assert resp.status_code == 401, resp.status_code
        finally:
            del os.environ["RLM_WEB_TOKEN"]

    def test_job_events_allowed_after_login(self):
        os.environ["RLM_WEB_TOKEN"] = "sekrit"
        try:
            with TestClient(app) as c:
                assert c.post("/login", data={"token": "sekrit"}).status_code in (200, 303)
                resp = c.get("/jobs/nope/events")
                # Authenticated: the auth gate passes. The stream itself is
                # empty for an unknown job, which is a different concern.
                assert resp.status_code == 200, resp.status_code
        finally:
            del os.environ["RLM_WEB_TOKEN"]

    def test_chat_events_allowed_after_login(self, monkeypatch):
        import rlm_web.app as webapp

        # An unknown stream id is reported after a bounded wait rather than
        # holding the request open forever.
        monkeypatch.setattr(webapp, "CHAT_STREAM_WAIT_SECONDS", 0.4)
        os.environ["RLM_WEB_TOKEN"] = "sekrit"
        try:
            with TestClient(app) as c:
                assert c.post("/login", data={"token": "sekrit"}).status_code in (200, 303)
                resp = c.get("/chat/events/nope")
                assert resp.status_code == 200, resp.status_code
                assert "unknown stream id" in resp.text
        finally:
            del os.environ["RLM_WEB_TOKEN"]


class TestAuthCoverageByConstruction:
    """R21 — auth is a route dependency, so a new route cannot forget it."""

    PUBLIC_ALLOWLIST = {
        "/login",
        "/logout",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }

    def test_every_api_route_declares_auth(self):
        from fastapi.routing import APIRoute

        from rlm_web.app import require_auth

        missing = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path in self.PUBLIC_ALLOWLIST or route.path.startswith("/static"):
                continue
            calls = [d.call for d in route.dependant.dependencies]
            if require_auth not in calls:
                missing.append(route.path)
        assert missing == [], f"routes without the auth dependency: {missing}"

    def test_the_guard_actually_finds_unprotected_routes(self):
        """Non-vacuity: the checker would notice a route that skipped auth."""
        from fastapi import Depends, FastAPI
        from fastapi.routing import APIRoute

        from rlm_web.app import require_auth

        probe = FastAPI()

        @probe.get("/protected", dependencies=[Depends(require_auth)])
        async def _a():  # pragma: no cover - not called
            return {}

        @probe.get("/unprotected")
        async def _b():  # pragma: no cover - not called
            return {}

        def unauth_paths(application):
            out = []
            for route in application.routes:
                if not isinstance(route, APIRoute):
                    continue
                calls = [d.call for d in route.dependant.dependencies]
                if require_auth not in calls:
                    out.append(route.path)
            return out

        assert unauth_paths(probe) == ["/unprotected"]


class TestAuthFailClosed:
    """S5/R21 — no implicit trust for a test client or a missing peer address."""

    def test_no_token_remote_client_is_rejected(self):
        os.environ.pop("RLM_WEB_TOKEN", None)
        with TestClient(app, client=("10.0.0.9", 1234)) as remote:
            assert remote.get("/").status_code == 401

    def test_no_token_loopback_client_is_allowed(self):
        os.environ.pop("RLM_WEB_TOKEN", None)
        with TestClient(app, client=("127.0.0.1", 1234)) as local:
            assert local.get("/").status_code == 200

    def test_testclient_host_is_rejected_without_the_env_optin(self):
        os.environ.pop("RLM_WEB_TOKEN", None)
        old = os.environ.pop("RLM_WEB_ALLOW_TESTCLIENT", None)
        try:
            with TestClient(app) as c:
                assert c.get("/").status_code == 401
        finally:
            if old is not None:
                os.environ["RLM_WEB_ALLOW_TESTCLIENT"] = old
            os.environ["RLM_WEB_ALLOW_TESTCLIENT"] = "1"

    def test_authorize_helper_table(self):
        from rlm_web.app import _authorize

        # No token: loopback only, and only with the explicit opt-in for a
        # missing peer address.
        assert _authorize(client_host="127.0.0.1", session_authenticated=False,
                          token="", allow_testclient=False) is True
        assert _authorize(client_host="::1", session_authenticated=False,
                          token="", allow_testclient=False) is True
        assert _authorize(client_host="localhost", session_authenticated=False,
                          token="", allow_testclient=False) is True
        assert _authorize(client_host="10.0.0.9", session_authenticated=False,
                          token="", allow_testclient=False) is False
        assert _authorize(client_host=None, session_authenticated=False,
                          token="", allow_testclient=False) is False
        assert _authorize(client_host=None, session_authenticated=False,
                          token="", allow_testclient=True) is True
        assert _authorize(client_host="testclient", session_authenticated=False,
                          token="", allow_testclient=False) is False
        assert _authorize(client_host="testclient", session_authenticated=False,
                          token="", allow_testclient=True) is True
        # Token configured: only an authenticated session passes, and a remote
        # client cannot be saved by the dev opt-in.
        assert _authorize(client_host="10.0.0.9", session_authenticated=True,
                          token="t", allow_testclient=False) is True
        assert _authorize(client_host="127.0.0.1", session_authenticated=False,
                          token="t", allow_testclient=False) is False
        assert _authorize(client_host="testclient", session_authenticated=False,
                          token="t", allow_testclient=True) is False


class TestLoginPolicy:
    def test_login_without_configured_token_is_400(self, client):
        """A login form with no token to check against is a misconfiguration."""
        old = os.environ.pop("RLM_WEB_TOKEN", None)
        try:
            resp = client.post("/login", data={"token": "anything"})
            assert resp.status_code == 400, resp.status_code
        finally:
            if old is not None:
                os.environ["RLM_WEB_TOKEN"] = old

    def test_login_uses_constant_time_comparison(self):
        """Token comparison must not leak length/prefix through timing."""
        import inspect

        import rlm_web.app as webapp

        src = inspect.getsource(webapp)
        assert "compare_digest" in src


class TestSessionSecret:
    """S5 follow-up — the session-signing secret must never be a published value.

    The middleware used to fall back to the hardcoded string
    `rlm-web-dev-secret-change-in-production`, which is in the repository. With
    `RLM_WEB_TOKEN` set (auth on) but `RLM_WEB_SECRET` unset, anyone who could
    reach the port could forge `{"authenticated": true}` and bypass the token:

        no cookie                                        -> 401
        cookie forged with the published default secret  -> 200
        same forgery with a different secret             -> 401

    The first two lines are the bug. `_forge_session` below reproduces exactly
    what Starlette's SessionMiddleware signs, so these tests fail if the
    published constant ever comes back.
    """

    LEAKED_DEFAULT = "rlm-web-dev-secret-change-in-production"

    @staticmethod
    def _forge_session(secret: str) -> str:
        import base64
        import json

        import itsdangerous

        signer = itsdangerous.TimestampSigner(secret)
        payload = base64.b64encode(json.dumps({"authenticated": True}).encode("utf-8"))
        return signer.sign(payload).decode("utf-8")

    @staticmethod
    def _start_app(token: str | None, secret: str | None) -> None:
        """Resolve the env and (re)install the session middleware.

        The signing secret is fixed when the middleware is installed — i.e. at
        process start — so a test that changes `RLM_WEB_SECRET` must reinstall
        it, exactly as `main()` does before uvicorn binds. The module's autouse
        `_web_state` fixture restores the previous stack and env afterwards.
        """
        import rlm_web.app as webapp

        for key, value in (("RLM_WEB_TOKEN", token), ("RLM_WEB_SECRET", secret)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        webapp._install_session_middleware(https_only=False)

    def test_published_default_secret_no_longer_authenticates(self):
        self._start_app(token="the-real-token", secret=None)
        with TestClient(app) as c:
            assert c.get("/").status_code == 401
            c.cookies.set("session", self._forge_session(self.LEAKED_DEFAULT))
            resp = c.get("/")
            assert resp.status_code == 401, (
                "a cookie forged with the published default secret was accepted"
            )

    def test_configured_secret_still_works_and_others_do_not(self):
        self._start_app(token="the-real-token", secret="operator-chosen-secret")

        with TestClient(app) as c:
            c.cookies.set("session", self._forge_session("operator-chosen-secret"))
            assert c.get("/").status_code == 200, (
                "a cookie signed with the configured secret must authenticate"
            )

        with TestClient(app) as c:
            c.cookies.set("session", self._forge_session(self.LEAKED_DEFAULT))
            assert c.get("/").status_code == 401

    def test_unset_secret_generates_an_unguessable_one(self):
        from rlm_web.app import _session_secret

        os.environ.pop("RLM_WEB_SECRET", None)
        first = _session_secret()
        assert first and first != self.LEAKED_DEFAULT
        assert len(first) >= 32, "an ephemeral secret must be long enough to matter"

    def test_no_hardcoded_secret_literal_remains_in_the_source(self):
        """The constant must not appear as a *string value* anywhere.

        Quoted form on purpose: prose explaining what the old default was is
        fine, using it as a value is not.
        """
        src = Path(__file__).parent.parent / "src" / "rlm_web" / "app.py"
        text = src.read_text(encoding="utf-8")
        assert f'"{self.LEAKED_DEFAULT}"' not in text
        assert f"'{self.LEAKED_DEFAULT}'" not in text

    def test_main_refuses_to_start_with_a_token_but_no_secret(self):
        """Fail closed: an exposed console must not sign sessions with a secret
        the operator did not choose."""
        import uvicorn

        import rlm_web.app as webapp

        os.environ["RLM_WEB_TOKEN"] = "the-real-token"
        os.environ.pop("RLM_WEB_SECRET", None)
        started: list[dict] = []
        _orig = uvicorn.run
        uvicorn.run = lambda *a, **kw: started.append(kw)
        try:
            with pytest.raises(SystemExit) as exc:
                webapp.main([])
            assert exc.value.code not in (0, None), "must exit non-zero"
            assert started == [], "uvicorn must not be started"
        finally:
            uvicorn.run = _orig
            del os.environ["RLM_WEB_TOKEN"]

    def test_main_starts_when_token_and_secret_are_both_set(self):
        import uvicorn

        import rlm_web.app as webapp

        os.environ["RLM_WEB_TOKEN"] = "the-real-token"
        os.environ["RLM_WEB_SECRET"] = "operator-chosen-secret"
        started: list[dict] = []
        _orig = uvicorn.run
        uvicorn.run = lambda *a, **kw: started.append(kw)
        try:
            assert webapp.main([]) in (0, None)
            assert started, "uvicorn should have been started"
        finally:
            uvicorn.run = _orig
            del os.environ["RLM_WEB_TOKEN"]
            del os.environ["RLM_WEB_SECRET"]

    def test_main_starts_in_loopback_mode_without_either(self):
        """No token means loopback-only mode, which needs no login — and no
        secret to sign it with."""
        import uvicorn

        import rlm_web.app as webapp

        os.environ.pop("RLM_WEB_TOKEN", None)
        os.environ.pop("RLM_WEB_SECRET", None)
        started: list[dict] = []
        _orig = uvicorn.run
        uvicorn.run = lambda *a, **kw: started.append(kw)
        try:
            assert webapp.main([]) in (0, None)
            assert started
        finally:
            uvicorn.run = _orig


class TestVaultTemplateEscaping:
    """S5 — the ingest result was built with innerHTML from server data."""

    def test_vault_template_does_not_use_innerhtml(self):
        tpl = Path(__file__).parent.parent / "src" / "rlm_web" / "templates" / "vault.html"
        text = tpl.read_text(encoding="utf-8")
        assert ".innerHTML" not in text, (
            "server-echoed filenames must not be injected as HTML"
        )
        assert "insertAdjacentHTML" not in text
        assert "textContent" in text


class TestCheckPageRemoved:
    """R16/R21 — check.html posts to a route that does not exist (405)."""

    def test_check_html_is_gone(self):
        tpl = Path(__file__).parent.parent / "src" / "rlm_web" / "templates" / "check.html"
        assert not tpl.exists(), "dead UI should be deleted or implemented"

    def test_get_check_is_not_a_route(self, client):
        assert client.get("/check").status_code == 404


class TestBoundedStores:
    """R22 — jobs and chat sessions were unbounded in-memory dictionaries."""

    def test_lru_evicts_oldest(self):
        from rlm_web.app import _BoundedLRU

        store = _BoundedLRU(maxlen=3)
        for i in range(5):
            store[f"k{i}"] = i
        assert len(store) == 3
        assert "k0" not in store and "k1" not in store
        assert list(store) == ["k2", "k3", "k4"]

    def test_lru_keeps_recently_used_entries(self):
        from rlm_web.app import _BoundedLRU

        store = _BoundedLRU(maxlen=3)
        for i in range(3):
            store[f"k{i}"] = i
        _ = store.get("k0")  # touch
        store["k3"] = 3
        assert "k0" in store, "a touched entry must survive eviction"
        assert "k1" not in store

    def test_job_and_chat_stores_are_bounded(self):
        from rlm_web.app import _chat_sessions, _jobs, MAX_STORED_ENTRIES

        assert _jobs._maxlen == MAX_STORED_ENTRIES
        assert _chat_sessions._maxlen == MAX_STORED_ENTRIES
        assert MAX_STORED_ENTRIES == 100


# ── Cross-origin (CSRF) protection ─────────────────────────────────────────
#
# Open item in the 2026-09-10 validation §8.1: the console authenticated with a
# session cookie and relied on `SameSite=Lax` alone, so nothing stopped a page
# the operator visited from POSTing to a console they were logged into.

_HOME = "http://testserver"  # what TestClient uses as base_url
_EVIL = "https://evil.example"

_JOB_FORM = {"query": "what is 2+2", "context": "2+2=4"}


class TestOriginNormalization:
    @pytest.mark.parametrize(
        "value",
        [
            "http://localhost:8778",
            "http://127.0.0.1:8778",
            "http://[::1]:8778",
            "http://LOCALHOST:8778",
            "http://localhost:8778/",
        ],
    )
    def test_loopback_aliases_are_one_origin(self, value):
        """The console is reached as both `localhost` and `127.0.0.1`.

        Treating those as different origins would lock the operator out of their
        own console, which is how a CSRF fix usually gets reverted.
        """
        assert _normalize_origin(value) == "http://loopback:8778"

    def test_default_ports_are_made_explicit(self):
        assert _normalize_origin("https://console.example") == "https://console.example:443"
        assert _normalize_origin("http://console.example") == "http://console.example:80"

    def test_the_scheme_is_part_of_the_origin(self):
        assert _normalize_origin("https://console.example") != _normalize_origin(
            "http://console.example"
        )

    def test_a_path_and_query_are_ignored(self):
        assert _normalize_origin("https://evil.example/page?q=1") == "https://evil.example:443"

    @pytest.mark.parametrize(
        "value",
        ["", "null", "not a url", "console.example", "/relative", "ftp://x.example"],
    )
    def test_unusable_values_normalize_to_empty(self, value):
        assert _normalize_origin(value) == ""

    def test_unknown_mode_falls_back_to_the_default_not_to_off(self, monkeypatch):
        """A typo must not silently disable the check."""
        import rlm_web.app as webapp

        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "on")
        assert webapp._origin_check_mode() == "same-origin"

    def test_mode_is_case_insensitive(self, monkeypatch):
        import rlm_web.app as webapp

        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "STRICT")
        assert webapp._origin_check_mode() == "strict"


class TestOriginDecisionTable:
    """`_origin_allowed` is pure, so the whole policy is one table."""

    OWN = f"{_HOME}:80"

    @pytest.mark.parametrize(
        "supplied,mode,allowlist,expected",
        [
            # same-origin (default): a claimed origin must be this server's.
            (None, "same-origin", frozenset(), True),
            (OWN, "same-origin", frozenset(), True),
            (_EVIL, "same-origin", frozenset(), False),
            # a claim that cannot be parsed (Origin: null) always fails closed
            ("", "same-origin", frozenset(), False),
            # strict: an origin must be claimed and match
            (None, "strict", frozenset(), False),
            (_EVIL, "strict", frozenset(), False),
            # off: documented escape hatch
            (None, "off", frozenset(), True),
            (_EVIL, "off", frozenset(), True),
            ("", "off", frozenset(), True),
            # allowlist replaces the same-origin comparison, and does not
            # implicitly include this server (DNS-rebinding-safe)
            (_EVIL, "same-origin", frozenset({_EVIL}), True),
            (OWN, "same-origin", frozenset({_EVIL}), False),
        ],
    )
    def test_policy(self, supplied, mode, allowlist, expected):
        allowed, _reason = _origin_allowed(
            supplied=supplied, mode=mode, allowlist=allowlist, own_origin=self.OWN,
        )
        assert allowed is expected

    def test_every_refusal_says_why(self):
        for supplied, mode, allowlist in [
            (_EVIL, "same-origin", frozenset()),
            (None, "strict", frozenset()),
            ("", "same-origin", frozenset()),
            (self.OWN, "same-origin", frozenset({_EVIL})),
        ]:
            allowed, reason = _origin_allowed(
                supplied=supplied, mode=mode, allowlist=allowlist,
                own_origin=self.OWN,
            )
            assert allowed is False
            assert reason, f"refusal for {supplied!r} in {mode} had no reason"


class TestCrossOriginRequests:
    def test_cross_origin_post_is_rejected_with_a_live_session(self):
        """The attack: the operator is logged in, another site causes the POST.

        A session cookie rides along on any request the browser makes, so the
        auth gate alone would have accepted this one.
        """
        os.environ["RLM_WEB_TOKEN"] = "sekrit"
        try:
            with TestClient(app) as c:
                login = c.post("/login", data={"token": "sekrit"})
                assert login.status_code in (200, 303), login.status_code
                resp = c.post("/jobs", data=_JOB_FORM, headers={"Origin": _EVIL})
                assert resp.status_code == 403, resp.status_code
                assert "Cross-origin" in resp.text
        finally:
            del os.environ["RLM_WEB_TOKEN"]

    def test_same_origin_post_still_works(self, client):
        resp = client.post(
            "/jobs", data=_JOB_FORM, headers={"Origin": _HOME},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.status_code

    def test_a_client_that_claims_no_origin_is_allowed(self, client):
        """curl, a script, or a typed URL is not a cross-site browser request."""
        resp = client.post("/jobs", data=_JOB_FORM, follow_redirects=False)
        assert resp.status_code == 303, resp.status_code

    def test_origin_null_fails_closed(self, client):
        """A sandboxed iframe or `file://` page sends `Origin: null`."""
        resp = client.post("/jobs", data=_JOB_FORM, headers={"Origin": "null"})
        assert resp.status_code == 403, resp.status_code

    def test_referer_is_checked_when_origin_is_absent(self, client):
        """Browsers that withhold `Origin` on a same-origin POST still send it."""
        evil = client.post("/jobs", data=_JOB_FORM, headers={"Referer": f"{_EVIL}/page"})
        assert evil.status_code == 403, evil.status_code
        same = client.post(
            "/jobs", data=_JOB_FORM, headers={"Referer": f"{_HOME}/"},
            follow_redirects=False,
        )
        assert same.status_code == 303, same.status_code

    def test_strict_mode_demands_an_origin(self, client, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "strict")
        assert client.post("/jobs", data=_JOB_FORM).status_code == 403
        with_origin = client.post(
            "/jobs", data=_JOB_FORM, headers={"Origin": _HOME},
            follow_redirects=False,
        )
        assert with_origin.status_code == 303, with_origin.status_code

    def test_off_disables_the_check(self, client, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "off")
        resp = client.post(
            "/jobs", data=_JOB_FORM, headers={"Origin": _EVIL},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.status_code

    def test_allowlist_admits_the_named_origin_only(self, client, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ALLOWED_ORIGINS", "https://console.example")
        admitted = client.post(
            "/jobs", data=_JOB_FORM, headers={"Origin": "https://console.example"},
            follow_redirects=False,
        )
        assert admitted.status_code == 303, admitted.status_code
        refused = client.post("/jobs", data=_JOB_FORM, headers={"Origin": _HOME})
        assert refused.status_code == 403, refused.status_code

    @pytest.mark.parametrize(
        "path,request_body",
        [
            ("/chat/send", {"json": {"message": "hi"}}),
            ("/chat/clear", {"json": {}}),
            ("/chat/context", {"json": {}}),
            ("/vault/ingest", {}),
        ],
    )
    def test_every_state_changing_endpoint_is_covered(self, client, path, request_body):
        # NB: the parameter is not called `payload` — the node id would then
        # contain "load" and `-k "not slow and not load"`, the documented fast
        # command, would silently deselect it.
        resp = client.post(path, headers={"Origin": _EVIL}, **request_body)
        assert resp.status_code == 403, f"{path} -> {resp.status_code}"

    def test_logout_is_not_a_free_for_all(self, client):
        """Logout changes state and was reachable by any cross-site `<img>`."""
        resp = client.get("/logout", headers={"Referer": f"{_EVIL}/x"})
        assert resp.status_code == 403, resp.status_code
        same_origin = client.get(
            "/logout", headers={"Origin": _HOME}, follow_redirects=False
        )
        assert same_origin.status_code == 303, same_origin.status_code

    def test_reads_are_not_origin_checked(self, client):
        """Only state-changing routes carry the check; reads carry auth."""
        assert client.get("/", headers={"Origin": _EVIL}).status_code == 200


class TestOriginCoverageByConstruction:
    """A new state-changing route must not be able to forget the CSRF check."""

    #: GET routes that change state and so are checked as well.
    STATE_CHANGING_GETS = {"/logout"}

    @classmethod
    def _unchecked(cls, application) -> list[str]:
        from fastapi.routing import APIRoute

        from rlm_web.app import require_same_origin

        missing: list[str] = []
        for route in application.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path.startswith("/static"):
                continue
            state_changing = (
                "POST" in (route.methods or set())
                or route.path in cls.STATE_CHANGING_GETS
            )
            if not state_changing:
                continue
            calls = [d.call for d in route.dependant.dependencies]
            if require_same_origin not in calls:
                missing.append(route.path)
        return missing

    def test_every_state_changing_route_is_checked(self):
        assert self._unchecked(app) == []

    def test_at_least_one_state_changing_route_exists(self):
        """Non-vacuity: an empty route table would pass the check above."""
        assert self._unchecked(app) == [] and any(
            "POST" in (getattr(r, "methods", None) or set()) for r in app.routes
        )

    def test_the_guard_actually_finds_unprotected_routes(self):
        from fastapi import Depends, FastAPI

        from rlm_web.app import require_same_origin

        probe = FastAPI()

        @probe.post("/protected", dependencies=[Depends(require_same_origin)])
        async def _a():  # pragma: no cover - not called
            return {}

        @probe.post("/unprotected")
        async def _b():  # pragma: no cover - not called
            return {}

        assert self._unchecked(probe) == ["/unprotected"]


class TestOriginConfigStartupValidation:
    """A policy that cannot mean what was typed must not start a server."""

    @staticmethod
    def _start(argv: list[str] | None = None) -> tuple[int, list[dict]]:
        import uvicorn

        import rlm_web.app as webapp

        started: list[dict] = []
        original = uvicorn.run
        uvicorn.run = lambda *a, **kw: started.append(kw)
        try:
            return webapp.main(argv or []), started
        finally:
            uvicorn.run = original

    def test_unknown_mode_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "on")
        os.environ.pop("RLM_WEB_TOKEN", None)
        with pytest.raises(SystemExit) as exc:
            self._start()
        assert exc.value.code == 2

    def test_unusable_allowlist_entry_refuses_to_start(self, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ALLOWED_ORIGINS", "console.example")
        os.environ.pop("RLM_WEB_TOKEN", None)
        with pytest.raises(SystemExit) as exc:
            self._start()
        assert exc.value.code == 2

    def test_a_valid_policy_starts(self, monkeypatch):
        monkeypatch.setenv("RLM_WEB_ORIGIN_CHECK", "strict")
        monkeypatch.setenv(
            "RLM_WEB_ALLOWED_ORIGINS",
            "https://console.example,http://127.0.0.1:8778",
        )
        os.environ.pop("RLM_WEB_TOKEN", None)
        os.environ.pop("RLM_WEB_SECRET", None)
        rc, started = self._start()
        assert rc == 0
        assert started, "uvicorn should have been started"
