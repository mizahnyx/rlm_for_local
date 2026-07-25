"""Vault store — page CRUD with atomic writes and git versioning (§3.1, §5).

The vault is the persistent "image": a directory tree of markdown pages,
structured as `kind/name.md`. All writes are atomic (tmp + fsync + rename).
Git tracks changes with batched commits.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from rlm_kernel.schema import Page, PageKind, parse_page


# ── Protocol ────────────────────────────────────────────────────────────────

@runtime_checkable
class VaultStore(Protocol):
    """Protocol for page storage backends (§10 — seam for HttpVault later)."""

    def get(self, path: str) -> Page | None: ...
    def put(self, page: Page, path: str) -> None: ...
    def delete(self, path: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]: ...
    def resolve_wikilink(self, name: str) -> Page | None: ...


# ── LocalVault ──────────────────────────────────────────────────────────────

class LocalVault:
    """Filesystem-backed vault with atomic writes and optional git versioning."""

    def __init__(self, root: Path, init_git: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._git_available = False
        if init_git:
            self._init_git()

    # ── Core CRUD ──────────────────────────────────────────────────────────

    def get(self, path: str) -> Page | None:
        """Read a page from the vault by its relative path."""
        file_path = self.root / path
        if not file_path.is_file():
            return None
        content = file_path.read_text(encoding="utf-8")
        return parse_page(content, path)

    def put(self, page: Page, path: str) -> None:
        """Write a page with atomic semantics (tmp + fsync + rename)."""
        file_path = self.root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        page.path = path

        content = page.to_markdown()
        content_bytes = content.encode("utf-8")

        # Atomic write: write to temp file in same directory, fsync, rename
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent), prefix="." + file_path.name + ".",
            suffix=".tmp",
        )
        try:
            os.write(tmp_fd, content_bytes)
            os.fsync(tmp_fd)
            os.close(tmp_fd)
            os.replace(tmp_path, str(file_path))
        except Exception:
            os.close(tmp_fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        if self._git_available:
            self._git_add(file_path)

    def delete(self, path: str) -> None:
        """Remove a page from the vault."""
        file_path = self.root / path
        if file_path.is_file():
            file_path.unlink()
            if self._git_available:
                self._git_rm(file_path)

    def exists(self, path: str) -> bool:
        return (self.root / path).is_file()

    def list(self, prefix: str = "", kind: str | None = None) -> list[Page]:
        """List pages, optionally filtered by path prefix and/or kind."""
        search_root = self.root / prefix if prefix else self.root
        if not search_root.exists():
            return []

        pages: list[Page] = []
        for md_file in sorted(search_root.rglob("*.md")):
            if ".index" in md_file.parts or ".git" in md_file.parts:
                continue
            if "quarantine" in md_file.parts and prefix != "quarantine":
                continue
            rel_path = str(md_file.relative_to(self.root)).replace("\\", "/")
            try:
                page = self.get(rel_path)
                if page is None:
                    continue
                if kind is not None and page.kind.value != kind:
                    continue
                pages.append(page)
            except Exception:
                continue

        return pages

    # ── Wikilinks ──────────────────────────────────────────────────────────

    def resolve_wikilink(self, name: str) -> Page | None:
        """Resolve a [[wikilink]] to a page (case-insensitive by name)."""
        name_lower = name.lower()
        # Walk all .md files and match by basename without extension
        for md_file in self.root.rglob("*.md"):
            if ".index" in md_file.parts or ".git" in md_file.parts or "quarantine" in md_file.parts:
                continue
            file_stem = md_file.stem.lower()
            if file_stem == name_lower:
                rel = str(md_file.relative_to(self.root)).replace("\\", "/")
                return self.get(rel)
        return None

    # ── Git ─────────────────────────────────────────────────────────────────

    def _init_git(self) -> None:
        """Initialize git repo if not already present."""
        git_dir = self.root / ".git"
        if git_dir.exists():
            self._git_available = True
            return

        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(self.root),
                capture_output=True,
                check=True,
                timeout=10,
            )
            # Create .gitignore for derived files
            gitignore = self.root / ".gitignore"
            gitignore.write_text(".index/\n*.tmp\n")
            self._git_available = True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            self._git_available = False

    def _git_add(self, file_path: Path) -> None:
        try:
            subprocess.run(
                ["git", "add", "--", str(file_path.relative_to(self.root))],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

    def _git_rm(self, file_path: Path) -> None:
        try:
            subprocess.run(
                ["git", "rm", "--", str(file_path.relative_to(self.root))],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

    def git_commit(self, message: str = "vault: update") -> bool:
        """Commit all staged changes. Returns True on success."""
        if not self._git_available:
            return False
        try:
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(self.root),
                capture_output=True,
                timeout=30,
                check=True,
            )
            return True
        except Exception:
            return False
