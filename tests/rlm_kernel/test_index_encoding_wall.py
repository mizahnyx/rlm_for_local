"""A name that is not valid UTF-8 can be indexed, and one bad item never ends a pass.

`text_chunks.display` is TEXT, and SQLite encodes TEXT as UTF-8 and refuses a lone
surrogate outright. Python hands back a name that is not valid UTF-8 from `scandir` with
surrogate escapes, so the raw `rel` cannot be stored — which is how the first repair pass
died six seconds in, and how `mine_queue` came to carry a handful of `UnicodeEncodeError`
rows with no tail after them (RO20).

The path index had already solved this: `CorpusIndex` stores a path twice, exactly as
bytes in `raw` and as surrogate-free text in `path`, and `raw_for(display)` recovers the
bytes. These tests pin the same rendering on the chunk side, so a chunk's display is the
string `entries.path` holds and the read path keeps resolving it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex, path_text
from rlm_kernel.mine import INDEX_TEXT, TaskContext, task_index_text
from rlm_kernel.mounts import LocalTreeMount

#: A name with one byte that is not valid UTF-8 — the shortest reproduction of the wall.
BAD_BYTES = b"docs/na\xedve.txt"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "plain.txt").write_text("plain text here\n", encoding="utf-8")
    # Written as bytes: this name is not decodable as UTF-8, which is the point.
    (root / "docs" / b"na\xedve.txt".decode("ascii", "surrogateescape")).write_bytes(
        b"the naive file holds words\n")
    return root


@pytest.fixture
def index(corpus: Path, tmp_path: Path) -> CorpusIndex:
    derived = tmp_path / "derived"
    derived.mkdir()
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(LocalTreeMount(corpus))
    idx.classifications().ensure()
    classify_entries(LocalTreeMount(corpus), idx.classifications())
    idx.text().ensure()
    yield idx
    idx.close()


@pytest.fixture
def ctx(corpus: Path, index: CorpusIndex, tmp_path: Path) -> TaskContext:
    return TaskContext(mount=LocalTreeMount(corpus), store=index.mining(),
                       cache_root=tmp_path / "derived" / "cache", text_index=index.text())


def _outcome_for(ctx: TaskContext, raw: bytes):
    rel = raw.decode("utf-8", "surrogateescape")
    source_hash = index_hash(ctx, raw)
    return task_index_text(ctx, rel, size=100, source_hash=source_hash)


def index_hash(ctx: TaskContext, raw: bytes) -> str:
    row = ctx.text_index._conn.execute(  # noqa: SLF001 - the task's own database
        "SELECT head_hash FROM classification WHERE raw = ?", (raw,)).fetchone()
    return row[0] if row else "h"


class TestANonUtf8NameIsIndexable:
    def test_the_raw_name_cannot_be_stored_as_text(self, ctx: TaskContext) -> None:
        """The wall itself, stated as an assertion so the fix's reason cannot drift."""
        rel = BAD_BYTES.decode("utf-8", "surrogateescape")
        with pytest.raises(UnicodeEncodeError):
            ctx.text_index._conn.execute(  # noqa: SLF001
                "SELECT ? AS probe", (rel,)).fetchone()

    def test_indexing_such_a_file_succeeds_and_is_searchable(
        self, ctx: TaskContext,
    ) -> None:
        outcome = _outcome_for(ctx, BAD_BYTES)
        assert outcome.state == "done", outcome
        hits = ctx.text_index.search("naive", k=5).hits
        assert hits, "the file's words must be findable once it is indexed"
        # `Hit.source` is the *display* (row 2 of the chunk row); the raw bytes live in
        # the table and the path index is what recovers them. The name's damaged byte is
        # not a letter, so the display reads `na\ufffdve` — the honest rendering of it.
        assert hits[0].source == path_text(BAD_BYTES.decode("utf-8", "surrogateescape"))

    def test_the_stored_display_is_the_path_indexes_own_rendering(
        self, ctx: TaskContext, index: CorpusIndex,
    ) -> None:
        """The display must be the string `entries.path` holds, or `raw_for` cannot resolve it.

        This is the property that makes the fix safe rather than merely working: a display
        of its own — `backslashreplace`, say — would be storable but would not match
        `entries.path`, and `read_address` resolves display → bytes through exactly that
        lookup.
        """
        _outcome_for(ctx, BAD_BYTES)
        stored = ctx.text_index._conn.execute(  # noqa: SLF001
            "SELECT DISTINCT display FROM text_chunks WHERE source = ?",
            (BAD_BYTES,)).fetchall()
        assert stored, "the chunk must be stored under the raw bytes"
        display = stored[0][0]
        assert "surrogate" not in repr(display)
        assert display == path_text(BAD_BYTES.decode("utf-8", "surrogateescape")), display
        assert index.raw_for(display) == BAD_BYTES, (
            "the path index must resolve the display back to the exact bytes"
        )

    def test_a_plain_name_is_still_stored_unchanged(self, ctx: TaskContext) -> None:
        """The rendering must be identity for every name that was already storable."""
        _outcome_for(ctx, b"docs/plain.txt")
        stored = ctx.text_index._conn.execute(  # noqa: SLF001
            "SELECT DISTINCT display FROM text_chunks WHERE source = ?",
            (b"docs/plain.txt",)).fetchall()
        assert [r[0] for r in stored] == ["docs/plain.txt"]


class TestOneBadItemNeverEndsAPass:
    def test_an_unstorable_item_is_a_recorded_failure_not_an_abort(
        self, ctx: TaskContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The discipline RO19's repair pass already learned, applied to the writer.

        Simulated by making the store raise where it would for an unencodable value, so
        the assertion is about the task's behaviour rather than about the encoding:
        a failure returns an outcome, and the next item still runs.
        """
        real = ctx.text_index.add_text

        def explode(**kwargs):
            raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")

        monkeypatch.setattr(ctx.text_index, "add_text", explode)
        bad = _outcome_for(ctx, BAD_BYTES)
        assert bad.state == "failed", bad
        assert bad.note == "UnicodeEncodeError", bad

        monkeypatch.setattr(ctx.text_index, "add_text", real)
        good = _outcome_for(ctx, b"docs/plain.txt")
        assert good.state == "done", good

    def test_a_sqlite_error_is_also_a_recorded_failure(
        self, ctx: TaskContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def explode(**kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(ctx.text_index, "add_text", explode)
        outcome = _outcome_for(ctx, BAD_BYTES)
        assert outcome.state == "failed", outcome
        assert outcome.note == "OperationalError", outcome
