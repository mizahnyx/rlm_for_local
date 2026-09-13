"""Tests for rlm_kernel.classify — the Stage 1 content sniffing pass (RO1/RO3).

What matters here beyond the individual verdicts:

* **The sniff reads a bounded head, never a whole file.** A 200 GiB VM image must
  cost what a 4-byte file costs, or the pass cannot run over 1,010 GiB.
* **It is resumable.** An interrupted pass loses at most one batch; a second run
  classifies nothing new, and `--redo` is the only way to redo it.
* **Its output carries no paths**, because that output is meant to be quotable
  off the corpus machine (AGENTS.md §1.9).
* **Opening the index must not disturb the path index.** The path index rebuilds
  by dropping its table on a version change (95 minutes on the real corpus), so
  the classification table carries its own version key and an ordinary open must
  leave every existing row alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from rlm_kernel.classify import (
    ARCHIVE,
    BINARY,
    DATABASE,
    DOCUMENT,
    EMPTY,
    MEDIA,
    SNIFF_VERSION,
    TEXT,
    UNREADABLE,
    classify_entries,
    format_report,
    head_hash,
    sniff,
)
from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.mounts import LocalTreeMount


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """One file per sniffing branch, plus an unreadable-to-the-mount path."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "ascii.txt").write_text("plain Spanish text\n", encoding="utf-8")
    (root / "acentos.txt").write_text("canción, niñez, corazón\n", encoding="utf-8")
    (root / "legacy.txt").write_bytes("canci\u00f3n en latin-1\n".encode("cp1252"))
    (root / "archive.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    (root / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    (root / "song.mp3").write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 40)
    (root / "data.sqlite").write_bytes(b"SQLite format 3\x00" + b"\x00" * 40)
    (root / "manual.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 40)
    (root / "blob.bin").write_bytes(bytes(range(256)) * 8)
    (root / "empty.txt").write_bytes(b"")
    (root / "twins-a.txt").write_text("identical content\n", encoding="utf-8")
    (root / "twins-b.txt").write_text("identical content\n", encoding="utf-8")
    return root


@pytest.fixture
def mount(corpus: Path) -> LocalTreeMount:
    return LocalTreeMount(corpus)


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    d = tmp_path / "derived"
    d.mkdir()
    return d


@pytest.fixture
def index(corpus: Path, derived: Path, mount: LocalTreeMount) -> CorpusIndex:
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(mount)
    yield idx
    idx.close()


class TestTheSniffItself:
    def test_utf8_text(self) -> None:
        assert sniff(b"hola, mundo\n") == sniff(b"hola, mundo\n")
        result = sniff(b"hola, mundo\n")
        assert result.kind == TEXT
        assert result.encoding == "utf-8"

    def test_a_bom_does_not_stop_text(self) -> None:
        assert sniff("\ufeff# heading\n".encode("utf-8")).kind == TEXT

    def test_cp1252_text_is_text_not_binary(self) -> None:
        """The owner's corpus is Spanish; old CDs carry single-byte encodings."""
        result = sniff("canción de niñez\n".encode("cp1252"))
        assert result.kind == TEXT
        assert result.encoding in {"cp1252", "latin-1"}

    def test_empty_is_empty_not_binary(self) -> None:
        assert sniff(b"").kind == EMPTY

    def test_strong_magics_win_over_the_text_test(self) -> None:
        assert sniff(b"PK\x03\x04rest of a zip").kind == ARCHIVE
        assert sniff(b"%PDF-1.7\nbinary follows").kind == DOCUMENT
        assert sniff(b"SQLite format 3\x00").kind == DATABASE
        assert sniff(b"\x89PNG\r\n\x1a\n...").kind == MEDIA

    def test_a_word_starting_with_BM_is_still_text(self) -> None:
        """The weak magic is consulted only after the text test fails."""
        assert sniff(b"BMW cars are fine\n").kind == TEXT

    def test_binary_with_nul_bytes(self) -> None:
        assert sniff(b"\x00\x01\x02\x03\xff\xfe").kind == BINARY

    def test_high_entropy_without_nul_is_binary(self) -> None:
        assert sniff(bytes(range(256)) * 4).kind == BINARY

    def test_a_text_file_with_a_few_control_bytes_is_still_text(self) -> None:
        """A stray BEL or ESC must not turn a log into a binary.

        The ratio alone would: two escapes in 32 bytes is 6% of the file. It must
        also stay labelled UTF-8 — falling through to the single-byte branch would
        return `cp1252`, which is a wrong answer of exactly the kind that makes a
        classifier untrustworthy.
        """
        result = sniff(b"log line one\n\x07log line two\n\x1b[0m\n")
        assert result.kind == TEXT
        assert result.encoding == "utf-8"


class TestTheHeadHash:
    def test_identical_heads_with_identical_sizes_match(self) -> None:
        assert head_hash(b"same", 4) == head_hash(b"same", 4)

    def test_the_size_is_part_of_the_hash(self) -> None:
        """Two files sharing a prefix but differing in length are not duplicates."""
        assert head_hash(b"prefix", 6) != head_hash(b"prefix", 600_000)

    def test_it_is_a_sha256(self) -> None:
        expected = hashlib.sha256(b"x" + b"\x00" + b"1").hexdigest()
        assert head_hash(b"x", 1) == expected


class TestTheClassifyPass:
    def test_it_classifies_every_file(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        stats = classify_entries(mount, table)
        assert stats.classified == 12
        assert stats.files_seen == 12
        report = table.report()
        assert report["classified"] == 12
        kinds = {k: v["files"] for k, v in report["by_kind"].items()}
        assert kinds[ARCHIVE] == 1
        assert kinds[MEDIA] == 2
        assert kinds[DATABASE] == 1
        assert kinds[DOCUMENT] == 1
        assert kinds[BINARY] == 1
        assert kinds[EMPTY] == 1
        assert kinds[TEXT] == 5

    def test_the_read_is_bounded_to_the_head(
        self, corpus: Path, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        """A big file must cost what a small one costs."""
        (corpus / "huge.bin").write_bytes(b"\x00" * (5 * 1024 * 1024))
        index.build(mount)
        table = index.classifications()
        table.ensure()

        class Spy(LocalTreeMount):
            def __init__(self, root: Path) -> None:
                super().__init__(root)
                self.requested: list[int | None] = []

            def open_readonly(self, rel: str, max_bytes: int | None = None):
                self.requested.append(max_bytes)
                return super().open_readonly(rel, max_bytes=max_bytes)

        spy = Spy(corpus)
        classify_entries(spy, table, sniff_bytes=4096, hash_bytes=8192)
        assert spy.requested and all(
            n is not None and n <= 8192 for n in spy.requested
        ), spy.requested

    def test_a_second_run_classifies_nothing_new(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        again = classify_entries(mount, table)
        assert again.files_seen == 0
        assert table.count() == 12

    def test_redo_reclassifies_everything(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        redone = classify_entries(mount, table, redo=True)
        assert redone.files_seen == 12

    def test_limit_pilots_without_claiming_completeness(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        stats = classify_entries(mount, table, limit=3)
        assert stats.files_seen == 3
        assert table.count() == 3

    def test_it_reports_unreadable_paths_without_aborting(
        self, corpus: Path, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        """The path index can name a file the mount can no longer open."""
        table = index.classifications()
        table.ensure()
        (corpus / "vanishing.txt").write_text("here\n", encoding="utf-8")
        index.build(mount)
        (corpus / "vanishing.txt").unlink()
        stats = classify_entries(mount, table)
        assert stats.unreadable == 1
        report = table.report()
        assert report["by_kind"][UNREADABLE]["files"] == 1

    def test_hashes_find_duplicates(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        report = table.report()
        assert report["distinct_hashes"] < report["hashed_files"]
        assert report["duplicate_bytes"] > 0

    def test_hash_none_skips_hashing(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table, hash_mode="none")
        report = table.report()
        assert report["hashed_files"] == 0

    def test_progress_is_reported(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        seen: list[int] = []
        classify_entries(mount, table, batch_size=4,
                         progress=lambda s: seen.append(s.files_seen))
        assert seen[-1] == 12


class TestTheReport:
    def test_it_contains_no_paths(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        """No path may appear anywhere in the output, checked against every one.

        Asserting on a hand-written list of names was vacuous: a mutation that
        added `SELECT path ... LIMIT 1` to the report survived it, because the row
        it happened to return was not in the list. The guard now enumerates the
        index.
        """
        table = index.classifications()
        table.ensure()
        stats = classify_entries(mount, table)
        report = table.report()
        text = format_report(report, total_files=12, total_bytes=1000, stats=stats)
        blob = json.dumps(report) + text
        paths = [str(row[0]) for row in index._conn.execute(  # noqa: SLF001
            "SELECT path FROM entries"
        )]
        assert paths, "fixture bug: no paths to check against"
        for path in paths:
            assert path not in blob, f"the report leaked a path: {path}"
        assert str(index.path) not in blob

    def test_it_reports_the_numbers_that_decide_scope(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        report = table.report()
        assert report["by_kind"][TEXT]["bytes"] > 0
        assert report["by_encoding"]["utf-8"] >= 2
        assert report["classification_version"] == SNIFF_VERSION
        assert "unreadable" in report


class TestOpeningTheIndexLeavesThePathIndexAlone:
    def test_classifying_does_not_truncate_the_path_index(
        self, mount: LocalTreeMount, index: CorpusIndex,
    ) -> None:
        """The classification table is additive and must stay that way."""
        before = index.count()
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        assert index.count() == before
        assert index.is_complete() is True

    def test_a_reopened_index_keeps_both_tables(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx.classifications().ensure()
        classify_entries(mount, idx.classifications())
        idx.close()
        reopened = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        assert reopened.count() == 12
        assert reopened.classifications().count() == 12
        reopened.close()
