"""Tests for the OCR handler (Gate 2, sub-question B).

The properties that matter, and each is a way this could lie: the kernel never imports a model,
a document whose text layer was empty is *transcribed* rather than skipped forever, the
transcription is indexed as **derived** text so a citation to it is labelled as a transcription
rather than quoted as the document's own words, an empty transcription is a skip with a reason
rather than a cached claim that the document is blank, and a cache hit still puts the words in
the index — a transcription that was paid for and cannot be searched for is the failure this
path exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.mine import (
    DONE,
    EXTRACT_TEXT,
    FAILED,
    IMPLEMENTED_TASKS,
    OCR_PAGE,
    PRIORITY_OCR_PAGE,
    SKIPPED,
    DerivationCache,
    MineStore,
    TaskContext,
    run_queue,
    task_extract_text,
    task_ocr_page,
)
from rlm_kernel.textindex import ORIGIN_CACHE


class FakeOcr:
    """A transcriber that records what it was handed, and can be made to fail."""

    def __init__(self, text: str = "the words on the page", error: Exception | None = None,
                 engine_tag: str = "fake-ocr@box") -> None:
        self.text = text
        self.error = error
        self.engine_tag = engine_tag
        self.calls: list[str] = []

    def __call__(self, mount, rel: str) -> tuple[str, dict]:
        self.calls.append(rel)
        if self.error is not None:
            raise self.error
        return self.text, {"engine": "fake", "dpi": 150, "pages": 3, "pages_failed": 1}


@pytest.fixture
def context(tmp_path: Path) -> tuple[TaskContext, MineStore, CorpusIndex, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "scan.pdf").write_bytes(b"%PDF-1.4 no text layer")
    (corpus / "notes.txt").write_text("plain words", encoding="utf-8")
    mount = LocalTreeMount(corpus)
    index = CorpusIndex.open_for(corpus, tmp_path / "index.sqlite")
    index.build(mount)
    index.classifications().ensure()
    classify_entries(mount, index.classifications())
    store = MineStore(index._conn)  # noqa: SLF001 - the index owns the connection
    store.ensure()
    ctx = TaskContext(mount=mount, store=store, cache_root=tmp_path / "derived")
    return ctx, store, index, corpus


def _with_index(ctx: TaskContext, index: CorpusIndex) -> None:
    text_index = index.text()
    text_index.ensure()
    ctx.text_index = text_index


def _cache_rows(store: MineStore, source_hash: str) -> int:
    row = store._conn.execute(  # noqa: SLF001 - same package
        "SELECT COUNT(*) FROM text_chunks WHERE source_hash = ? AND origin = ?",
        (source_hash, ORIGIN_CACHE),
    ).fetchone()
    return int(row[0]) if row else 0


class TestTheOcrTask:
    def test_a_transcription_is_cached_and_made_findable(self, context) -> None:
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        engine = FakeOcr()
        ctx.engines["ocr"] = engine

        outcome = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")

        assert outcome.state == DONE, outcome
        assert engine.calls == ["scan.pdf"], "the document reaches the engine"
        text, meta = DerivationCache(ctx.cache_root, OCR_PAGE).get(outcome.cache_key or "")
        assert text == "the words on the page"
        assert meta["pages"] == 3 and meta["pages_failed"] == 1, (
            "how many pages failed is part of the record, not a detail")
        assert _cache_rows(store, "hash-scan") > 0, "a transcription nobody can find is useless"

    def test_without_an_engine_it_is_a_skip_with_a_reason(self, context) -> None:
        ctx, _store, _index, _corpus = context
        outcome = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert outcome.state == SKIPPED
        assert outcome.note == "no_ocr_engine"

    def test_a_cache_hit_costs_no_engine_call_and_still_indexes(self, context) -> None:
        """The index can be rebuilt between runs, so a hit repairs it rather than assuming it."""
        ctx, store, index, _corpus = context
        first = FakeOcr()
        ctx.engines["ocr"] = first
        task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")

        ctx.text_index = None  # a window that has no index yet
        ctx.engines["ocr"] = FakeOcr()
        outcome = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert outcome.state == DONE and outcome.note == "cache"
        assert ctx.engines["ocr"].calls == [], "a cache hit must not call the engine"

        _with_index(ctx, index)
        again = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert again.state == DONE
        assert _cache_rows(store, "hash-scan") > 0, "the hit put it back in the index"

    def test_an_empty_transcription_is_a_skip_and_is_not_cached(self, context) -> None:
        """One unreadable scan must not become a permanent claim that the document is blank."""
        ctx, _store, index, _corpus = context
        _with_index(ctx, index)
        ctx.engines["ocr"] = FakeOcr(text="   ")
        outcome = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert outcome.state == SKIPPED
        assert outcome.note == "ocr_no_text"
        assert DerivationCache(ctx.cache_root, OCR_PAGE).stats()["entries"] == 0

    def test_a_failing_transcriber_is_a_row_not_the_end_of_the_pass(self, context) -> None:
        ctx, _store, index, _corpus = context
        _with_index(ctx, index)
        ctx.engines["ocr"] = FakeOcr(error=RuntimeError("the server died"))
        outcome = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert outcome.state == FAILED
        assert outcome.note == "RuntimeError"

    def test_one_engine_is_not_served_another_engines_words(self, context) -> None:
        """The engine identity is part of the key, so a different model re-transcribes."""
        ctx, _store, index, _corpus = context
        _with_index(ctx, index)
        ctx.engines["ocr"] = FakeOcr(text="first model's words", engine_tag="model-a@box")
        first = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        ctx.engines["ocr"] = FakeOcr(text="second model's words", engine_tag="model-b@box")
        second = task_ocr_page(ctx, "scan.pdf", 20, "hash-scan")
        assert first.cache_key != second.cache_key
        text, _meta = DerivationCache(ctx.cache_root, OCR_PAGE).get(second.cache_key or "")
        assert text == "second model's words"


class TestTheQueueReachesIt:
    def test_run_queue_reaches_the_ocr_engine(self, context) -> None:
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        store.enqueue([(b"scan.pdf", OCR_PAGE, PRIORITY_OCR_PAGE)])
        engine = FakeOcr()
        run = run_queue(store=store, conn=store._conn, mount=ctx.mount,  # noqa: SLF001
                        cache_root=ctx.cache_root, tasks=[OCR_PAGE],
                        engines={"ocr": engine}, text_index=ctx.text_index,
                        coverage_scan=False)
        assert run.stats.done == 1
        assert engine.calls == ["scan.pdf"]

    def test_a_document_that_needs_ocr_queues_the_ocr_task(self, context) -> None:
        """The extraction pass leaves the signal; the queue turns it into the next task."""
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        store.enqueue([(b"scan.pdf", EXTRACT_TEXT, 10)])
        # An empty text layer is what the real pdftotext returns for a scan.
        run = run_queue(store=store, conn=store._conn, mount=ctx.mount,  # noqa: SLF001
                        cache_root=ctx.cache_root, tasks=[EXTRACT_TEXT],
                        engines={"pdf": lambda handle: ("", {"engine": "fake"})},
                        text_index=ctx.text_index, coverage_scan=False)
        assert run.stats.skipped == 1
        queued = store.status().get(OCR_PAGE, {}) if isinstance(store.status(), dict) else {}
        claimed = store.claim([OCR_PAGE], limit=5)
        assert [bytes(raw) for raw, _task, _prio in claimed] == [b"scan.pdf"], (
            f"the document must be waiting for OCR, not lost; status={queued}")

    def test_an_ordinary_window_does_not_pick_ocr_up(self, context) -> None:
        """The handler exists, but a default window must not start a model by accident."""
        assert OCR_PAGE not in IMPLEMENTED_TASKS
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        store.enqueue([(b"scan.pdf", OCR_PAGE, PRIORITY_OCR_PAGE)])
        run = run_queue(store=store, conn=store._conn, mount=ctx.mount,  # noqa: SLF001
                        cache_root=ctx.cache_root, tasks=IMPLEMENTED_TASKS,
                        text_index=ctx.text_index, coverage_scan=False)
        assert run.stats.processed == 0, "nothing in the default set may claim an ocr row"

    def test_the_extraction_signal_is_not_queued_for_a_document_that_had_text(
        self, context,
    ) -> None:
        """Only an empty text layer queues OCR — otherwise every PDF would be re-read."""
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        store.enqueue([(b"scan.pdf", EXTRACT_TEXT, 10)])
        run_queue(store=store, conn=store._conn, mount=ctx.mount,  # noqa: SLF001
                  cache_root=ctx.cache_root, tasks=[EXTRACT_TEXT],
                  engines={"pdf": lambda handle: ("words found", {"engine": "fake"})},
                  text_index=ctx.text_index, coverage_scan=False)
        assert store.claim([OCR_PAGE], limit=5) == []

    def test_a_text_file_is_not_queued_for_ocr(self, context) -> None:
        ctx, store, index, _corpus = context
        _with_index(ctx, index)
        ctx.engines["pdf"] = lambda handle: ("", {"engine": "fake"})
        outcome = task_extract_text(ctx, "notes.txt", 11, "hash-notes")
        assert outcome.state == SKIPPED and outcome.note == "no_extraction_engine"
