"""Tests for rlm_kernel.mine — the mining queue, its caches and its windows.

The properties that matter, each with a test that would fail if it were weakened:

* **The map decides the work, idempotently.** Planning twice enqueues nothing new.
* **Derivations are keyed by content hash**, so duplicates are mined once — and a
  cache entry names no file.
* **A run is a window.** A budget, a deadline and a pause file each stop it
  cleanly between items, and every finished item is committed, so a stopped run
  keeps its work.
* **A damaged container is a row, not a crash.**
* **Only the mount reads the corpus.** A source scan enforces it.
* **Nothing that leaves the machine names a file.**
"""

from __future__ import annotations

import io
import os
import re
import time
import zipfile
from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.mine import (
    DONE,
    EXTRACT_TEXT,
    FAILED,
    IMPLEMENTED_TASKS,
    LIST_ARCHIVE,
    MAX_MEMBERS,
    PENDING,
    SKIPPED,
    DerivationCache,
    MineStore,
    acquire_lock,
    format_status,
    plan_queue,
    refresh_lock,
    release_lock,
    run_queue,
)
from rlm_kernel.mounts import LocalTreeMount

MINE_SRC = Path(__file__).resolve().parents[2] / "src" / "rlm_kernel" / "mine.py"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "report.pdf").write_bytes(b"%PDF-1.4\nnot really a pdf\n")
    (root / "docs" / "notes.txt").write_text("plain text\n", encoding="utf-8")
    (root / "docs" / "manual.docx").write_bytes(_docx_bytes("The engine is Godot."))
    with zipfile.ZipFile(root / "bundle.zip", "w") as archive:
        archive.writestr("inner/one.txt", "x" * 10)
        archive.writestr("inner/two.txt", "y" * 20)
    (root / "broken.zip").write_bytes(b"PK\x03\x04 this is not a zip")
    # Text wearing a document extension: the sniff says text, and the *sniff*
    # wins for choosing a task — a name must not send a text file to the PDF
    # engine, and it must not hide one from the archive lister either.
    (root / "misnamed.pdf").write_text("this is plain text\n", encoding="utf-8")
    return root


def _code_only(path: Path) -> str:
    """Source with comments and string literals removed, whitespace collapsed.

    The same helper the mount tests use, for the same reason: a guard that reads
    prose is a guard that gets satisfied by editing prose.
    """
    import io as _io
    import tokenize

    skip = {tokenize.COMMENT, tokenize.STRING}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        kind = getattr(tokenize, name, None)
        if kind is not None:
            skip.add(kind)
    reader = _io.StringIO(path.read_text(encoding="utf-8")).readline
    kept = [tok.string for tok in tokenize.generate_tokens(reader) if tok.type not in skip]
    # Whitespace is *removed*, not collapsed to spaces: with spaces between
    # tokens, `open (` never matches a pattern looking for `open(`, and the guard
    # silently becomes vacuous. (The sanity assertion in the test below exists
    # because that is exactly what happened the first time.)
    return re.sub(r"\s+", "", " ".join(kept))


def _refresh_map(index: CorpusIndex, mount: LocalTreeMount) -> None:
    """Rebuild the path index and classify anything new.

    Tests that create a file *after* the fixture built the map must call this:
    the mining tasks look up content hashes in `classification`, and a file with
    no row there is skipped as `unclassified` — which is correct behaviour and a
    confusing way to fail a test.
    """
    index.build(mount)
    index.classifications().ensure()
    classify_entries(mount, index.classifications())


def _docx_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document><w:t>{text}</w:t></w:document>',
        )
    return buffer.getvalue()


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    d = tmp_path / "derived"
    d.mkdir()
    return d


@pytest.fixture
def mount(corpus: Path) -> LocalTreeMount:
    return LocalTreeMount(corpus)


@pytest.fixture
def index(corpus: Path, derived: Path, mount: LocalTreeMount) -> CorpusIndex:
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(mount)
    idx.classifications().ensure()
    classify_entries(mount, idx.classifications())
    yield idx
    idx.close()


@pytest.fixture
def store(index: CorpusIndex) -> MineStore:
    s = MineStore(index._conn)  # noqa: SLF001 - same package, the index owns the file
    s.ensure()
    return s


