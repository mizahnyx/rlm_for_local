"""Tests for corpus access from a REPL cell (roadmap RO4).

Three layers are checked here, and the third is the one that matters:

1. the worker *defines* the corpus verbs and hands them to model code;
2. the parent process dispatches each verb onto the corpus bridge;
3. a real cell, in a real worker subprocess, gets a real answer from a real
   corpus on disk — and the corpus is byte-for-byte unchanged afterwards.

The prompt is checked against the worker's namespace too: a system prompt that
advertises a helper the worker does not define is a lie a small model will act
on, and this project has paid for that class of mistake before.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest

from rlm_kernel.corpus import CorpusBridge, CorpusIndex
from rlm_kernel.mounts import LocalTreeMount
from rlm_local.prompts import CORPUS_SECTION_LINES, corpus_helpers_section
from rlm_local.repl import REPLSandbox, _WORKER_SCRIPT

CORPUS_VERBS = (
    "corpus_find",
    "corpus_list",
    "corpus_stat",
    "corpus_read",
    "corpus_count",
    "corpus_search",
    "corpus_coverage",
)


class MockSubcallMgr:
    def llm_query(self, prompt, schema=None):
        return "unused"

    def llm_query_batched(self, prompts, schema=None):
        return ["unused"] * len(prompts)


class FakeWorkerSock:
    """Captures the framed messages the sandbox sends to its worker."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    def sendall(self, payload: bytes) -> None:
        size = struct.unpack(">I", payload[:4])[0]
        self.frames.append(json.loads(payload[4:4 + size].decode("utf-8")))

    def close(self) -> None:
        pass


