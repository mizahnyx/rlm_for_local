"""Tests for rlm_kernel.textindex — words, addressable and citable.

The properties that matter, each with a test that would fail if it were weakened:

* **Text is stored once.** The FTS companion is contentless; a hit carries an
  address, and the text comes back only by re-reading it.
* **A citation re-reads.** The bytes at `#L<start>-<end>` are the chunk, in both
  stores (a file, and a derived cache entry).
* **Diacritics fold** — `Maria` finds `María`, which a Spanish corpus needs.
* **FTS syntax cannot be injected**: a query with `*`, `NEAR`, quotes or `-` is
  terms, not operators.
* **Vendored matches are counted, not hidden silently.**
* **Coverage is reported**, because "no matches" is a lie without it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.textindex import (
    ADDRESS_IN_TEXT_RE,
    DEFAULT_CHUNK_BYTES,
    MAX_CHUNK_BYTES,
    ORIGIN_CACHE,
    TextIndex,
    chunk_ranges,
    coverage_note,
    format_hits,
    is_vendored,
)


class TestAddressShapeInProse:
    """The unanchored address pattern: does an answer cite anything at all?

    `ADDRESS_RE` validates a whole string as an address; this one finds an
    address *inside* prose, which is what measuring a corpus answer's citations
    needs. It is deliberately a shape check, not a validator: whether the address
    resolves is the index's business.
    """

    def test_it_finds_an_address_inside_a_sentence(self) -> None:
        found = ADDRESS_IN_TEXT_RE.findall(
            "The notes agree (notes/song.txt#L120-480) and so does the memo."
        )
        assert found == ["#L120-480"]

    def test_it_finds_several_addresses(self) -> None:
        found = ADDRESS_IN_TEXT_RE.findall(
            "Citations: a.txt#L1-2; b/c.pdf#L300-900"
        )
        assert found == ["#L1-2", "#L300-900"]

    def test_a_hash_L_without_offsets_is_not_an_address(self) -> None:
        assert ADDRESS_IN_TEXT_RE.search("see #L for the line marker") is None
        assert ADDRESS_IN_TEXT_RE.search("no addresses here at all") is None

    def test_a_bare_offset_range_is_not_an_address(self) -> None:
        assert ADDRESS_IN_TEXT_RE.search("L120-480") is None


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(tmp_path / "index.sqlite"))
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE classification (raw BLOB PRIMARY KEY, kind TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE entries (raw BLOB PRIMARY KEY, name TEXT, kind TEXT, size INTEGER)"
    )
    connection.execute(
        "CREATE TABLE mine_queue (raw BLOB, task TEXT, priority INTEGER, state TEXT,"
        " attempts INTEGER, note TEXT, updated_at REAL, PRIMARY KEY (raw, task))"
    )
    yield connection
    connection.close()


@pytest.fixture
def index(conn: sqlite3.Connection) -> TextIndex:
    idx = TextIndex(conn)
    idx.ensure()
    return idx


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "story.txt").write_text(
        "Cuicani was a singer.\n\nThe demo used the Godot engine.\n", encoding="utf-8"
    )
    (root / "docs" / "acentos.txt").write_text(
        "La canción de María y el niño.\n", encoding="utf-8"
    )
    (root / "docs" / "empty.txt").write_text("\n\n   \n", encoding="utf-8")
    return root


class TestChunking:
    def test_empty_input_has_no_chunks(self) -> None:
        assert chunk_ranges(b"") == []

    def test_whitespace_only_input_has_no_chunks(self) -> None:
        assert chunk_ranges(b"\n\n   \n") == []

    def test_offsets_address_the_original_bytes(self) -> None:
        data = b"line one\nline two\n"
        ranges = chunk_ranges(data, target=8, maximum=100)
        assert ranges[0] == (0, 9)
        assert data[ranges[0][0]:ranges[0][1]] == b"line one\n"
        assert data[ranges[-1][0]:ranges[-1][1]] == b"line two\n"

    def test_a_long_line_is_split_at_the_maximum(self) -> None:
        data = b"x" * 25 + b"\n"
        ranges = chunk_ranges(data, target=5, maximum=10)
        assert all(end - start <= 10 for start, end in ranges)
        assert ranges[0] == (0, 10)

    def test_chunks_cover_the_text(self) -> None:
        data = b"alpha\nbeta\ngamma\n"
        ranges = chunk_ranges(data, target=6, maximum=100)
        covered = b"".join(data[s:e] for s, e in ranges)
        assert covered == data

    def test_the_default_target_is_respected(self) -> None:
        data = (b"word " * 400) + b"\n"
        ranges = chunk_ranges(data)
        assert len(ranges) >= 1
        assert all(end - start <= MAX_CHUNK_BYTES for start, end in ranges)


class TestStorage:
    def test_the_fts_table_stores_no_text(self, conn: sqlite3.Connection) -> None:
        """Contentless by construction: the words exist once, in the source."""
        idx = TextIndex(conn)
        idx.ensure()
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'text_fts'"
        ).fetchone()[0]
        assert "content=''" in sql.replace('"', "'")

    def test_indexing_a_file_writes_chunks_and_postings(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        raw = b"docs/story.txt"
        text = (corpus / "docs" / "story.txt").read_bytes()
        written = index.add_text(raw=raw, display="docs/story.txt",
                                 source_hash="hash-story", text=text)
        assert written >= 1
        stats = index.stats()
        assert stats["sources"] == 1
        assert stats["chunks"] == written

    def test_indexing_is_idempotent_unless_replaced(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        raw = b"docs/story.txt"
        text = (corpus / "docs" / "story.txt").read_bytes()
        index.add_text(raw=raw, display="docs/story.txt", source_hash="h", text=text)
        assert index.add_text(raw=raw, display="docs/story.txt", source_hash="h",
                              text=text) == 0
        assert index.has_source(raw) is True

    def test_an_empty_source_indexes_nothing(self, index: TextIndex) -> None:
        assert index.add_text(raw=b"x", display="x", source_hash="h",
                              text=b"\n  \n") == 0
        assert index.stats()["chunks"] == 0


class TestSearchAndRead:
    def _indexed(self, index: TextIndex, corpus: Path) -> None:
        for name in ("story.txt", "acentos.txt"):
            raw = f"docs/{name}".encode()
            index.add_text(raw=raw, display=f"docs/{name}", source_hash=f"h-{name}",
                           text=(corpus / "docs" / name).read_bytes())

    def test_a_needle_is_found(self, index: TextIndex, corpus: Path) -> None:
        self._indexed(index, corpus)
        result = index.search("Godot")
        assert len(result.hits) == 1
        assert result.hits[0].source == "docs/story.txt"

    def test_the_hit_carries_an_address_and_reads_back(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        self._indexed(index, corpus)
        hit = index.search("Cuicani").hits[0]
        assert hit.address.startswith("docs/story.txt#L0-")
        text = index.read(hit, mount=LocalTreeMount(corpus))
        assert "Cuicani" in text
        # The address is the chunk: the same bytes, byte for byte.
        data = (corpus / hit.source).read_bytes()
        assert data[hit.byte_start:hit.byte_end] == text.encode("utf-8", "replace")

    def test_diacritics_fold(self, index: TextIndex, corpus: Path) -> None:
        """A Spanish corpus is searched by people who omit accents."""
        self._indexed(index, corpus)
        assert index.search("Maria").hits, "unaccented query must find María"
        assert index.search("canción").hits
        assert index.search("cancion").hits, "accented text, unaccented query"

    def test_all_terms_must_appear(self, index: TextIndex, corpus: Path) -> None:
        self._indexed(index, corpus)
        assert index.search("Cuicani singer").hits
        assert index.search("Cuicani helicopter").hits == []

    def test_fts_syntax_cannot_be_injected(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        self._indexed(index, corpus)
        for nasty in ('Godot*', 'NEAR(a b)', '"unclosed', '-Godot', 'Godot OR',
                      '^Godot$', 'a AND', '()'):
            result = index.search(nasty)  # must not raise
            assert isinstance(result.hits, list)

    def test_an_empty_query_matches_nothing(self, index: TextIndex) -> None:
        assert index.search("   ").hits == []
        assert index.search("!!!").hits == []

    def test_a_read_without_its_store_fails_loudly(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        self._indexed(index, corpus)
        hit = index.search("Cuicani").hits[0]
        from rlm_kernel.mounts import ReadOnlyViolation

        with pytest.raises(ReadOnlyViolation):
            index.read(hit)

    def test_a_derived_chunk_reads_from_the_cache(
        self, index: TextIndex, tmp_path: Path,
    ) -> None:
        cache_root = tmp_path / "cache"
        entry = cache_root / "extract_text" / "ab" / "abcdef.txt"
        entry.parent.mkdir(parents=True)
        entry.write_text("the engine is Godot\n", encoding="utf-8")
        index.add_text(raw=b"docs/manual.docx", display="docs/manual.docx",
                       source_hash="h", text=entry.read_bytes(),
                       origin=ORIGIN_CACHE, cache_task="extract_text",
                       cache_key="abcdef", derived=True, engine="zip+xml")
        hit = index.search("Godot").hits[0]
        assert hit.derived is True
        assert hit.engine == "zip+xml"
        assert "Godot" in index.read(hit, cache_root=cache_root)

    def test_a_missing_cache_entry_is_an_error_not_an_empty_string(
        self, index: TextIndex, tmp_path: Path,
    ) -> None:
        from rlm_kernel.mounts import ReadOnlyViolation

        index.add_text(raw=b"x", display="x", source_hash="h", text=b"needle here\n",
                       origin=ORIGIN_CACHE, cache_task="extract_text",
                       cache_key="gone", derived=True, engine="zip+xml")
        hit = index.search("needle").hits[0]
        with pytest.raises(ReadOnlyViolation):
            index.read(hit, cache_root=tmp_path / "cache")


class TestVendoredRanking:
    def test_vendored_paths_are_recognised(self) -> None:
        assert is_vendored("proj/node_modules/left-pad/index.js")
        assert is_vendored("py/site-packages/foo/bar.py")
        assert not is_vendored("docs/notes.md")

    def test_vendored_matches_are_filtered_and_counted(
        self, index: TextIndex,
    ) -> None:
        index.add_text(raw=b"own.txt", display="own.txt", source_hash="h1",
                       text=b"the engine is Godot\n")
        index.add_text(raw=b"node_modules/lib.js", display="node_modules/lib.js",
                       source_hash="h2", text=b"the engine is Godot\n")
        result = index.search("Godot")
        assert [h.source for h in result.hits] == ["own.txt"]
        assert result.hidden_vendored == 1

    def test_the_filter_can_be_lifted(self, index: TextIndex) -> None:
        index.add_text(raw=b"node_modules/lib.js", display="node_modules/lib.js",
                       source_hash="h2", text=b"the engine is Godot\n")
        result = index.search("Godot", include_vendored=True)
        assert [h.source for h in result.hits] == ["node_modules/lib.js"]
        assert result.hidden_vendored == 0

    def test_the_counter_is_reported_in_the_output(self, index: TextIndex) -> None:
        index.add_text(raw=b"own.txt", display="own.txt", source_hash="h1",
                       text=b"needle\n")
        index.add_text(raw=b"vendor/x.txt", display="vendor/x.txt", source_hash="h2",
                       text=b"needle\n")
        result = index.search("needle")
        text = format_hits(result, ["needle"])
        assert "hidden by the vendored filter" in text


class TestCoverage:
    def test_coverage_counts_against_the_map(
        self, index: TextIndex, conn: sqlite3.Connection,
    ) -> None:
        conn.executemany(
            "INSERT INTO classification (raw, kind) VALUES (?, ?)",
            [(b"a", "text"), (b"b", "text"), (b"c", "text"), (b"d", "document")],
        )
        conn.execute(
            "INSERT INTO mine_queue VALUES (?, 'extract_text', 10, 'done', 1, NULL, 0)",
            (b"d",),
        )
        conn.commit()
        index.add_text(raw=b"a", display="a", source_hash="h", text=b"one\n")
        coverage = index.coverage()
        assert coverage["text_files_in_map"] == 3
        assert coverage["sources_indexed"] == 1
        assert abs(coverage["text_coverage"] - 1 / 3) < 1e-9
        assert coverage["documents_extracted"] == 1

    def test_an_incomplete_search_says_so(self, index: TextIndex,
                                         conn: sqlite3.Connection) -> None:
        conn.executemany(
            "INSERT INTO classification (raw, kind) VALUES (?, ?)",
            [(b"a", "text"), (b"b", "text")],
        )
        conn.commit()
        index.add_text(raw=b"a", display="a", source_hash="h", text=b"one\n")
        note = coverage_note(index.coverage())
        assert "coverage:" in note
        assert "50.0%" in note

    def test_a_complete_index_adds_no_note(self, index: TextIndex,
                                          conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO classification (raw, kind) VALUES (?, 'text')", (b"a",))
        conn.commit()
        index.add_text(raw=b"a", display="a", source_hash="h", text=b"one\n")
        assert coverage_note(index.coverage()) == ""

    def test_missing_tables_do_not_crash_coverage(self, tmp_path: Path) -> None:
        bare = sqlite3.connect(str(tmp_path / "bare.sqlite"))
        bare.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        idx = TextIndex(bare)
        idx.ensure()
        coverage = idx.coverage()
        assert coverage["text_files_in_map"] == 0
        bare.close()


class TestReset:
    def test_reset_clears_both_tables(self, index: TextIndex) -> None:
        index.add_text(raw=b"a", display="a", source_hash="h", text=b"needle\n")
        assert index.stats()["chunks"] == 1
        index.reset()
        assert index.stats()["chunks"] == 0
        assert index.search("needle").hits == []
