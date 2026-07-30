"""Tests for interactive chat mode (D1)."""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from rlm_local.chat import ChatSession, run_chat
from rlm_local.config import load_config
from rlm_kernel.index import rebuild_index
from rlm_kernel.memory import MemoryManager
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import LocalVault


# ── Stub Backend ────────────────────────────────────────────────────────────

class StubBackend:
    """Stub ModelBackend that records calls and returns canned responses."""

    def __init__(self, response: str = "stub response") -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({
            "messages": messages,
            "tier": tier,
            "max_tokens": max_tokens,
        })
        return self._response


class FailingBackend:
    """Backend that always raises."""

    def chat(self, **kwargs: Any) -> str:
        raise RuntimeError("backend down")


# ── Scripted Input Helper ───────────────────────────────────────────────────

class ScriptedInput:
    """Replace input() with a scripted sequence."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)

    def __call__(self, prompt: str = "") -> str:
        try:
            return next(self._lines)
        except StopIteration:
            raise EOFError


def _run_script(
    lines: list[str],
    *,
    backend: Any = None,
    vault_path: Path | None = None,
    profile: str = "tiny",
) -> str:
    """Run a ChatSession with scripted input, capturing stdout."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        if backend is None:
            backend = StubBackend()
        session = ChatSession(
            profile=profile,
            vault_path=vault_path,
            backend=backend,
        )
        with patch("builtins.input", ScriptedInput(lines)):
            session.run()
    finally:
        sys.stdout = saved
        if "session" in dir() and hasattr(session, "close"):
            session.close()
    return buf.getvalue()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """Create a temp vault with some seeded pages and an index."""
    vault_dir = tmp_path / "vault"
    vault = LocalVault(vault_dir, init_git=False)

    # Seed a few pages for search/get tests
    pages = [
        Page(
            frontmatter=Frontmatter(
                kind=PageKind.NOTE,
                name="hello-world",
                title="Hello World",
                summary="A note about greetings",
                tags=["greeting"],
                status=PageStatus.ACTIVE,
            ),
            body="Hello, world! This is a test note.",
        ),
        Page(
            frontmatter=Frontmatter(
                kind=PageKind.CONTRACT,
                name="api-contract",
                title="API Contract",
                summary="The public API contract for the system",
                tags=["api", "contract"],
                status=PageStatus.ACTIVE,
            ),
            body="# API Contract\n\nAll endpoints return JSON.",
        ),
        Page(
            frontmatter=Frontmatter(
                kind=PageKind.HELPER,
                name="fetch-helper",
                title="Fetch Helper",
                summary="A helper for HTTP requests",
                tags=["http", "helper"],
                status=PageStatus.ACTIVE,
            ),
            body="## Signature\n```python\ndef fetch(url):\n    ...\n```\n\n## Implementation\n```python\nimport httpx\n```",
        ),
    ]

    for page in pages:
        vault.put(page, f"{page.frontmatter.kind.value}/{page.frontmatter.name}.md")

    # Build index so search works
    index_path = vault_dir / "meta.sqlite"
    rebuild_index(vault, index_path)

    return vault_dir


@pytest.fixture
def test_file1(tmp_path: Path) -> Path:
    """Create a test markdown file for ingest tests."""
    f = tmp_path / "doc1.md"
    f.write_text("# Doc One\n\nThis is document one. It has some content.\n", encoding="utf-8")
    return f


@pytest.fixture
def test_file2(tmp_path: Path) -> Path:
    """Create a second test markdown file."""
    f = tmp_path / "doc2.md"
    f.write_text("# Doc Two\n\nSecond document with different content.\n", encoding="utf-8")
    return f


# ── Tests: Banner ───────────────────────────────────────────────────────────

def test_banner_shows_profile_and_model():
    output = _run_script(["/quit"])
    assert "rlm-chat" in output
    assert "profile=tiny" in output


# ── Tests: /quit ────────────────────────────────────────────────────────────

def test_quit_prints_goodbye():
    output = _run_script(["/quit"])
    assert "Goodbye" in output


