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


class TestVault:
    def test_vault_page_not_found(self, client):
        resp = client.get("/vault/page/nonexistent.md", follow_redirects=False)
        assert resp.status_code in (404, 401)
