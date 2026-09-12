"""Read-only corpus mounts (roadmap RO2).

The source corpus is read-only by owner constraint, and that constraint cannot be
satisfied by good intentions: model-authored REPL cells run as the harness user
with no `open` jail (DG2/DG10), so *application code is not the boundary*. The
boundary is the mount (see `AGENTS.md` §1.8) — `cryptsetup open --readonly`, a
`ro` filesystem mount, and a corpus the harness user cannot write to.

What this module can do is remove the accident, which is the second layer:

* **no write verb exists.** Not "writes are checked" — there is no `write`,
  `unlink`, `rename`, `mkdir`, `chmod` or `truncate` on the mount at all, so no
  ingest code path can mutate the corpus and no caller can ask it to. A test scans
  this module for write-shaped names and fails if one appears.
* **containment.** Every path resolves inside the root, after symlink resolution,
  so a link pointing out of the tree is refused rather than followed.
* **streaming.** The primitive is a read-only file handle plus a bounded read;
  nothing here loads a directory listing into a list unless the caller asks for a
  page of it. A tree of millions of files cannot be materialised, and the
  inventory walk already showed why (4.1M entries and still climbing).
* **derived state lives elsewhere.** `assert_derived_outside_corpus` makes the one
  mistake that would write into the backup — pointing the vault or index at a path
  inside it — a hard failure at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class ReadOnlyViolation(RuntimeError):
    """Raised when a request would reach outside the mount or write to it."""


@dataclass(frozen=True)
class Entry:
    """One directory entry, as reported by the mount."""

    rel: str
    """Path relative to the mount root, POSIX separators."""

    kind: str
    """``file``, ``dir``, ``symlink`` or ``other``."""

    size: int
    """Bytes. For a symlink this is the link's own size, not the target's."""

    mode: int
    """Permission bits, as returned by `os.stat`."""

    mtime: float = 0.0
    """Modification time, seconds since the epoch.

    Defaulted so the field can be added without breaking positional
    construction. It exists because the path index records it: the census and
    the read-only proof both compare mtimes, and reading a file updates atime
    rather than mtime (see `AGENTS.md` §1.8).
    """


class LocalTreeMount:
    """A read-only view over a local directory tree.

    Construct it around the corpus path (`/srv/corpus` on the laptop) and hand it
    to whatever needs to read the corpus. It deliberately has no way to change
    anything: the type is the contract.
    """

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root)
        if not candidate.is_dir():
            raise ReadOnlyViolation(f"mount root is not a directory: {candidate}")
        self._root = candidate.resolve()

    @property
    def root(self) -> Path:
        """The resolved mount root. Absolute; symlinks already folded."""
        return self._root

    # ── Path containment ──────────────────────────────────────────────────

    def _resolve(self, rel: str) -> Path:
        """Resolve ``rel`` inside the root, or refuse.

        Symlinks are resolved *before* the containment check, so a link whose
        target lies outside the tree is rejected instead of followed. Absolute
        paths, drive-relative paths and NUL bytes are refused outright rather than
        interpreted.
        """
        if not isinstance(rel, str) or not rel:
            raise ReadOnlyViolation("empty path")
        if "\x00" in rel:
            raise ReadOnlyViolation("NUL byte in path")
        if rel.startswith("/") or rel.startswith("\\") or ":" in rel[:2]:
            raise ReadOnlyViolation(f"absolute path refused: {rel!r}")

        candidate = (self._root / rel).resolve()
        if not candidate.is_relative_to(self._root):
            raise ReadOnlyViolation(
                f"path escapes the mount root: {rel!r} -> {candidate}"
            )
        return candidate

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()

    # ── Reading ───────────────────────────────────────────────────────────

    def exists(self, rel: str) -> bool:
        try:
            return self._resolve(rel).exists()
        except ReadOnlyViolation:
            return False

    def is_contained(self, rel: str) -> bool:
        """True when ``rel`` resolves inside the root. Says nothing about existence.

        `exists` answers "is there something I can see there?", which is `False`
        for both a missing path and an escaping one. Some callers must tell those
        apart — "no such file" and "refused, that path leaves the corpus" are
        different answers, and a tool that reports one as the other is giving a
        confident wrong answer.
        """
        try:
            self._resolve(rel)
            return True
        except ReadOnlyViolation:
            return False

    def stat(self, rel: str) -> Entry:
        path = self._resolve(rel)
        try:
            info = path.lstat()
        except OSError as e:
            raise ReadOnlyViolation(f"cannot stat {rel!r}: {e}") from e
        return Entry(
            rel=self._relative(path),
            kind=_kind_of(info.st_mode),
            size=info.st_size,
            mode=info.st_mode & 0o7777,
            mtime=info.st_mtime,
        )

    def open_readonly(self, rel: str, max_bytes: int | None = None):
        """Open a file for reading. The only file-opening primitive here.

        Returns a binary file object positioned at the start, capped at
        ``max_bytes`` when given (so a caller cannot accidentally pull a 150 GB
        file into memory by naming it). Callers that need a whole large file
        should read in chunks from the handle rather than asking for it all.
        """
        path = self._resolve(rel)
        if not path.is_file():
            raise ReadOnlyViolation(f"not a regular file: {rel!r}")
        try:
            # O_RDONLY, explicitly. No O_CREAT, no mode argument: this call cannot
            # create or truncate anything even if the path were wrong.
            fd = os.open(path, os.O_RDONLY)
        except OSError as e:
            raise ReadOnlyViolation(f"cannot open {rel!r}: {e}") from e
        handle = os.fdopen(fd, "rb")
        if max_bytes is not None:
            return _BoundedReader(handle, max_bytes)
        return handle

    def read_bytes(self, rel: str, max_bytes: int | None = None) -> bytes:
        with self.open_readonly(rel, max_bytes=max_bytes) as handle:
            return handle.read()

    def read_text(
        self, rel: str, max_bytes: int | None = None, errors: str = "replace",
    ) -> str:
        """Read a file as text.

        ``errors="replace"`` by default: the corpus is an unorganised backup, and
        a single stray byte must not abort a census of four million files. The
        replacement character is visible in the output, so damage is reported
        rather than hidden.
        """
        return self.read_bytes(rel, max_bytes=max_bytes).decode("utf-8", errors=errors)

    # ── Walking ───────────────────────────────────────────────────────────

    def iter_entries(
        self,
        rel: str = "",
        *,
        max_entries: int | None = None,
        follow_symlinks: bool = False,
    ) -> Iterator[Entry]:
        """Yield entries under ``rel``, depth-first, one directory at a time.

        A generator on purpose: the caller decides how many entries to consume,
        and nothing builds a list of the whole tree. ``follow_symlinks=False``
        (the default) reports a link as a link instead of walking through it,
        which is what keeps a link out of the tree from turning into a walk of
        the filesystem.
        """
        start = self._resolve(rel) if rel else self._root
        if not start.is_dir():
            raise ReadOnlyViolation(f"not a directory: {rel!r}")

        yielded = 0
        stack = [start]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as scan:
                    for child in scan:
                        path = Path(child.path)
                        try:
                            info = child.stat(follow_symlinks=follow_symlinks)
                        except OSError:
                            continue
                        kind = _kind_of(info.st_mode)
                        yield Entry(
                            rel=self._relative(path),
                            kind=kind,
                            size=info.st_size,
                            mode=info.st_mode & 0o7777,
                            mtime=info.st_mtime,
                        )
                        yielded += 1
                        if max_entries is not None and yielded >= max_entries:
                            return
                        if kind == "dir":
                            stack.append(path)
                        elif kind == "symlink" and follow_symlinks:
                            target = path.resolve()
                            if target.is_dir() and target.is_relative_to(self._root):
                                stack.append(target)
            except OSError:
                continue

    def iter_children(
        self, rel: str = "", *, max_entries: int | None = None,
    ) -> Iterator[Entry]:
        """Yield the entries of **one** directory, not its subtree.

        `iter_entries` walks depth-first and is the wrong primitive for "what is
        in this directory": on a corpus of millions of files a bounded subtree
        walk answers a different question (and can return entries from four
        levels down). This is one `scandir`, so its cost is the directory's own
        size and nothing else.
        """
        directory = self._resolve(rel) if rel else self._root
        if not directory.is_dir():
            raise ReadOnlyViolation(f"not a directory: {rel!r}")
        yielded = 0
        with os.scandir(directory) as scan:
            for child in scan:
                path = Path(child.path)
                try:
                    info = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield Entry(
                    rel=self._relative(path),
                    kind=_kind_of(info.st_mode),
                    size=info.st_size,
                    mode=info.st_mode & 0o7777,
                    mtime=info.st_mtime,
                )
                yielded += 1
                if max_entries is not None and yielded >= max_entries:
                    return

    def iter_files(
        self,
        rel: str = "",
        *,
        suffixes: tuple[str, ...] | None = None,
        max_entries: int | None = None,
    ) -> Iterator[Entry]:
        """Yield only files, optionally filtered by suffix (case-insensitive)."""
        wanted = None
        if suffixes:
            wanted = tuple(s.lower() for s in suffixes)
        for entry in self.iter_entries(rel, max_entries=max_entries):
            if entry.kind != "file":
                continue
            if wanted is not None and not entry.rel.lower().endswith(wanted):
                continue
            yield entry


class _BoundedReader:
    """A file handle that will not hand out more than `limit` bytes.

    Wraps the real handle rather than slicing a `read()` result, so the cap holds
    for chunked reads too — which is the only way a caller should be reading
    corpus files.
    """

    def __init__(self, handle: Any, limit: int) -> None:
        self._handle = handle
        self._limit = limit
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._limit - self._read
        if remaining <= 0:
            return b""
        if size is None or size < 0 or size > remaining:
            size = remaining
        data = self._handle.read(size)
        self._read += len(data)
        return data

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "_BoundedReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def assert_derived_outside_corpus(
    corpus_root: str | Path, derived_root: str | Path,
) -> None:
    """Refuse a derived-state root that lives inside the corpus (RO2, layer 3).

    This is the mistake that would quietly write into the backup: pointing the
    vault, the index or the CAS at a path under the corpus. It is checked at
    startup, not at write time, because by write time the damage is the point.
    """
    corpus = Path(corpus_root).resolve()
    derived = Path(derived_root).resolve()
    if derived == corpus or derived.is_relative_to(corpus):
        raise ReadOnlyViolation(
            f"derived state would live inside the corpus: {derived} is under "
            f"{corpus}. Point the vault/index at a path outside the corpus "
            "(AGENTS.md 1.8, layer 3)."
        )
    if corpus.is_relative_to(derived):
        raise ReadOnlyViolation(
            f"the corpus would live inside the derived-state root: {corpus} is "
            f"under {derived}. Both would be rewritten by any vault operation."
        )


def _kind_of(mode: int) -> str:
    import stat as _stat

    if _stat.S_ISDIR(mode):
        return "dir"
    if _stat.S_ISLNK(mode):
        return "symlink"
    if _stat.S_ISREG(mode):
        return "file"
    return "other"
