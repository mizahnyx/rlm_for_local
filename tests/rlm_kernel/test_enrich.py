"""Tests for the enrichment selection (RO6).

The properties that matter: usage is evidence (a cited document outranks a served one), the plan
counts *documents* rather than addresses, and the order is deterministic so a plan can be
re-produced and argued about.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from rlm_kernel.enrich import (
    Candidate, iter_trajectories, rank_candidates, read_plan, read_plan_with_drops, read_usage,
    render_plan, select_documents, write_plan,
)


def _served(address: str) -> str:
    return json.dumps({"event": "corpus_served", "verb": "corpus_search",
                       "addresses": [{"address": address, "band": "weak"}]})


def _citation(answer: str) -> str:
    return json.dumps({"event": "end", "final_answer": answer})


class TestUsageIsEvidence:
    def test_a_cited_document_outranks_a_merely_served_one(self) -> None:
        """However often the served one was served: a citation answered a question."""
        served = collections.Counter({"served_only.txt": 9, "cited.txt": 1})
        cited = collections.Counter({"cited.txt": 1})
        ranked = rank_candidates(served, cited)
        assert [c.source for c in ranked][0] == "cited.txt"

    def test_ties_break_deterministically(self) -> None:
        served = collections.Counter({"b.txt": 2, "a.txt": 2, "c.txt": 2})
        assert [c.source for c in rank_candidates(served, collections.Counter())] == \
            ["a.txt", "b.txt", "c.txt"]

    def test_provenance_rides_along(self) -> None:
        served = collections.Counter({"src/a.py": 1, "notes/b.txt": 1})
        by_source = {c.source: c.provenance for c in rank_candidates(served,
                                                                    collections.Counter())}
        assert by_source["src/a.py"] == "code"
        assert by_source["notes/b.txt"] == "prose"


class TestItCountsDocumentsNotAddresses:
    def test_three_byte_ranges_of_one_file_are_one_candidate(self) -> None:
        """The measurement that produced 180 counted addresses; a plan consumes documents."""
        served = collections.Counter({"a/big.txt": 3})   # three ranges, one document
        ranked = rank_candidates(served, collections.Counter())
        assert len(ranked) == 1
        assert ranked[0].served == 3, "the count is still the evidence, folded onto the document"

    def test_usage_reading_folds_addresses(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        path.write_text("\n".join([
            _served("docs/one.txt#L0-99"),
            _served("docs/one.txt#L100-199"),
            _served("docs/two.txt#L0-9"),
            _citation("The answer is in docs/one.txt#L0-99"),
        ]), encoding="utf-8")
        served, cited = read_usage([path])
        assert served == collections.Counter({"docs/one.txt": 2, "docs/two.txt": 1})
        assert cited == collections.Counter({"docs/one.txt": 1})

    def test_prose_before_a_cited_address_is_not_part_of_the_path(self, tmp_path: Path) -> None:
        """A wrong candidate is worse than a missing one: it names a file that does not exist.

        The first version of the extractor allowed spaces, so this sentence produced a candidate
        called "The answer is in docs/one.txt". The assertion is on the *extraction*, which is
        where the bug was.
        """
        path = tmp_path / "run.jsonl"
        path.write_text(_citation("The answer is in docs/one.txt#L0-99"), encoding="utf-8")
        _served_counter, cited_counter = read_usage([path])
        assert list(cited_counter) == ["docs/one.txt"], (
            f"the prose before the address leaked into the path: {list(cited_counter)}"
        )


class TestThePlanIsSafeToShow:
    def test_the_rendered_plan_names_no_path(self) -> None:
        ranked = rank_candidates(collections.Counter({"a/secret-name.txt": 2}),
                                 collections.Counter({"a/secret-name.txt": 1}))
        text = render_plan(ranked)
        assert "secret-name" not in text
        assert "1 document" in text and "cited by an answer" in text
        assert "hours" in text, "the arithmetic is the point of the plan"

    def test_the_written_plan_names_paths_and_is_where_the_data_is(self, tmp_path: Path) -> None:
        ranked = rank_candidates(collections.Counter({"a/b.txt": 1}), collections.Counter())
        path = tmp_path / "plan.tsv"
        write_plan(ranked, path)
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# rank\tsource")
        assert "a/b.txt" in text, "the local plan is allowed to name files"

    def test_an_empty_plan_says_so(self) -> None:
        assert "nothing has been served or cited" in render_plan([])

    def test_only_cited_restricts_the_set(self) -> None:
        served = collections.Counter({"served.txt": 5, "cited.txt": 1})
        cited = collections.Counter({"cited.txt": 1})
        assert [c.source for c in rank_candidates(served, cited, only={"cited"})] == ["cited.txt"]


def test_iter_trajectories_skips_rendered_pages(tmp_path: Path) -> None:
    (tmp_path / "run.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "page.jsonl").write_text("{}", encoding="utf-8")
    names = [p.name for p in iter_trajectories(tmp_path)]
    assert names == ["run.jsonl"]


def test_the_rank_key_is_a_total_order() -> None:
    """Two candidates can never compare equal, or a plan is not reproducible."""
    a = Candidate("a.txt", "prose", 1, 0)
    b = Candidate("b.txt", "prose", 1, 0)
    assert a.rank_key != b.rank_key
    assert sorted([b, a], key=lambda c: c.rank_key) == [a, b]


class TestThePlanIsReadBack:
    """A plan is the list a generation pass spends inference on, so it must round-trip."""

    def _write(self, tmp_path: Path) -> Path:
        path = tmp_path / "enrich-plan.tsv"
        write_plan(
            [
                Candidate("cited.txt", "prose", 3, 2),
                Candidate("served.txt", "code", 9, 0),
            ],
            path,
        )
        return path

    def test_a_written_plan_reads_back_identically(self, tmp_path: Path) -> None:
        candidates, dropped = read_plan_with_drops(self._write(tmp_path))
        assert dropped == 0
        assert [(c.source, c.provenance, c.served, c.cited) for c in candidates] == [
            ("cited.txt", "prose", 3, 2),
            ("served.txt", "code", 9, 0),
        ]

    def test_the_header_is_not_a_document(self, tmp_path: Path) -> None:
        assert all(not c.source.startswith("#") for c in read_plan(self._write(tmp_path)))

    def test_a_malformed_row_is_dropped_and_counted(self, tmp_path: Path) -> None:
        """Dropped, never guessed at: a silently shortened plan is one nobody can audit."""
        path = tmp_path / "plan.tsv"
        path.write_text(
            "# rank\tsource\tserved\tcited\tprovenance\n"
            "1\tshort.txt\n"
            "2\tgood.txt\t1\t1\tprose\n"
            "3\t\t2\t0\tcode\n",
            encoding="utf-8",
        )
        candidates, dropped = read_plan_with_drops(path)
        assert [c.source for c in candidates] == ["good.txt"]
        assert dropped == 2, "the truncated row and the row with no document"

    def test_an_unparsable_count_costs_the_count_not_the_document(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.tsv"
        path.write_text("1\todd.txt\tlots\tmany\tprose\n", encoding="utf-8")
        candidates, dropped = read_plan_with_drops(path)
        assert dropped == 0
        assert (candidates[0].source, candidates[0].served, candidates[0].cited) == (
            "odd.txt", 0, 0,
        )


class TestTheSelection:
    """What gets spent on, which is the only decision in RO6 that costs real time."""

    def _candidates(self) -> list[Candidate]:
        return [
            Candidate("cited-one.txt", "prose", 4, 3),
            Candidate("cited-two.txt", "code", 2, 1),
            Candidate("served.txt", "markup", 11, 0),
        ]

    def test_the_order_is_the_plans_order(self) -> None:
        """No re-ranking: sorting again here would disagree with the plan a reader is holding."""
        chosen = select_documents(self._candidates(), limit=2)
        assert [c.source for c in chosen] == ["cited-one.txt", "cited-two.txt"]

    def test_cited_only_keeps_the_documents_an_answer_used(self) -> None:
        chosen = select_documents(self._candidates(), cited_only=True)
        assert [c.source for c in chosen] == ["cited-one.txt", "cited-two.txt"]

    def test_no_limit_means_all_of_it_deliberately(self) -> None:
        assert len(select_documents(self._candidates())) == 3

    def test_a_limit_of_zero_selects_nothing(self) -> None:
        assert select_documents(self._candidates(), limit=0) == []

    def test_a_limit_beyond_the_set_is_not_an_error(self) -> None:
        assert len(select_documents(self._candidates(), limit=99)) == 3