class TestPlanningFromTheMap:
    def test_it_queues_documents_and_archives_only(self, store: MineStore) -> None:
        plan = plan_queue(store)
        tasks = store.status()["queued_by_task"]
        # report.pdf + manual.docx need extraction; bundle.zip and broken.zip
        # need listing; notes.txt does not (its bytes are already the text).
        assert tasks[EXTRACT_TEXT][PENDING] == 2
        assert tasks[LIST_ARCHIVE][PENDING] == 2
        assert plan["enqueued"] == 4

    def test_planning_twice_enqueues_nothing_new(self, store: MineStore) -> None:
        plan_queue(store)
        again = plan_queue(store)
        assert again["enqueued"] == 0

    def test_priority_puts_documents_before_archives(self, store: MineStore) -> None:
        plan_queue(store)
        claimed = store.claim(IMPLEMENTED_TASKS, limit=1)
        assert claimed[0][1] == EXTRACT_TEXT

    def test_a_text_file_named_like_a_document_is_not_sent_to_the_engine(
        self, store: MineStore, index: CorpusIndex,
    ) -> None:
        """The sniff decides the task; the name only decides *which* engine.

        `misnamed.pdf` holds plain text. Queueing it for PDF extraction would
        hand a text file to poppler and record a pointless failure; the queue
        therefore requires a document-ish *kind* as well as a document-ish name.
        """
        plan_queue(store)
        queued = {
            bytes(raw) for (raw,) in store._conn.execute(  # noqa: SLF001
                "SELECT raw FROM mine_queue WHERE task = ?", (EXTRACT_TEXT,)
            )
        }
        assert b"misnamed.pdf" not in queued
        assert b"docs/report.pdf" in queued


class TestTheQueue:
    def test_finish_records_state_and_note(self, store: MineStore) -> None:
        store.enqueue([(b"a", LIST_ARCHIVE, 30)])
        store.finish(b"a", LIST_ARCHIVE, DONE, "cache")
        row = store._conn.execute(  # noqa: SLF001
            "SELECT state, note, attempts FROM mine_queue"
        ).fetchone()
        assert row == (DONE, "cache", 1)

    def test_failed_rows_can_be_retried(self, store: MineStore) -> None:
        store.enqueue([(b"a", LIST_ARCHIVE, 30), (b"b", LIST_ARCHIVE, 30)])
        store.finish(b"a", LIST_ARCHIVE, FAILED, "BadZipFile")
        assert store.reset_failed(LIST_ARCHIVE) == 1
        assert len(store.claim([LIST_ARCHIVE], limit=5)) == 2

    def test_status_is_aggregates_only(self, store: MineStore) -> None:
        store.enqueue([(b"/private/name.txt", LIST_ARCHIVE, 30)])
        text = format_status(store.status())
        assert "name.txt" not in text
        assert "private" not in text
        assert "list_archive" in text


class TestTheDerivationCache:
    def test_key_is_content_addressed(self, derived: Path) -> None:
        cache = DerivationCache(derived, EXTRACT_TEXT)
        assert cache.key("abc") == cache.key("abc")
        assert cache.key("abc") != cache.key("abd")

    def test_round_trip(self, derived: Path) -> None:
        cache = DerivationCache(derived, EXTRACT_TEXT)
        key = cache.key("hash1")
        assert cache.has(key) is False
        cache.put(key, "the words", {"engine": "test"})
        text, meta = cache.get(key) or ("", {})
        assert text == "the words"
        assert meta["engine"] == "test"
        assert meta["chars"] == 9
        assert cache.has(key) is True

    def test_it_names_no_file(self, derived: Path) -> None:
        """The cache must not carry the source path: duplicates share entries."""
        cache = DerivationCache(derived, EXTRACT_TEXT)
        key = cache.key("hash1")
        cache.put(key, "text", {"engine": "test"})
        blob = "".join(p.read_text(encoding="utf-8")
                       for p in derived.rglob("*") if p.is_file())
        assert "corpus" not in blob
        assert "report.pdf" not in blob

    def test_identical_sources_share_one_entry(self, derived: Path) -> None:
        cache = DerivationCache(derived, EXTRACT_TEXT)
        assert cache.key("same") == cache.key("same")
        assert cache.stats()["entries"] == 0


