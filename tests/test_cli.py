"""Tests for rlm CLI frontend (D1)."""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from rlm_local.cli import build_parser
from rlm_local.cli import main as cli_main


class TestAsk:
    def test_ask_with_context_file(self, monkeypatch):
        """rlm ask with --context-file runs completion and prints answer."""
        import rlm_local
        monkeypatch.setattr(rlm_local, "completion",
                            lambda q, c, **kw: "The answer is blue.")

        with tempfile.NamedTemporaryFile(suffix=".md", mode="w",
                                         encoding="utf-8", delete=False) as f:
            f.write("# Test\n\nThe sky is blue.")
            fpath = f.name

        try:
            rc = cli_main(["ask", "What color?", "--context-file", fpath])
            assert rc == 0
        finally:
            Path(fpath).unlink()

    def test_ask_no_context_fails(self):
        """rlm ask without context returns error code 2."""
        rc = cli_main(["ask", "What color?"])
        assert rc == 2

    def test_ask_with_stdin(self, monkeypatch):
        """rlm ask --stdin reads from stdin."""
        import rlm_local
        monkeypatch.setattr(rlm_local, "completion",
                            lambda q, c, **kw: "answer")

        saved_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("Some context from stdin.")
            rc = cli_main(["ask", "What?", "--stdin"])
            assert rc == 0
        finally:
            sys.stdin = saved_stdin


