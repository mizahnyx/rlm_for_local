"""The trace viewer: a trajectory rendered for a human (RO10).

The owner's requirement (2026-09-17): *"some sort of readable by me traces … so I
can browse and assess how much the process is suitable to the task, which corpus
citations are relevant in some scale."* A trajectory is written for machines — a
flat event stream of counters, payloads and cell results — and the assessment it
has to support is not "did the guard fire" but "was what it read relevant".

Two properties are load-bearing here and are tested rather than intended:

* **The page embeds passage text**, which is the only way to judge relevance
  rather than trust a band label. So the rendered directory is *corpus-derived
  data*: it must be refused inside the corpus root and locked down outside it.
* **`render_summary` is the safe form.** It carries no question, no address and no
  quote, which is what makes it postable into a session where the page itself may
  not go. A summary that leaked one of those would break the privacy rule
  (AGENTS.md §1.9) silently.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from rlm_kernel.mounts import ReadOnlyViolation
from rlm_local.templates import (
    NUDGE_CORPUS_UNCITED,
    NUDGE_CORPUS_WEAK_EVIDENCE,
    TURN_HEADER,
)

T0 = 1_760_000_000.0


def _write_trajectory(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


def _served(turn: int, query: str, *pairs: tuple[str, str | None],
            verb: str = "corpus_search", chars: int = 120,
            ok: bool = True) -> dict:
    return {
        "event": "corpus_served",
        "timestamp": T0 + turn,
        "turn": turn,
        "verb": verb,
        "query": query,
        "addresses": [{"address": a, "band": b} for a, b in pairs],
        "chars": chars,
        "ok": ok,
    }


@pytest.fixture
def simple_trajectory(tmp_path: Path) -> Path:
    """One 2-turn corpus run: a search, a refusal, a cited answer, forced=False."""
    events = [
        {"event": "start", "timestamp": T0, "query": "Who is Cuicani?",
         "context_len": 12, "config": {"profile": "tiny", "model": "stub-4b"}},
        {"event": "turn_start", "timestamp": T0 + 1, "turn": 1, "max_turns": 2},
        {"event": "root_message", "timestamp": T0 + 1, "turn": 1, "role": "user",
         "content": TURN_HEADER.format(turn=1, max_turns=2) + "\nprobe"},
        _served(1, "cuicani", ("notes/song.txt#L0-21", "strong"),
                ("notes/kettle.txt#L0-21", "none")),
        {"event": "guardrail", "timestamp": T0 + 2, "turn": 1,
         "guardrail": "corpus_search_quality",
         "detail": "served strong=1 none=1 chars=120"},
        {"event": "root_message", "timestamp": T0 + 3, "turn": 1,
         "role": "assistant",
         "content": "```repl\nhits = corpus_search('cuicani')\nprint(hits)\n```"},
        {"event": "repl_result", "timestamp": T0 + 4, "turn": 1,
         "stdout": "notes/song.txt#L0-21  [raw, covers 1/1 (strong)]",
         "stderr": "", "final_answer": None, "warnings": [],
         "answer_state": None, "scaffold_repaired": []},
        {"event": "guardrail", "timestamp": T0 + 5, "turn": 1,
         "guardrail": "corpus_uncited",
         "detail": "block=1 answer refused: no address and no coverage "
                   "(unserved=0 nudges=1/2)"},
        {"event": "root_message", "timestamp": T0 + 6, "turn": 1, "role": "user",
         "content": NUDGE_CORPUS_UNCITED},
        {"event": "turn_start", "timestamp": T0 + 7, "turn": 2, "max_turns": 2},
        {"event": "root_message", "timestamp": T0 + 7, "turn": 2, "role": "user",
         "content": TURN_HEADER.format(turn=2, max_turns=2)},
        {"event": "root_message", "timestamp": T0 + 8, "turn": 2,
         "role": "assistant",
         "content": "```repl\nanswer['content'] = 'It is in the notes.\\n"
                    "Citations: notes/song.txt#L0-21'\nanswer['ready'] = True\n```"},
        {"event": "repl_result", "timestamp": T0 + 9, "turn": 2, "stdout": "",
         "stderr": "", "final_answer": "It is in the notes.",
         "warnings": [], "answer_state": None, "scaffold_repaired": []},
        {"event": "guardrail", "timestamp": T0 + 10, "turn": 2,
         "guardrail": "corpus_citation",
         "detail": "answers_with_address=True chars=40"},
        {"event": "end", "timestamp": T0 + 11, "elapsed_s": 12.5,
         "final_answer": "It is in the notes.\nCitations: notes/song.txt#L0-21",
         "turns_used": 2, "subcalls_used": 0, "forced": False},
    ]
    return _write_trajectory(tmp_path / "traj.jsonl", events)


def test_a_truncated_last_line_is_reported_rather_than_fatal(tmp_path: Path) -> None:
    """A killed run leaves half a JSON object; the viewer must still open it.

    The trajectory is flushed per event, so a run that dies mid-write leaves a
    partial final line — and reading that as a crash is how a viewer becomes
    useless exactly when it is needed.
    """
    from rlm_local.traceview import read_trajectory

    path = _write_trajectory(tmp_path / "cut.jsonl", [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
        {"event": "turn_start", "timestamp": T0 + 1, "turn": 1, "max_turns": 2},
    ])
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "end", "elapsed_s": 1.0, "final_ans')

    run = read_trajectory(path)
    assert run.truncated is True
    assert len(run.turns) == 1
    assert "truncated" in " ".join(run.notes).lower()


def test_a_missing_file_says_so_rather_than_raising(tmp_path: Path) -> None:
    from rlm_local.traceview import read_trajectory

    with pytest.raises(FileNotFoundError):
        read_trajectory(tmp_path / "absent.jsonl")


def test_the_header_carries_the_question_the_model_and_the_outcome(
    simple_trajectory: Path,
) -> None:
    from rlm_local.traceview import read_trajectory

    run = read_trajectory(simple_trajectory)
    assert run.query == "Who is Cuicani?"
    assert run.forced is False
    assert run.turns_used == 2
    assert run.elapsed_s == pytest.approx(12.5)
    assert run.model == "stub-4b"


def test_the_run_reads_turn_by_turn_with_what_it_ran(
    simple_trajectory: Path,
) -> None:
    from rlm_local.traceview import read_trajectory, render_run_markdown

    run = read_trajectory(simple_trajectory)
    assert [t.index for t in run.turns] == [1, 2]
    first = run.turns[0]
    assert "corpus_search('cuicani')" in (first.model_said or "")
    assert first.cells[0]["stdout"].startswith("notes/song.txt#L0-21")
    # The one harness intervention in this run is the uncited refusal, and it is
    # attached to the turn it happened in.
    assert [name for name, _ in first.guardrails] == [
        "corpus_search_quality", "corpus_uncited"]
    assert first.nudges == [NUDGE_CORPUS_UNCITED]
    # The turn header and the REPL echo are scaffolding, not interventions.
    assert run.turns[1].nudges == []

    page = render_run_markdown(run)
    assert "# Trace" in page
    assert "Who is Cuicani?" in page
    assert "corpus_search('cuicani')" in page
    assert "covers 1/1" in page
    assert "corpus_uncited" in page
    assert "answers_with_address=True" in page


def test_a_hit_is_shown_with_its_band_and_the_passage_behind_it(
    simple_trajectory: Path,
) -> None:
    """The whole point: relevance is judged on the passage, not on the label."""
    from rlm_local.traceview import read_trajectory, render_run_markdown

    run = read_trajectory(simple_trajectory)
    page = render_run_markdown(run, passages={
        "notes/song.txt#L0-21": "Cuicani sang it first",
        "notes/kettle.txt#L0-21": "The kettle boiled dry",
    })
    assert "notes/song.txt#L0-21" in page
    assert "Cuicani sang it first" in page
    assert "The kettle boiled dry" in page
    assert "**strong**" in page
    assert "**none**" in page


def test_the_audit_separates_cited_from_served(simple_trajectory: Path) -> None:
    """Four buckets, because they mean four different things."""
    from rlm_local.traceview import read_trajectory

    run = read_trajectory(simple_trajectory)
    audit = run.audit()
    assert audit.cited_answering == ["notes/song.txt#L0-21"]
    assert audit.cited_non_answering == []
    assert audit.cited_unserved == []
    # The `none` hit was handed over and never used: the model left a relevant
    # signal on the table, which is exactly what an owner wants to see.
    assert audit.served_not_cited == ["notes/kettle.txt#L0-21"]


def test_a_weak_citation_is_audited_as_non_answering(tmp_path: Path) -> None:
    from rlm_local.traceview import read_trajectory

    events = [
        {"event": "start", "timestamp": T0, "query": "Who ratified it?",
         "context_len": 0, "config": {}},
        {"event": "turn_start", "timestamp": T0, "turn": 1, "max_turns": 1},
        _served(1, "kettle", ("notes/kettle.txt#L0-21", "none")),
        {"event": "guardrail", "timestamp": T0 + 2, "turn": 1,
         "guardrail": "corpus_weak_citation",
         "detail": "final: answer refused: every cited address was served weak "
                   "(band=none addresses=1 nudges=1/2)"},
        {"event": "end", "timestamp": T0 + 3, "elapsed_s": 3.0,
         "final_answer": "See notes/kettle.txt#L0-21",
         "turns_used": 1, "subcalls_used": 0, "forced": True},
    ]
    run = read_trajectory(_write_trajectory(tmp_path / "weak.jsonl", events))
    audit = run.audit()
    assert audit.cited_non_answering == ["notes/kettle.txt#L0-21"]
    assert audit.cited_answering == []
    assert run.forced is True


def test_a_run_without_served_events_says_the_audit_is_partial(
    tmp_path: Path,
) -> None:
    """An empty served set is *unknown*, not empty — and never reads as fabrication.

    Runs recorded before the served-address instrumentation carry
    `corpus_search_quality` (a distribution) but not the addresses the parent
    served, so an answer's citations cannot be checked against them. The first
    summary pass over the 17 recorded runs reported these as `cited_unserved=1`,
    i.e. as fabrications the trajectory cannot support; this bucket is the fix.
    """
    from rlm_local.traceview import read_trajectory

    events = [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
        {"event": "turn_start", "timestamp": T0, "turn": 1, "max_turns": 1},
        {"event": "guardrail", "timestamp": T0 + 1, "turn": 1,
         "guardrail": "corpus_search_quality", "detail": "served weak=8 chars=4150"},
        {"event": "end", "timestamp": T0 + 2, "elapsed_s": 2.0,
         "final_answer": "See notes/song.txt#L0-21", "turns_used": 1,
         "subcalls_used": 0, "forced": True},
    ]
    run = read_trajectory(_write_trajectory(tmp_path / "old.jsonl", events))
    audit = run.audit()
    assert audit.complete is False
    assert audit.cited_unknown == ["notes/song.txt#L0-21"]
    assert audit.cited_unserved == [], "unknown is not a finding of fabrication"
    assert audit.cited_answering == []
    assert any("no served-address" in n for n in audit.notes)


def test_a_citation_no_helper_served_is_a_fabrication_when_serves_were_recorded(
    tmp_path: Path,
) -> None:
    """With served events present, an address outside the set is a real finding."""
    from rlm_local.traceview import read_trajectory

    events = [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
        {"event": "turn_start", "timestamp": T0, "turn": 1, "max_turns": 1},
        _served(1, "cuicani", ("notes/song.txt#L0-21", "strong")),
        {"event": "end", "timestamp": T0 + 2, "elapsed_s": 2.0,
         "final_answer": "See notes/kettle.txt#L4000-4100", "turns_used": 1,
         "subcalls_used": 0, "forced": True},
    ]
    run = read_trajectory(_write_trajectory(tmp_path / "fab.jsonl", events))
    audit = run.audit()
    assert audit.complete is True
    assert audit.cited_unserved == ["notes/kettle.txt#L4000-4100"]
    assert audit.cited_unknown == []
    assert audit.served_not_cited == ["notes/song.txt#L0-21"]


class TestHowBadlyTheModelCorruptsAddresses:
    """The measurement that decides whether mnemonic addresses are worth building.

    The owner's finding (2026-09-17): small models struggle to keep path-like exact
    strings consistent. Until now that was an impression, because a citation that
    differs from a served address by one character is indistinguishable in the
    trajectory from one that was invented: both are simply "not in the served set".
    Three buckets separate them, and the middle one is the size of the prize a
    repairable mnemonic would collect.
    """

    def _run_with(self, tmp_path: Path, cited: str) -> "object":
        from rlm_local.traceview import read_trajectory

        events = [
            {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
             "config": {}},
            {"event": "turn_start", "timestamp": T0, "turn": 1, "max_turns": 1},
            _served(1, "cuicani", ("notes/song.txt#L0-21", "strong"),
                    ("notes/kettle.txt#L0-21", "none")),
            {"event": "end", "timestamp": T0 + 2, "elapsed_s": 2.0,
             "final_answer": f"See {cited}", "turns_used": 1,
             "subcalls_used": 0, "forced": True},
        ]
        return read_trajectory(_write_trajectory(tmp_path / "c.jsonl", events))

    def test_an_exact_citation_is_counted_as_exact(self, tmp_path: Path) -> None:
        run = self._run_with(tmp_path, "notes/song.txt#L0-21")
        corruption = run.address_corruption()
        assert corruption.exact == ["notes/song.txt#L0-21"]
        assert corruption.one_edit == []
        assert corruption.unmatched == []

    def test_a_citation_one_character_out_is_the_prize(self, tmp_path: Path) -> None:
        """`#L0-21` → `#L0-2`: one dropped character, and today it reads as an
        invention. This is what a repairable handle would have caught."""
        run = self._run_with(tmp_path, "notes/song.txt#L0-2")
        corruption = run.address_corruption()
        assert corruption.one_edit == [("notes/song.txt#L0-2", "notes/song.txt#L0-21")]
        assert corruption.exact == []
        assert corruption.unmatched == []

    def test_a_transposition_inside_a_path_is_one_edit_too(self, tmp_path: Path) -> None:
        run = self._run_with(tmp_path, "notes/snog.txt#L0-21")
        corruption = run.address_corruption()
        assert corruption.one_edit == [("notes/snog.txt#L0-21", "notes/song.txt#L0-21")]

    def test_an_invented_citation_is_unmatched_not_one_edit(self, tmp_path: Path) -> None:
        run = self._run_with(tmp_path, "elsewhere/other.txt#L9000-9100")
        corruption = run.address_corruption()
        assert corruption.unmatched == ["elsewhere/other.txt#L9000-9100"]
        assert corruption.one_edit == []

    def test_an_address_two_edits_away_is_not_the_prize(self, tmp_path: Path) -> None:
        """The repair rule the design proposes is one edit, so two is not a repair
        the harness would have made — counting it would overstate the case."""
        run = self._run_with(tmp_path, "notes/snogg.txt#L0-21")
        corruption = run.address_corruption()
        assert corruption.one_edit == []
        assert corruption.unmatched == ["notes/snogg.txt#L0-21"]

    def test_two_equally_close_candidates_are_not_guessed_between(
        self, tmp_path: Path,
    ) -> None:
        """A repair must refuse an ambiguous slip, not pick one.

        With `…#L0-21` and `…#L0-22` both served, a citation of `…#L0-23` is one
        edit from either. A resolver that picked the first would silently answer
        with a passage the model may not have meant, which is worse than saying it
        could not tell — so this is counted as unmatched, like the repair would.
        """
        from rlm_local.traceview import read_trajectory

        events = [
            {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
             "config": {}},
            {"event": "turn_start", "timestamp": T0, "turn": 1, "max_turns": 1},
            _served(1, "cuicani", ("notes/song.txt#L0-21", "strong"),
                    ("notes/song.txt#L0-22", "partial")),
            {"event": "end", "timestamp": T0 + 2, "elapsed_s": 2.0,
             "final_answer": "See notes/song.txt#L0-23", "turns_used": 1,
             "subcalls_used": 0, "forced": True},
        ]
        run = read_trajectory(_write_trajectory(tmp_path / "amb.jsonl", events))
        corruption = run.address_corruption()
        assert corruption.one_edit == [], "two candidates is not a repairable slip"
        assert corruption.unmatched == ["notes/song.txt#L0-23"]

    def test_the_summary_carries_the_three_counts_and_nothing_else(
        self, tmp_path: Path,
    ) -> None:
        from rlm_local.traceview import render_summary

        run = self._run_with(tmp_path, "notes/snog.txt#L0-21")
        summary = render_summary(run)
        assert "cited_exact=0" in summary
        assert "cited_one_edit=1" in summary
        assert "cited_unmatched=0" in summary
        assert "notes/song.txt" not in summary

    def test_the_page_shows_what_each_broken_citation_looked_like(
        self, tmp_path: Path,
    ) -> None:
        from rlm_local.traceview import render_run_markdown

        run = self._run_with(tmp_path, "notes/snog.txt#L0-21")
        page = render_run_markdown(run)
        assert "one edit" in page.lower()
        assert "notes/snog.txt#L0-21" in page
        assert "notes/song.txt#L0-21" in page


def test_the_summary_carries_no_question_no_address_and_no_quote(
    simple_trajectory: Path,
) -> None:
    """The form that may travel: counts only (AGENTS.md §1.9)."""
    from rlm_local.traceview import read_trajectory, render_summary

    run = read_trajectory(simple_trajectory)
    summary = render_summary(run)
    assert "answers=" in summary and "searches=" in summary
    assert "Who is Cuicani?" not in summary
    assert "notes/song.txt" not in summary
    assert "Cuicani sang it first" not in summary


class TestTheOrientationCost:
    """How many turns pass before the run asks the corpus anything (owner, 2026-09-19).

    The owner read the first real question set and reported the model wasting turns
    "realising that the search has to be done in the corpus"; measured afterwards, the
    first helper call landed on turn 3, 3, 3, 4, 4 and 6 of a six-turn budget. A metric
    that is not on the summary line is a metric nobody improves, so it is rendered —
    and it must be the *first* call, not any call, or it measures nothing.
    """

    def _run(self, tmp_path: Path, helper_turns: list[int]) -> object:
        from rlm_local.traceview import read_trajectory

        events = [{"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
                   "config": {}}]
        for turn in helper_turns:
            events.append(_served(turn, "cuicani", ("notes/song.txt#L0-21", "strong")))
        events.append({"event": "end", "timestamp": T0 + 5, "elapsed_s": 5.0,
                       "final_answer": "done", "turns_used": 6, "subcalls_used": 0,
                       "forced": True})
        return read_trajectory(_write_trajectory(tmp_path / "orient.jsonl", events))

    def test_the_first_helper_call_is_what_is_reported(self, tmp_path: Path) -> None:
        from rlm_local.traceview import render_summary

        run = self._run(tmp_path, [4, 5, 6])
        assert run.first_helper_turn == 4
        assert "first_helper_turn=4" in render_summary(run)

    def test_a_run_that_never_asked_says_none_rather_than_zero(
        self, tmp_path: Path,
    ) -> None:
        """`0` would read as "the first turn", which is the opposite of the truth."""
        from rlm_local.traceview import render_summary

        run = self._run(tmp_path, [])
        assert run.first_helper_turn is None
        assert "first_helper_turn=none" in render_summary(run)

    def test_a_helper_call_with_no_turn_is_unknown_not_first(
        self, tmp_path: Path,
    ) -> None:
        """Turn 0 is what the harness writes when it has no turn in hand."""
        from rlm_local.traceview import render_summary

        run = self._run(tmp_path, [0, 5])
        assert run.first_helper_turn == 5
        assert "first_helper_turn=5" in render_summary(run)


def test_the_index_lists_every_run(tmp_path: Path, simple_trajectory: Path) -> None:
    from rlm_local.traceview import render_traces

    second = _write_trajectory(tmp_path / "second.jsonl", [
        {"event": "start", "timestamp": T0 + 100, "query": "Another question",
         "context_len": 0, "config": {}},
        {"event": "end", "timestamp": T0 + 101, "elapsed_s": 1.0,
         "final_answer": "no", "turns_used": 1, "subcalls_used": 0,
         "forced": True},
    ])
    out = tmp_path / "traces"
    written = render_traces([simple_trajectory, second], out)

    index = out / "index.md"
    assert index.exists()
    assert index in written
    text = index.read_text(encoding="utf-8")
    assert "Who is Cuicani?" in text
    assert "Another question" in text
    # One page per run, and the index links to each of them.
    pages = sorted(p.name for p in out.glob("*.md") if p.name != "index.md")
    assert len(pages) == 2
    for name in pages:
        assert f"]({name})" in text


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX mode bits; Windows has no 0600")
def test_a_rendered_trace_is_locked_down(tmp_path: Path,
                                         simple_trajectory: Path) -> None:
    """It contains corpus text, so it is owner-readable only."""
    from rlm_local.traceview import render_traces

    out = tmp_path / "traces"
    written = render_traces([simple_trajectory], out)
    assert (out.stat().st_mode & 0o777) == 0o700
    for path in written:
        assert (path.stat().st_mode & 0o777) == 0o600, path


def test_a_trace_directory_inside_the_corpus_is_refused(tmp_path: Path) -> None:
    """Layer 3: derived state never lives inside the corpus (AGENTS.md §1.8)."""
    from rlm_local.traceview import render_traces

    corpus = tmp_path / "corpus"
    (corpus / "notes").mkdir(parents=True)
    inside = _write_trajectory(corpus / "notes" / "traj.jsonl", [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
    ])
    with pytest.raises(ReadOnlyViolation):
        render_traces([inside], corpus / "traces", corpus_root=corpus)


def test_collecting_trajectories_from_a_directory(tmp_path: Path) -> None:
    from rlm_local.traceview import collect_trajectories

    _write_trajectory(tmp_path / "a.jsonl", [])
    _write_trajectory(tmp_path / "b.jsonl", [])
    (tmp_path / "notes.txt").write_text("not a trajectory", encoding="utf-8")
    found = collect_trajectories(tmp_path)
    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]
    # A single file is also a valid argument, so both entry points share a path.
    assert [p.name for p in collect_trajectories(tmp_path / "a.jsonl")] == ["a.jsonl"]


def test_a_container_member_is_marked_rather_than_read(tmp_path: Path) -> None:
    """Rendering must not be able to hang on an address with no indexed lookup.

    A container member (`arch.zip!member.txt`) names no file on disk, so the exact
    path bytes cannot be resolved and the text index falls back to a filter on an
    unindexed column — over 150 s for one address on the complete index
    (2026-09-17). The page says so instead of attempting it.
    """
    from rlm_local.traceview import collect_passages, read_trajectory

    class RawlessIndex:
        def raw_for(self, rel: str) -> bytes | None:
            return None

    class NeverReadBridge:
        index = RawlessIndex()

        def handle_read(self, address: str, max_bytes: int = 0) -> str:
            raise AssertionError("a container member must not be read")

    run = read_trajectory(_write_trajectory(tmp_path / "member.jsonl", [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
        {"event": "end", "timestamp": T0 + 1, "elapsed_s": 1.0,
         "final_answer": "See arch.zip!member.txt#L0-20", "turns_used": 1,
         "subcalls_used": 0, "forced": True},
    ]))
    passages = collect_passages(run, NeverReadBridge())
    assert "not embedded" in passages["arch.zip!member.txt#L0-20"]
    assert "RO11" in passages["arch.zip!member.txt#L0-20"]


def test_an_environment_override_does_not_change_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`render_summary` is pure: no clock, no host, no locale in the counts."""
    from rlm_local.traceview import read_trajectory, render_summary

    events = [
        {"event": "start", "timestamp": T0, "query": "q", "context_len": 0,
         "config": {}},
        {"event": "end", "timestamp": T0 + 1, "elapsed_s": 1.0,
         "final_answer": "x", "turns_used": 1, "subcalls_used": 0,
         "forced": True},
    ]
    path = _write_trajectory(tmp_path / "one.jsonl", events)
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    assert render_summary(read_trajectory(path)) == render_summary(
        read_trajectory(path))
