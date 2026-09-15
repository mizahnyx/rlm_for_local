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
            "Cuicani sang it first.\n", encoding="utf-8"
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
                "hits = corpus_search('Cuicani')\n"
                "print(len(hits))\n"
                "print(corpus_read(hits[0]))\n"
            )
            assert "notes/song.txt#L" in result.stdout
            assert "Cuicani sang it first." in result.stdout
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