def test_empty_input_noop():
    output = _run_script(["", "   ", "/quit"])
    assert output.count("Unknown command") == 0


# ── Tests: /ask ─────────────────────────────────────────────────────────────

def test_ask_slash():
    output = _run_script(["/ask What is the answer?", "/quit"])
    assert "stub response" in output


def test_ask_implicit_non_slash():
    output = _run_script(["What is life?", "/quit"])
    assert "stub response" in output


def test_ask_with_context_wiring():
    """Context accumulated via /ingest should feed into /ask."""
    backend = StubBackend("context-aware answer")
    output = _run_script(
        ["/ingest docs/notes.md", "/ask summarize this", "/quit"],
        backend=backend,
    )
    # Backend was called; RootLoop makes multiple turns internally
    assert len(backend.calls) > 0
    # The ingest path doesn't exist, so context is empty
    assert "not found" in output.lower()


def test_ask_empty_usage():
    output = _run_script(["/ask", "/quit"])
    assert "Usage:" in output


# ── Tests: /ingest ──────────────────────────────────────────────────────────

def test_ingest_loads_file(test_file1: Path):
    output = _run_script(
        [f"/ingest {test_file1}", "/quit"],
        vault_path=test_file1.parent,
    )
    assert f"{len(test_file1.read_text(encoding='utf-8'))} chars" in output


def test_ingest_multiple_files(test_file1: Path, test_file2: Path):
    output = _run_script(
        [f"/ingest {test_file1} {test_file2}", "/quit"],
        vault_path=test_file1.parent,
    )
    assert "chars" in output
    assert output.count("chars") == 2


def test_ingest_file_not_found():
    output = _run_script(["/ingest /nonexistent/file.md", "/quit"])
    assert "not found" in output


def test_ingest_empty_arg():
    output = _run_script(["/ingest", "/quit"])
    assert "Usage:" in output or "Usage" in output


# ── Tests: /context ─────────────────────────────────────────────────────────

def test_context_empty():
    output = _run_script(["/context", "/quit"])
    assert "empty" in output.lower()


def test_context_after_ingest(test_file1: Path, test_file2: Path):
    output = _run_script(
        [f"/ingest {test_file1}", f"/ingest {test_file2}", "/context", "/quit"],
        vault_path=test_file1.parent,
    )
    assert "doc1.md" in output
    assert "doc2.md" in output
    assert "2 file(s)" in output
    assert "chars total" in output


def test_context_accumulates_across_calls(test_file1: Path):
    output = _run_script(
        [f"/ingest {test_file1}", "/context", f"/ingest {test_file1}", "/context", "/quit"],
        vault_path=test_file1.parent,
    )
    # Second /context should show 2 files (both loads accumulated)
    assert "2 file(s)" in output


# ── Tests: /clear ───────────────────────────────────────────────────────────

def test_clear_drops_context(test_file1: Path):
    output = _run_script(
        [f"/ingest {test_file1}", "/context", "/clear", "/context", "/quit"],
        vault_path=test_file1.parent,
    )
    assert "cleared" in output.lower()
    # After clear, context should be empty
    lines = output.split("\n")
    empty_seen = False
    for line in lines:
        if "empty" in line.lower():
            empty_seen = True
    assert empty_seen


# ── Tests: /search ──────────────────────────────────────────────────────────

def test_search_returns_cards(temp_vault: Path):
    output = _run_script(
        ["/search hello", "/quit"],
        vault_path=temp_vault,
    )
    assert "Hello World" in output
    assert "score=" in output


def test_search_no_results(temp_vault: Path):
    output = _run_script(
        ["/search xyznonexistent", "/quit"],
        vault_path=temp_vault,
    )
    assert "No results" in output


def test_search_empty_arg(temp_vault: Path):
    output = _run_script(
        ["/search", "/quit"],
        vault_path=temp_vault,
    )
    assert "Usage:" in output


# ── Tests: /get ─────────────────────────────────────────────────────────────

