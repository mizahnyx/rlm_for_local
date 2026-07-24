"""Tests for context store module."""

from __future__ import annotations

from rlm_local.context_store import ContextStore, _InMemoryContext


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