class RecordingBridge(CorpusBridge):
    """A bridge that records what it was asked instead of answering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs) -> str:
        self.calls.append((name, args, kwargs))
        return f"recorded {name}"

    def handle_find(self, query, limit=20, kind=None, under=""):  # type: ignore[override]
        return self._record("find", query, limit=limit, kind=kind, under=under)

    def handle_list(self, rel="", limit=50):  # type: ignore[override]
        return self._record("list", rel, limit=limit)

    def handle_stat(self, rel):  # type: ignore[override]
        return self._record("stat", rel)

    def handle_read(self, rel, max_bytes=20_000):  # type: ignore[override]
        return self._record("read", rel, max_bytes=max_bytes)

    def handle_count(self, *, kind=None, under=""):  # type: ignore[override]
        return self._record("count", kind=kind, under=under)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "notes" / "deep").mkdir(parents=True)
    (root / "notes" / "budget.md").write_text("# Budget\n\nsails and ropes\n", encoding="utf-8")
    (root / "notes" / "deep" / "memo.txt").write_text("buried\n", encoding="utf-8")
    (root / "index.html").write_text("<html>index</html>", encoding="utf-8")
    return root


@pytest.fixture
def bridge(corpus: Path, tmp_path: Path) -> CorpusBridge:
    index_path = tmp_path / "derived" / "corpus.sqlite"
    index_path.parent.mkdir()
    idx = CorpusIndex.open_for(corpus, index_path)
    idx.build(LocalTreeMount(corpus))
    # A text index too, because a corpus run that cannot search is not the thing
    # these tests exercise — and the served-citation rule (RO4, 2026-09-16) needs
    # a search to return something before an address can be cited at all.
    text_index = idx.text()
    text_index.ensure()
    for rel in ("notes/budget.md", "notes/deep/memo.txt", "index.html"):
        text_index.add_text(
            raw=rel.encode("utf-8"), display=rel, source_hash=f"h-{rel}",
            text=(corpus / rel).read_bytes(),
        )
    b = CorpusBridge(mount=LocalTreeMount(corpus), index=idx)
    yield b
    b.close()


class TestWorkerDefinesTheCorpusVerbs:
    @pytest.mark.parametrize("verb", CORPUS_VERBS)
    def test_the_worker_defines_and_exports_the_verb(self, verb: str) -> None:
        assert f"def _harness_{verb}(" in _WORKER_SCRIPT
        assert f"\n{verb} = _harness_{verb}\n" in _WORKER_SCRIPT

    @pytest.mark.parametrize("verb", CORPUS_VERBS)
    def test_a_clobbered_verb_is_repaired(self, verb: str) -> None:
        """The scaffold registry restores helpers model code overwrites."""
        block = _WORKER_SCRIPT.split("_SCAFFOLD_ORIGINALS[_scaffold_name]")[0]
        assert f"'{verb}'" in block.rsplit("for _scaffold_name in (", 1)[1]

    def test_every_advertised_helper_exists_in_the_worker(self) -> None:
        """The prompt must not advertise a helper the worker does not define."""
        text = "\n".join(CORPUS_SECTION_LINES)
        advertised = set(re.findall(r"\b(corpus_\w+)\(", text))
        assert advertised == set(CORPUS_VERBS), (
            f"prompt advertises {sorted(advertised)}, worker defines {sorted(CORPUS_VERBS)}"
        )
        for verb in advertised:
            assert f"\n{verb} = _harness_{verb}\n" in _WORKER_SCRIPT

    def test_the_section_names_the_root_and_the_missing_index(self) -> None:
        with_index = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "/srv/corpus" in with_index
        assert "No path index" not in with_index
        without = corpus_helpers_section("/srv/corpus", has_index=False)
        assert "No path index" in without

    def test_the_section_warns_against_walking(self) -> None:
        section = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "Never walk the corpus" in section

    def test_the_section_explains_the_match_quality_label(self) -> None:
        """The label is only useful if the prompt says what to do with it (RO4).

        A weak match is the moment to answer "the corpus does not contain this",
        and that sentence has to be in the prompt or the label is decoration.
        """
        section = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "match quality" in section
        assert "question words" in section
        assert "weak" in section and "strong" in section
        assert "does not contain the answer" in section

    def test_the_section_says_the_first_move_is_a_corpus_call(self) -> None:
        """Orientation cost: 2–3 of a six-turn budget before the corpus is touched.

        Measured across both question sets (first helper turn 4, 4, 3, 3, 4, 3 and
        4, 4, 3, 3, 4, 3), and it is the most stable thing in those traces: the model
        spends turns working out that the material is behind a helper rather than in
        `context`. The prompt has to say so before the run starts.
        """
        section = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "first cell calls the corpus" in section
        assert "placeholder" in section

    def test_the_section_says_to_search_the_questions_own_words(self) -> None:
        """Query drift is measurable, and it is the difference between nothing and evidence.

        Thirteen recorded runs: an off-question query was served `none` **40 times out
        of 40**, while an on-question one was served `strong`/`weak` every time. The
        prompt has to tell the model to try the question's own words before it starts
        guessing synonyms.
        """
        section = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "question's own words first" in section
        assert "synonyms" in section

    def test_the_section_requires_the_answer_to_cite_its_evidence(self) -> None:
        """RO4, owner call 2026-09-14: the answer must carry the addresses it used.

        The requirement is stated as a standing rule of the run, not as a clause
        inside a helper's description — the live rerun read three passages,
        printed seven addresses, and then submitted an answer citing none of
        them. The address shape is spelled out because a model told only "cite"
        invents a format, and then the harness cannot tell a citation from a
        guess.
        """
        section = corpus_helpers_section("/srv/corpus", has_index=True)
        assert "Cite your evidence" in section
        assert "Citations:" in section
        assert "#L<start>-<end>" in section
        # ...and what to do when the corpus does not hold the answer: an answer
        # with no address and no coverage note is the shape this rule forbids.
        assert "corpus_coverage()" in section


class TestParentDispatchesCorpusVerbs:
    def _sandbox_with(self, bridge) -> tuple[REPLSandbox, FakeWorkerSock]:
        repl = REPLSandbox()
        sock = FakeWorkerSock()
        repl._worker_sock = sock  # type: ignore[assignment]
        repl._corpus_bridge = bridge
        return repl, sock

    def test_find_reaches_the_bridge(self) -> None:
        bridge = RecordingBridge()
        repl, sock = self._sandbox_with(bridge)
        repl._handle_request("corpus_find", {"query": "budget", "limit": 7, "kind": "file"})
        assert bridge.calls == [
            ("find", ("budget",), {"limit": 7, "kind": "file", "under": ""})
        ]
        assert sock.frames[-1]["result"] == "recorded find"

    def test_list_stat_read_and_count_reach_the_bridge(self) -> None:
        bridge = RecordingBridge()
        repl, sock = self._sandbox_with(bridge)
        repl._handle_request("corpus_list", {"rel": "notes", "limit": 5})
        repl._handle_request("corpus_stat", {"rel": "index.html"})
        repl._handle_request("corpus_read", {"rel": "notes/budget.md", "max_bytes": 64})
        repl._handle_request("corpus_count", {"under": "notes", "kind": "file"})
        assert [c[0] for c in bridge.calls] == ["list", "stat", "read", "count"]
        assert bridge.calls[0][2] == {"limit": 5}
        assert bridge.calls[2][2] == {"max_bytes": 64}
        assert sock.frames[-1]["result"] == "recorded count"

    def test_a_missing_rel_is_an_empty_path_not_a_crash(self) -> None:
        bridge = RecordingBridge()
        repl, sock = self._sandbox_with(bridge)
        repl._handle_request("corpus_read", {})
        assert bridge.calls[0][1] == ("",)
        assert sock.frames[-1]["result"] == "recorded read"

    def test_without_a_corpus_the_model_is_told_so(self) -> None:
        repl, sock = self._sandbox_with(None)
        repl._handle_request("corpus_find", {"query": "anything"})
        assert "no corpus is configured" in sock.frames[-1]["result"]

    def test_a_failing_bridge_reports_text_not_a_traceback(self) -> None:
        class Exploding(RecordingBridge):
            def handle_read(self, rel, max_bytes=20_000):  # type: ignore[override]
                raise RuntimeError("disk on fire")

        repl, sock = self._sandbox_with(Exploding())
        repl._handle_request("corpus_read", {"rel": "x"})
        result = sock.frames[-1]["result"]
        assert result.startswith("Error: corpus helper failed")
        assert "disk on fire" in result

    def test_an_unknown_corpus_verb_is_reported(self) -> None:
        repl, sock = self._sandbox_with(RecordingBridge())
        repl._handle_request("corpus_teleport", {})
        assert "unsupported corpus helper" in sock.frames[-1]["result"]


class TestTheSandboxRemembersWhichAddressesItServed:
    """The evidence a citation is checked against (RO4, 2026-09-16).

    The parent serves every corpus helper call, so it knows exactly which
    addresses a run was handed. That set is what makes a citation checkable: on
    2026-09-16 a 4-turn run ended with a `Citations:` line for an address it had
    never been served — a well-formed, entirely fabricated citation, which a
    pattern match accepted. The harness ranks that below silence, so the set is
    remembered here and required at submission.
    """

    def _sandbox(self, bridge) -> REPLSandbox:
        repl = REPLSandbox()
        repl._worker_sock = FakeWorkerSock()  # type: ignore[assignment]
        repl._corpus_bridge = bridge
        return repl

    def test_a_search_serves_the_addresses_it_returns(self, bridge) -> None:
        repl = self._sandbox(bridge)
        # `budget.md` is what this fixture's corpus actually contains.
        repl._handle_request("corpus_search", {"query": "sails"})
        served = repl.corpus_addresses_served
        assert served, "a search that returned hits served them"
        assert all(re.fullmatch(r".+#L\d+-\d+", a) for a in served)

    def test_a_successful_read_serves_the_address_it_was_asked_for(self, bridge) -> None:
        repl = self._sandbox(bridge)
        hits = bridge.handle_search("sails")
        address = hits[0].split()[0]
        repl._handle_request("corpus_read", {"rel": address})
        assert address in repl.corpus_addresses_served

    def test_a_failed_read_serves_nothing(self, bridge) -> None:
        """Otherwise asking for an address would be enough to 'serve' it."""
        repl = self._sandbox(bridge)
        repl._handle_request("corpus_read", {"rel": "notes/absent.txt#L0-10"})
        assert repl.corpus_addresses_served == set()

    def test_path_helpers_serve_no_addresses(self, bridge) -> None:
        repl = self._sandbox(bridge)
        repl._handle_request("corpus_find", {"query": "budget"})
        repl._handle_request("corpus_count", {})
        repl._handle_request("corpus_coverage", {})
        assert repl.corpus_addresses_served == set()


class TestTheSandboxRemembersWhichBandAServedHitHad:
    """The label is served *and read back*: how much of the question a hit covered.

    A search already tells the model how much of its question each hit covered. The
    signal's first measurement (2026-09-16) found the model was served a `weak`
    match eight times and cited the hits anyway — a label nobody checks is a label
    that can be ignored, so the parent remembers the band beside the address and the
    submission guard reads its own label back.
    """

    def _sandbox(self, bridge) -> REPLSandbox:
        repl = REPLSandbox()
        repl._worker_sock = FakeWorkerSock()  # type: ignore[assignment]
        repl._corpus_bridge = bridge
        return repl

    def test_a_passage_cannot_label_itself(self) -> None:
        """The band is read from the hit's header line, never from its text.

        A passage that happens to quote the word `(weak)` — a note about this very
        label, a transcript of a run — must not thereby acquire a band, because a
        band is a judgement the harness made and the passage is the thing being
        judged. The `{}` case is the AGENTS.md §1.8 corollary in miniature: the
        harness said nothing about this hit, so the answer is `unknown`, and unknown
        is not a verdict.
        """
        from rlm_local.repl import _served_bands

        # No label in the header, `(weak)` in the passage: this hit carries none.
        assert _served_bands([
            "notes/x.txt#L0-10  [raw]\n    the note says the search called it (weak)"
        ]) == {}

        # And when the header does carry a label, that is the one recorded.
        assert _served_bands([
            "notes/x.txt#L0-10  [raw, covers 1/4 of the question's words (weak)]\n"
            "    the passage's own text says (strong) and (partial)"
        ]) == {"notes/x.txt#L0-10": "weak"}

    def test_a_search_remembers_the_band_its_hit_was_served_with(self, bridge) -> None:
        # Four content words in the question and none of them in `budget.md`: the
        # honest band is `none`, and that is what the sandbox must remember.
        bridge.question = "Who ratified the Lisbon protocol safeguards?"
        repl = self._sandbox(bridge)
        repl._handle_request("corpus_search", {"query": "sails"})
        bands = repl.corpus_address_bands
        assert bands, "a hit served with a label must be remembered with it"
        assert set(bands.values()) == {"none"}

    def test_the_strongest_band_for_an_address_is_the_one_kept(self, bridge) -> None:
        """A later, worse match must not demote an address already judged good."""
        repl = self._sandbox(bridge)
        bridge.question = "Which sails?"
        repl._handle_request("corpus_search", {"query": "sails"})
        address, band = next(iter(repl.corpus_address_bands.items()))
        assert band == "strong"
        # The same hit, found again by a question it does not answer: the hit has
        # not changed, and the harness has already told the model it answers.
        bridge.question = "Who ratified the Lisbon protocol safeguards?"
        repl._handle_request("corpus_search", {"query": "sails"})
        assert repl.corpus_address_bands[address] == "strong"

    def test_an_unlabelled_address_has_no_band(self, bridge) -> None:
        """`corpus_read` hands over a passage, not a judgement about it.

        Reading an address is evidence that the model *saw* it; it is not evidence
        that the passage answers the question. The band map must say so by staying
        silent rather than by inventing a band, and a silent band never refuses an
        answer (AGENTS.md §1.8: a check that cannot see the truth says `unknown`).
        """
        repl = self._sandbox(bridge)
        address = bridge.handle_search("sails")[0].split()[0]
        repl._handle_request("corpus_read", {"rel": address})
        assert address in repl.corpus_addresses_served
        assert repl.corpus_address_bands == {}


class TestTheSandboxReportsWhatItServed:
    """The parent logs the *result* of every helper call, not only a distribution.

    `corpus_search_quality` says how many of each band a search served; a citation
    audit needs to know *which* addresses and with which band, and the parent is
    the only party that can know: the hit list lives inside a tool result the model
    may never print, which is exactly the state the first match-quality
    measurement found itself in (RO10, 2026-09-17).
    """

    def _sandbox_with(self, bridge, calls: list) -> REPLSandbox:
        repl = REPLSandbox()
        repl._worker_sock = FakeWorkerSock()  # type: ignore[assignment]
        repl._corpus_bridge = bridge

        def report(verb, query, addresses, chars, ok):
            calls.append({"verb": verb, "query": query, "addresses": addresses,
                          "chars": chars, "ok": ok})

        repl._corpus_serve_logger = report
        return repl

    def test_a_search_reports_every_address_with_its_band(self, bridge) -> None:
        bridge.question = "Which sails?"
        calls: list = []
        repl = self._sandbox_with(bridge, calls)
        repl._handle_request("corpus_search", {"query": "sails"})

        assert len(calls) == 1
        call = calls[0]
        assert call["verb"] == "corpus_search"
        assert call["query"] == "sails"
        assert call["ok"] is True
        assert call["chars"] > 0
        assert [a["band"] for a in call["addresses"]] == ["strong"]
        assert all(a["address"].startswith("notes/budget.md#L") for a in call["addresses"])

    def test_a_read_reports_the_address_it_resolved_with_no_band(self, bridge) -> None:
        """A read hands over a passage without judging whether it answers."""
        calls: list = []
        repl = self._sandbox_with(bridge, calls)
        address = bridge.handle_search("sails")[0].split()[0]
        repl._handle_request("corpus_read", {"rel": address})

        assert calls[0]["verb"] == "corpus_read"
        assert [a["address"] for a in calls[0]["addresses"]] == [address]
        assert calls[0]["addresses"][0]["band"] is None

    def test_a_failed_call_is_reported_as_a_failure_that_served_nothing(self, bridge) -> None:
        calls: list = []
        repl = self._sandbox_with(bridge, calls)
        repl._handle_request("corpus_read", {"rel": "notes/absent.txt#L0-10"})

        assert calls[0]["ok"] is False
        assert calls[0]["addresses"] == []

    def test_a_call_that_serves_no_address_is_still_recorded(self, bridge) -> None:
        """`corpus_coverage` hands over no address, and that is a fact worth having."""
        calls: list = []
        repl = self._sandbox_with(bridge, calls)
        repl._handle_request("corpus_coverage", {})

        assert [c["verb"] for c in calls] == ["corpus_coverage"]
        assert calls[0]["addresses"] == []


class TestTheSandboxCountsCorpusCalls:
    """`corpus_calls` is the evidence the root loop nudges on.

    The third live run submitted "not mentioned in the corpus" after a single
    `print(len(context))` — no search, no read, no helper call of any kind. The
    parent process serves every helper request, so "did it look?" is a fact the
    harness owns rather than something it has to guess from the model's prose.
    These tests pin the fact to the mechanism.
    """

    def _sandbox_with(self, bridge) -> REPLSandbox:
        repl = REPLSandbox()
        repl._worker_sock = FakeWorkerSock()  # type: ignore[assignment]
        repl._corpus_bridge = bridge
        return repl

    def test_a_fresh_sandbox_has_not_looked(self) -> None:
        assert REPLSandbox().corpus_calls == 0

    @pytest.mark.parametrize("msg_type,msg", [
        ("corpus_find", {"query": "budget"}),
        ("corpus_list", {"rel": ""}),
        ("corpus_stat", {"rel": "x"}),
        ("corpus_read", {"rel": "x"}),
        ("corpus_count", {}),
        ("corpus_coverage", {}),
        ("corpus_search", {"query": "budget"}),
    ])
    def test_every_served_verb_counts(self, msg_type: str, msg: dict) -> None:
        repl = self._sandbox_with(RecordingBridge())
        repl._handle_request(msg_type, msg)
        assert repl.corpus_calls == 1, f"{msg_type} was served but not counted"

    def test_a_failed_call_still_counts_as_having_looked(self) -> None:
        """The nudge asks "did the model look at the corpus", not "did it win".

        A search that raises is still a search: the model read the corpus and
        learned something from the failure. Nudging it to search *again* would
        restate an instruction it already followed.
        """
        class Exploding(RecordingBridge):
            def handle_search(self, query, k=8, include_vendored=False):  # type: ignore[override]
                raise RuntimeError("index is busy")

        repl = self._sandbox_with(Exploding())
        repl._handle_request("corpus_search", {"query": "x"})
        assert repl.corpus_calls == 1

    def test_even_a_typoed_verb_counts_as_an_attempt(self) -> None:
        """`corpus_teleport` is not data, but the model *tried* to reach the corpus.

        Counting attempts rather than successes is deliberate: any request the
        parent answers puts a harness-written error back into the cell, which is
        feedback the model can act on. The nudge exists for the run that produced
        no corpus request at all — where there is nothing to act on.
        """
        repl = self._sandbox_with(RecordingBridge())
        repl._handle_request("corpus_teleport", {})
        assert repl.corpus_calls == 1

    def test_without_a_corpus_nothing_was_looked_at(self) -> None:
        repl = self._sandbox_with(None)
        repl._handle_request("corpus_find", {"query": "anything"})
        assert repl.corpus_calls == 0

    def test_calls_accumulate_across_verb_kinds(self) -> None:
        repl = self._sandbox_with(RecordingBridge())
        repl._handle_request("corpus_find", {"query": "a"})
        repl._handle_request("corpus_read", {"rel": "b"})
        repl._handle_request("corpus_coverage", {})
        assert repl.corpus_calls == 3


class TestCorpusHelpersInALiveCell:
    def test_a_cell_can_search_and_read_the_corpus(self, corpus: Path, bridge) -> None:
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context needed", MockSubcallMgr())
        try:
            found = repl.execute("print(corpus_find('budget'))")
            assert "notes/budget.md" in found.stdout
            read = repl.execute("print(corpus_read('notes/budget.md'))")
            assert "sails and ropes" in read.stdout
            listed = repl.execute("print(corpus_list('notes'))")
            assert "notes/budget.md" in listed.stdout
            assert "memo.txt" not in listed.stdout  # one level only
            counted = repl.execute("print(corpus_count())")
            assert "5 entries" in counted.stdout
            assert "3 files" in counted.stdout
            stat = repl.execute("print(corpus_stat('index.html'))")
            assert "kind: file" in stat.stdout
        finally:
            repl.shutdown()

    def test_a_cell_cannot_read_outside_the_corpus(self, corpus: Path, bridge) -> None:
        outside = corpus.parent / "outside.txt"
        outside.write_text("not yours\n", encoding="utf-8")
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context", MockSubcallMgr())
        try:
            result = repl.execute("print(corpus_read('../outside.txt'))")
            assert "refused" in result.stdout
            assert "not yours" not in result.stdout
        finally:
            repl.shutdown()

    def test_the_corpus_is_unchanged_by_a_cell(self, corpus: Path, bridge) -> None:
        """The end-to-end read-only check: mtimes and sizes, before and after."""
        def snapshot() -> dict[str, tuple[int, float]]:
            out: dict[str, tuple[int, float]] = {}
            for path in sorted(corpus.rglob("*")):
                st = path.lstat()
                out[str(path.relative_to(corpus))] = (st.st_size, st.st_mtime)
            return out

        before = snapshot()
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context", MockSubcallMgr())
        try:
            repl.execute(
                "print(corpus_find('.'))\n"
                "print(corpus_read('notes/budget.md'))\n"
                "print(corpus_list(''))\n"
                "print(corpus_count())\n"
                "print(corpus_stat('notes'))\n"
            )
        finally:
            repl.shutdown()
        assert snapshot() == before

    def test_corpus_find_returns_a_list_the_model_can_iterate(
        self, corpus: Path, bridge,
    ) -> None:
        """`len(hits)`, `hits[0]` and iteration must work: the shape *is* the contract.

        The owner's finding (2026-09-20), reading the second question set: the model
        "tried to loop over the characters of the string returned by `corpus_find`" —
        because `corpus_search` had taught it that a list of hits is a list. One verb
        returning a list and its sibling returning a blob is a trap, not a contract,
        and the model paid a whole turn for it.
        """
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context needed", MockSubcallMgr())
        try:
            shape = repl.execute(
                "hits = corpus_find('budget')\n"
                "print(type(hits).__name__, len(hits))\n"
                "print(hits[0][:20])"
            )
            assert shape.stderr == "", shape.stderr
            assert shape.stdout.splitlines()[0].startswith("list "), shape.stdout
            assert "notes/budget.md" in shape.stdout
            # A miss is still a list: one element carrying the reason, as a search does.
            empty = repl.execute("print(type(corpus_find('nothing-like-this')).__name__)")
            assert empty.stdout.strip() == "list", empty.stdout
        finally:
            repl.shutdown()

    def test_a_wrong_keyword_is_answered_instead_of_raising(
        self, corpus: Path, bridge,
    ) -> None:
        """A mistyped parameter costs one tool result, not a turn and a traceback.

        Measured on the second question set: question 1 logged exactly two `stderr`
        guardrails, which is what `corpus_search('…', limit=5)` produces when the
        wrapper takes `k=`. The traceback told the model nothing it could use; the
        message must name the helper and the parameters it does take.
        """
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context needed", MockSubcallMgr())
        try:
            result = repl.execute("print(corpus_search('budget', limit=5))")
            assert result.stderr == "", result.stderr
            assert "corpus_search" in result.stdout, result.stdout
            assert "k=" in result.stdout, result.stdout
            assert "Traceback" not in result.stdout
            # …and for a verb whose arguments are entirely different, so the message
            # is built from the signature rather than hard-coded for one case.
            other = repl.execute("print(corpus_count(limit=5))")
            assert other.stderr == "", other.stderr
            assert "corpus_count" in other.stdout, other.stdout
            assert "Traceback" not in other.stdout
        finally:
            repl.shutdown()

    def test_a_cell_without_a_corpus_gets_an_honest_answer(self) -> None:
        repl = REPLSandbox(cell_timeout=30.0)
        repl.start("no context", MockSubcallMgr())
        try:
            result = repl.execute("print(corpus_find('anything'))")
            assert "no corpus is configured" in result.stdout
        finally:
            repl.shutdown()

    def test_a_cell_can_search_the_words_inside_the_corpus(
        self, corpus: Path, bridge,
    ) -> None:
        """The helper this whole step exists for: text, not names."""
        text_index = bridge.index.text()
        text_index.ensure()
        (corpus / "notes" / "budget.md").write_text(
            "The engine was Godot and the budget was tight.\n", encoding="utf-8"
        )
        text_index.add_text(
            raw=b"notes/budget.md", display="notes/budget.md", source_hash="h",
            text=(corpus / "notes" / "budget.md").read_bytes(),
            # The file on disk changed, so the index has to replace what it held:
            # `add_text` is idempotent by default, which is right for a first pass
            # and wrong for a file that has been edited (the dynamic-corpus gap).
            replace=True,
        )
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context", MockSubcallMgr())
        try:
            result = repl.execute(
                "hits = corpus_search('Godot')\n"
                "print(type(hits).__name__, len(hits))\n"
                "print(hits[0])\n"
                "print(corpus_coverage())\n"
            )
            assert "list 1" in result.stdout, "a list of hits must be a list"
            assert "notes/budget.md#L" in result.stdout
            assert "Godot" in result.stdout
            assert "coverage" in result.stdout
        finally:
            repl.shutdown()

    def test_a_cell_can_read_back_a_search_hit(self, corpus: Path, bridge) -> None:
        """A hit is an address; reading it is what makes the citation real."""
        text_index = bridge.index.text()
        text_index.ensure()
        (corpus / "notes" / "song.txt").write_text(
            "Vantrel sang it first.\n", encoding="utf-8"
        )
        text_index.add_text(
            raw=b"notes/song.txt", display="notes/song.txt", source_hash="h",
            text=(corpus / "notes" / "song.txt").read_bytes(),
        )
        repl = REPLSandbox(cell_timeout=30.0)
        repl._corpus_bridge = bridge
        repl.start("no context", MockSubcallMgr())
        try:
            result = repl.execute(
                "hits = corpus_search('Vantrel')\n"
                "print(len(hits))\n"
                "print(corpus_read(hits[0]))\n"
            )
            assert "notes/song.txt#L" in result.stdout
            assert "Vantrel sang it first." in result.stdout
        finally:
            repl.shutdown()


class ScriptedBackend:
    """A ModelBackend that submits an answer immediately, recording messages."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def chat(self, messages, *, tier="root", max_tokens=1500, temperature=0.0,
             response_schema=None) -> str:
        self.system_prompts.append(
            next(m["content"] for m in messages if m.get("role") == "system")
        )
        return "\n".join([
            "```repl",
            "answer['content'] = 'done'",
            "answer['ready'] = True",
            "```",
        ])


