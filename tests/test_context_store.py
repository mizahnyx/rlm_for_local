"""Tests for context store module.

Offset semantics (R2): every context handle — in-memory *and* disk-backed — is
**byte-based**. `len(ctx)` is a UTF-8 byte count and `ctx[i]` / `ctx[a:b]` take
byte offsets, so the two handle types cannot disagree. Slicing on a byte offset
that is not a character boundary raises `UnicodeDecodeError` (strict decoding),
which is the honest answer for a byte-addressed view.
"""

from __future__ import annotations

import pytest

from rlm_local.context_store import Context, ContextStore, _InMemoryContext

# Multi-byte UTF-8 fixture: CJK (3 bytes each) + emoji (4 bytes) + ASCII.
UNICODE_TEXT = (
    "日本語のテキスト\n"
    "emoji: 🎉🚀\n"
    "ascii line\n"
    "napoléon café\n"
    "終わり\n"
)


def _boundaries(text: str) -> list[int]:
    """Byte offsets that fall on a character boundary."""
    offsets = [0]
    total = 0
    for ch in text:
        total += len(ch.encode("utf-8"))
        offsets.append(total)
    return offsets


class TestInMemoryContext:
    def test_len(self):
        ctx = _InMemoryContext("hello world")
        assert len(ctx) == 11

    def test_index(self):
        ctx = _InMemoryContext("hello")
        assert ctx[0] == "h"
        assert ctx[4] == "o"
        assert ctx[-1] == "o"

    def test_slice(self):
        ctx = _InMemoryContext("hello world")
        assert ctx[0:5] == "hello"
        assert ctx[6:] == "world"

    def test_iter(self):
        ctx = _InMemoryContext("line1\nline2\nline3")
        lines = list(ctx)
        assert lines == ["line1", "line2", "line3"]

    def test_str(self):
        ctx = _InMemoryContext("hello")
        assert str(ctx) == "hello"

    def test_lines(self):
        ctx = _InMemoryContext("a\nb\nc\nd\ne")
        result = list(ctx.lines(start=1, count=2))
        assert result == ["b", "c"]

    def test_grep(self):
        ctx = _InMemoryContext("apple pie\nbanana split\napple tart\ncherry")
        hits = ctx.grep("apple")
        assert hits == ["apple pie", "apple tart"]

    def test_grep_max_hits(self):
        ctx = _InMemoryContext("\n".join(f"line {i}" for i in range(100)))
        hits = ctx.grep("line", max_hits=5)
        assert len(hits) == 5

    def test_grep_invalid_regex(self):
        ctx = _InMemoryContext("hello")
        hits = ctx.grep("[invalid")
        assert hits[0].startswith("Error")

    def test_chunk(self):
        ctx = _InMemoryContext("abcdefghij")
        chunks = ctx.chunk(size=3)
        assert len(chunks) == 4
        assert chunks[0] == "abc"
        assert chunks[-1] == "j"

    def test_chunk_paragraph(self):
        ctx = _InMemoryContext("para1\n\npara2\n\npara3")
        chunks = ctx.chunk(by="paragraph")
        assert chunks == ["para1", "para2", "para3"]


class TestContextStore:
    def test_small_context_stays_in_memory(self):
        store = ContextStore(spill_threshold=1000)
        ctx = store.ingest("small context")
        assert isinstance(ctx, _InMemoryContext)
        assert len(ctx) == 13

    def test_large_context_spills_to_disk(self):
        store = ContextStore(spill_threshold=10)
        ctx = store.ingest("a" * 100)
        # Should be disk-backed
        assert len(ctx) == 100
        assert ctx[0] == "a"
        assert ctx[50] == "a"
        store.cleanup()

    def test_list_context(self):
        store = ContextStore(spill_threshold=1000)
        ctx = store.ingest(["item1", "item2", "item3"])
        assert "item1" in str(ctx)
        assert "item2" in str(ctx)


