"""Interactive chat mode (D1) — slash-command REPL over the RLM harness.

Usage:
    from rlm_local.chat import run_chat
    run_chat(profile="laptop", vault_path="/path/to/vault")
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from rlm_local.config import Config, load_config
from rlm_local.model_backend import HTTPModelBackend, ModelBackend
from rlm_kernel.memory import MemoryManager
from rlm_kernel.search import search_vault
from rlm_kernel.vault import LocalVault, VaultStore


class ChatSession:
    """Interactive chat session with slash-command dispatch.

    Maintains accumulated session context, vault access, and a dispatch dict
    of slash commands. Non-slash input is treated as ``/ask <input>``.
    """

    def __init__(
        self,
        profile: str = "laptop",
        vault_path: str | Path | None = None,
        *,
        backend: ModelBackend | None = None,
        config: Config | None = None,
    ) -> None:
        self.profile = profile
        self.config = config or load_config(profile)

        # ── Vault ────────────────────────────────────────────────────────
        if vault_path is None:
            vault_path = Path.home() / ".local" / "share" / "rlm-kernel" / "vault"
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.vault: VaultStore = LocalVault(self.vault_path, init_git=False)
        self.index_path = self.vault_path / "meta.sqlite"

        # ── Memory ───────────────────────────────────────────────────────
        self.memory = MemoryManager(vault=self.vault, index_path=self.index_path)

        # ── Backend ──────────────────────────────────────────────────────
        if backend is not None:
            self.backend = backend
        else:
            self.backend = HTTPModelBackend(
                root_endpoint=self.config.root_endpoint,
                sub_endpoint=self.config.sub_endpoint or self.config.root_endpoint,
                root_model=self.config.root_model,
                sub_model=self.config.sub_model,
                verify=False,
                timeout=300.0,
            )

        # ── Session state ────────────────────────────────────────────────
        self._context_parts: list[str] = []
        self._context_files: list[str] = []
        self._running: bool = False

        # ── Command dispatch ─────────────────────────────────────────────
        self._commands: dict[str, Callable[[str], None]] = {
            "/ask": self._cmd_ask,
            "/ingest": self._cmd_ingest,
            "/context": self._cmd_context,
            "/clear": self._cmd_clear,
            "/search": self._cmd_search,
            "/get": self._cmd_get,
            "/note": self._cmd_note,
            "/check": self._cmd_check,
            "/quit": self._cmd_quit,
        }

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def session_context(self) -> str:
        return "\n\n".join(self._context_parts)

    # ── Banner ──────────────────────────────────────────────────────────────

    def _banner(self) -> None:
        model = self.config.root_model or "unknown"
        print(f"rlm-chat — profile={self.profile}, model={model}")

    # ── Main loop ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self._banner()
        self._running = True
        while self._running:
            try:
                line = input("> ").strip()
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                print()
                break

            if not line:
                continue

            if line.startswith("/"):
                parts = line.split(maxsplit=1)
                cmd = parts[0]
                arg = parts[1] if len(parts) > 1 else ""
                handler = self._commands.get(cmd)
                if handler:
                    try:
                        handler(arg)
                    except Exception as exc:
                        print(f"Error: {exc}")
                else:
                    print(f"Unknown command: {cmd}")
            else:
                self._cmd_ask(line)

    # ── Commands ────────────────────────────────────────────────────────────

    def _cmd_ask(self, arg: str) -> None:
        """Run a completion with the accumulated session context."""
        if not arg:
            print("Usage: /ask <query>")
            return

        context = self.session_context
        if not context.strip():
            # R16: an empty context used to be passed straight to completion(),
            # which spends a full run discovering there is nothing to read.
            print(
                "No context loaded. Use /ingest <path> to add Markdown files "
                "before asking."
            )
            return

        import rlm_local

        try:
            answer = rlm_local.completion(
                arg,
                context,
                profile=self.profile,
                backend=self.backend,
                config=self.config,
            )
            print(answer)
        except Exception as exc:
            print(f"Completion error: {exc}")

    def _cmd_ingest(self, arg: str) -> None:
        """Load one or more .md files into the session context."""
        if not arg:
            print("Usage: /ingest <path> [path ...]")
            return

        paths = arg.split()
        for p in paths:
            fp = Path(p).expanduser().resolve()
            if not fp.is_file():
                print(f"  {p}: not found")
                continue
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception as exc:
                print(f"  {p}: read error — {exc}")
                continue

            self._context_parts.append(text)
            self._context_files.append(str(fp))
            print(f"  {p}: {len(text)} chars")

    def _cmd_context(self, arg: str) -> None:
        """Show what's loaded in the session context."""
        total = sum(len(p) for p in self._context_parts)
        if not self._context_files:
            print("Context is empty.")
        else:
            for f in self._context_files:
                # Show filename only, not full path
                print(f"  {Path(f).name}")
            print(f"  ({len(self._context_files)} file(s), {total} chars total)")

    def _cmd_clear(self, arg: str) -> None:
        """Drop all accumulated session context."""
        count = len(self._context_files)
        self._context_parts.clear()
        self._context_files.clear()
        print(f"Context cleared ({count} file(s) dropped).")

    def _cmd_search(self, arg: str) -> None:
        """Hybrid search over the vault index."""
        if not arg:
            print("Usage: /search <query>")
            return

        try:
            results = search_vault(
                self.vault,
                self.index_path,
                arg,
                k=5,
                detail="card",
            )
        except Exception as exc:
            print(f"Search error: {exc}")
            return

        if not results:
            print("No results.")
            return

        for r in results:
            print(
                f"  [{r['kind']}] {r['title']}  "
                f"(score={r['score']:.2f}, {r['path']})"
            )
            summary = r.get("summary", "")
            if summary:
                print(f"    {summary}")

    def _cmd_get(self, arg: str) -> None:
        """Print a vault page by path."""
        if not arg:
            print("Usage: /get <path>")
            return

        page = self.vault.get(arg)
        if page is None:
            print(f"Page not found: {arg}")
            return

        print(f"--- {page.frontmatter.title} ({page.frontmatter.kind.value}) ---")
        print(page.body)

    def _cmd_note(self, arg: str) -> None:
        """Create a memory note from text (regex extraction, no model call)."""
        if not arg:
            print("Usage: /note <text>")
            return

        try:
            page = self.memory.add(text=arg, llm=None)
            print(f"Note created: {page.frontmatter.title!r} → {page.path}")
        except Exception as exc:
            print(f"Note error: {exc}")

    def _cmd_check(self, arg: str) -> None:
        """Run a model suitability battery against the configured endpoint."""
        model_id = arg.strip() or self.config.root_model
        if not model_id:
            print("No model configured.")
            return

        print(f"Checking model: {model_id}")

        checks: list[tuple[str, bool, str]] = []

        # 1. Connectivity — try a minimal chat completion
        try:
            resp = self.backend.chat(
                [{"role": "user", "content": "Say hello."}],
                tier="root",
                max_tokens=10,
                temperature=0.0,
            )
            ok = bool(resp and len(resp.strip()) > 0)
            checks.append(
                ("connectivity", ok, f"response: {resp[:60]!r}" if ok else "empty response")
            )
        except Exception as exc:
            checks.append(("connectivity", False, str(exc)))

        # 2. JSON schema compliance — try structured output
        try:
            resp = self.backend.chat(
                [{"role": "user", "content": "Return {\"value\": 42} as JSON."}],
                tier="root",
                max_tokens=50,
                temperature=0.0,
            )
            # Loose check: response contains a JSON-like structure
            ok = "{" in resp and "}" in resp
            checks.append(
                ("json-output", ok, f"response: {resp[:60]!r}" if ok else "no JSON found")
            )
        except Exception as exc:
            checks.append(("json-output", False, str(exc)))

        # 3. Context handling — try with a longer prompt
        try:
            ctx = "The capital of France is Paris. " * 10
            resp = self.backend.chat(
                [
                    {"role": "system", "content": "Answer briefly."},
                    {"role": "user", "content": f"{ctx}\n\nWhat is the capital of France?"},
                ],
                tier="root",
                max_tokens=20,
                temperature=0.0,
            )
            ok = "paris" in resp.lower()
            checks.append(
                ("context-window", ok, f"response: {resp[:60]!r}" if ok else "unexpected reply")
            )
        except Exception as exc:
            checks.append(("context-window", False, str(exc)))

        # Print results
        passed = 0
        for name, ok, detail in checks:
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name}: {detail}")
            if ok:
                passed += 1

        print(f"  {passed}/{len(checks)} checks passed.")

    def _cmd_quit(self, arg: str) -> None:
        """Exit the chat session."""
        self._running = False
        print("Goodbye.")

    def close(self) -> None:
        """Release backend resources."""
        if isinstance(self.backend, HTTPModelBackend):
            self.backend.close()


def run_chat(
    profile: str = "laptop",
    vault_path: str | Path | None = None,
    *,
    backend: ModelBackend | None = None,
    config: Config | None = None,
) -> None:
    """Entry point: start an interactive chat session.

    Args:
        profile: Hardware profile name ("tiny", "laptop", "workstation").
        vault_path: Path to vault directory (default: ~/.local/share/rlm-kernel/vault).
        backend: Optional pre-configured ModelBackend (for testing).
        config: Optional pre-built Config.
    """
    session = ChatSession(
        profile=profile,
        vault_path=vault_path,
        backend=backend,
        config=config,
    )
    try:
        session.run()
    finally:
        session.close()
