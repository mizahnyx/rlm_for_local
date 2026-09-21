"""A container member reads through the container's extraction cache (RO14).

`corpus_find` reports a match inside an archive as `container!member`. That address
names no file on disk: the member is not a path, the container's extracted text is the
only text the harness holds for it, and `text_chunks` has no row whose display is a
member. So before this change a read of such an address took the *unindexed* `display`
fallback in `TextIndex.find_chunk` — a scan of 29 015 791 rows on the live index, over
150 s against a 120 s cell limit — and then answered "no such path" anyway, because
there is nothing there to find. A reference that costs the whole table and then fails
is the worst combination: the model loses the cell and learns nothing.

The owner's rule for it (2026-09-17, roadmap RO14): *"compressed files should behave
transparently as directories … either route thru the extraction cache, or if that can't
be done, it probably signals that the compressed file must be marked as needed to be
mined."* Both halves are tested here: the extraction cache answers, or the message says
what to do about it.

The traces in `TestAMemberReadDoesNotScan` are the load-bearing ones, in the style of
the CL6 and RO11 guards: they assert *which column is filtered*, because the cost that
matters is invisible in the answer.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import (
    CORPUS_CONTAINER_NEEDS_MINING,
    CORPUS_MEMBER_FROM_CONTAINER,
    CorpusBridge,
    CorpusIndex,
)
from rlm_kernel.mine import EXTRACT_TEXT, DerivationCache
from rlm_kernel.mounts import LocalTreeMount


CONTAINER = "pack.zip"
MEMBER = "pack.zip!inside/notes.txt"
CONTAINER_TEXT = "Vantrel sang at the festival, and the archive holds the only copy.\n"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A corpus holding one archive, and one ordinary path with a `!` in its name."""
    root = tmp_path / "corpus"
    (root / "inside").mkdir(parents=True)
    # A real file whose *name* contains the member separator. It is a path, so the
    # index must win over the member reading — the member form is a fallback, not a
    # rule about the character.
    (root / "inside" / "a!b.txt").write_text("bang in the name\n", encoding="utf-8")
    (root / "readme.md").write_text("# Notes\n\nsails\n", encoding="utf-8")
    # The archive itself, as bytes: `list_archive` opens it, and `raw_for` needs a row.
    (root / CONTAINER).write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    return root


@pytest.fixture
def derived(tmp_path: Path) -> Path:
    d = tmp_path / "derived"
    d.mkdir()
    return d


@pytest.fixture
def index(corpus: Path, derived: Path) -> CorpusIndex:
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(LocalTreeMount(corpus))
    # The text tables must exist even though this suite indexes no text: the claim
    # under test is that a member read issues *no* statement against `text_chunks`, and
    # a missing table would make that claim true for the wrong reason — the mutation
    # that restores the display fallback then has nothing to scan and comes back green,
    # which is how this was found.
    idx.text().ensure()
    yield idx
    idx.close()


@pytest.fixture
def cache_root(derived: Path) -> Path:
    root = derived / "cache"
    root.mkdir()
    return root


@pytest.fixture
def bridge(index: CorpusIndex, corpus: Path, cache_root: Path) -> CorpusBridge:
    return CorpusBridge(
        mount=LocalTreeMount(corpus), index=index, cache_root=cache_root,
    )


def _classify_container(index: CorpusIndex, corpus: Path) -> str:
    """Classify the corpus so the container has a `head_hash`, and return it.

    The read path recovers the container's cache key from this row, so a corpus that
    has never been classified has no key to try — that is its own case below.
    """
    table = index.classifications()
    table.ensure()
    classify_entries(LocalTreeMount(corpus), table, limit=None)
    raw = f"{CONTAINER}".encode("utf-8")
    row = index._conn.execute(  # noqa: SLF001 - the bridge's own connection
        "SELECT head_hash FROM classification WHERE raw = ?", (raw,)
    ).fetchone()
    return row[0] if row else ""


def _write_extraction(cache_root: Path, source_hash: str, text: str) -> str:
    """Put an extracted text in the `extract_text` cache the way mining does."""
    cache = DerivationCache(cache_root, EXTRACT_TEXT)
    key = cache.key(source_hash)
    cache.put(key, text, {"engine": "fixture"})
    return key