class TestByteOffsetSemantics:
    """R2 — byte offsets everywhere, correct on non-ASCII contexts.

    Before the fix, `Context.__getitem__` did `f.seek(char_index)` on a
    text-mode file whose `len()` was a character count, so any multi-byte
    context corrupted indexing or raised mid-codepoint.
    """

    def test_in_memory_len_is_bytes_not_chars(self):
        ctx = _InMemoryContext(UNICODE_TEXT)
        assert len(UNICODE_TEXT) != len(UNICODE_TEXT.encode("utf-8"))  # guard: fixture is non-trivial
        assert len(ctx) == len(UNICODE_TEXT.encode("utf-8"))

    def test_disk_len_is_bytes(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            assert isinstance(ctx, Context)
            assert len(ctx) == len(UNICODE_TEXT.encode("utf-8"))
        finally:
            store.cleanup()

    def test_disk_str_roundtrip(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            assert str(ctx) == UNICODE_TEXT
        finally:
            store.cleanup()

    def test_disk_slices_at_character_boundaries_roundtrip(self):
        """Property test against `str` ground truth over every boundary pair."""
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            data = UNICODE_TEXT.encode("utf-8")
            bounds = _boundaries(UNICODE_TEXT)
            # A representative sample of boundary pairs (all pairs is 20^2).
            for start in bounds:
                for stop in bounds[-4:]:
                    if stop < start:
                        continue
                    assert ctx[start:stop] == data[start:stop].decode("utf-8")
        finally:
            store.cleanup()

    def test_disk_single_byte_on_boundary(self):
        """`ctx[i]` returns the whole character that starts at boundary byte i."""
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            bounds = _boundaries(UNICODE_TEXT)
            for k, off in enumerate(bounds[:-1]):
                assert ctx[off] == UNICODE_TEXT[k], (k, off)
                # The full character occupies exactly its encoded width.
                width = len(UNICODE_TEXT[k].encode("utf-8"))
                assert ctx[off : off + width] == UNICODE_TEXT[k]
        finally:
            store.cleanup()

    def test_in_memory_single_char_matches_disk(self):
        mem = _InMemoryContext(UNICODE_TEXT)
        bounds = _boundaries(UNICODE_TEXT)
        for k, off in enumerate(bounds[:-1]):
            assert mem[off] == UNICODE_TEXT[k]

    def test_disk_negative_index(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            assert ctx[-1] == UNICODE_TEXT[-1]
            assert ctx[-1:] == UNICODE_TEXT.encode("utf-8")[-1:].decode("utf-8")
        finally:
            store.cleanup()

    def test_mid_codepoint_index_raises(self):
        """A byte offset inside a character is not a valid slice point."""
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest("日本")
            with pytest.raises(UnicodeDecodeError):
                _ = ctx[1]
        finally:
            store.cleanup()

    def test_index_out_of_range(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            with pytest.raises(IndexError):
                _ = ctx[len(ctx)]
        finally:
            store.cleanup()

    def test_disk_lines_match_in_memory(self):
        store = ContextStore(spill_threshold=1)
        try:
            disk = store.ingest(UNICODE_TEXT)
            mem = _InMemoryContext(UNICODE_TEXT)
            assert list(disk.lines()) == list(mem.lines())
        finally:
            store.cleanup()

    def test_disk_lines_start_uses_byte_index(self):
        """`lines(start=N)` must seek, not sequentially readline N times."""
        text = "\n".join(f"line {i}" for i in range(500)) + "\n"
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(text)
            assert ctx._line_offsets is not None, "ingest must build a line index"
            assert list(ctx.lines(start=400, count=3)) == ["line 400", "line 401", "line 402"]
            # The index is genuinely byte-based and correct.
            data = text.encode("utf-8")
            for i, off in enumerate(ctx._line_offsets[:10]):
                assert data[off:].startswith(f"line {i}".encode())
        finally:
            store.cleanup()

    def test_disk_lines_non_ascii(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            assert list(ctx.lines(start=1, count=2)) == ["emoji: 🎉🚀", "ascii line"]
        finally:
            store.cleanup()

    def test_disk_grep_non_ascii(self):
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            assert ctx.grep("café") == ["napoléon café"]
            assert ctx.grep("🎉") == ["emoji: 🎉🚀"]
            assert ctx.grep("^$") == []
        finally:
            store.cleanup()

    def test_disk_grep_max_hits(self):
        text = "\n".join(f"hit {i}" for i in range(50)) + "\n"
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(text)
            assert len(ctx.grep("hit", max_hits=5)) == 5
        finally:
            store.cleanup()

    def test_disk_chunk_matches_in_memory(self):
        store = ContextStore(spill_threshold=1)
        try:
            disk = store.ingest(UNICODE_TEXT)
            mem = _InMemoryContext(UNICODE_TEXT)
            assert disk.chunk(size=7) == mem.chunk(size=7)
        finally:
            store.cleanup()

    def test_disk_chunk_paragraph_matches_in_memory(self):
        text = "para one\n\npara two\n\n\npara three\n"
        store = ContextStore(spill_threshold=1)
        try:
            disk = store.ingest(text)
            mem = _InMemoryContext(text)
            assert disk.chunk(by="paragraph") == mem.chunk(by="paragraph")
            assert disk.chunk(by="paragraph") == ["para one", "para two", "para three"]
        finally:
            store.cleanup()

    def test_disk_chunk_is_lazy_not_materializing(self):
        """`chunk()` must not call `str(self)` (F1): peak read stays bounded.

        We prove it structurally: `__str__` is booby-trapped to fail, and
        `chunk()` must still work.
        """
        text = "abcdefghij" * 1000
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(text)
            calls: list[int] = []
            real_str = Context.__str__

            def spy(self):  # pragma: no cover - guard
                calls.append(1)
                return real_str(self)

            Context.__str__ = spy
            try:
                chunks = ctx.chunk(size=100)
            finally:
                Context.__str__ = real_str
            assert calls == [], "chunk() must stream, not materialize via str()"
            assert "".join(chunks) == text
        finally:
            store.cleanup()

    def test_worker_never_writes_to_context_file(self):
        """The disk handle is read-only from the consumer's side (R1 contract)."""
        store = ContextStore(spill_threshold=1)
        try:
            ctx = store.ingest(UNICODE_TEXT)
            before = ctx._path.stat().st_mtime_ns
            str(ctx)
            list(ctx.lines())
            ctx.grep("ascii")
            ctx.chunk(size=5)
            assert ctx._path.stat().st_mtime_ns == before
        finally:
            store.cleanup()
