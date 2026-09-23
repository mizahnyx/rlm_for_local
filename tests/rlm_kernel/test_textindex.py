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

import random
import sqlite3
import time
from pathlib import Path

import pytest

from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.textindex import (
    ADDRESS_IN_TEXT_RE,
    DEFAULT_CHUNK_BYTES,
    MAX_CHUNK_BYTES,
    NON_PROSE_EXTENSIONS,
    ORIGIN_CACHE,
    PROSE_FLOOR,
    TextIndex,
    chunk_ranges,
    coverage_note,
    format_hits,
    is_vendored,
    non_prose_extension,
    prose_score,
)


PROSE = (
    "La casa estaba vacía desde hacía años, pero nadie se atrevía a decirlo en voz alta. "
    "Mi abuela contaba que en el verano de 1974 alguien dejó una carta sobre la mesa del "
    "comedor y que, desde entonces, la puerta permaneció cerrada. Cuando por fin entramos, "
    "encontramos las sillas cubiertas con sábanas y un reloj de pared que seguía andando."
)

MINIFIED_JS = (
    "function a(b){return b.map(function(c){return c*2})}var d={e:[1,2,3],f:function(g)"
    "{if(g){return null}else{return(void 0)}}};window.__x=function(){return d};"
)

JSON_DUMP = (
    '{"id":1234,"name":"x","items":[{"k":1,"v":"a"},{"k":2,"v":"b"}],"meta":{"n":2,'
    '"ts":1695384000,"ok":true,"ratio":0.9123,"path":"a/b/c"}}'
)

SUBTITLE = (
    "1\n00:00:01,000 --> 00:00:04,000\nHola, ¿cómo estás?\n\n"
    "2\n00:00:04,500 --> 00:00:08,000\nBien, gracias.\n"
)


class TestProvenanceIsVisibleOnTheHitLine:
    """The owner's finding (2026-09-22): prose questions were answered from in-tree
    documentation and markup, and the hit line said only "vendored" or nothing.

    Measured on that set: 84 distinct addresses served, 30 of them markup, 23 carrying a
    documentation word in the path, and 5 plain `.txt`. A label is not a filter — which
    passages a search returns stays the owner's call — but naming what came back is
    checkable, and it was only visible after reading 15 rendered pages.
    """

    def test_the_classes_are_named_by_path_and_suffix(self) -> None:
        from rlm_kernel.textindex import provenance_class as pc
        assert pc("node_modules/lib/index.js") == "vendored"
        assert pc("project/docs/manual.htm") == "documentation"
        assert pc("project/README") == "documentation"
        assert pc("site/pages/about.html") == "markup"
        assert pc("src/engine/core.cpp") == "code"
        assert pc("data/config.yaml") == "data"
        assert pc("notes/letter.txt") == "prose"
        assert pc("letter with no extension") == "prose"
        assert pc("archive/thing.stl") == "other"

    def test_a_vendored_path_outranks_its_suffix(self) -> None:
        """The strongest statement available wins, whatever the file looks like."""
        from rlm_kernel.textindex import provenance_class as pc
        assert pc("vendor/site-packages/pkg/docs/readme.md") == "vendored"
        assert pc("build/docs/index.html") == "vendored", (
            "`build` is a vendored marker here, so this is vendored before it is anything "
            "else — the first version of this test asserted `documentation` and was wrong"
        )
        assert pc("project/docs/index.html") == "documentation", (
            "a documentation path is documentation even when the file is markup"
        )

    def test_prose_is_not_labelled_and_the_rest_are(self) -> None:
        from rlm_kernel.textindex import provenance_label
        assert provenance_label("notes/letter.txt") is None, (
            "a label on every hit is noise; the unremarkable case stays unlabelled"
        )
        assert provenance_label("project/docs/manual.htm") == "documentation"
        assert provenance_label("site/index.html") == "markup"
        assert provenance_label("src/a.py") == "code"

    def test_the_label_reaches_the_formatted_hits(self) -> None:
        from rlm_kernel.textindex import TextIndex  # noqa: F401 - the line under test

        class Row:
            def __init__(self, source: str) -> None:
                self.address = f"{source}#L0-9"
                self.source = source
                self.origin = "file"
                self.derived = False
                self.engine = None
                self.vendored = False
                self.covers = None
                self.band = None
                self.snippet = "text"

        class Result:
            query = "what happened"
            hits = [Row("project/docs/manual.htm")]
            hidden_vendored = 0
            coverage_note = None

        line = format_hits(Result(), ["the manual says nothing happened"])
        assert "documentation" in line, line
        # The band label is parsed back off this same line by the harness, so a new token
        # must not disturb it.
        assert "covers" in line and line.index("documentation") != line.index("covers")
    """Sampling must prioritise prose (owner, 2026-09-22).

    The setting: a question devised from minified JavaScript, a JSON dump or a subtitle
    file is not a question about the corpus a person would ask, and the sampler was drawing
    uniformly from 29M chunks whose population is ~37% code. Two filters, and the tests
    below pin each one separately because they fail differently: the *name* is free (it
    rides in the indexed lookup, so a rejected candidate is never read) and the *content*
    is what actually decides, since a `.txt` can be a data dump.
    """

    def test_the_score_separates_prose_from_its_lookalikes(self) -> None:
        assert prose_score(PROSE) >= PROSE_FLOOR, prose_score(PROSE)
        for label, other in (("minified javascript", MINIFIED_JS),
                             ("a json dump", JSON_DUMP),
                             ("empty", ""),
                             ("whitespace", "   \n\t\n")):
            assert prose_score(other) < PROSE_FLOOR, (label, prose_score(other))

    def test_a_subtitle_file_is_rejected_by_name_not_by_score(self) -> None:
        """The honest division of labour: `- ->` timestamps and short lines score high.

        A subtitle reads *almost* like prose — letters, spaces, sentence punctuation — so
        expecting the score to catch it would be expecting the wrong filter to work. It is
        the name that rejects it, which is why both filters exist.
        """
        assert non_prose_extension("films/una-pelicula.srt") == ".srt"
        assert non_prose_extension("build/app.min.js") == ".js"
        assert non_prose_extension("notes/letter.txt") is None
        assert non_prose_extension("MEMORY.DMP") is None
        assert ".srt" in NON_PROSE_EXTENSIONS