class TestTheMemberNameIsSplitOffTheContainer:
    """`container!member` is recognised, and a real path containing `!` is not."""

    def test_a_member_address_names_its_container(self, bridge: CorpusBridge) -> None:
        assert bridge.container_of(MEMBER) == CONTAINER

    def test_a_real_path_with_a_bang_is_not_a_member(self, bridge: CorpusBridge) -> None:
        """The index decides. `inside/a!b.txt` exists, so it is a path, not a member.

        Treating every `!` as a member would break reads of files that legitimately
        carry one — and the test corpus has one for exactly that reason. Here the part
        before the `!` (`inside/a`) is not a file, so the reference is refused; the
        harder case is next.
        """
        assert bridge.container_of("inside/a!b.txt") is None

    def test_a_real_path_named_like_a_member_beats_the_member_reading(
        self, bridge: CorpusBridge, corpus: Path,
    ) -> None:
        """The subtle case: the container *does* exist, and the whole name is a file.

        `pack.zip!notes.txt` written on disk alongside `pack.zip` is the one shape where
        the container check alone would get it wrong — the part before the `!` is a real
        file, so the reference looks exactly like a member. The index is what settles it,
        and `handle_read` settles it in the same order: the file is read as the file it
        is.
        """
        (corpus / f"{CONTAINER}!notes.txt").write_text(
            "the bang is part of my name\n", encoding="utf-8")
        bridge.index.build(LocalTreeMount(corpus))
        assert bridge.container_of(f"{CONTAINER}!notes.txt") is None, (
            "the whole name is a stored path, so it is not a member reference"
        )
        text = bridge.handle_read(f"{CONTAINER}!notes.txt")
        assert "bang is part of my name" in text, text
        assert "extracted from the container" not in text, text

    def test_a_container_that_is_not_in_the_index_has_no_key(
        self, bridge: CorpusBridge,
    ) -> None:
        assert bridge.container_of("absent.zip!member.txt") is None

    def test_a_member_with_no_separator_is_not_a_member(self, bridge: CorpusBridge) -> None:
        assert bridge.container_of("readme.md") is None
        assert bridge.container_of("pack.zip") is None


