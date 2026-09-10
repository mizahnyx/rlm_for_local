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
    """Filesystem-backed vault with atomic writes and optional git versioning.

    Every path this class touches goes through :meth:`_resolve`, which refuses
    absolute paths and any `..` segment and re-checks containment against the
    resolved root (S4/R20). Page *names* are constrained separately by
    `schema.NAME_PATTERN`, so `promote`'s `f"{kind}/{name}.md"` default target
    cannot escape by construction either.
    """

    def __init__(self, root: Path, init_git: bool = True) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._git_available = False
        if init_git:
            self._init_git()

    # ── Path containment ───────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """Resolve a vault-relative path, or raise ValueError if it escapes.

        Mirrors the `/docs` jail in `rlm_web/app.py`: normalize, reject
        traversal, then confirm the resolved path is still inside the root.
        """
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"vault path must be a non-empty string, got {path!r}")

        normalized = path.replace("\\", "/")
        if normalized.startswith("/") or Path(path).is_absolute():
            raise ValueError(f"vault path must be relative to the vault root: {path!r}")

        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError(f"vault path must not contain '..': {path!r}")

        root = self.root.resolve()
        candidate = root.joinpath(*parts) if parts else root
        resolved = candidate.resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise ValueError(
                f"vault path escapes the vault root: {path!r} -> {resolved}"
            )
        return resolved

    # ── Core CRUD ──────────────────────────────────────────────────────────

    def get(self, path: str) -> Page | None:
        """Read a page from the vault by its relative path."""
        file_path = self._resolve(path)
        if not file_path.is_file():
            return None
        content = file_path.read_text(encoding="utf-8")
        return parse_page(content, path)

    def put(self, page: Page, path: str) -> None:
        """Write a page with atomic semantics (tmp + fsync + rename)."""
        file_path = self._resolve(path)
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
        file_path = self._resolve(path)
        if file_path.is_file():
            file_path.unlink()
            if self._git_available:
                self._git_rm(file_path)

    def exists(self, path: str) -> bool:
        """True if a page exists at `path`.

        Raises ValueError for a path that escapes the vault root: a traversal
        attempt is an error, not a "no".
        """
        return self._resolve(path).is_file()

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

    def _relative_to_root(self, file_path: Path) -> str:
        """Repo-relative path for git, tolerating a symlinked vault root.

        `put`/`delete` now hand these helpers a **resolved** path (R20), so a
        root that resolves differently (a symlink or Windows junction) would
        otherwise make `relative_to` raise and silently skip staging.
        """
        try:
            return str(file_path.relative_to(self.root))
        except ValueError:
            return str(file_path.resolve().relative_to(self.root.resolve()))

    def _git_add(self, file_path: Path) -> None:
        try:
            subprocess.run(
                ["git", "add", "--", self._relative_to_root(file_path)],
                cwd=str(self.root),
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

    def _git_rm(self, file_path: Path) -> None:
        try:
            subprocess.run(
                ["git", "rm", "--", self._relative_to_root(file_path)],
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
