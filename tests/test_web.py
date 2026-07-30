"""Tests for rlm_web frontend (D2)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from rlm_web.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


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
        """GET / returns 200 OK — template renders without hash error."""
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
        """GET /chat returns the chat interface."""
        resp = client.get("/chat")
        assert resp.status_code == 200
        assert "Chat" in resp.text
        assert "chat-input" in resp.text

    def test_chat_context_command(self, client):
        """Slash /context returns current context (empty initially)."""
        resp = client.post("/chat/send", json={
            "session_id": "test-session",
            "message": "/context",
            "profile": "tiny",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "stream_id" in data

    def test_chat_clear_then_context(self, client):
        """After /clear, /context shows empty."""
        client.post("/chat/send", json={
            "session_id": "test-clear",
            "message": "hello",
            "profile": "tiny",
        })
        client.post("/chat/clear", json={"session_id": "test-clear"})
        resp = client.post("/chat/context", json={"session_id": "test-clear"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_chars"] == 0

    def test_chat_slash_commands_return_stream_id(self, client):
        """Each slash command returns a valid stream_id."""
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
        """main() parses --host and --port from argv."""
        from rlm_web.app import main as web_main
        # Just verify it doesn't crash with valid args (don't actually start server)
        import argparse
        try:
            # Patch uvicorn.run to avoid actually starting
            import uvicorn
            _orig = uvicorn.run
            called = []
            def fake_run(app_str, **kw):
                called.append(kw)
            uvicorn.run = fake_run
            web_main(["--host", "0.0.0.0", "--port", "9999"])
            assert len(called) == 1
            assert called[0]["host"] == "0.0.0.0"
            assert called[0]["port"] == 9999
        finally:
            uvicorn.run = _orig

    def test_main_passes_ssl_args(self):
        """main() passes --ssl-keyfile and --ssl-certfile to uvicorn."""
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
class TestVault:
    def test_vault_page_not_found(self, client):
        resp = client.get("/vault/page/nonexistent.md", follow_redirects=False)
        assert resp.status_code in (404, 401)
