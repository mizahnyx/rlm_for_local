"""A search reads only the opening of each hit's passage (RO21).

`handle_search` decorates every hit with a 300-character snippet and a
`covers n/m … (band)` label. Both need words from the passage, and the FTS table is
contentless by design (the words live once, in the file), so the words come from a real
file read per hit — *inside the search*. Measured on the live index on 2026-09-21: one
search for a single word took 0.3 s to 116 s across eight runs, while one
`corpus_read` of a real address took 0.10–1.19 s. The search, not the read, is the
expensive operation, because it amplifies a per-hit open across k hits.

These tests pin the bound: what a search reads per hit is the passage's opening
(`SEARCH_SNIPPET_BYTES`), never the whole chunk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.corpus import (
    SEARCH_SNIPPET_BYTES,
    CorpusBridge,
    CorpusIndex,
)
from rlm_kernel.mounts import LocalTreeMount


class CountingMount(LocalTreeMount):
    """A mount that records how many bytes each read asked for."""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self.reads: list[tuple[int, int]] = []   # (seek offset, requested length)

    def open_readonly(self, rel: str, max_bytes: int | None = None):
        handle = super().open_readonly(rel, max_bytes=max_bytes)
        return _CountingHandle(handle, self.reads)


class _CountingHandle:
    def __init__(self, handle, log: list[tuple[int, int]]) -> None:
        self._handle = handle
        self._log = log
        self._offset = 0

    def seek(self, offset: int, whence: int = 0):
        self._offset = offset
        return self._handle.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        self._log.append((self._offset, size))
        return self._handle.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    # A file far larger than one chunk, so a whole-chunk read and a bounded read are
    # distinguishable by what the mount was asked for.
    body = "Vantrel sang at the festival. " + ("padding words here. " * 4_000)
    (root / "long.txt").write_text(body, encoding="utf-8")
    (root / "short.txt").write_text("Vantrel again.\n", encoding="utf-8")
    return root


@pytest.fixture
def mount(corpus: Path) -> CountingMount:
    return CountingMount(corpus)


@pytest.fixture
def bridge(corpus: Path, mount: CountingMount, tmp_path: Path) -> CorpusBridge:
    index_path = tmp_path / "derived" / "corpus.sqlite"
    index_path.parent.mkdir()
    index = CorpusIndex.open_for(corpus, index_path)
    index.build(LocalTreeMount(corpus))
    text = index.text()
    text.ensure()
    text.add_text(
        raw=b"long.txt", display="long.txt", source_hash="h1",
        text=(corpus / "long.txt").read_bytes(),
    )
    text.add_text(
        raw=b"short.txt", display="short.txt", source_hash="h2",
        text=(corpus / "short.txt").read_bytes(),
    )
    b = CorpusBridge(mount=mount, index=index)
    yield b
    b.close()


class TestASearchReadsOnlyTheOpening:
    def test_no_read_asks_for_a_whole_chunk(
        self, bridge: CorpusBridge, mount: CountingMount,
    ) -> None:
        """The bound, asserted where it is observable: what the mount was asked for."""
        mount.reads.clear()
        hits = bridge.handle_search("Vantrel", k=10)

        assert hits, "the fixture's text must be found"
        assert mount.reads, "a search reads each hit to build its snippet and label"
        over = [r for r in mount.reads if r[1] > SEARCH_SNIPPET_BYTES]
        assert not over, (
            f"a search must not read more than {SEARCH_SNIPPET_BYTES} bytes per hit; "
            f"it asked for {over}"
        )

    def test_a_long_passage_is_still_snippetted_and_labelled(
        self, bridge: CorpusBridge,
    ) -> None:
        """The bound must not cost the two things the read was for."""
        bridge.question = "Who is Vantrel?"
        hits = bridge.handle_search("Vantrel", k=5)
        first = hits[0]
        assert "Vantrel" in first, "the snippet must still carry the match"
        assert "covers" in first, "the label must still be computed"
        assert "(strong)" in first or "(partial)" in first, first

    def test_a_search_and_a_read_agree_on_the_address(
        self, bridge: CorpusBridge,
    ) -> None:
        """A bounded search must still serve an address that reads in full.

        The largest served passage is the one to check, since the bound is what could
        truncate it: the fixture's long file is many chunks, so at least one hit's chunk
        is comfortably larger than the snippet bound.
        """
        hits = bridge.handle_search("Vantrel", k=10)
        spans = []
        text_index = bridge.index.text()
        for line in hits:
            addr = str(line).split()[0]
            hit = text_index.find_chunk(addr)
            if hit is not None:
                spans.append(hit.byte_end - hit.byte_start)
        assert spans, "the fixture must serve at least one chunk"
        assert max(spans) > SEARCH_SNIPPET_BYTES, (
            f"the fixture must produce a chunk larger than the bound to test it; "
            f"spans were {spans}"
        )
        address = str(hits[0]).split()[0]
        full = bridge.handle_read(address, max_bytes=100_000)
        assert "Vantrel" in full


class TestTheIndexReadHonoursTheBound:
    def _big_chunk(self, bridge: CorpusBridge):
        """The largest chunk the search serves, whose span exceeds the snippet bound."""
        text_index = bridge.index.text()
        best = None
        for hit in text_index.search("Vantrel", k=10).hits:
            span = hit.byte_end - hit.byte_start
            if best is None or span > best[1]:
                best = (hit, span)
        assert best is not None, "the fixture must serve a chunk"
        return best

    def test_read_max_bytes_truncates_a_source_chunk(
        self, bridge: CorpusBridge, mount: CountingMount,
    ) -> None:
        text_index = bridge.index.text()
        hit, span = self._big_chunk(bridge)
        bounded = text_index.read(hit, mount=mount, max_bytes=64)
        whole = text_index.read(hit, mount=mount)
        assert len(bounded) <= 64 + 4, bounded[:80]     # decode may pad a partial char
        assert len(whole) > len(bounded), (len(whole), len(bounded), span)

    def test_a_bound_larger_than_the_chunk_reads_the_whole_chunk(
        self, bridge: CorpusBridge, mount: CountingMount,
    ) -> None:
        """The bound is a cap, not a mode: it must not truncate below the chunk's size."""
        text_index = bridge.index.text()
        hit, span = self._big_chunk(bridge)
        bounded = text_index.read(hit, mount=mount, max_bytes=span * 4)
        whole = text_index.read(hit, mount=mount)
        assert bounded == whole

    def test_no_bound_still_reads_the_whole_chunk(
        self, bridge: CorpusBridge, mount: CountingMount,
    ) -> None:
        """`corpus_read` and every other caller keep their full read by default."""
        text_index = bridge.index.text()
        hit, span = self._big_chunk(bridge)
        mount.reads.clear()
        text_index.read(hit, mount=mount)
        assert mount.reads == [(hit.byte_start, span)]