class TestArchiveListing:
    def test_a_zip_is_listed_and_its_members_recorded(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE])
        assert run.stats.done == 1
        assert store.member_count() == 2
        names = {row[0] for row in store._conn.execute(  # noqa: SLF001
            "SELECT member FROM archive_members"
        )}
        assert names == {"inner/one.txt", "inner/two.txt"}

    def test_a_damaged_container_is_a_row_not_a_crash(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"broken.zip", LIST_ARCHIVE, 30)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE])
        assert run.stats.failed == 1
        note = store._conn.execute(  # noqa: SLF001
            "SELECT note FROM mine_queue WHERE raw = ?", (b"broken.zip",)
        ).fetchone()[0]
        assert note == "BadZipFile"

    def test_the_handler_reports_rather_than_raises(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        """The handler's own contract, tested directly.

        Through `run_queue` this looked covered, but it was not: the worker has a
        defensive `except` of its own, so removing the handler's catch changed
        nothing observable — belt and braces hiding a vacuous test. Called
        directly, the handler must *return* a failed outcome.
        """
        from rlm_kernel.mine import TaskContext, task_list_archive

        ctx = TaskContext(mount=mount, store=store, cache_root=derived)
        outcome = task_list_archive(ctx, "broken.zip", 32, "hash-broken")
        assert outcome.state == FAILED
        assert outcome.note == "BadZipFile"

    def test_a_second_run_is_a_cache_hit(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                  cache_root=derived, tasks=[LIST_ARCHIVE])
        store.reset_failed()
        store._conn.execute("UPDATE mine_queue SET state = ?", (PENDING,))  # noqa: SLF001
        store._conn.commit()  # noqa: SLF001
        second = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                           cache_root=derived, tasks=[LIST_ARCHIVE])
        assert second.stats.cache_hits == 1

    def test_the_listing_is_bounded(
        self, corpus: Path, index: CorpusIndex, store: MineStore,
        mount: LocalTreeMount, derived: Path,
    ) -> None:
        with zipfile.ZipFile(corpus / "many.zip", "w") as archive:
            for n in range(MAX_MEMBERS + 50):
                archive.writestr(f"m{n}.txt", "x")
        _refresh_map(index, mount)
        store.enqueue([(b"many.zip", LIST_ARCHIVE, 30)])
        run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                  cache_root=derived, tasks=[LIST_ARCHIVE])
        assert store.member_count() == MAX_MEMBERS

    def test_members_are_read_through_the_mount(
        self, store: MineStore, corpus: Path, derived: Path,
    ) -> None:
        class Spy(LocalTreeMount):
            def __init__(self, root: Path) -> None:
                super().__init__(root)
                self.opened: list[str] = []

            def open_readonly(self, rel: str, max_bytes: int | None = None):
                self.opened.append(rel)
                return super().open_readonly(rel, max_bytes=max_bytes)

        spy = Spy(corpus)
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        run_queue(store=store, conn=store._conn, mount=spy,  # noqa: SLF001
                  cache_root=derived, tasks=[LIST_ARCHIVE])
        assert spy.opened == ["bundle.zip"]