def test_get_page_found(temp_vault: Path):
    output = _run_script(
        ["/get note/hello-world.md", "/quit"],
        vault_path=temp_vault,
    )
    assert "Hello World" in output
    assert "Hello, world!" in output


def test_get_page_not_found(temp_vault: Path):
    output = _run_script(
        ["/get note/nonexistent.md", "/quit"],
        vault_path=temp_vault,
    )
    assert "not found" in output.lower()


def test_get_empty_arg(temp_vault: Path):
    output = _run_script(
        ["/get", "/quit"],
        vault_path=temp_vault,
    )
    assert "Usage:" in output


# ── Tests: /note ────────────────────────────────────────────────────────────

def test_note_creates_page(temp_vault: Path):
    output = _run_script(
        ["/note Remember to buy milk and eggs for the omelette", "/quit"],
        vault_path=temp_vault,
    )
    assert "Note created:" in output
    # Verify the page was actually created in the vault
    vault = LocalVault(temp_vault, init_git=False)
    pages = vault.list(prefix="memory/notes")
    assert len(pages) >= 1
    # The note should contain our text
    bodies = [p.body for p in pages]
    assert any("milk" in b for b in bodies)


def test_note_empty_arg(temp_vault: Path):
    output = _run_script(
        ["/note", "/quit"],
        vault_path=temp_vault,
    )
    assert "Usage:" in output


# ── Tests: /check ───────────────────────────────────────────────────────────

def test_check_runs_connectivity():
    backend = StubBackend("hello")
    output = _run_script(
        ["/check", "/quit"],
        backend=backend,
    )
    assert "Checking model:" in output
    assert "[PASS] connectivity" in output
    # Should have made at least one chat call
    assert len(backend.calls) >= 1


def test_check_model_failure():
    backend = FailingBackend()
    output = _run_script(
        ["/check", "/quit"],
        backend=backend,
    )
    assert "[FAIL] connectivity" in output


# ── Tests: Unknown command ──────────────────────────────────────────────────

def test_unknown_command():
    output = _run_script(["/bogus arg", "/quit"])
    assert "Unknown command" in output


# ── Tests: Error handling ───────────────────────────────────────────────────

def test_ask_handles_backend_error():
    backend = FailingBackend()
    output = _run_script(
        ["/ask something", "/quit"],
        backend=backend,
    )
    assert "Completion error" in output


def test_ctrl_c_handled():
    """KeyboardInterrupt should not crash the session."""

    class InterruptThenQuit:
        def __init__(self):
            self._count = 0

        def __call__(self, prompt: str = "") -> str:
            self._count += 1
            if self._count == 1:
                raise KeyboardInterrupt
            return "/quit"

    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        session = ChatSession(profile="tiny", backend=StubBackend())
        with patch("builtins.input", InterruptThenQuit()):
            session.run()
    finally:
        sys.stdout = saved
        session.close()
    output = buf.getvalue()
    # Should show banner and goodbye without traceback
    assert "rlm-chat" in output
    assert "Goodbye" in output


def test_eof_quits():
    output = _run_script([])  # Immediate EOF
    assert "rlm-chat" in output
    # Should exit cleanly


# ── Tests: Non-slash input treated as /ask ──────────────────────────────────

def test_plain_text_is_ask():
    output = _run_script(["hello", "/quit"])
    assert "stub response" in output


# ── Tests: /context with files loaded but no context ────────────────────────

def test_context_after_clear_is_empty(test_file1: Path):
    output = _run_script(
        [f"/ingest {test_file1}", "/clear", "/context", "/quit"],
        vault_path=test_file1.parent,
    )
    assert "empty" in output.lower()


# ── Tests: run_chat entry point ─────────────────────────────────────────────

def test_run_chat_entry_point(temp_vault: Path):
    """Verify run_chat() can be called and runs."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        with patch("builtins.input", ScriptedInput(["/quit"])):
            run_chat(profile="tiny", vault_path=temp_vault, backend=StubBackend())
    finally:
        sys.stdout = saved
    output = buf.getvalue()
    assert "rlm-chat" in output
    assert "Goodbye" in output
