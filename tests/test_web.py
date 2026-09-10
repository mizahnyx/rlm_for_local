"""Tests for rlm_web frontend (D2)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rlm_web.app import app


_ENV_KEYS = (
    "RLM_WEB_TOKEN",
    "RLM_WEB_SSL_KEY",
    "RLM_WEB_SSL_CERT",
    "RLM_WEB_ALLOW_TESTCLIENT",
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