class TestTextExtraction:
    def test_a_docx_yields_its_words(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"docs/manual.docx", EXTRACT_TEXT, 10)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[EXTRACT_TEXT])
        assert run.stats.done == 1
        cache = DerivationCache(derived, EXTRACT_TEXT)
        entry = next(derived.joinpath(EXTRACT_TEXT).rglob("*.txt"))
        assert "Godot" in entry.read_text(encoding="utf-8")
        assert cache.stats()["entries"] == 1

    def test_a_pdf_without_a_text_layer_asks_for_ocr(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        """An empty text layer is not a failure — it is the signal to OCR.

        The engine is injected so the test does not depend on poppler being
        installed; the real `pdftotext` path is exercised on the corpus machine.
        """
        from rlm_kernel.mine import TaskContext, task_extract_text

        ctx = TaskContext(mount=mount, store=store, cache_root=derived,
                          engines={"pdf": lambda handle: ("", {"engine": "fake"})})
        outcome = task_extract_text(ctx, "docs/report.pdf", 20, "hash-pdf")
        assert outcome.state == SKIPPED
        assert outcome.note == "needs_ocr"

    def test_a_pdf_with_a_text_layer_is_cached(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        from rlm_kernel.mine import TaskContext, task_extract_text

        ctx = TaskContext(mount=mount, store=store, cache_root=derived,
                          engines={"pdf": lambda handle: ("pages of words",
                                                          {"engine": "fake"})})
        outcome = task_extract_text(ctx, "docs/report.pdf", 20, "hash-pdf")
        assert outcome.state == DONE
        text, meta = DerivationCache(derived, EXTRACT_TEXT).get(outcome.cache_key or "")
        assert text == "pages of words"
        assert meta["engine"] == "fake"

    def test_an_unsupported_format_is_skipped_with_a_reason(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        from rlm_kernel.mine import TaskContext, task_extract_text

        ctx = TaskContext(mount=mount, store=store, cache_root=derived)
        outcome = task_extract_text(ctx, "docs/notes.txt", 20, "hash-txt")
        assert outcome.state == SKIPPED
        assert outcome.note == "no_extraction_engine"


class TestWindows:
    def _queue_ten(self, store: MineStore) -> None:
        store.enqueue([(f"bundle.zip".encode(), LIST_ARCHIVE, 30)])

    def test_a_budget_stops_the_run(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        """The budget is checked *between* items, so a run ends with work left."""
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30), (b"broken.zip", LIST_ARCHIVE, 30)])
        # now() is called once for the start, then once per loop iteration.
        ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE],
                        budget_seconds=1.0, now=lambda: next(ticks, 999.0))
        assert run.stop_reason == "budget"
        assert run.stats.processed == 1, "it should stop after the item in flight"
        remaining = store.claim([LIST_ARCHIVE], limit=5)
        assert len(remaining) == 1, "the second item must remain queued"

    def test_a_pause_file_stops_the_run(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        pause = derived / "mine.pause"
        pause.write_text("", encoding="utf-8")
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE], pause_file=pause)
        assert run.stop_reason == "paused"
        assert run.stats.processed == 0

    def test_max_items_bounds_the_run(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30), (b"broken.zip", LIST_ARCHIVE, 30)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE], max_items=1)
        assert run.stats.processed == 1
        assert run.stop_reason == "max_items"

    def test_a_deadline_stops_the_run(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=[LIST_ARCHIVE],
                        deadline=time.time() - 1)
        assert run.stop_reason == "deadline"

    def test_finished_items_are_committed_as_they_land(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        """A run stopped by an exception keeps everything already finished."""
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30), (b"broken.zip", LIST_ARCHIVE, 30)])
        seen: list[str] = []
        original = store.finish

        def exploding_finish(raw, task, state, note=None):
            original(raw, task, state, note)
            seen.append(state)
            raise RuntimeError("worker killed")

        store.finish = exploding_finish  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                      cache_root=derived, tasks=[LIST_ARCHIVE])
        rows = dict(store._conn.execute(  # noqa: SLF001
            "SELECT raw, state FROM mine_queue"
        ))
        # Claim order is (priority, raw), so `broken.zip` was the item in flight:
        # its row is committed, and the item that never started is still pending.
        assert rows[b"broken.zip"] in (DONE, FAILED)
        assert rows[b"bundle.zip"] == PENDING
        assert seen, "nothing was committed before the failure"

    def test_unimplemented_tasks_are_skipped_not_faked(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        store.enqueue([(b"bundle.zip", "vlm_describe", 60)])
        run = run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                        cache_root=derived, tasks=["vlm_describe"])
        assert run.stats.skipped == 1
        note = store._conn.execute(  # noqa: SLF001
            "SELECT note FROM mine_queue WHERE task = 'vlm_describe'"
        ).fetchone()[0]
        assert note == "no_handler"


