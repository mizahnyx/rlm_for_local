"""The derived-text retrieval pass (owner's call, 2026-09-25).

Measured before this existed: descriptions were indexed and *invisible* — 6 of 6 found when a search
was restricted to derived text, 0 of 6 in the ordinary ranking even at k=64. A one-chunk description
loses to whole files on bm25. The owner's decision was to give derived text its own bounded pass.

The test that matters is the first one: it asserts not only that the description is *returned*, but
that the snippet shown comes from the **description** rather than from the file bytes at the same
address — a description is stored under its document's own display, so an address can point at two
different texts, and silently showing the wrong one is the substitution this project ranks below
silence.
"""

from __future__ import annotations

from pathlib import Path

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusBridge, CorpusIndex
from rlm_kernel.mine import SUMMARISE, DerivationCache
from rlm_kernel.mounts import LocalTreeMount
from rlm_kernel.textindex import ORIGIN_CACHE

DESCRIPTION = "Zanzibaricum ferromagnetic sprocket calibration notes for the turbine retrofit."
FILLER = "Quarterly logistics report: pallet counts by depot and route. "


def _bridge(tmp_path: Path) -> tuple[CorpusBridge, CorpusIndex, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "filed.txt").write_text(FILLER * 40, encoding="utf-8")
    (corpus / "described.txt").write_text("Depot pallet counts. " * 40, encoding="utf-8")
    mount = LocalTreeMount(corpus)
    index = CorpusIndex.open_for(corpus, tmp_path / "index.sqlite")
    index.build(mount)
    index.classifications().ensure()
    classify_entries(mount, index.classifications())
    text_index = index.text()
    text_index.ensure()
    # The document's own text, as a mining window would write it.
    text_index.add_text(raw=b"filed.txt", display="filed.txt", source_hash="h-filed",
                        text=(FILLER * 40).encode("utf-8"))
    # Its description, under the cache origin — stored against the *document's* display, and with
    # the derivation-cache entry the reader needs: an index row without it is a hit whose text
    # cannot be read, which the harness reports rather than papering over.
    cache_root = tmp_path / "derived"
    key = DerivationCache(cache_root, SUMMARISE).key("h-described", params="summary<=v1,400tok")
    DerivationCache(cache_root, SUMMARISE).put(key, DESCRIPTION, {"engine": "test"})
    text_index.add_text(raw=b"described.txt", display="described.txt", source_hash="h-described",
                        text=DESCRIPTION.encode("utf-8"), origin=ORIGIN_CACHE, derived=True,
                        cache_task=SUMMARISE, cache_key=key)
    # Competing file chunks. This is not decoration: the pass exists because a one-chunk
    # description *loses to whole files on bm25* (measured: 0 of 6 descriptions in the ordinary
    # ranking at k=64, 6 of 6 when restricted). A fixture without competition cannot tell the pass
    # from its absence — the first version of this test was VACUOUS for exactly that reason,
    # because the ordinary search has no origin filter and simply returned the description.
    for number in range(20):
        name = f"competitor{number}.txt"
        body = ("Zanzibaricum sprocket calibration. " * 3).encode("utf-8")
        text_index.add_text(raw=name.encode("utf-8"), display=name, source_hash=f"h-{number}",
                            text=body)
    bridge = CorpusBridge(mount=mount, index=index, cache_root=cache_root)
    return bridge, index, corpus


class TestTheDerivedPass:
    def test_a_description_only_match_is_served_with_its_own_text(self, tmp_path: Path) -> None:
        """Before the pass this search returned nothing: no file contains the word."""
        bridge, index, _corpus = _bridge(tmp_path)
        try:
            # The precondition this pass exists for, asserted rather than assumed: the ordinary
            # search alone does not surface the description.
            plain = index.text().search("zanzibaricum", k=8)
            assert not [hit for hit in plain.hits if hit.origin == "cache"], (
                "the fixture must reproduce the measured condition, or this test proves nothing"
            )
            lines = bridge.handle_search("zanzibaricum", k=8)
        finally:
            index.close()
        joined = "\n".join(lines)
        # Assert on a *hit line*, not on the whole output: with the pass switched off the search
        # returns the no-match note, which echoes the query's word and mentions derived text — so
        # the first version of this test passed with the guard removed (VACUOUS, caught by the
        # mutation table). A hit line carries an address.
        hit_lines = [line for line in lines if "#L" in line.split("  [")[0]]
        assert hit_lines, "the description must be returned as a hit, not as a no-match note"
        assert any("cache" in line.split("  [")[1] and "Zanzibaricum" in line
                   for line in hit_lines), (
            "the hit must be the derived one, showing the description's own text"
        )
        assert "derived" in joined, "the description must be labelled as derived"

    def test_file_hits_are_unchanged_and_addresses_are_not_repeated(self, tmp_path: Path) -> None:
        bridge, index, _corpus = _bridge(tmp_path)
        try:
            lines = bridge.handle_search("pallet", k=8)
            derived_only = bridge.handle_search("pallet", k=8, derived_only=True)
        finally:
            index.close()
        # A hit line always carries an address; the coverage and vendored notes do not, and one of
        # them contains brackets, so filtering on "[" counts notes as hits.
        hit_lines = [line for line in lines if "#L" in line.split("  [")[0]]
        addresses = [line.split("  [")[0] for line in hit_lines]
        assert hit_lines, "an ordinary search must still return its file hits"
        assert len(addresses) == len(set(addresses)), "one address must not be listed twice"
        assert not [line for line in derived_only if "#L" in line.split("  [")[0]], (
            "derived_only must keep meaning 'derived only': no file hit may appear"
        )
