"""Tests for reading a printed address back — the step the first live run got wrong.

`corpus_search` prints `path#L<start>-<end>`; the first model handed that string
back indexed into it character by character (`corpus_read('A')`). These tests pin
that the printed citation works verbatim, for both stores (a file and a derived
cache entry), and that a bad address is refused rather than misread.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusBridge, CorpusIndex
from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.textindex import ORIGIN_CACHE, TextIndex


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "story.txt").write_text(
        "Cuicani sang at the festival in 1998.\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def wired(corpus: Path, tmp_path: Path) -> CorpusBridge:
    mount = LocalTreeMount(corpus)
    (tmp_path / "derived").mkdir()
    index = CorpusIndex.open_for(corpus, tmp_path / "derived" / "corpus.sqlite")
    index.build(mount)
    index.classifications().ensure()
    classify_entries(mount, index.classifications())
    text_index = index.text()
    text_index.ensure()
    text_index.add_text(
        raw=b"notes/story.txt", display="notes/story.txt", source_hash="h1",
        text=(corpus / "notes" / "story.txt").read_bytes(),
    )
    cache_root = tmp_path / "derived" / "cache"
    (cache_root / "extract_text" / "ab").mkdir(parents=True)
    (cache_root / "extract_text" / "ab" / "abcdef.txt").write_text(
        "the demo used the Godot engine\n", encoding="utf-8"
    )
    text_index.add_text(
        raw=b"notes/deep.docx", display="notes/deep.docx", source_hash="h2",
        text=b"the demo used the Godot engine\n", origin=ORIGIN_CACHE,
        cache_task="extract_text", cache_key="abcdef", derived=True,
        engine="zip+xml",
    )
    return CorpusBridge(mount=mount, index=index, cache_root=cache_root)


class TestAddressParsing:
    def test_an_address_round_trips_through_the_index(self, wired: CorpusBridge) -> None:
        hit_address = wired.handle_search("Cuicani").splitlines()[0].split()[0]
        assert "#L" in hit_address
        assert wired.handle_search("Cuicani")  # sanity: there is a hit to use
        text_index = wired.index.text()
        chunk = text_index.find_chunk(hit_address)
        assert chunk is not None
        assert chunk.source == "notes/story.txt"

    def test_a_file_address_reads_verbatim(self, wired: CorpusBridge) -> None:
        """The exact string a search printed is what corpus_read is given."""
        address = wired.handle_search("Cuicani").splitlines()[0].split()[0]
        out = wired.handle_read(address)
        assert "Cuicani sang at the festival" in out
        assert address in out, "the read must say which address it answered"

    def test_a_derived_address_reads_from_the_cache(self, wired: CorpusBridge) -> None:
        address = wired.handle_search("Godot").splitlines()[0].split()[0]
        assert "extract_text" not in address, "the address is a source path, not a store"
        out = wired.handle_read(address)
        assert "Godot engine" in out

    def test_an_unindexed_address_is_refused_not_misread(
        self, wired: CorpusBridge,
    ) -> None:
        out = wired.handle_read("notes/story.txt#L999999-1000000")
        assert out.startswith("Error:")
        assert "no such path" in out

    def test_a_plain_path_still_reads(self, wired: CorpusBridge) -> None:
        assert "Cuicani" in wired.handle_read("notes/story.txt")

    def test_a_missing_file_still_reports_not_found(self, wired: CorpusBridge) -> None:
        assert "no such path" in wired.handle_read("notes/absent.txt")

    def test_an_escaping_address_is_still_refused(self, wired: CorpusBridge) -> None:
        assert wired.handle_read("../../etc/passwd#L0-10").startswith("Error:")

    def test_find_chunk_ignores_anything_that_is_not_an_address(
        self, wired: CorpusBridge,
    ) -> None:
        text_index: TextIndex = wired.index.text()
        assert text_index.find_chunk("notes/story.txt") is None
        assert text_index.find_chunk("") is None
        assert text_index.find_chunk("notes/story.txt#Lx-y") is None