class TestThePromptMatchesTheWorker:
    """A run with a corpus must advertise exactly what the worker can do."""

    def _run(self, corpus_bridge):
        from rlm_local.config import load_config
        from rlm_local.root_loop import RootLoop

        backend = ScriptedBackend()
        loop = RootLoop(load_config("tiny", max_turns=2), backend,
                        kernel_bridge=None, corpus_bridge=corpus_bridge)
        try:
            loop.run("What is in the corpus?", "stub context")
        finally:
            loop.shutdown()
        return backend

    def test_the_system_prompt_names_the_corpus_and_its_helpers(
        self, corpus: Path, bridge,
    ) -> None:
        backend = self._run(bridge)
        prompt = backend.system_prompts[0]
        assert "The corpus (read-only)" in prompt
        assert str(corpus) in prompt
        for verb in CORPUS_VERBS:
            assert verb in prompt, f"{verb} is callable but not advertised"
        assert "Never walk the corpus" in prompt

    def test_without_a_corpus_the_section_is_absent(self) -> None:
        backend = self._run(None)
        prompt = backend.system_prompts[0]
        assert "The corpus (read-only)" not in prompt
        assert "corpus_read" not in prompt

    def test_a_bridge_without_an_index_says_so_in_the_prompt(self, corpus: Path) -> None:
        backend = self._run(CorpusBridge.open_for(corpus, None))
        assert "No path index" in backend.system_prompts[0]
