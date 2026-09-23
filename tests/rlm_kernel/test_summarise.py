"""Tests for the summarise handler (RO6).

The properties that matter: the kernel never imports a model, a cache hit costs nothing, a
document too short to need a description is skipped with a reason, and a summary is indexed as
*derived* so it can be found — a description nobody can search for is a description nobody reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.mine import (
    DONE,
    FAILED,
    MAX_SUMMARY_INPUT_BYTES,
    MIN_SUMMARY_INPUT_BYTES,
    SKIPPED,
    SUMMARISE,
    DerivationCache,
    MineStore,
    TaskContext,
    task_summarise,
)


class Fake:
    """A summariser that records what it was asked, and can be made to fail."""

    def __init__(self, reply: str = "A short description of the document.", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def __call__(self, text: str, max_tokens: int) -> tuple[str, dict]:
        self.calls.append((len(text), max_tokens))
        if self.error is not None:
            raise self.error
        return self.reply, {"engine": "fake", "input_chars": len(text)}


@pytest.fixture
def context(tmp_path: Path) -> tuple[TaskContext, MineStore, CorpusIndex, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "long.txt").write_text("A sentence. " * 200, encoding="utf-8")
    (corpus / "tiny.txt").write_text("short", encoding="utf-8")
    mount = LocalTreeMount(corpus)
    index = CorpusIndex.open_for(corpus, tmp_path / "index.sqlite")
    index.build(mount)
    store = MineStore(index._conn)  # noqa: SLF001 - the index owns the connection
    store.ensure()
    ctx = TaskContext(mount=mount, store=store, cache_root=tmp_path / "derived")
    return ctx, store, index, corpus


class TestItSummarisesByValue:
    def test_a_document_is_summarised_cached_and_made_findable(self, context) -> None:
        from rlm_kernel.textindex import TextIndex

        ctx, store, index, _corpus = context
        engine = Fake()
        ctx.engines["summarise"] = engine
        ctx.text_index = TextIndex(store._conn)  # noqa: SLF001
        ctx.text_index.ensure()
        rel = "long.txt"
        size = (Path(ctx.mount.root) / rel).stat().st_size
        outcome = task_summarise(ctx, rel, size, "hash-long")
        assert outcome.state == DONE, outcome.note
        assert len(engine.calls) == 1
        assert engine.calls[0][1] == 400, "the output bound travels to the model"
        cache = DerivationCache(ctx.cache_root, SUMMARISE)
        key = cache.key("hash-long", params="summary<=v1,400tok")
        assert cache.has(key), "the summary must be cached"
        text, _meta = cache.get(key)
        assert "short description" in text
        rows = index._conn.execute(  # noqa: SLF001
            "SELECT origin, derived FROM text_chunks WHERE source_hash = ?", ("hash-long",)
        ).fetchall()
        assert rows and rows[0][1] == 1, "the summary is indexed as derived text"

    def test_a_cache_hit_costs_no_model_call(self, context) -> None:
        ctx, _store, _index, corpus = context
        engine = Fake()
        ctx.engines["summarise"] = engine
        size = (corpus / "long.txt").stat().st_size
        first = task_summarise(ctx, "long.txt", size, "hash-long")
        second = task_summarise(ctx, "long.txt", size, "hash-long")
        assert first.state == DONE and second.state == DONE
        assert second.note == "cache"
        assert len(engine.calls) == 1, "a summary already paid for is not paid for again"

    def test_a_short_document_is_skipped_with_a_reason(self, context) -> None:
        """It *is* its own description; ten minutes of this machine would be absurd."""
        ctx, _store, _index, corpus = context
        ctx.engines["summarise"] = Fake()
        outcome = task_summarise(ctx, "tiny.txt", (corpus / "tiny.txt").stat().st_size, "h-tiny")
        assert outcome.state == SKIPPED
        assert outcome.note == "too_short_to_summarise"

    def test_without_an_engine_it_is_a_skip_with_a_reason(self, context) -> None:
        """The kernel has no model, by construction: no engine means a reason, not a fabrication."""
        ctx, _store, _index, corpus = context
        outcome = task_summarise(ctx, "long.txt", (corpus / "long.txt").stat().st_size, "h")
        assert outcome.state == SKIPPED
        assert outcome.note == "no_summarise_engine"

    def test_a_failing_model_is_a_row_not_the_end_of_the_pass(self, context) -> None:
        ctx, _store, _index, corpus = context
        ctx.engines["summarise"] = Fake(error=RuntimeError("router down"))
        outcome = task_summarise(ctx, "long.txt", (corpus / "long.txt").stat().st_size, "h")
        assert outcome.state == FAILED
        assert outcome.note == "RuntimeError"

    def test_an_empty_reply_is_not_cached_as_a_summary(self, context) -> None:
        ctx, _store, _index, corpus = context
        ctx.engines["summarise"] = Fake(reply="   ")
        outcome = task_summarise(ctx, "long.txt", (corpus / "long.txt").stat().st_size, "h")
        assert outcome.state == SKIPPED
        assert outcome.note == "empty_summary"

    def test_the_input_is_capped(self, context) -> None:
        ctx, _store, _index, corpus = context
        engine = Fake()
        ctx.engines["summarise"] = engine
        (corpus / "huge.txt").write_text("word " * 40_000, encoding="utf-8")
        size = (corpus / "huge.txt").stat().st_size
        assert size > MAX_SUMMARY_INPUT_BYTES
        task_summarise(ctx, "huge.txt", size, "h-huge")
        assert engine.calls[0][0] <= MAX_SUMMARY_INPUT_BYTES + 8, (
            "a document is shown up to the cap, and no more"
        )


class TestTheBoundsAreThePoint:
    def test_the_short_floor_is_below_the_input_cap(self) -> None:
        assert 0 < MIN_SUMMARY_INPUT_BYTES < MAX_SUMMARY_INPUT_BYTES
