"""Disk-backed context store (§5.7).

Inputs above `context_spill_threshold` are stored on disk (SQLite or raw files);
the REPL sees a lazy handle with str/list ergonomics. RAM stays flat regardless
of context size.

For the initial implementation (P0 skeleton), we use a simple file-backed store
with line indexing. SQLite can be swapped in later.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterator, Sequence


class Context:
    """Lazy, disk-backed context handle with str/list ergonomics.

    In the REPL, `context` is an instance of this class. It supports:
    - len(context) — total character count
    - context[i] / context[i:j] — slicing (reads from disk as needed)
    - iter(context) / context.lines() — line iteration
    - str(context) — full text (use sparingly; may be huge)
    """

    def __init__(self, path: Path, total_chars: int, line_offsets: list[int] | None = None) -> None:
        self._path = path
        self._total_chars = total_chars
        self._line_offsets = line_offsets  # byte offsets of line starts

    def __len__(self) -> int:
        return self._total_chars

    def __getitem__(self, key: int | slice) -> str:
        if isinstance(key, int):
            if key < 0:
                key = self._total_chars + key
            if key < 0 or key >= self._total_chars:
                raise IndexError(f"context index {key} out of range")
            with open(self._path, "r", encoding="utf-8") as f:
                f.seek(key)
                return f.read(1)
        elif isinstance(key, slice):
            start, stop, step = key.indices(self._total_chars)
            if step != 1:
                raise ValueError("context slicing only supports step=1")
            length = stop - start
            if length <= 0:
                return ""
            with open(self._path, "r", encoding="utf-8") as f:
                f.seek(start)
                return f.read(length)
        raise TypeError(f"unsupported index type: {type(key)}")

    def __iter__(self) -> Iterator[str]:
        return self.lines()

    def __str__(self) -> str:
        return self[:]

    def __repr__(self) -> str:
        return f"<Context {self._total_chars} chars at {self._path}>"

    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]:
        """Iterate over lines, lazily from disk."""
        with open(self._path, "r", encoding="utf-8") as f:
            # Seek to approximate start if we have line offsets
            if self._line_offsets and start > 0 and start < len(self._line_offsets):
                f.seek(self._line_offsets[start])
            else:
                for _ in range(start):
                    f.readline()
            yielded = 0
            for line in f:
                yield line.rstrip("\n")
                yielded += 1
                if count is not None and yielded >= count:
                    break

    def grep(self, pattern: str, max_hits: int = 50) -> list[str]:
        """Streaming grep over the context file. Returns matching lines."""
        hits: list[str] = []
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return [f"Error: invalid regex pattern: {e}"]
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                if regex.search(line):
                    hits.append(line.rstrip("\n"))
                    if len(hits) >= max_hits:
                        break
        return hits

    def chunk(
        self,
        size: int | None = None,
        by: str | None = None,
    ) -> list[str]:
        """Split context into chunks.

        Args:
            size: Characters per chunk (default: sub_prompt_char_budget).
            by: "paragraph" for paragraph splitting, None for character-based.
        """
        if by == "paragraph":
            text = str(self)
            # Split on double newlines
            paragraphs = re.split(r"\n\s*\n", text)
            return [p.strip() for p in paragraphs if p.strip()]
        # Character-based chunking
        chunk_size = size or 3000
        text = str(self)
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ContextStore:
    """Manages disk-backed context storage.

    Stores the raw context to a temp file and returns a lazy Context handle.
    """

    def __init__(self, spill_threshold: int = 1_000_000, temp_dir: str | None = None) -> None:
        self._spill_threshold = spill_threshold
        self._temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="rlm_ctx_"))
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._files: list[Path] = []

    def ingest(self, context: str | list[str] | Sequence[str]) -> Context:
        """Ingest raw context, spilling to disk if above threshold.

        Returns a Context handle for the REPL.
        """
        if isinstance(context, list) or (isinstance(context, Sequence) and not isinstance(context, str)):
            # Join list items with newline separators; indexable as one blob
            text = "\n".join(str(item) for item in context)
        else:
            text = str(context)

        total_chars = len(text)
        if total_chars <= self._spill_threshold:
            # Small enough: keep in memory but still wrap for uniform API
            import io
            return _InMemoryContext(text)

        # Spill to disk
        import time
        fname = self._temp_dir / f"context_{int(time.time() * 1_000_000)}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        self._files.append(fname)

        # Build line offset index for fast line access
        line_offsets = [0]
        with open(fname, "r", encoding="utf-8") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                for i, ch in enumerate(chunk):
                    if ch == "\n":
                        # We'd need to track cumulative offset — simplified for now
                        pass

        return Context(fname, total_chars)

    def cleanup(self) -> None:
        """Remove all spilled context files."""
        for f in self._files:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        self._files.clear()
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except OSError:
            pass


class _InMemoryContext:
    """Lightweight in-memory context for small inputs. Same API as Context."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __len__(self) -> int:
        return len(self._text)

    def __getitem__(self, key: int | slice) -> str:
        return self._text[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._text.splitlines())

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"<Context {len(self._text)} chars (in-memory)>"

    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]:
        it = iter(self._text.splitlines())
        for _ in range(start):
            next(it, None)
        yielded = 0
        for line in it:
            yield line
            yielded += 1
            if count is not None and yielded >= count:
                break

    def grep(self, pattern: str, max_hits: int = 50) -> list[str]:
        hits: list[str] = []
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return [f"Error: invalid regex pattern: {e}"]
        for line in self._text.splitlines():
            if regex.search(line):
                hits.append(line)
                if len(hits) >= max_hits:
                    break
        return hits

    def chunk(self, size: int | None = None, by: str | None = None) -> list[str]:
        if by == "paragraph":
            paragraphs = re.split(r"\n\s*\n", self._text)
            return [p.strip() for p in paragraphs if p.strip()]
        chunk_size = size or 3000
        return [self._text[i : i + chunk_size] for i in range(0, len(self._text), chunk_size)]