class TestRemoteEndpoint:
    """Pointing the harness at a model server that is not on localhost.

    The shipped profiles hardcode `https://localhost:9010/v1`, and `rlm ask` /
    `rlm chat` offered no override, so a router on another LAN or Tailscale host
    (e.g. a llama.cpp router that loads models on demand) was reachable only from
    Python. These tests pin the CLI path and the `RLM_ENDPOINT`/`RLM_MODEL`
    defaults.
    """

    def test_ask_passes_endpoint_and_model_to_both_tiers(self, monkeypatch, tmp_path):
        import rlm_local

        captured: dict = {}

        def fake_completion(query, context, **kwargs):
            captured.update(kwargs)
            captured["query"] = query
            return "ok"

        monkeypatch.setattr(rlm_local, "completion", fake_completion)

        md = tmp_path / "ctx.md"
        md.write_text("# doc\n\nbody", encoding="utf-8")

        rc = cli_main([
            "ask", "what?", "--context-file", str(md),
            "--endpoint", "https://lunacode:9010/v1",
            "--model", "Qwen3.5-2B-Instruct",
        ])
        assert rc == 0
        assert captured["root_endpoint"] == "https://lunacode:9010/v1"
        assert captured["root_model"] == "Qwen3.5-2B-Instruct"
        assert captured["sub_model"] == "Qwen3.5-2B-Instruct", (
            "the sub tier must follow --model, or sub-calls silently run on the "
            "profile's model instead of the one being assessed"
        )

    def test_ask_env_vars_supply_the_defaults(self, monkeypatch, tmp_path):
        import rlm_local

        captured: dict = {}
        monkeypatch.setattr(
            rlm_local, "completion",
            lambda query, context, **kw: captured.update(kw) or "ok",
        )
        monkeypatch.setenv("RLM_ENDPOINT", "https://lunacode:9010/v1")
        monkeypatch.setenv("RLM_MODEL", "Nanbeige4.2-3B-Heretic")

        md = tmp_path / "ctx.md"
        md.write_text("body", encoding="utf-8")

        assert cli_main(["ask", "q", "--context-file", str(md)]) == 0
        assert captured["root_endpoint"] == "https://lunacode:9010/v1"
        assert captured["root_model"] == "Nanbeige4.2-3B-Heretic"

    def test_flags_beat_env(self, monkeypatch, tmp_path):
        import rlm_local

        captured: dict = {}
        monkeypatch.setattr(
            rlm_local, "completion",
            lambda query, context, **kw: captured.update(kw) or "ok",
        )
        monkeypatch.setenv("RLM_ENDPOINT", "https://ignored:9010/v1")

        md = tmp_path / "ctx.md"
        md.write_text("body", encoding="utf-8")

        assert cli_main([
            "ask", "q", "--context-file", str(md),
            "--endpoint", "https://lunacode:9010/v1",
        ]) == 0
        assert captured["root_endpoint"] == "https://lunacode:9010/v1"

    def test_ask_without_overrides_leaves_the_profile_alone(self, monkeypatch, tmp_path):
        import rlm_local

        captured: dict = {}
        monkeypatch.setattr(
            rlm_local, "completion",
            lambda query, context, **kw: captured.update(kw) or "ok",
        )
        monkeypatch.delenv("RLM_ENDPOINT", raising=False)
        monkeypatch.delenv("RLM_MODEL", raising=False)

        md = tmp_path / "ctx.md"
        md.write_text("body", encoding="utf-8")

        assert cli_main(["ask", "q", "--context-file", str(md)]) == 0
        assert "root_endpoint" not in captured
        assert "root_model" not in captured

    def test_chat_receives_endpoint_and_model(self, monkeypatch):
        import rlm_local.chat as chat_mod

        captured: dict = {}
        monkeypatch.setattr(
            chat_mod, "run_chat",
            lambda **kw: captured.update(kw),
        )

        rc = cli_main([
            "chat", "--endpoint", "https://lunacode:9010/v1",
            "--model", "Qwen3.5-4B-HauhauCS",
        ])
        assert rc == 0
        config = captured.get("config")
        assert config is not None, "chat must receive a Config carrying the overrides"
        assert config.root_endpoint == "https://lunacode:9010/v1"
        assert config.root_model == "Qwen3.5-4B-HauhauCS"
        assert config.sub_model == "Qwen3.5-4B-HauhauCS"

    def test_check_endpoint_default_honours_the_env_var(self, monkeypatch):
        import argparse

        monkeypatch.setenv("RLM_ENDPOINT", "https://lunacode:9010/v1")
        subparsers = next(
            a for a in build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        check = subparsers.choices["check"]
        endpoint_action = next(a for a in check._actions if a.dest == "endpoint")
        assert endpoint_action.default == "https://lunacode:9010/v1"

    def test_every_model_command_exposes_endpoint_and_model(self):
        import argparse

        subparsers = next(
            a for a in build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        for command in ("ask", "chat"):
            dests = {a.dest for a in subparsers.choices[command]._actions}
            assert "endpoint" in dests, f"{command} needs --endpoint"
            assert "model" in dests, f"{command} needs --model"


class TestCheckWeights:
    """The battery's scale is selectable, and a bad name is a usage error."""

    def test_check_exposes_weights_and_honours_the_env_var(self, monkeypatch):
        import argparse

        monkeypatch.setenv("RLM_CHECK_WEIGHTS", "p1-heavy")
        subparsers = next(
            a for a in build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        check = subparsers.choices["check"]
        action = next(a for a in check._actions if a.dest == "weights")
        assert action.default == "p1-heavy"

    def test_check_weights_defaults_to_the_default_profile(self, monkeypatch):
        import argparse

        monkeypatch.delenv("RLM_CHECK_WEIGHTS", raising=False)
        subparsers = next(
            a for a in build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        check = subparsers.choices["check"]
        action = next(a for a in check._actions if a.dest == "weights")
        assert action.default == "default"

    def test_an_unknown_profile_exits_2_without_touching_the_network(
        self, capsys, monkeypatch
    ):
        """A typo must not silently score on a different scale."""
        def _explode(*a, **kw):  # pragma: no cover - must not be called
            raise AssertionError("the CLI contacted the model server")

        monkeypatch.setattr("httpx.Client.post", _explode)
        rc = cli_main(["check", "some-model", "--weights", "p1-heavvy"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown weight profile" in err
        assert "default, p1-heavy" in err


class TestSearch:
    def test_search_no_index_reports_rebuild(self, tmp_path, capsys):
        """rlm search on a vault with no index exits 1 and says what to run."""
        rc = cli_main(["search", "xyzzy", "--vault", str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "Index not found" in out
        assert "rlm vault index --rebuild" in out

    def test_search_no_results(self, tmp_path, capsys):
        """rlm search over a real index prints '(no results)' and exits 0."""
        from rlm_kernel.index import rebuild_index
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        vault_path = tmp_path / "vault"
        vault = LocalVault(vault_path, init_git=False)
        seed_vault(vault)
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        rc = cli_main(["search", "xyzzyplugh", "--vault", str(vault_path)])
        assert rc == 0
        assert "(no results)" in capsys.readouterr().out


class TestGet:
    def test_get_nonexistent(self):
        """rlm get for missing page returns error."""
        rc = cli_main(["get", "nonexistent/page.md", "--vault",
                       str(Path.home() / ".local/share/rlm-kernel/vault")])
        assert rc == 1


class TestIngest:
    def _vault(self, prefix: str):
        import tempfile

        from rlm_kernel.index import rebuild_index
        from rlm_kernel.seed import seed_vault
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        vault = LocalVault(vault_path, init_git=False)
        seed_vault(vault)
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")
        return td, vault_path, vault

    def test_ingest_writes_into_the_singular_kind_directory(self):
        """R13 — pages live in `<kind>/`, not `<kind>s/`.

        `--kind definition` used to write `definitions/<name>.md`, the plural
        convention R13 retired, so an ingested page landed beside the tree that
        `gate.promote` and the seeded pages use.
        """
        td, vault_path, vault = self._vault("cli_kinddir_")
        try:
            md = vault_path / "a_concept.md"
            md.write_text("# A Concept\n\nBody text.", encoding="utf-8")

            rc = cli_main(["ingest", str(md), "--vault", str(vault_path),
                           "--kind", "definition"])
            assert rc == 0

            assert vault.exists("definition/a-concept.md"), (
                "ingest must write into the singular kind directory"
            )
            assert not vault.exists("definitions/a-concept.md")
        finally:
            td.cleanup()

    def test_every_kind_choice_is_a_real_page_kind(self):
        """A `--kind` choice the schema does not know would be a ValueError.

        `--kind source` was offered but is not a `PageKind`, so choosing it
        crashed instead of ingesting.
        """
        import argparse

        from rlm_kernel.schema import PageKind

        subparsers = next(
            a for a in build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        ingest = subparsers.choices["ingest"]
        kind_action = next(a for a in ingest._actions if a.dest == "kind")

        for choice in kind_action.choices:
            assert choice in {k.value for k in PageKind}, (
                f"--kind {choice!r} is not a PageKind value"
            )
        assert kind_action.choices, "no --kind choices declared"

    def test_ingest_creates_page(self, monkeypatch):
        """rlm ingest creates a vault page from a markdown file."""
        import tempfile
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="cli_ingest_",
                                         ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        # Init vault with git disabled
        vault = LocalVault(vault_path, init_git=False)
        from rlm_kernel.seed import seed_vault
        seed_vault(vault)
        # Build index
        from rlm_kernel.index import rebuild_index
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        # Create a test markdown file
        test_md = vault_path / "test_doc.md"
        test_md.write_text("# Hello World\n\nThis is a test document.",
                           encoding="utf-8")

        rc = cli_main(["ingest", str(test_md), "--vault", str(vault_path),
                       "--kind", "note"])
        assert rc == 0

        # Verify the page was created
        notes = vault.list(kind="note")
        assert len(notes) >= 1
        titles = [p.frontmatter.title for p in notes]
        assert any("Hello" in t for t in titles)

        td.cleanup()

    def test_ingest_duplicate_skipped(self, monkeypatch):
        """Re-ingesting the same file is skipped."""
        import tempfile
        from rlm_kernel.vault import LocalVault

        td = tempfile.TemporaryDirectory(prefix="cli_ingest2_",
                                         ignore_cleanup_errors=True)
        vault_path = Path(td.name)
        vault = LocalVault(vault_path, init_git=False)
        from rlm_kernel.seed import seed_vault
        seed_vault(vault)
        from rlm_kernel.index import rebuild_index
        rebuild_index(vault, vault_path / ".index" / "meta.sqlite")

        test_md = vault_path / "dup_test.md"
        test_md.write_text("# Duplicate\n\nSame content.", encoding="utf-8")

        # First ingest
        rc1 = cli_main(["ingest", str(test_md), "--vault", str(vault_path)])
        assert rc1 == 0

        # Second ingest — should skip
        rc2 = cli_main(["ingest", str(test_md), "--vault", str(vault_path)])
        assert rc2 == 0

        # Only one note should exist
        notes = vault.list(kind="note")
        dup_titles = [p for p in notes if p.frontmatter.title == "Duplicate"]
        assert len(dup_titles) == 1

        td.cleanup()


class TestVaultPassThrough:
    def test_vault_init(self, tmp_path, capsys):
        """rlm vault init delegates to rlm-kernel and seeds a real vault.

        This test used to be a literal `pass`. It now asserts the pass-through
        actually reaches the kernel command: exit code 0, the kernel's
        confirmation line, and pages on disk afterwards.
        """
        from rlm_kernel.vault import LocalVault

        vault_path = tmp_path / "vault"
        rc = cli_main(["vault", "init", "--vault", str(vault_path)])

        assert rc == 0
        assert f"Vault seeded at {vault_path}" in capsys.readouterr().out

        vault = LocalVault(vault_path, init_git=False)
        assert vault.list(), "vault init reported success but seeded no pages"