class TestTheLock:
    """The lock holds by heartbeat, and it must never probe a process.

    The first version decided liveness with `os.kill(pid, 0)`. On POSIX that is a
    harmless probe; on Windows Python's `os.kill` calls `TerminateProcess`, so the
    probe **kills the process it asks about** — it killed this test run and the
    harness running it. These tests pin the heartbeat, and a source scan below
    makes the idiom impossible to reintroduce.
    """

    def test_a_live_worker_holds_the_lock(self, derived: Path) -> None:
        lock = derived / "mine.lock"
        assert acquire_lock(lock) is not None
        assert acquire_lock(lock) is None
        release_lock(lock)
        assert acquire_lock(lock) is not None
        release_lock(lock)

    def test_a_stale_lock_is_taken_over(self, derived: Path) -> None:
        lock = derived / "mine.lock"
        lock.write_text("999999999 0\n", encoding="utf-8")
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        assert acquire_lock(lock) is not None
        release_lock(lock)

    def test_a_garbage_lock_is_taken_over_once_it_is_old(self, derived: Path) -> None:
        lock = derived / "mine.lock"
        lock.write_text("not a pid\n", encoding="utf-8")
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        assert acquire_lock(lock) is not None
        release_lock(lock)

    def test_a_fresh_garbage_lock_is_still_respected(self, derived: Path) -> None:
        """The safe direction: never steal a lock we merely fail to understand."""
        lock = derived / "mine.lock"
        lock.write_text("not a pid\n", encoding="utf-8")
        assert acquire_lock(lock) is None

    def test_the_heartbeat_keeps_a_long_run_alive(self, derived: Path) -> None:
        lock = derived / "mine.lock"
        assert acquire_lock(lock) is not None
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        # Stale: the next worker takes it over rather than waiting forever.
        assert acquire_lock(lock) is not None
        assert acquire_lock(lock) is None, "a freshly taken lock must be respected"
        release_lock(lock)
        assert acquire_lock(lock) is not None
        refresh_lock(lock)
        assert acquire_lock(lock) is None, "a refreshed lock must not be stolen"
        release_lock(lock)

    def test_the_worker_refreshes_the_lock_as_it_works(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        """A long run must not look stale to the next worker."""
        lock = derived / "mine.lock"
        assert acquire_lock(lock) is not None
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        store.enqueue([(b"bundle.zip", LIST_ARCHIVE, 30)])
        run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                  cache_root=derived, tasks=[LIST_ARCHIVE], lock_file=lock)
        assert time.time() - lock.stat().st_mtime < 5, "the lock was not refreshed"
        assert acquire_lock(lock) is None, "a refreshed lock must not be stolen"
        release_lock(lock)


class TestReadOnlyByConstruction:
    def test_the_module_never_calls_builtin_open(self) -> None:
        """Reads must go through the mount, as everywhere else in the harness.

        Tokenised, not grepped — this module's own docstring says "a test scans
        this module for a builtin `open(`", and a guard that trips on its own
        documentation gets fixed by editing the documentation.
        """
        code = _code_only(MINE_SRC)
        assert not re.search(r"(?<![\w.])open\(", code), (
            "mine.py uses the builtin open(); reads must go through the mount"
        )
        # Sanity: the scan sees real calls, so a zero result means something.
        assert "tarfile.open(" in code, "the scanner lost the code it should read"

    def test_the_module_never_probes_a_process(self) -> None:
        """`os.kill(pid, 0)` terminates a process on Windows, not probes it.

        This is the guard for the worst bug this project has shipped: a liveness
        check that killed the harness. Any process-signalling call here is either
        a repeat of it or a new way to lose the machine, and neither belongs in a
        mining worker.

        The source is tokenised, not grepped: the incident is *documented* in this
        module's docstrings, so a raw scan would trip on the prose that explains
        it. And the tokenised text has its whitespace **removed** — the first
        version of this test joined tokens with spaces, which turns `os.kill` into
        `os . kill` and made the check silently vacuous. The mutation table caught
        that; the sanity assertion below is what stops it happening again.
        """
        code = _code_only(MINE_SRC)
        for forbidden in ("os.kill", "signal.SIG", "TerminateProcess",
                          "taskkill", "Popen"):
            assert forbidden not in code, (
                f"mine.py calls {forbidden!r}; liveness is decided by the lock's "
                "heartbeat, never by signalling a process"
            )
        # Sanity: a dotted call that *is* in the module must survive the scan, or
        # the check above proves nothing.
        assert "zipfile.ZipFile(" in code, "the scanner lost the code it reads"

    def test_it_does_not_write_into_the_corpus(
        self, store: MineStore, mount: LocalTreeMount, derived: Path,
    ) -> None:
        before = {p: p.stat().st_mtime for p in Path(mount.root).rglob("*")}
        plan_queue(store)
        run_queue(store=store, conn=store._conn, mount=mount,  # noqa: SLF001
                  cache_root=derived, tasks=IMPLEMENTED_TASKS)
        after = {p: p.stat().st_mtime for p in Path(mount.root).rglob("*")}
        assert before == after

    def test_the_derived_cache_is_not_the_corpus(self, derived: Path) -> None:
        """Layer 3: an assertion, not a convention."""
        from rlm_kernel.mounts import ReadOnlyViolation, assert_derived_outside_corpus

        with pytest.raises(ReadOnlyViolation):
            assert_derived_outside_corpus(derived / "corpus", derived)
