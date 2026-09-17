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

from rlm_kernel.classify import classify_entries
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
from rlm_kernel.textindex import ORIGIN_CACHE

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

    def test_a_directory_mtime_change_does_not_move_the_digest(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """Directory mtimes are excluded, and that is a decision with evidence.

        Directory mtimes move on their own here: on the real corpus two
        consecutive reads of the same directory differed by a millisecond, which
        made a digest that covered them report a breach that never happened. So
        the digest covers files and zeroes the directory term.

        This is asserted directly, by moving a directory's mtime and requiring the
        digest not to move, rather than by comparing a walk against an index
        build: that comparison is only *usually* sensitive to the mutation that
        puts directory mtimes back (it depends on whether the two reads happened
        to see the same timestamp), and a guard whose verdict is a coin flip is
        not a guard.
        """
        from rlm_kernel.corpus import digest_of_mount

        notes = corpus / "sub"
        assert notes.is_dir(), "fixture bug: no directory to move"
        before = digest_of_mount(mount)

        stat = os.stat(notes)
        os.utime(notes, (stat.st_atime, stat.st_mtime - 3600))
        assert os.stat(notes).st_mtime != stat.st_mtime, "fixture bug: mtime unmoved"

        after = digest_of_mount(mount)
        assert before["digest"] == after["digest"]

    def test_a_file_mtime_change_moves_the_digest(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """The control for the test above: the digest *can* see an mtime move.

        Without this, a digest that covered nothing at all would satisfy "a
        directory mtime change does not move it".
        """
        from rlm_kernel.corpus import digest_of_mount

        readme = corpus / "readme.md"
        before = digest_of_mount(mount)

        stat = os.stat(readme)
        os.utime(readme, (stat.st_atime, stat.st_mtime - 3600))

        after = digest_of_mount(mount)
        assert before["digest"] != after["digest"]

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

    def test_a_timestamp_that_does_not_fit_in_nanoseconds_is_handled(self) -> None:
        """The real corpus broke the first encoding: `mtime x 1e9` > int64.

        Its future-dated entries reach far enough ahead that the nanosecond
        integer overflowed, which killed a full walk. The digest packs the double
        instead, so no timestamp can fail it.
        """
        from rlm_kernel.corpus import _entry_digest

        for mtime in (1e11, 1e18, -1e11, 0.0, float("inf")):
            entry = Entry(rel="x", kind="file", size=1, mode=0, mtime=mtime)
            assert isinstance(_entry_digest(entry), int)

    def test_a_walk_and_an_index_agree_on_names_that_are_not_utf8(
        self, corpus: Path, derived: Path, mount: LocalTreeMount,
    ) -> None:
        """The case that would have made this comparison useless on the real corpus.

        The index stores a surrogate-free display form as well as the exact bytes;
        a walk returns the exact bytes. Digesting the display column would have
        made the two sides disagree on the 98 damaged names and agree on the other
        4.97M — a proof that looks like it works and can never come out clean.
        """
        from rlm_kernel.corpus import _INSERT, digest_of_index, digest_of_mount
        from rlm_kernel.corpus import path_bytes, path_text

        surrogate = b"sub/caf\xe9.txt".decode("utf-8", "surrogateescape")
        idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        idx.build(mount)
        idx._conn.execute(  # noqa: SLF001
            _INSERT,
            (path_bytes(surrogate), path_text(surrogate), "sub",
             "caf\ufffd.txt", "file", 31, 1.0),
        )
        idx._conn.commit()
        # The walk side cannot see this row (no such file exists here), so the
        # comparison is done on the same row read both ways: from `raw`.
        assert digest_of_index(idx)["entries"] == 7
        plain = digest_of_index(idx)
        assert plain["kinds"] == {"dir": 2, "file": 5}
        idx.close()
        # And the walk of the files that DO exist agrees with the index's view of
        # them, entry for entry, for the ASCII ones. The damaged row is covered by
        # `digest_of_index` reading `raw`, which is what the assertion above pins.
        rebuilt = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
        rebuilt.build(mount)
        assert digest_of_mount(mount)["digest"] == digest_of_index(rebuilt)["digest"]
        rebuilt.close()

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


class TestBridgeContentSearch:
    """`handle_search` — the helper that reaches words, not names."""

    @pytest.fixture
    def searched(self, corpus: Path, index: CorpusIndex,
                 mount: LocalTreeMount, tmp_path: Path) -> CorpusBridge:
        (corpus / "sub" / "story.txt").write_text(
            "Cuicani sang at the festival.\n", encoding="utf-8"
        )
        index.build(mount)
        table = index.classifications()
        table.ensure()
        classify_entries(mount, table)
        text_index = index.text()
        text_index.ensure()
        text_index.add_text(
            raw=b"sub/story.txt", display="sub/story.txt", source_hash="h1",
            text=(corpus / "sub" / "story.txt").read_bytes(),
        )
        cache_root = tmp_path / "cache"
        (cache_root / "extract_text" / "ab").mkdir(parents=True)
        (cache_root / "extract_text" / "ab" / "abcdef.txt").write_text(
            "the demo used the Godot engine\n", encoding="utf-8"
        )
        text_index.add_text(
            raw=b"sub/deep.docx", display="sub/deep.docx", source_hash="h2",
            text=b"the demo used the Godot engine\n", origin=ORIGIN_CACHE,
            cache_task="extract_text", cache_key="abcdef", derived=True,
            engine="zip+xml",
        )
        return CorpusBridge(mount=mount, index=index, cache_root=cache_root)

    def test_a_word_is_found_with_its_address(self, searched: CorpusBridge) -> None:
        hits = searched.handle_search("Cuicani")
        assert isinstance(hits, list), "a list of hits must be a list"
        assert len(hits) == 1
        assert "sub/story.txt#L" in hits[0]
        assert "Cuicani sang" in hits[0]

    def test_the_result_is_indexable_like_a_list(self, searched: CorpusBridge) -> None:
        """The shape the first live run assumed and the interface denied it."""
        hits = searched.handle_search("Cuicani")
        assert len(hits) >= 1
        assert hits[0].startswith("sub/story.txt#L")
        assert hits[:1] == [hits[0]]

    def test_coverage_is_available_on_its_own(self, searched: CorpusBridge) -> None:
        assert "coverage:" in searched.handle_coverage()

    def test_a_missing_word_carries_the_coverage_in_its_only_element(
        self, searched: CorpusBridge,
    ) -> None:
        """No hits is the case where a false negative would look like proof."""
        hits = searched.handle_search("helicopter")
        assert len(hits) == 1
        assert "no text matches" in hits[0]
        assert "coverage:" in hits[0]

    def test_every_hit_carries_how_much_of_the_question_it_covers(
        self, searched: CorpusBridge,
    ) -> None:
        """The signal that makes "these are not an answer" sayable (RO4).

        Measured against the run's *question*, because the FTS expression is an AND:
        every hit contains every search word, so a query-relative number would read
        `strong` by construction. Here the question has six content words and the
        passage holds one of them — which is the live shape of the failure.
        """
        searched.question = ("Who ratified the Zxqvarn Protocol at the festival?")
        hits = searched.handle_search("festival")
        assert hits, "the fixture has a festival passage"
        # Four content words in the question, one of them in the passage.
        assert "covers 1/4" in hits[0]
        assert "(weak)" in hits[0], hits[0]

    def test_a_hit_sharing_no_question_word_is_none_not_weak(
        self, searched: CorpusBridge,
    ) -> None:
        """The strongest form of "not an answer": the passage has none of the
        question's content words, and the label says so rather than shrugging."""
        searched.question = "Who ratified the Zxqvarn Protocol?"
        hits = searched.handle_search("festival")
        assert "covers 0/3" in hits[0]
        assert "(none)" in hits[0], hits[0]

    def test_without_a_question_no_coverage_is_claimed(
        self, searched: CorpusBridge,
    ) -> None:
        """No question means nothing to compare against, and the honest label is
        none rather than a number that is true only because of the AND."""
        assert searched.question is None
        hits = searched.handle_search("Godot")
        assert hits and "question's words" not in hits[0]

    def test_a_miss_states_the_match_quality_as_none_not_just_no_matches(
        self, searched: CorpusBridge,
    ) -> None:
        searched.question = "Who ratified the Zxqvarn Protocol?"
        hits = searched.handle_search("zxqvarn orbital")
        assert len(hits) == 1
        assert "no text matches" in hits[0]
        assert "match quality" in hits[0]

    def test_the_hits_list_is_not_padded_with_a_footer(
        self, searched: CorpusBridge,
    ) -> None:
        """`len(hits)` is the contract; a footer would corrupt it — which is the
        exact failure the first live run had (it indexed into a formatted string)."""
        hits = searched.handle_search("Godot")
        assert len(hits) == 1, "one hit, one element"
        assert hits[0].startswith("sub/deep.docx#L")

    def test_derived_text_is_labelled_with_its_engine(
        self, searched: CorpusBridge,
    ) -> None:
        hits = searched.handle_search("Godot")
        assert any("derived:zip+xml" in hit for hit in hits)

    def test_a_hit_whose_text_is_gone_says_so_rather_than_pretending(
        self, corpus: Path, index: CorpusIndex, mount: LocalTreeMount,
    ) -> None:
        text_index = index.text()
        text_index.ensure()
        text_index.add_text(
            raw=b"gone.docx", display="gone.docx", source_hash="h",
            text=b"a missing artifact\n", origin=ORIGIN_CACHE,
            cache_task="extract_text", cache_key="dead", derived=True,
            engine="zip+xml",
        )
        bridge = CorpusBridge(mount=mount, index=index, cache_root=None)
        assert "cannot re-read" in "\n".join(bridge.handle_search("missing"))

    def test_archives_listed_for_search_are_searched_by_find(
        self, corpus: Path, index: CorpusIndex, mount: LocalTreeMount,
    ) -> None:
        """A member cannot be read directly, so a hit must name its container."""
        store = index.mining()
        store.ensure()
        store.add_members(b"bundle.zip", [("inner/treasure.txt", 12, "file")])
        out = CorpusBridge(mount=mount, index=index).handle_find("treasure")
        assert "bundle.zip!inner/treasure.txt" in out
        assert "inside bundle.zip" in out

    def test_a_corpus_without_mining_tables_still_searches_paths(
        self, corpus: Path, index: CorpusIndex, mount: LocalTreeMount,
    ) -> None:
        assert "readme.md" in CorpusBridge(mount=mount, index=index).handle_find("readme")


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


class TestSearchCoverageIsPublishedNotCounted:
    """A corpus search must not count the chunk table (RO4, 2026-09-15).

    On the real index `search` takes 0.2 s and `COUNT(*) FROM text_chunks` takes
    972 s, so a search that computes its own coverage cannot finish inside the
    REPL's 120 s cell limit — which is exactly how a live run burned two hours and
    produced no answer. These tests pin the interface: coverage is *read* from a
    published snapshot, and when nothing has published one the search says
    "unknown" rather than scanning or claiming zero.
    """

    @pytest.fixture
    def searched_index(self, corpus: Path, index: CorpusIndex,
                       mount: LocalTreeMount) -> CorpusBridge:
        (corpus / "sub" / "story.txt").write_text(
            "Cuicani sang at the festival.\n", encoding="utf-8")
        index.build(mount)
        text_index = index.text()
        text_index.ensure()
        text_index.add_text(
            raw=b"sub/story.txt", display="sub/story.txt", source_hash="h1",
            text=(corpus / "sub" / "story.txt").read_bytes(),
        )
        return CorpusBridge(mount=mount, index=index)

    @staticmethod
    def _counting_scans(statements: list[str]) -> list[str]:
        """The scans a search must never run, whatever else it does.

        `MATCH` is excluded on purpose: counting how many of *this query's* FTS
        matches were hidden by the vendored filter is bounded by the match set and
        is part of producing the answer, whereas `COUNT(*) FROM text_chunks` with
        no match predicate is a full scan of 25M rows. The distinction is the
        point of the guard, so it is written down rather than left to a regex that
        happens to pass.
        """
        scans = []
        for statement in statements:
            if re.search(r"MATCH", statement, re.IGNORECASE):
                continue
            if re.search(r"COUNT\s*\(", statement, re.IGNORECASE) and re.search(
                r"text_chunks|classification|mine_queue", statement, re.IGNORECASE
            ):
                scans.append(statement)
        return scans

    def test_a_miss_quotes_the_published_snapshot(
        self, searched_index: CorpusBridge, index: CorpusIndex,
    ) -> None:
        text_index = index.text()
        text_index.publish_coverage({
            "sources_indexed": 5, "text_files_in_map": 100, "text_coverage": 0.05,
            "documents_extracted": 1, "documents_needing_ocr": 0,
            "chunks": 12,
        })
        hits = searched_index.handle_search("zzznotaword")
        assert len(hits) == 1
        assert "5 sources indexed" in hits[0]

    def test_a_miss_without_a_snapshot_says_unknown_and_does_not_scan(
        self, searched_index: CorpusBridge, index: CorpusIndex,
    ) -> None:
        conn = index._conn  # noqa: SLF001 - the bridge's own connection
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            hits = searched_index.handle_search("zzznotaword")
        finally:
            conn.set_trace_callback(None)

        assert len(hits) == 1
        assert "unknown" in hits[0].lower()
        assert "0 sources" not in hits[0], "unknown is not zero"
        assert self._counting_scans(statements) == [], (
            "a search must never count text_chunks/classification/mine_queue"
        )

    def test_coverage_without_a_snapshot_says_unknown_and_does_not_scan(
        self, searched_index: CorpusBridge, index: CorpusIndex,
    ) -> None:
        conn = index._conn  # noqa: SLF001
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            line = searched_index.handle_coverage()
        finally:
            conn.set_trace_callback(None)

        assert "unknown" in line.lower()
        assert self._counting_scans(statements) == []

    def test_coverage_with_a_snapshot_quotes_it(
        self, searched_index: CorpusBridge, index: CorpusIndex,
    ) -> None:
        index.text().publish_coverage({
            "sources_indexed": 42, "text_files_in_map": 1000,
            "text_coverage": 0.042, "documents_extracted": 3,
            "documents_needing_ocr": 1, "chunks": 99,
        })
        line = searched_index.handle_coverage()
        assert "42 sources indexed" in line
        assert "1 awaiting OCR" in line


class TestReadingOneAddressDoesNotScanEveryChunk:
    """One passage must not cost the whole chunk table (2026-09-17).

    Measured on the complete index: `corpus_search` took **43 s** for one query,
    while `corpus_read(<address>)` **did not return in 150 s** — `find_chunk`
    filtered on `text_chunks.display`, which has no index, so reading one passage
    scanned 29 015 791 rows. The indexed column is `source` (the exact path bytes),
    and the path index resolves display → bytes in one lookup (`entries_path`).

    This is not cosmetic. `corpus_read` is how a citation is checked and how a
    model turns a hit into evidence it can quote; at full scale it exceeded the
    120 s REPL cell limit, so a run could search but not open what it found. Same
    family as CL6: a per-item operation paying a whole-table price.
    """

    @pytest.fixture
    def read_bridge(self, corpus: Path, index: CorpusIndex,
                    mount: LocalTreeMount) -> CorpusBridge:
        (corpus / "sub" / "story.txt").write_text(
            "Cuicani sang at the festival.\n", encoding="utf-8")
        index.build(mount)
        text_index = index.text()
        text_index.ensure()
        text_index.add_text(
            raw=b"sub/story.txt", display="sub/story.txt", source_hash="h1",
            text=(corpus / "sub" / "story.txt").read_bytes(),
        )
        return CorpusBridge(mount=mount, index=index)

    def test_the_lookup_filters_on_the_indexed_column(
        self, read_bridge: CorpusBridge,
    ) -> None:
        # The address comes from a search, not from arithmetic on the file's size:
        # a chunk's stored range is the index's business, and a hard-coded guess
        # tests the guess rather than the read.
        address = read_bridge.handle_search("Cuicani", k=1)[0].split()[0]
        conn = read_bridge.index._conn  # noqa: SLF001 - the bridge's own connection
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            text = read_bridge.handle_read(address)
        finally:
            conn.set_trace_callback(None)

        lookups = [s for s in statements
                   if "text_chunks" in s and s.lstrip().upper().startswith("SELECT")]
        assert lookups, "an address read must look its chunk up"
        # The trace callback renders bound parameters inline, so the assertion is on
        # the WHERE clause rather than on the placeholder text.
        assert all("WHERE source" in s for s in lookups), (
            "an address read must filter on the indexed column (`source`), never "
            "on `display`, which has no index and costs a full scan"
        )
        assert not any("WHERE display" in s for s in lookups)
        assert text and "Cuicani" in text

    def test_a_display_that_is_not_a_path_in_the_index_still_resolves(
        self, read_bridge: CorpusBridge,
    ) -> None:
        """A container member has no `entries` row, so the fallback must still find it.

        `arch.zip!member.txt` is a real address shape for text extracted out of an
        archive, and it names no file on disk. There are no exact bytes to resolve,
        so the lookup falls back to the display filter — slow, bounded to the
        derivations that need it, and *correct*, which is the part that matters
        here. (Reading such a chunk goes on through the derivation cache, which
        needs a cache entry this fixture does not create; the lookup is the claim.)
        """
        read_bridge.index.text().add_text(
            raw=b"arch.zip", display="arch.zip!member.txt", source_hash="h2",
            text=b"derived body text here",
        )
        address = read_bridge.handle_search("derived", k=1)[0].split()[0]
        assert address.startswith("arch.zip!member.txt#L"), address
        hit = read_bridge.index.text().find_chunk(address)
        assert hit is not None, "the display fallback must still resolve the chunk"