class TestSamplingProse:
    def test_a_name_rejected_candidate_is_never_read(
        self, index: TextIndex, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The free half of the filter, and the one that must not silently cost reads."""
        index.add_text(raw=b"a", display="notes/letter.txt", source_hash="h1",
                       text=PROSE.encode("utf-8"))
        index.add_text(raw=b"b", display="build/app.min.js", source_hash="h2",
                       text=MINIFIED_JS.encode("utf-8"))
        index.add_text(raw=b"c", display="films/film.srt", source_hash="h3",
                       text=SUBTITLE.encode("utf-8"))
        read: list[str] = []

        def spy(self, hit, **kwargs):  # type: ignore[no-untyped-def]
            read.append(hit.source)
            return PROSE if hit.source == "notes/letter.txt" else JSON_DUMP

        monkeypatch.setattr(TextIndex, "read", spy)
        kept, texts, stats = index.random_prose(1, rng=random.Random(7), attempts=20)
        assert [hit.source for hit in kept] == ["notes/letter.txt"]
        assert read == ["notes/letter.txt"], read
        assert stats["kept"] == 1

    def test_content_decides_when_the_name_looks_fine(
        self, index: TextIndex, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A `.txt` holding a data dump is not prose, and this is the filter that sees it."""
        index.add_text(raw=b"a", display="data/dump.txt", source_hash="h1",
                       text=JSON_DUMP.encode("utf-8"))
        monkeypatch.setattr(TextIndex, "read",
                            lambda self, hit, **kw: JSON_DUMP)
        kept, _texts, stats = index.random_prose(1, rng=random.Random(3), attempts=10)
        assert kept == []
        assert stats["drawn"] >= 1 and stats["rejected_content"] >= 1, stats

    def test_a_floor_nothing_clears_returns_fewer_rather_than_spinning(
        self, index: TextIndex, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        index.add_text(raw=b"a", display="data/dump.txt", source_hash="h1",
                       text=JSON_DUMP.encode("utf-8"))
        monkeypatch.setattr(TextIndex, "read", lambda self, hit, **kw: JSON_DUMP)
        kept, _texts, stats = index.random_prose(
            5, rng=random.Random(11), attempts=8, floor=0.99)
        assert stats["kept"] == 0 and stats["drawn"] <= 8, stats

    def test_duplicates_count_against_the_budget(self, index: TextIndex,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
        """A pool smaller than the request must terminate — and the accounting is why.

        The first version counted only *new* chunks against the draw budget, so a batch of
        nothing-but-duplicates could not advance it and the loop probed forever. The
        `fresh == 0` early exit would also stop that loop, which is exactly why this test
        asserts on the *accounting* instead: with one chunk in the table and a budget of 30,
        the draw must report more than one candidate drawn.
        """
        index.add_text(raw=b"a", display="notes/one.txt", source_hash="h1",
                       text=PROSE.encode("utf-8"))
        monkeypatch.setattr(TextIndex, "read", lambda self, hit, **kw: JSON_DUMP)
        kept, _texts, stats = index.random_prose(5, rng=random.Random(5), attempts=30)
        assert kept == []
        assert stats["drawn"] > 1, f"duplicates must count against the budget: {stats}"
        assert stats["drawn"] <= 30, stats

    def test_the_same_seed_draws_the_same_passages(
        self, index: TextIndex, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for i in range(12):
            index.add_text(raw=f"r{i}".encode(), display=f"notes/letter-{i}.txt",
                           source_hash=f"h{i}", text=PROSE.encode("utf-8"))
        monkeypatch.setattr(TextIndex, "read", lambda self, hit, **kw: PROSE)
        first, _, _ = index.random_prose(3, rng=random.Random(99), attempts=20)
        second, _, _ = index.random_prose(3, rng=random.Random(99), attempts=20)
        assert [h.chunk_id for h in first] == [h.chunk_id for h in second]
        assert len(first) == 3


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
        "CREATE TABLE classification (raw BLOB PRIMARY KEY, kind TEXT NOT NULL,"
        " encoding TEXT)"
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
        "Vantrel was a singer.\n\nThe demo used the Godot engine.\n", encoding="utf-8"
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
        hit = index.search("Vantrel").hits[0]
        assert hit.address.startswith("docs/story.txt#L0-")
        text = index.read(hit, mount=LocalTreeMount(corpus))
        assert "Vantrel" in text
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
        assert index.search("Vantrel singer").hits
        assert index.search("Vantrel helicopter").hits == []

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
        hit = index.search("Vantrel").hits[0]
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


class TestMatchQuality:
    """How well a hit answered the *question* (RO4, 2026-09-16).

    The reason this exists: against a complete index, an unanswerable question is
    search-**hard**, not search-empty. A model asked about the Zxqvarn Protocol
    searches *orbital*, *tether*, *ratified* and gets real hits — passages
    containing the words that do not answer the question — because nothing in the
    result said how weak those matches were. Five weak hits looked exactly like
    five strong ones, so it kept searching (8/8 turns, twice) and, at a shorter
    budget, invented a citation to look finished. The signal is deliberately
    textual and checkable — "covers 1 of 5 query terms" — rather than a raw BM25
    float a small model cannot calibrate.
    """

    def test_stopwords_and_short_tokens_are_dropped(self) -> None:
        from rlm_kernel.textindex import content_terms

        terms = content_terms("Who ratified the Zxqvarn Protocol for orbital tether maintenance?")
        assert terms == ["ratified", "zxqvarn", "protocol", "orbital", "tether",
                         "maintenance"]

    def test_terms_are_deduplicated_and_ordered(self) -> None:
        from rlm_kernel.textindex import content_terms

        assert content_terms("tether tether orbital") == ["tether", "orbital"]

    def test_a_query_of_only_stopwords_has_no_content_terms(self) -> None:
        from rlm_kernel.textindex import content_terms

        assert content_terms("who is it that they are") == []

    def test_coverage_counts_terms_present_in_the_text(self) -> None:
        from rlm_kernel.textindex import term_coverage

        covered, total = term_coverage(
            "The tether was ratified in 1999.",
            ["ratified", "zxqvarn", "tether"],
        )
        assert (covered, total) == (2, 3)

    def test_coverage_is_case_insensitive(self) -> None:
        from rlm_kernel.textindex import term_coverage

        assert term_coverage("ORBITAL TETHER", ["orbital", "tether"]) == (2, 2)

    def test_coverage_matches_word_starts_but_not_word_insides(self) -> None:
        """`protocols` counts for `protocol`; `ratification` does not count for
        `ratified`. The number is a lower bound and is documented as one — the
        alternative is a stemmer, which is a policy this code does not need."""
        from rlm_kernel.textindex import term_coverage

        assert term_coverage("two protocols", ["protocol"]) == (1, 1)
        assert term_coverage("the ratification", ["ratified"]) == (0, 1)

    @pytest.mark.parametrize("covered,total,expected", [
        (1, 1, "strong"),
        (2, 2, "strong"),
        (6, 6, "strong"),
        (4, 6, "strong"),
        (3, 6, "partial"),
        (2, 6, "partial"),
        (1, 6, "weak"),
        (0, 6, "none"),
        (0, 0, "unknown"),
    ])
    def test_quality_bands(self, covered: int, total: int, expected: str) -> None:
        from rlm_kernel.textindex import match_quality

        assert match_quality(covered, total) == expected

    def test_a_one_word_question_answered_by_that_word_is_strong(self) -> None:
        """The band must not punish a short question: covering *every* content word
        is the strongest match there is, whatever the count."""
        from rlm_kernel.textindex import match_quality

        assert match_quality(1, 1) == "strong"
        assert match_quality(1, 6) == "weak"

    def test_the_note_states_the_numbers_not_a_verdict_alone(self) -> None:
        from rlm_kernel.textindex import match_note

        weak = match_note(1, 5)
        assert "weak" in weak
        assert "1 of 5" in weak
        # The actionable half: a weak best hit is the moment to say the corpus does
        # not contain the answer, not to cite the coincidence.
        assert "does not contain it" in weak.lower()

        strong = match_note(5, 5)
        assert "strong" in strong
        assert "5 of 5" in strong

    def test_an_unknown_match_is_not_reported_as_weak(self) -> None:
        """A query with no content terms is unjudgeable, and saying "weak" would
        be a confident wrong answer about the result the model is holding."""
        from rlm_kernel.textindex import match_note

        note = match_note(0, 0)
        assert "unknown" in note
        assert "weak" not in note


class TestCoverageSnapshot:
    """The search path must never count the chunk table (RO4, 2026-09-15).

    Measured on the real index: `TextIndex.search` returns in 0.2 s, while
    `COUNT(*) FROM text_chunks` takes 972 s and `COUNT(DISTINCT source)` and the
    classification counts each run past 45 s. `corpus_search` and
    `corpus_coverage` both call `coverage()`, so every corpus cell hit the REPL's
    120 s cell limit, the worker was killed and restarted four times, and a live
    run ended after two hours with `(No answer produced — forced finalization
    failed)`.

    The fix is a *published* snapshot: the process that may be slow (the miner, or
    `rlm corpus counters --refresh`) computes it once, and a search reads it — or
    says "unknown", which is the honest answer when nobody has published one,
    because zero would be a lie.
    """

    def test_a_published_snapshot_round_trips(self, conn: sqlite3.Connection) -> None:
        ti = TextIndex(conn)
        ti.ensure()
        ti.publish_coverage({
            "sources_indexed": 7, "chunks": 9, "text_files_in_map": 100,
            "text_coverage": 0.07, "documents_extracted": 1,
            "documents_needing_ocr": 2,
        })
        got = ti.published_coverage()
        assert got is not None
        assert got["sources_indexed"] == 7
        assert got["documents_needing_ocr"] == 2
        assert got["published_at"] is not None, "a snapshot carries its age"

    def test_nothing_published_is_none_not_zero(self, conn: sqlite3.Connection) -> None:
        ti = TextIndex(conn)
        ti.ensure()
        assert ti.published_coverage() is None

    def test_a_corrupt_snapshot_reads_as_none(self, conn: sqlite3.Connection) -> None:
        """A reader must never raise on a snapshot it cannot parse."""
        ti = TextIndex(conn)
        ti.ensure()
        ti.publish_coverage({"sources_indexed": 1})
        conn.execute("UPDATE meta SET value = ? WHERE key = ?",
                     ("{not json", "coverage_snapshot"))
        conn.commit()
        assert ti.published_coverage() is None

    def test_the_snapshot_survives_a_reopen(self, tmp_path: Path) -> None:
        """The point of storing it: the next process reads it without scanning."""
        path = tmp_path / "index.sqlite"
        first = sqlite3.connect(str(path))
        first.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        first.commit()
        ti = TextIndex(first)
        ti.ensure()
        ti.publish_coverage({"sources_indexed": 3, "chunks": 4})
        first.close()

        second = sqlite3.connect(str(path))
        got = TextIndex(second).published_coverage()
        second.close()
        assert got is not None and got["sources_indexed"] == 3

    def test_a_stale_snapshot_says_how_old_it_is(self) -> None:
        from rlm_kernel.textindex import coverage_note

        fresh = coverage_note({"sources_indexed": 5, "text_files_in_map": 100,
                               "text_coverage": 0.05, "published_at": time.time()})
        old = coverage_note({"sources_indexed": 5, "text_files_in_map": 100,
                             "text_coverage": 0.05,
                             "published_at": time.time() - 3600})
        assert "5 sources indexed" in fresh
        assert "ago" not in fresh, "a fresh snapshot needs no apology"
        assert "60 min ago" in old, "an old one must say so"

    def test_without_published_at_there_is_no_age_claim(self) -> None:
        """Back-compat: coverage dicts that predate the snapshot have no timestamp."""
        from rlm_kernel.textindex import coverage_note

        note = coverage_note({"sources_indexed": 5, "text_files_in_map": 100,
                              "text_coverage": 0.05})
        assert "5 sources indexed" in note
        assert "ago" not in note


class TestNonUtf8TextIsSearchable:
    """Windows-Latin text must be findable, and its addresses must still name raw bytes.

    Found by the owner reading the first live question set (2026-09-19): *"some files
    have Windows Latin codepoint and thus words with tilded vowels are not found."*
    The index decoded every source as UTF-8 with `errors="replace"`, so `canción` in
    cp1252 became `canci` + U+FFFD + `n` — and FTS5's `remove_diacritics` never saw
    the word it exists to fold. `classification.encoding` had recorded the right
    answer for every file all along (46 107 cp1252, 628 latin-1).

    The trap this class holds: **an address is a byte offset into the raw file.**
    Decoding before chunking would move every offset and break `corpus_read(address)`
    for exactly the files the fix is meant to help, so the decode belongs only where
    bytes become tokens and where they are displayed.
    """

    BODY = "El comité aprobó la canción de María y el niño.\n"

    def test_a_windows_latin_word_is_found_once_the_encoding_is_known(
        self, index: TextIndex,
    ) -> None:
        body = self.BODY.encode("cp1252")
        index.add_text(raw=b"docs/legacy.txt", display="docs/legacy.txt",
                       source_hash="h-legacy", text=body, encoding="cp1252")
        # The same bytes with no encoding known are the bug being fixed: indexed as
        # UTF-8 with replacement characters, the word cannot be matched.
        index.add_text(raw=b"docs/unlabelled.txt", display="docs/unlabelled.txt",
                       source_hash="h-unlabelled", text=body)

        found = [hit.source for hit in index.search("canción", k=8).hits]
        assert found == ["docs/legacy.txt"], found
        # Diacritic folding is not what was broken: FTS5 folds both sides already, so
        # the unaccented spelling finds the fixed file too.
        assert [h.source for h in index.search("cancion", k=8).hits] == ["docs/legacy.txt"]

    def test_the_encoding_comes_from_the_classification_row(
        self, index: TextIndex, conn: sqlite3.Connection,
    ) -> None:
        """The sniffer's answer is the index's answer: one lookup, no second guess."""
        body = self.BODY.encode("cp1252")
        conn.execute("INSERT INTO classification (raw, kind, encoding) VALUES (?, ?, ?)",
                     (b"docs/legacy.txt", "text", "cp1252"))
        conn.commit()

        index.add_text(raw=b"docs/legacy.txt", display="docs/legacy.txt",
                       source_hash="h-legacy", text=body)

        hits = index.search("canción", k=8).hits
        assert [h.source for h in hits] == ["docs/legacy.txt"]
        assert hits[0].encoding == "cp1252", hits[0]

    def test_an_address_still_names_the_raw_bytes(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        """The offset must land on the word in the *original* bytes, not in decoded text.

        cp1252 encodes `ó` as one byte and UTF-8 as two, so a decode before chunking
        would shift every address in the file after the first accented word — and
        `corpus_read` seeks those offsets into the file on disk.
        """
        legacy = corpus / "docs" / "legacy.txt"
        legacy.write_bytes(self.BODY.encode("cp1252"))

        index.add_text(raw=b"docs/legacy.txt", display="docs/legacy.txt",
                       source_hash="h-legacy", text=legacy.read_bytes(),
                       encoding="cp1252")
        hit = index.search("canción", k=8).hits[0]

        raw_slice = legacy.read_bytes()[hit.byte_start:hit.byte_end]
        assert "canción" in raw_slice.decode("cp1252"), raw_slice
        # ...and the same slice is *not* valid UTF-8 text, which is the whole point.
        assert "canción" not in raw_slice.decode("utf-8", "replace")

        mount = LocalTreeMount(corpus)
        text = index.read(hit, mount=mount)
        assert "canción" in text, text
        assert "comité" in text, text

    def test_an_unusable_encoding_name_is_not_recorded_as_if_it_worked(
        self, index: TextIndex,
    ) -> None:
        """A codec that does not exist falls back to UTF-8 — and says so.

        Recording the *requested* name would make the row claim an encoding that was
        never used, which is the same class of lie as a probe that cannot see the
        truth and reports a default.
        """
        body = self.BODY.encode("utf-8")
        assert index.add_text(raw=b"docs/odd.txt", display="docs/odd.txt",
                              source_hash="h-odd", text=body,
                              encoding="not-a-codec") >= 1
        assert [h.encoding for h in index.search("comité", k=8).hits] == ["utf-8"]

    def test_an_existing_index_gains_the_column_without_a_rewrite(
        self, tmp_path: Path,
    ) -> None:
        """Migration path: the live index has 29M rows and is not rebuilt for a schema.

        Rows written before this change read as `None` — *unknown*, not UTF-8 — which
        is what lets the repair pass find exactly the sources that need re-indexing.
        """
        legacy = sqlite3.connect(str(tmp_path / "old.sqlite"))
        legacy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        legacy.execute(
            "CREATE TABLE text_chunks (id INTEGER PRIMARY KEY, source BLOB NOT NULL,"
            " display TEXT NOT NULL, source_hash TEXT NOT NULL, origin TEXT NOT NULL,"
            " cache_task TEXT, cache_key TEXT, byte_start INTEGER NOT NULL,"
            " byte_end INTEGER NOT NULL, derived INTEGER NOT NULL DEFAULT 0,"
            " engine TEXT, vendored INTEGER NOT NULL DEFAULT 0)"
        )
        legacy.execute(
            "INSERT INTO text_chunks (source, display, source_hash, origin, byte_start,"
            " byte_end) VALUES (?, ?, ?, ?, ?, ?)",
            (b"docs/old.txt", "docs/old.txt", "h", "file", 0, 10),
        )
        legacy.commit()

        upgraded = TextIndex(legacy)
        upgraded.ensure()

        columns = {row[1] for row in legacy.execute("PRAGMA table_info(text_chunks)")}
        assert "encoding" in columns, columns
        row = legacy.execute("SELECT encoding FROM text_chunks").fetchone()
        assert row[0] is None, "an old row's encoding is unknown, not utf-8"
        legacy.close()


class TestRandomPassages:
    """Drawing real passages out, to devise questions against (2026-09-18).

    The owner's request: something that hands back *text blocks with their
    addresses* from the live corpus, so the questions used as probes come from the
    corpus rather than from a fixture that sleeps. Three properties are guarded
    here, because each of them silent-fails in a different way:

    * **reproducible** — the same seed must give the same passages, or a question
      set cannot be re-run against a changed harness and "same question, different
      answer" means nothing;
    * **reachable** — every indexed source must be drawable. A sampler that
      returns the same little corner of the index every time seeds questions that
      say nothing about the corpus as a whole;
    * **filtered** — vendored and derived (container-member) chunks are out by
      default: one is noise the ranking already down-weights, and the other is the
      address family that still costs >150 s to re-read (RO11).
    """

    def _stock(self, index: TextIndex, corpus: Path, display: str,
               body: str | None = None, **kwargs) -> None:
        relative = corpus / display
        relative.parent.mkdir(parents=True, exist_ok=True)
        text = (body or (display + " — " + ("filler sentence. " * 40) + "\n"))
        relative.write_text(text, encoding="utf-8")
        index.add_text(raw=display.encode("utf-8"), display=display,
                       source_hash=f"h-{display}", text=relative.read_bytes(),
                       **kwargs)

    def test_a_draw_is_reproducible_from_its_seed(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        import random

        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            self._stock(index, corpus, f"docs/{name}")

        first = index.random_chunks(3, rng=random.Random(7))
        again = index.random_chunks(3, rng=random.Random(7))
        assert [h.address for h in first] == [h.address for h in again]
        # Distinct: a draw of three passages must not be one passage three times.
        assert len({h.chunk_id for h in first}) == len(first) == 3

    def test_a_draw_never_hands_back_the_same_passage_twice(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        """No duplicates, checked over many seeds rather than one lucky draw.

        The first version of this guard asserted distinctness on a *single* draw from
        a four-chunk table, and the mutation table reported it VACUOUS: with the
        dedupe removed, three random probes into four chunks usually do land on three
        different chunks, so the test passed with the guard gone. Thirty seeds fix
        that — a duplicate-free draw is certain with the dedupe and near-certain to
        show a duplicate without it.
        """
        import random

        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            self._stock(index, corpus, f"docs/{name}")

        for seed in range(30):
            drawn = index.random_chunks(4, rng=random.Random(seed))
            ids = [h.chunk_id for h in drawn]
            assert len(ids) == len(set(ids)) == 4, (seed, ids)

    def test_every_indexed_source_can_be_reached_by_drawing(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        """No source is unreachable, and none is sampled only by accident.

        A weak uniformity claim on purpose: over 60 draws, all four sources must
        appear. With 60 independent draws over 4 equally likely chunks the chance
        of missing one is ~(3/4)**60 ≈ 3e-8, so this fails only if the draw is
        genuinely biased towards part of the table — which is the failure a
        "take the first N rows" sampler has.
        """
        import random

        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            self._stock(index, corpus, f"docs/{name}")

        seen = {h.source for h in
                index.random_chunks(60, rng=random.Random(11))}
        assert seen == {f"docs/{name}" for name in
                        ("a.txt", "b.txt", "c.txt", "d.txt")}

    def test_a_draw_skips_vendored_and_derived_chunks_unless_asked(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        import random

        from rlm_kernel.textindex import ORIGIN_CACHE

        self._stock(index, corpus, "docs/ordinary.txt")
        self._stock(index, corpus, "node_modules/lib/index.js")
        self._stock(index, corpus, "docs/from-a-container.txt",
                    origin=ORIGIN_CACHE, cache_task="extract_text",
                    cache_key="a" * 64, derived=True, engine="pdftotext")

        default = index.random_chunks(20, rng=random.Random(3))
        assert {h.source for h in default} == {"docs/ordinary.txt"}

        with_vendored = index.random_chunks(20, rng=random.Random(3),
                                            include_vendored=True)
        assert "node_modules/lib/index.js" in {h.source for h in with_vendored}

        derived = index.random_chunks(20, rng=random.Random(3),
                                      include_derived=True)
        assert "docs/from-a-container.txt" in {h.source for h in derived}

    def test_an_empty_index_draws_nothing(self, index: TextIndex) -> None:
        assert index.random_chunks(5) == []

    def test_a_filter_that_matches_nothing_terminates(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        """An index whose every chunk is filtered out returns [] — it does not spin.

        The budget is explicit here so the test proves the *bound* rather than
        waiting for a hang: a sampler that retried until it found a match would
        never return on an all-vendored index, and the operator would see a
        command that simply stopped responding.
        """
        self._stock(index, corpus, "node_modules/lib/index.js")

        assert index.random_chunks(3, attempts=40) == []

    def test_the_draw_never_asks_sqlite_for_both_extremes_at_once(
        self, index: TextIndex, corpus: Path,
    ) -> None:
        """`MIN(id), MAX(id)` together cost 272 s on the live index; seeks cost nothing.

        Measured 2026-09-18 on the complete index (29 015 791 chunks): asked for both
        extremes in one query, SQLite chose `SCAN text_chunks USING COVERING INDEX
        text_chunks_source` — a full scan of a covering index, **272.5 seconds** — and
        that single statement was 8m17s of an 8m17s `rlm corpus sample` run. The
        probes themselves were instant (`SEARCH … USING INTEGER PRIMARY KEY`). The
        bounds are therefore read as two single-ended `ORDER BY id` seeks, and this
        test holds the query *shape*, because the cost is invisible on a fixture
        index and the failure only appears at the corpus's scale.
        """
        import random

        self._stock(index, corpus, "docs/one.txt")
        self._stock(index, corpus, "docs/two.txt")
        issued: list[str] = []
        index._conn.set_trace_callback(lambda sql: issued.append(sql))
        try:
            assert len(index.random_chunks(2, rng=random.Random(1))) == 2
        finally:
            index._conn.set_trace_callback(None)

        assert issued, "the draw must read the index"
        assert not [sql for sql in issued if "MIN(" in sql.upper()
                    or "MAX(" in sql.upper()], issued
        assert any("ORDER BY id DESC" in sql for sql in issued), issued

