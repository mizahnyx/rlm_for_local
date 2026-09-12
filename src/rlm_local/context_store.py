"""Disk-backed context store (§5.7).

Inputs above `context_spill_threshold` are stored on disk; the REPL sees a lazy
handle with str/list ergonomics. RAM stays flat regardless of context size: the
handle reads ranges off disk and never materializes the whole blob.

Offset semantics (R2)
---------------------
Every context handle is **byte-based**, in-memory and disk-backed alike:

- ``len(ctx)`` is a UTF-8 **byte** count, not a character count.
- ``ctx[a:b]`` takes **byte** offsets and decodes the addressed range strictly.
- ``ctx[i]`` returns the single character *starting* at byte offset ``i``.
- Addressing a byte that is not a character boundary raises
  ``UnicodeDecodeError`` — that is the honest answer for a byte-addressed view
  and it can never silently return the wrong characters.
- ``lines()``, ``grep()``, ``chunk()`` yield decoded ``str`` as before.

Having one contract for both handle types matters because the model is told
``context`` is a Python object it can index; the spill threshold must not
change what indexing *means*.

SQLite can be swapped in later (design §5.7); the file-backed store is the
shipping implementation.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Iterator, Sequence

from rlm_local.templates import WORKER_INVALID_REGEX

# Read block size for streaming operations that must not materialize the blob.
_STREAM_BLOCK = 65536

# Paragraph separator used by ``chunk(by="paragraph")`` (§5.3).
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def _decode_one_char(raw: bytes, offset: int) -> str:
    """Decode exactly one character from the head of ``raw``.

    ``raw`` must start at a character boundary. If it does not (continuation
    byte, invalid lead byte, or a truncated sequence at EOF) this raises
    ``UnicodeDecodeError`` instead of returning a byte dressed up as a char.
    """
    for n in range(1, len(raw) + 1):
        try:
            ch = raw[:n].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        if len(ch) == 1:
            return ch
        break
    raise UnicodeDecodeError(
        "utf-8", raw or b"", 0, 1,
        f"byte offset {offset} is not a character boundary",
    )


class Context:
    """Lazy, disk-backed context handle with str/list ergonomics.

    In the REPL, `context` is an instance of this class when the input spilled
    to disk. It supports:

    - ``len(context)`` — total **byte** count
    - ``context[i]`` — the character starting at byte offset ``i``
    - ``context[i:j]`` — the text decoded from byte offsets ``i``..``j``
    - ``iter(context)`` / ``context.lines(start, count)`` — decoded line iteration
    - ``context.grep(pattern, max_hits)`` — streaming regex search
    - ``context.chunk(size, by)`` — streaming chunking
    - ``str(context)`` — full text (use sparingly; may be huge)

    The file is written once by :class:`ContextStore` and is read-only from the
    consumer's side: nothing in this class mutates it.
    """

    def __init__(
        self,
        path: Path,
        total_bytes: int,
        line_offsets: list[int] | None = None,
    ) -> None:
        self._path = path
        self._total_bytes = total_bytes
        self._line_offsets = line_offsets  # byte offset of each line start

    # ── Addressing ────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self._total_bytes

    def __getitem__(self, key: int | slice) -> str:
        if isinstance(key, int):
            if key < 0:
                key = self._total_bytes + key
            if key < 0 or key >= self._total_bytes:
                raise IndexError(f"context index {key} out of range")
            with open(self._path, "rb") as f:
                f.seek(key)
                raw = f.read(4)  # a UTF-8 character is at most 4 bytes
            return _decode_one_char(raw, key)

        if isinstance(key, slice):
            start, stop, step = key.indices(self._total_bytes)
            if step != 1:
                raise ValueError("context slicing only supports step=1")
            length = stop - start
            if length <= 0:
                return ""
            with open(self._path, "rb") as f:
                f.seek(start)
                raw = f.read(length)
            return raw.decode("utf-8", errors="strict")

        raise TypeError(f"unsupported index type: {type(key)}")

    def __iter__(self) -> Iterator[str]:
        return self.lines()

    def __str__(self) -> str:
        return self[:]

    def __repr__(self) -> str:
        return f"<Context {self._total_bytes} bytes at {self._path}>"

    # ── Streaming operations ──────────────────────────────────────────────

    def lines(self, start: int = 0, count: int | None = None) -> Iterator[str]:
        """Iterate over decoded lines, lazily from disk.

        ``start`` skips whole lines. When a byte-offset line index is present
        the file is seeked straight to the line; otherwise it is read forward.
        """
        with open(self._path, "rb") as f:
            if (
                self._line_offsets
                and start > 0
                and start < len(self._line_offsets)
            ):
                f.seek(self._line_offsets[start])
            else:
                for _ in range(start):
                    if not f.readline():
                        return
            yielded = 0
            for raw in f:
                yield raw.decode("utf-8", errors="strict").rstrip("\r\n")
                yielded += 1
                if count is not None and yielded >= count:
                    break

    def grep(self, pattern: str, max_hits: int = 50) -> list[str]:
        """Streaming grep over the context file. Returns decoded matching lines."""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return [WORKER_INVALID_REGEX.format(error=e)]
        hits: list[str] = []
        with open(self._path, "rb") as f:
            for raw in f:
                line = raw.decode("utf-8", errors="strict")
                if regex.search(line):
                    hits.append(line.rstrip("\r\n"))
                    if len(hits) >= max_hits:
                        break
        return hits

    def chunk(
        self,
        size: int | None = None,
        by: str | None = None,
    ) -> list[str]:
        """Split context into chunks, streaming from disk (never ``str(self)``).

        Args:
            size: Characters per chunk (default 3000).
            by: "paragraph" for paragraph splitting, None for character-based.
        """
        if by == "paragraph":
            return list(self._iter_paragraphs())
        chunk_size = size or 3000
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        chunks: list[str] = []
        with open(self._path, "r", encoding="utf-8") as f:
            while True:
                piece = f.read(chunk_size)
                if not piece:
                    break
                chunks.append(piece)
        return chunks

    def _iter_paragraphs(self, block: int = _STREAM_BLOCK) -> Iterator[str]:
        """Yield stripped paragraphs without holding the whole file in memory."""
        buf = ""
        with open(self._path, "r", encoding="utf-8") as f:
            while True:
                piece = f.read(block)
                if not piece:
                    break
                buf += piece
                parts = _PARAGRAPH_RE.split(buf)
                buf = parts.pop()
                for p in parts:
                    p = p.strip()
                    if p:
                        yield p
        for p in _PARAGRAPH_RE.split(buf):
            p = p.strip()
            if p:
                yield p


class ContextStore:
    """Manages disk-backed context storage.

    Stores the raw context to a temp file and returns a lazy Context handle.
    """

    def __init__(self, spill_threshold: int = 1_000_000, temp_dir: str | None = None) -> None:
        self._spill_threshold = spill_threshold
        self._temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="rlm_ctx_"))
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._files: list[Path] = []

    def ingest(self, context: str | list[str] | Sequence[str]) -> "Context | _InMemoryContext":
        """Ingest raw context, spilling to disk if above threshold.

        Returns a Context handle for the REPL.
        """
        if isinstance(context, list) or (isinstance(context, Sequence) and not isinstance(context, str)):
            # Join list items with newline separators; indexable as one blob
            text = "\n".join(str(item) for item in context)
        else:
            text = str(context)

        # Byte length is the governing measure (§5.7 spill decision is about
        # bytes on disk, matching Context's byte-addressed contract).
        data = text.encode("utf-8")
        total_bytes = len(data)
        if total_bytes <= self._spill_threshold:
            # Small enough: keep in memory but still wrap for uniform API
            return _InMemoryContext(text)

        # Spill to disk
        import time
        fname = self._temp_dir / f"context_{time.time_ns()}.txt"
        with open(fname, "wb") as f:
            f.write(data)
        self._files.append(fname)

        # Byte-offset line index: offset of the start of each line.
        line_offsets = [0]
        idx = data.find(b"\n")
        while idx != -1:
            line_offsets.append(idx + 1)
            idx = data.find(b"\n", idx + 1)

        return Context(fname, total_bytes, line_offsets)

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
    """Lightweight in-memory context for small inputs. Same API as Context.

    Byte-based like :class:`Context` (R2) so that indexing semantics do not
    silently change when a context crosses the spill threshold.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._data = text.encode("utf-8")

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: int | slice) -> str:
        if isinstance(key, int):
            if key < 0:
                key = len(self._data) + key
            if key < 0 or key >= len(self._data):
                raise IndexError(f"context index {key} out of range")
            return _decode_one_char(self._data[key : key + 4], key)
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self._data))
            if step != 1:
                raise ValueError("context slicing only supports step=1")
            return self._data[start:stop].decode("utf-8", errors="strict")
        raise TypeError(f"unsupported index type: {type(key)}")

    def __iter__(self) -> Iterator[str]:
        return iter(self._text.splitlines())

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"<Context {len(self._data)} bytes (in-memory)>"

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
            return [WORKER_INVALID_REGEX.format(error=e)]
        for line in self._text.splitlines():
            if regex.search(line):
                hits.append(line)
                if len(hits) >= max_hits:
                    break
        return hits

    def chunk(self, size: int | None = None, by: str | None = None) -> list[str]:
        if by == "paragraph":
            paragraphs = _PARAGRAPH_RE.split(self._text)
            return [p.strip() for p in paragraphs if p.strip()]
        chunk_size = size or 3000
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")
        return [self._text[i : i + chunk_size] for i in range(0, len(self._text), chunk_size)]