class TestReadingThroughTheExtractionCache:
    """The cache answers, or the reply says what would make it answer."""

    def test_a_member_reads_the_containers_extracted_text(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        source_hash = _classify_container(bridge.index, corpus)
        assert source_hash, "the container must have a classification row"
        _write_extraction(cache_root, source_hash, CONTAINER_TEXT)

        text = bridge.handle_read(MEMBER)

        assert "Vantrel sang" in text, text
        # The substitution is *named*, not silent: what came back is the container's
        # extracted text, and the model can only judge it if it is told so.
        assert CORPUS_MEMBER_FROM_CONTAINER.format(
            member=MEMBER.split("!", 1)[1], container=CONTAINER).split("\n")[0] in text

    def test_the_reply_names_both_the_member_and_the_container(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        source_hash = _classify_container(bridge.index, corpus)
        _write_extraction(cache_root, source_hash, CONTAINER_TEXT)
        text = bridge.handle_read(MEMBER)
        assert MEMBER.split("!", 1)[1] in text
        assert CONTAINER in text

    def test_a_member_of_an_unextracted_container_says_what_to_do(
        self, bridge: CorpusBridge, corpus: Path,
    ) -> None:
        """A cache miss is a work item, and the message says which one.

        No extraction exists for this container, so there is nothing to serve. The
        reply must not be "no such path" — the path is real, and the model's next
        move (ask for something else, or say the corpus does not hold it) depends on
        knowing that the container has not been mined rather than that the member is
        absent.
        """
        _classify_container(bridge.index, corpus)
        text = bridge.handle_read(MEMBER)
        assert "mined" in text.lower(), text
        assert "no such path" not in text.lower(), text
        assert CONTAINER in text or MEMBER in text

    def test_a_member_of_an_unclassified_container_says_what_to_do(
        self, bridge: CorpusBridge, corpus: Path,
    ) -> None:
        """Without a `classification` row there is no cache key to try at all.

        The message is the same one — the container needs mining — because from the
        model's side the two situations are identical: no text is held for it.
        """
        text = bridge.handle_read(MEMBER)
        assert "mined" in text.lower(), text

    def test_a_large_extraction_is_capped_like_any_other_read(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        """The byte cap is what stops one read filling the model's context."""
        source_hash = _classify_container(bridge.index, corpus)
        _write_extraction(cache_root, source_hash, "x" * 50_000)
        text = bridge.handle_read(MEMBER, max_bytes=1_000)
        assert len(text) < 5_000, "the read cap must apply to a container's text too"
        assert "truncated" in text.lower(), text

    def test_a_real_path_with_a_bang_still_reads_as_a_file(
        self, bridge: CorpusBridge,
    ) -> None:
        text = bridge.handle_read("inside/a!b.txt")
        assert "bang in the name" in text, text

    def test_a_missing_container_still_reports_not_found(
        self, bridge: CorpusBridge,
    ) -> None:
        """`absent.zip!m.txt` names a container that does not exist — that is a miss."""
        text = bridge.handle_read("absent.zip!member.txt")
        assert "no such path" in text.lower(), text


class TestAMemberReadDoesNotScan:
    """The cost that motivated RO14: no filter on the unindexed `display` column.

    `text_chunks` is indexed on `source` and not on `display`. One read filtered on
    `display` measured **over 150 s** on the live index (29 015 791 rows), against a
    120 s cell limit — and for a member address it returned nothing, because no member
    is ever indexed under that display. These assertions are on the `WHERE` clause,
    because the trace callback renders bound parameters inline and the *column* is the
    decision that costs.
    """

    def _member_statements(self, bridge: CorpusBridge, corpus: Path,
                           cache_root: Path) -> list[str]:
        source_hash = _classify_container(bridge.index, corpus)
        _write_extraction(cache_root, source_hash, CONTAINER_TEXT)
        conn = bridge.index._conn  # noqa: SLF001 - the bridge's own connection
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            bridge.handle_read(MEMBER)
        finally:
            conn.set_trace_callback(None)
        return statements

    def test_a_member_address_reads_from_the_cache_without_a_scan(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        """The address form — `…!member.txt#L0-9` — is RO14's actual residual.

        Worth being exact about which input was expensive, because it is not the obvious
        one. A *bare* member name (`pack.zip!inside/notes.txt`) never reached the chunk
        lookup at all: it has no `#L` fragment, so `_read_address` declines it and the
        mount check answers "no such path" — cheap, and useless. The expensive input is
        the **address** form, which is what a caller who has a citation actually has, and
        which took the display fallback straight to a 29 015 791-row scan and then found
        nothing.

        Both are answered here now: this one from the container's extraction cache.
        """
        source_hash = _classify_container(bridge.index, corpus)
        _write_extraction(cache_root, source_hash, CONTAINER_TEXT)
        conn = bridge.index._conn  # noqa: SLF001 - the bridge's own connection
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            text = bridge.handle_read(f"{MEMBER}#L0-9")
        finally:
            conn.set_trace_callback(None)
        assert "Vantrel sang" in text, text
        assert not any("WHERE display" in s for s in statements if "text_chunks" in s), (
            statements
        )

    def test_the_member_read_never_filters_on_display(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        statements = self._member_statements(bridge, corpus, cache_root)
        chunk_lookups = [s for s in statements
                         if "text_chunks" in s and "SELECT" in s.upper()]
        assert not any("WHERE display" in s for s in chunk_lookups), (
            f"a member read must not scan text_chunks on the unindexed column: "
            f"{[s for s in chunk_lookups if 'WHERE display' in s]}"
        )

    def test_the_container_is_resolved_through_the_indexed_column(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        statements = self._member_statements(bridge, corpus, cache_root)
        path_lookups = [s for s in statements if "FROM entries" in s]
        assert path_lookups, "the container must be resolved through the path index"
        assert all("WHERE path" in s for s in path_lookups), path_lookups

    def test_an_unextracted_member_also_does_not_scan(
        self, bridge: CorpusBridge, corpus: Path,
    ) -> None:
        """The miss path is the one that used to burn the whole table.

        The address form again, with no cache entry: the answer is "needs mining", and
        it must arrive without the scan.
        """
        _classify_container(bridge.index, corpus)
        conn = bridge.index._conn  # noqa: SLF001
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            text = bridge.handle_read(f"{MEMBER}#L0-9")
        finally:
            conn.set_trace_callback(None)
        assert not any("WHERE display" in s for s in statements
                       if "text_chunks" in s), statements
        assert "mined" in text.lower(), text


class TestTheCacheKeyComesFromTheClassification:
    """The key is derived, not guessed, and a missing row is a miss rather than a key."""

    def test_the_container_text_is_found_by_the_key_the_classifier_recorded(
        self, bridge: CorpusBridge, corpus: Path, cache_root: Path,
    ) -> None:
        source_hash = _classify_container(bridge.index, corpus)
        key = _write_extraction(cache_root, source_hash, CONTAINER_TEXT)
        text = bridge.handle_read(MEMBER)
        assert "Vantrel sang" in text
        cache = DerivationCache(cache_root, EXTRACT_TEXT)
        assert cache.has(key), "the key written must be the key read"
