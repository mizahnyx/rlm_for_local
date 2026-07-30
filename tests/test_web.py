"""Tests for rlm_web frontend (D2)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
