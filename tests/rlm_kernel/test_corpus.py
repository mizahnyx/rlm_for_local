"""Tests for rlm_kernel.corpus — the path index and the corpus tool handlers (RO3/RO4).

Two properties matter more than the individual behaviours, and each has a test
that would fail if the property were weakened:

* **The index reads names, never contents.** A spy mount whose read methods raise
  is indexed successfully; if any build path opened a file, that test turns red.
* **A model cell cannot reach outside the corpus, and cannot pull a whole file
  into its context.** Containment and the byte caps are tested against real
  targets: the escape test creates the file *outside* the corpus first, so
  containment is the only thing that can refuse it.
"""

from __future__ import annotations

import json
import os
import time
import re
from pathlib import Path

import pytest

from rlm_kernel.corpus import (
    CORPUS_NO_INDEX,
    CORPUS_NO_MATCHES,
    CORPUS_NOT_A_FILE,
    CORPUS_NOT_FOUND,
    CORPUS_REFUSED,
    FIND_LIMIT_MAX,
    CorpusBridge,
    CorpusIndex,
    _bounded,
)
from rlm_kernel.mounts import Entry, LocalTreeMount, ReadOnlyViolation

CORPUS_SRC = Path(__file__).resolve().parents[2] / "src" / "rlm_kernel" / "corpus.py"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small corpus, plus a file *outside* it that a path must not reach."""
    root = tmp_path / "corpus"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "readme.md").write_text("# Notes\n\nSome text.\n", encoding="utf-8")
    (root / "sub" / "page.html").write_text("<html>hi</html>", encoding="utf-8")
    (root / "sub" / "deeper" / "code.py").write_text("print('x')\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01" + b"z" * 500)
    (tmp_path / "outside.txt").write_text("secret, outside the corpus\n", encoding="utf-8")
    return root


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    """Derived state lives outside the corpus, as layer 3 requires."""
    d = tmp_path / "derived"
    d.mkdir()
    return d


@pytest.fixture
def mount(corpus: Path) -> LocalTreeMount:
    return LocalTreeMount(corpus)


@pytest.fixture
def index(mount: LocalTreeMount, corpus: Path, derived: Path) -> CorpusIndex:
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(mount)
    yield idx
    idx.close()


class SpyMount(LocalTreeMount):
    """A mount that records content reads and can be made to forbid them."""

    def __init__(self, root: str | Path, *, forbid_reads: bool = False) -> None:
        super().__init__(root)
        self.read_calls: list[str] = []
        self.forbid_reads = forbid_reads

    def open_readonly(self, rel: str, max_bytes: int | None = None):
        self.read_calls.append(rel)
        if self.forbid_reads:
            raise AssertionError(f"content was read during a name-only operation: {rel}")
        return super().open_readonly(rel, max_bytes=max_bytes)


class TestIndexReadsNamesNotContents:
    def test_build_records_every_entry(self, corpus: Path, derived: Path) -> None:
        mount = LocalTreeMount(corpus)
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        written = idx.build(mount)
        # sub/ + sub/deeper/ + readme.md + page.html + code.py + blob.bin. The
        # mount root itself is not an entry: the index describes what is *in* the
        # corpus, not the corpus as a member of itself.
        assert written == 6
        assert idx.count() == 6
        assert idx.count(kind="file") == 4
        assert idx.count(kind="dir") == 2
        idx.close()

    def test_build_never_opens_a_file(self, corpus: Path, derived: Path) -> None:
        """The index is the cheap half of RO3 because it reads no contents."""
        spy = SpyMount(corpus, forbid_reads=True)
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(spy)
        assert spy.read_calls == []
        assert idx.count(kind="file") == 4
        idx.close()

    def test_build_records_kind_size_and_mtime(self, index: CorpusIndex) -> None:
        entry = index.stat("blob.bin")
        assert entry is not None
        assert entry.kind == "file"
        assert entry.size == 504
        assert entry.mtime > 0

    def test_rebuild_replaces_the_previous_contents(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        assert idx.stat("readme.md") is not None
        (corpus / "readme.md").unlink()
        idx.build(mount)
        assert idx.stat("readme.md") is None
        assert idx.count() == 5
        idx.close()

    def test_the_corpus_module_never_calls_builtin_open(self) -> None:
        """Reads go through the mount, so containment cannot be bypassed."""
        code = CORPUS_SRC.read_text(encoding="utf-8")
        assert not re.search(r"(?<![\w.])open\(", code), (
            "corpus.py uses the builtin open(); reads must go through the mount"
        )

    def test_progress_is_reported_by_interval_not_by_batch(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """`progress_every` counts entries, and the final total always arrives.

        The flag shipped inert the first time: the callback fired once per
        5,000-entry batch whatever the operator asked for, which on a 4.97M-entry
        corpus is a progress report nobody reads.
        """
        seen: list[int] = []
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount, progress=seen.append, progress_every=2, batch_size=1)
        assert seen == [2, 4, 6]  # six entries; never reported twice
        idx.close()

    def test_progress_reports_every_batch_when_ungated(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        seen: list[int] = []
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount, progress=seen.append, batch_size=2)
        assert seen == [2, 4, 6]
        idx.close()


class TestProgressIsCheckpointedAndCompletenessIsRecorded:
    """A killed walk must leave an index that says it is partial.

    The first real build died (non-UTF-8 names) and the whole load was one
    transaction, so it left nothing — and a docstring claiming otherwise. Now
    each batch is committed *and* the running count is written to `meta`, and
    `complete` stays `0` until the walk reaches the end. A partial index that
    says "no matches" would be a confident wrong answer.
    """

    def test_a_finished_build_is_marked_complete(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        assert idx.is_complete() is True
        assert idx.meta()["entries"] == str(idx.count()) == "6"
        idx.close()

    def test_each_batch_is_checkpointed(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """`meta` shows the running count while the walk is still going."""
        snapshots: list[tuple[int, str | None]] = []
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(
            mount,
            batch_size=2,
            progress=lambda n: snapshots.append((n, idx.meta().get("entries"))),
        )
        assert snapshots == [(2, "2"), (4, "4"), (6, "6")]
        idx.close()

    def test_the_secondary_indexes_exist_after_a_bulk_load(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """They are dropped for speed during the load, so they must come back."""
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        names = {
            row[0] for row in idx._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {"entries_parent", "entries_name", "entries_path"} <= names
        idx.close()

    def _partial(self, corpus: Path, derived: Path, mount: LocalTreeMount):
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx._set_meta("complete", "0")  # noqa: SLF001 - what a killed walk leaves
        return idx

    def test_a_partial_index_says_so_in_find(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = self._partial(corpus, derived, mount)
        bridge = CorpusBridge(mount=mount, index=idx)
        out = bridge.handle_find("readme")
        assert out.startswith("[warning: the path index is INCOMPLETE")
        assert "readme.md" in out
        idx.close()

    def test_a_partial_index_says_so_even_when_nothing_matches(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = self._partial(corpus, derived, mount)
        bridge = CorpusBridge(mount=mount, index=idx)
        out = bridge.handle_find("nothing-at-all")
        assert "INCOMPLETE" in out
        assert CORPUS_NO_MATCHES in out
        idx.close()

    def test_a_partial_index_says_so_in_count(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = self._partial(corpus, derived, mount)
        bridge = CorpusBridge(mount=mount, index=idx)
        assert "INCOMPLETE" in bridge.handle_count()
        idx.close()

    def test_an_index_without_the_flag_counts_as_complete(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """An index built before the flag existed is not retroactively partial."""
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx._conn.execute("DELETE FROM meta WHERE key = 'complete'")  # noqa: SLF001
        idx._conn.commit()
        assert idx.is_complete() is True
        idx.close()


class TestTheReadOnlyProof:
    """`scan_newer` — the AGENTS.md 1.8 proof obligation, in the harness.

    The first version of this proof was a hand-typed `find -newer`, which is
    shell work over the corpus (now ruled out) and which reads a clock-skewed
    file — one already dated in the future — as a breach. The scan reports
    aggregates only, and says which of the two it is looking at.
    """

    @pytest.fixture
    def timed_corpus(self, corpus: Path) -> Path:
        """Two files: one old, one with an mtime far in the future."""
        old = 1_600_000_000.0
        for path in corpus.rglob("*"):
            os.utime(path, (old, old))
        return corpus

    def test_nothing_newer_is_a_complete_proof(self, timed_corpus: Path) -> None:
        from rlm_kernel.corpus import scan_newer

        mount = LocalTreeMount(timed_corpus)
        scan = scan_newer(mount, since=1_700_000_000.0, sample=None)
        assert scan.newer == 0
        assert scan.complete is True
        assert scan.scanned == 6
        assert "PROOF: nothing" in scan.verdict()

    def test_a_future_dated_file_is_not_reported_as_a_write(
        self, timed_corpus: Path,
    ) -> None:
        """The false positive: dated in the future, long before this run.

        This is the case the real corpus produced, where a bare `find -newer`
        reported a breach that the harness could show was not one.
        """
        from rlm_kernel.corpus import scan_newer

        future = time.time() + 86400 * 365  # a year after the scan
        os.utime(timed_corpus / "blob.bin", (future, future))
        mount = LocalTreeMount(timed_corpus)
        scan = scan_newer(mount, since=time.time() - 3600, sample=None)
        assert scan.newer == 1
        assert scan.kinds == {"file": 1}
        assert scan.future_dated == 1
        assert scan.in_run_window == 0
        verdict = scan.verdict()
        assert verdict.startswith("NO WRITES BY THIS RUN")
        assert "pre-existing timestamps" in verdict

    def test_a_write_during_the_run_is_reported_as_a_breach(
        self, timed_corpus: Path,
    ) -> None:
        from rlm_kernel.corpus import scan_newer

        started = time.time() - 60
        os.utime(timed_corpus / "readme.md", None)  # now
        mount = LocalTreeMount(timed_corpus)
        scan = scan_newer(mount, since=started, sample=None, run_started=started)
        assert scan.newer == 1
        assert scan.in_run_window == 1
        assert scan.verdict().startswith("BREACH")

    def test_a_marker_set_before_the_run_is_reported_as_such(
        self, timed_corpus: Path,
    ) -> None:
        from rlm_kernel.corpus import scan_newer

        now = time.time()
        scan = scan_newer(
            LocalTreeMount(timed_corpus),
            since=1_500_000_000.0,  # older than the files, so all of them count
            sample=None,
            run_started=now - 60,
        )
        assert scan.stale_dated == 6
        assert scan.in_run_window == 0
        assert scan.verdict().startswith("UNEXPLAINED")

    def test_the_sample_bounds_the_cost_of_a_bad_answer(
        self, timed_corpus: Path,
    ) -> None:
        from rlm_kernel.corpus import scan_newer

        mount = LocalTreeMount(timed_corpus)
        scan = scan_newer(mount, since=0.0, sample=2)
        assert scan.newer == 2
        assert scan.complete is False
        assert scan.scanned == 2, "the scan must stop, not walk the whole tree"

    def test_the_report_carries_no_paths(self, timed_corpus: Path) -> None:
        from rlm_kernel.corpus import scan_newer, verify_report

        mount = LocalTreeMount(timed_corpus)
        scan = scan_newer(mount, since=0.0, sample=None)
        report = verify_report(scan)
        # A proof has to be quotable: aggregates may travel, identifiers may not.
        assert "readme.md" not in report
        assert "blob.bin" not in report
        assert json.loads(report)["newer"] == 6

    def test_the_scan_reads_no_file_contents(self, timed_corpus: Path) -> None:
        from rlm_kernel.corpus import scan_newer

        spy = SpyMount(timed_corpus, forbid_reads=True)
        scan_newer(spy, since=0.0, sample=None)
        assert spy.read_calls == []


class TestTheCorpusDigest:
    """A before/after proof that can clear a corpus with future-dated files."""

    def test_a_walk_and_an_index_of_the_same_corpus_agree(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        from rlm_kernel.corpus import digest_of_index, digest_of_mount

        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        walk = digest_of_mount(mount)
        from_index = digest_of_index(idx)
        assert walk["digest"] == from_index["digest"]
        assert walk["entries"] == from_index["entries"] == 6
        idx.close()

    def test_the_digest_carries_no_paths(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        from rlm_kernel.corpus import digest_of_mount

        report = json.dumps(digest_of_mount(mount))
        assert "readme.md" not in report
        assert "code.py" not in report

    def test_an_unchanged_corpus_compares_equal(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        before = digest_of_mount(mount)
        after = digest_of_mount(mount)
        assert compare_digests(before, after).startswith("PROOF")

    def test_a_changed_size_is_caught(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        before = digest_of_mount(mount)
        (corpus / "readme.md").write_text("# Notes\n\nSome other text.\n", encoding="utf-8")
        after = digest_of_mount(mount)
        verdict = compare_digests(before, after)
        assert verdict.startswith("DIFFERENT")
        assert "directory names" not in verdict

    def test_a_changed_size_with_the_same_mtime_is_caught(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """Isolates size: rewriting a file changes both size and mtime, so the
        mtime is put back and only the size differs."""
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        readme = corpus / "readme.md"
        before = digest_of_mount(mount)
        stat = os.stat(readme)
        readme.write_text("# Notes\n\nSome other text entirely.\n", encoding="utf-8")
        os.utime(readme, (stat.st_atime, stat.st_mtime))
        after = digest_of_mount(mount)
        assert os.path.getsize(readme) != stat.st_size, "fixture bug: size unchanged"
        # Asserted on the digest itself, not on `compare_digests`: the composite
        # verdict also compares the byte total, so it would report a difference
        # even if the digest had stopped covering sizes.
        assert before["digest"] != after["digest"]

    def test_a_changed_mtime_is_caught(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """The case a size-only check would miss — reading does not change it,
        but anything that rewrites a file does."""
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        before = digest_of_mount(mount)
        os.utime(corpus / "readme.md", (1_600_000_000.0, 1_600_000_000.0))
        after = digest_of_mount(mount)
        assert compare_digests(before, after).startswith("DIFFERENT")

    def test_a_new_file_is_caught(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        before = digest_of_mount(mount)
        (corpus / "added.txt").write_text("new\n", encoding="utf-8")
        after = digest_of_mount(mount)
        assert after["entries"] == before["entries"] + 1
        assert compare_digests(before, after).startswith("DIFFERENT")

    def test_directory_timestamps_are_not_part_of_the_digest(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """Measured, not aesthetic: dir mtimes moved between two reads.

        Two consecutive reads of the same directory in this environment returned
        mtimes a millisecond apart while every file's held still, so including a
        directory's timestamp would have produced a false "the corpus changed" —
        the same failure the marker scan was fixed for.
        """
        from rlm_kernel.corpus import compare_digests, digest_of_mount

        before = digest_of_mount(mount)
        os.utime(corpus / "sub", (1_600_000_000.0, 1_600_000_000.0))
        assert compare_digests(before, digest_of_mount(mount)).startswith("PROOF")
        # A *file's* timestamp, by contrast, is exactly the kind of change this
        # digest exists to catch.
        os.utime(corpus / "readme.md", (1_600_000_000.0, 1_600_000_000.0))
        assert compare_digests(before, digest_of_mount(mount)).startswith("DIFFERENT")

    def test_the_digest_reads_no_file_contents(
        self, corpus: Path, derived: Path,
    ) -> None:
        from rlm_kernel.corpus import digest_of_mount

        spy = SpyMount(corpus, forbid_reads=True)
        digest_of_mount(spy)
        assert spy.read_calls == []


class TestSchemaChangesCostARebuild:
    """A layout change drops the table instead of migrating it."""

    def test_a_stale_layout_is_dropped_and_reusable(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        import sqlite3

        stale = derived / "corpus.sqlite"
        conn = sqlite3.connect(str(stale))
        conn.executescript(
            "CREATE TABLE entries (path TEXT PRIMARY KEY, parent TEXT, name TEXT,"
            " kind TEXT, size INTEGER, mtime REAL);"
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO meta VALUES ('schema_version', '1');"
            "INSERT INTO entries VALUES ('old/path.md', 'old', 'path.md', 'file', 1, 1.0);"
        )
        conn.commit()
        conn.close()

        idx = CorpusIndex.open_for(corpus, stale)
        assert idx.count() == 0, "the stale row should not have survived"
        assert idx.build(mount) == 6
        assert idx.stat("readme.md") is not None
        idx.close()

    def test_a_current_layout_keeps_its_rows(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx.close()
        reopened = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        assert reopened.count() == 6
        reopened.close()


class TestDerivedStateStaysOutside:
    def test_an_index_inside_the_corpus_is_refused(self, corpus: Path) -> None:
        with pytest.raises(ReadOnlyViolation):
            CorpusIndex.open_for(corpus, corpus / "corpus.sqlite")

    def test_a_bridge_refuses_an_index_inside_the_corpus(self, corpus: Path) -> None:
        with pytest.raises(ReadOnlyViolation):
            CorpusBridge.open_for(corpus, corpus / "sub" / "corpus.sqlite")


class TestQueries:
    def test_stat_hit_and_miss(self, index: CorpusIndex) -> None:
        assert index.stat("sub/deeper/code.py") is not None
        assert index.stat("sub/deeper/nope.py") is None

    def test_list_dir_is_one_level_only(self, index: CorpusIndex) -> None:
        names = {e.rel for e in index.list_dir("sub")}
        assert names == {"sub/deeper", "sub/page.html"}

    def test_list_dir_at_the_root(self, index: CorpusIndex) -> None:
        names = {e.rel for e in index.list_dir("")}
        assert names == {"sub", "readme.md", "blob.bin"}

    def test_find_matches_names_case_insensitively(self, index: CorpusIndex) -> None:
        assert [e.rel for e in index.find("README")] == ["readme.md"]

    def test_find_with_a_slash_searches_paths(self, index: CorpusIndex) -> None:
        assert {e.rel for e in index.find("sub/deeper")} == {
            "sub/deeper", "sub/deeper/code.py",
        }
        assert {e.rel for e in index.find("deeper/code")} == {"sub/deeper/code.py"}

    def test_find_on_a_bare_name_does_not_match_by_directory(
        self, index: CorpusIndex,
    ) -> None:
        """A file is not matched because a directory above it is named that."""
        assert [e.rel for e in index.find("deeper")] == ["sub/deeper"]

    def test_find_escapes_like_wildcards(self, index: CorpusIndex) -> None:
        """A query of `%` must not be a wildcard that matches the whole corpus."""
        assert index.find("%") == []
        assert index.find("_") == []

    def test_find_filters_by_kind_and_subtree(self, index: CorpusIndex) -> None:
        assert {e.rel for e in index.find("e", kind="dir")} == {"sub/deeper"}
        assert {e.rel for e in index.find("e", under="sub")} == {
            "sub/deeper", "sub/page.html", "sub/deeper/code.py",
        }

    def test_find_honours_the_limit_and_caps_it(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        for n in range(FIND_LIMIT_MAX + 25):
            (corpus / f"match_{n}.txt").write_text("x", encoding="utf-8")
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        assert len(idx.find("match_", limit=5)) == 5
        assert len(idx.find("match_", limit=10_000)) == FIND_LIMIT_MAX
        idx.close()

    def test_count_and_bytes_total(self, index: CorpusIndex, corpus: Path) -> None:
        assert index.count() == 6
        assert index.count(under="sub") == 3
        # Compare against the filesystem rather than against literal lengths: the
        # sizes the index records must be the sizes on disk.
        on_disk = sum(
            os.path.getsize(corpus / rel)
            for rel in ("readme.md", "sub/page.html", "sub/deeper/code.py", "blob.bin")
        )
        assert index.bytes_total() == on_disk
        # Directories have their own sizes; summing them would be meaningless.
        assert index.bytes_total(kind="dir") < index.bytes_total()

    def test_summary_reports_aggregates_only(self, index: CorpusIndex) -> None:
        summary = index.summary()
        assert summary["by_kind"] == {"dir": 2, "file": 4}
        assert summary["entries"] == 6
        # Aggregates may travel; identifiers may not (AGENTS.md 1.9). No path
        # from the corpus may appear in a summary.
        assert "readme.md" not in str(summary)

    def test_bounded_helper_is_not_vacuous(self) -> None:
        assert _bounded(5, 200) == 5
        assert _bounded(10_000, 200) == 200
        assert _bounded(0, 200) == 1
        assert _bounded("junk", 200) == 200


class TestNamesThatAreNotUtf8:
    """The case the first real build died on, 75,000 entries in.

    Linux allows any byte but `/` and NUL in a file name, and a personal backup
    is full of them. Python hands those back with surrogate escapes, and SQLite
    cannot store a lone surrogate in a TEXT column at all — the first build of
    this index died on `UnicodeEncodeError: surrogates not allowed` after the
    ASCII-named unit tests had all passed.

    So a path is stored twice: exactly, as bytes, and as a surrogate-free display
    form. Without the bytes, a damaged name would be visible in a search result
    and permanently unopenable — the worst of both.

    The end-to-end tests need a POSIX filesystem (Windows file names are UTF-16,
    so a name like `caf\\xe9` in latin-1 cannot exist there). The recovery logic
    is therefore also tested against a fake mount that behaves the way POSIX
    does, which keeps that guard inside the mutation table on every platform.
    """

    DAMAGED_RAW = b"sub/caf\xe9-latin1.txt"
    DAMAGED_SHOWN = "sub/caf\ufffd-latin1.txt"
    DAMAGED_SURROGATE = DAMAGED_RAW.decode("utf-8", "surrogateescape")

    @pytest.fixture
    def with_a_damaged_row(self, corpus: Path, derived: Path, mount: LocalTreeMount):
        """An index that also knows about a name that is not valid UTF-8.

        The row is inserted directly because the *walk* cannot produce one on
        Windows; what is under test here is what the index does with such a row.
        """
        from rlm_kernel.corpus import _INSERT, path_bytes, path_text

        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx._conn.execute(  # noqa: SLF001 - the row this platform cannot walk to
            _INSERT,
            (
                path_bytes(self.DAMAGED_SURROGATE),
                path_text(self.DAMAGED_SURROGATE),
                "sub",
                "caf\ufffd-latin1.txt",
                "file",
                31,
                1.0,
            ),
        )
        idx._conn.commit()
        yield idx
        idx.close()

    def test_a_damaged_path_is_displayed_safely_and_stored_exactly(
        self, with_a_damaged_row, tmp_path: Path,
    ) -> None:
        idx = with_a_damaged_row
        hits = idx.find("caf")
        assert len(hits) == 1
        shown = hits[0].rel
        # Displayable: no surrogates, so it can be printed, JSON-round-tripped
        # through a cell, and written to a log.
        assert "\ufffd" in shown
        shown.encode("utf-8")  # raises if a surrogate survived
        # And the exact bytes are still recoverable from the same row.
        assert idx.raw_for(shown) == self.DAMAGED_RAW

    def test_a_damaged_file_can_still_be_read(self, with_a_damaged_row) -> None:
        """The recovery path, against a mount that only answers to exact bytes."""

        class ExactBytesMount(LocalTreeMount):
            """Stands in for POSIX: the display form names nothing on disk."""

            def exists(self, rel: str) -> bool:
                return rel == TestNamesThatAreNotUtf8.DAMAGED_SURROGATE

            def is_contained(self, rel: str) -> bool:
                return True

            def stat(self, rel: str) -> Entry:
                return Entry(rel=rel, kind="file", size=31, mode=0o644, mtime=1.0)

            def read_bytes(self, rel: str, max_bytes: int | None = None) -> bytes:
                return b"damaged name, readable content\n"

        bridge = CorpusBridge(mount=ExactBytesMount(with_a_damaged_row.path.parent),
                              index=with_a_damaged_row)
        out = bridge.handle_read(self.DAMAGED_SHOWN)
        assert "readable content" in out

    def test_an_ambiguous_display_path_is_refused_rather_than_guessed(
        self, with_a_damaged_row,
    ) -> None:
        """Two names can share one display form; guessing one would be wrong."""
        from rlm_kernel.corpus import _INSERT, path_bytes, path_text

        idx = with_a_damaged_row
        for raw in (b"sub/two-\xe9.txt", b"sub/two-\xff.txt"):
            surrogate = raw.decode("utf-8", "surrogateescape")
            idx._conn.execute(  # noqa: SLF001
                _INSERT,
                (path_bytes(surrogate), path_text(surrogate), "sub",
                 "two-\ufffd.txt", "file", 1, 1.0),
            )
        idx._conn.commit()
        hits = idx.find("two-")
        assert len(hits) == 2
        assert hits[0].rel == hits[1].rel  # same display form, different files
        assert idx.raw_for(hits[0].rel) is None, "ambiguity must not be guessed"

    @pytest.mark.skipif(
        os.name != "posix", reason="POSIX-only: byte-exact file names"
    )
    def test_the_build_survives_a_real_damaged_name(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        with open(os.path.join(os.fsencode(corpus), self.DAMAGED_RAW), "wb") as handle:
            handle.write(b"damaged name, readable content\n")
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        assert idx.build(mount) == 7
        assert idx.meta()["paths_not_utf8"] == "1"
        idx.close()

    @pytest.mark.skipif(
        os.name != "posix", reason="POSIX-only: byte-exact file names"
    )
    def test_a_real_damaged_name_reads_end_to_end(
        self, corpus: Path, mount: LocalTreeMount, with_a_damaged_row,
    ) -> None:
        with open(os.path.join(os.fsencode(corpus), self.DAMAGED_RAW), "wb") as handle:
            handle.write(b"damaged name, readable content\n")
        bridge = CorpusBridge(mount=mount, index=with_a_damaged_row)
        out = bridge.handle_read(self.DAMAGED_SHOWN)
        assert "readable content" in out
        assert self.DAMAGED_SHOWN in out


class TestBridgeWithoutAnIndex:
    @pytest.fixture
    def bridge(self, corpus: Path) -> CorpusBridge:
        return CorpusBridge.open_for(corpus)

    def test_find_says_there_is_no_index(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_find("readme") == CORPUS_NO_INDEX

    def test_count_says_there_is_no_index(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_count() == CORPUS_NO_INDEX

    def test_list_still_works_from_the_mount(self, bridge: CorpusBridge) -> None:
        out = bridge.handle_list("sub")
        assert "sub/page.html" in out
        assert "sub/deeper" in out
        # One level only: the nested file must not appear. `iter_entries` would
        # happily walk into `deeper/` and answer a different question.
        assert "code.py" not in out

    def test_stat_and_read_still_work_from_the_mount(self, bridge: CorpusBridge) -> None:
        assert "blob.bin" in bridge.handle_stat("blob.bin")
        assert "Some text." in bridge.handle_read("readme.md")

    def test_find_never_walks_when_the_index_is_missing(self, corpus: Path) -> None:
        """No index means "no answer", not "walk four million files"."""
        spy = SpyMount(corpus)
        bridge = CorpusBridge(mount=spy, index=None)
        walked: list[str] = []
        original = spy.iter_entries

        def counting_iter_entries(*args, **kwargs):
            walked.append("walk")
            return original(*args, **kwargs)

        spy.iter_entries = counting_iter_entries  # type: ignore[method-assign]
        assert bridge.handle_find("readme") == CORPUS_NO_INDEX
        assert walked == []


class TestBridgeHandlerMessages:
    @pytest.fixture
    def bridge(self, index: CorpusIndex, mount: LocalTreeMount) -> CorpusBridge:
        return CorpusBridge(mount=mount, index=index)

    def test_find_formats_paths_and_reports_totals(self, bridge: CorpusBridge) -> None:
        out = bridge.handle_find("page")
        assert "sub/page.html" in out
        assert "[file," in out

    def test_find_with_no_matches(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_find("nothing-like-this") == CORPUS_NO_MATCHES

    def test_find_reports_truncation(self, bridge: CorpusBridge) -> None:
        out = bridge.handle_find("e", limit=1)
        assert "shown" in out

    def test_list_of_a_missing_directory(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_list("nope") == CORPUS_NOT_FOUND.format(rel="nope")

    def test_list_of_a_file_is_an_error(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_list("readme.md") == CORPUS_NOT_FOUND.format(rel="readme.md")

    def test_stat_of_a_missing_path(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_stat("nope.md") == CORPUS_NOT_FOUND.format(rel="nope.md")

    def test_read_reports_a_missing_path(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_read("nope.md") == CORPUS_NOT_FOUND.format(rel="nope.md")

    def test_read_refuses_a_directory(self, bridge: CorpusBridge) -> None:
        assert bridge.handle_read("sub") == CORPUS_NOT_A_FILE.format(rel="sub")

    def test_read_refuses_to_escape_the_corpus(self, bridge: CorpusBridge) -> None:
        """The outside file exists, so containment is the only thing refusing."""
        out = bridge.handle_read("../../outside.txt")
        assert out.startswith("Error")
        assert CORPUS_REFUSED.format(rel="../../outside.txt") in out

    def test_read_is_capped_and_says_so(self, bridge: CorpusBridge) -> None:
        out = bridge.handle_read("blob.bin", max_bytes=32)
        assert "32 of 504 bytes shown" in out
        assert out.count("z") <= 32

    def test_read_cap_cannot_be_raised_past_the_maximum(
        self, corpus: Path, bridge: CorpusBridge,
    ) -> None:
        (corpus / "big.txt").write_text("y" * 300_000, encoding="utf-8")
        out = bridge.handle_read("big.txt", max_bytes=10**9)
        assert "200000 of 300000 bytes shown" in out

    def test_read_replaces_damaged_bytes_instead_of_raising(
        self, bridge: CorpusBridge,
    ) -> None:
        out = bridge.handle_read("blob.bin")
        assert "blob.bin" in out
        assert "\ufffd" in out

    def test_count_reports_files_dirs_and_bytes(self, bridge: CorpusBridge) -> None:
        out = bridge.handle_count()
        assert "6 entries" in out
        assert "4 files" in out
        assert "2 directories" in out


class TestBridgeReadsThroughTheMount:
    def test_every_content_read_goes_through_the_mount(self, corpus: Path) -> None:
        spy = SpyMount(corpus)
        bridge = CorpusBridge(mount=spy, index=None)
        bridge.handle_read("readme.md")
        assert spy.read_calls == ["readme.md"]
