"""Integration test — calls RootLoop.run() and rlm_local.completion() end-to-end.

Per conformity review R1: this test must exercise the full harness pipeline
against a stub model backend, covering:
  (a) completion without kernel bridge
  (b) completion with KernelBridge backed by a temp seeded vault
  (c) turn-0 probe → code → answer-dict flow
  (d) forced-finalization path

This test SHOULD fail on the current commit (D1–D3) and pass after R2 fixes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from rlm_local import completion
from rlm_local.config import Config, load_config
from rlm_local.model_backend import ModelBackend
from rlm_local.root_loop import RootLoop


# ── Stub model backend ─────────────────────────────────────────────────────

class StubBackend:
    """A ModelBackend that returns scripted responses in sequence.

    A response may be an `Exception` instance, which is *raised* instead of
    returned — that is how a transport failure is scripted, and it is the only
    honest way to test that a run survives one.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, Any]] = []
        # Length of the byte-stable prefix (system prompt + metadata + prologue
        # + few-shot + first turn header), captured on the first call so tests
        # can tell live messages from prompt scaffolding.
        self.prefix_len: int | None = None

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "root",
        max_tokens: int = 1500,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        if self.prefix_len is None:
            self.prefix_len = len(messages)
        self.calls.append({
            "tier": tier,
            "message_count": len(messages),
            "last_role": messages[-1]["role"] if messages else None,
            "messages": [dict(m) for m in messages],
        })
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        # Default: answer-dict response
        return "\n".join([
            "I'll submit the answer now.",
            "```repl",
            "answer['content'] = '42'",
            "answer['ready'] = True",
            "```",
        ])

    def user_messages(self) -> list[str]:
        """Every user-role message the loop has sent so far (incl. prefix)."""
        return [
            m["content"]
            for call in self.calls
            for m in call["messages"]
            if m.get("role") == "user"
        ]

    def live_user_messages(self) -> list[str]:
        """User messages appended *during* the run, each counted once.

        The message list is cumulative and the bundled few-shot in the prefix
        is itself made of `REPL output:` messages, so live output is read from
        the last call's post-`prefix_len` slice.
        """
        if self.prefix_len is None or not self.calls:
            return []
        return [
            m["content"]
            for m in self.calls[-1]["messages"][self.prefix_len:]
            if m.get("role") == "user"
        ]

    def live_assistant_messages(self) -> list[str]:
        if self.prefix_len is None or not self.calls:
            return []
        return [
            m["content"]
            for m in self.calls[-1]["messages"][self.prefix_len:]
            if m.get("role") == "assistant"
        ]

    def count_appended(self, text: str) -> int:
        """How many times `text` was appended as a user message during the run."""
        return sum(1 for m in self.live_user_messages() if m == text)

    def trailing_user_messages(self) -> list[str]:
        """The last user message of each call — i.e. what the loop just appended.

        Needed for counting: the message list is cumulative, so a nudge sent
        once appears in every later call's history.
        """
        out = []
        for call in self.calls:
            msgs = call["messages"]
            if msgs and msgs[-1].get("role") == "user":
                out.append(msgs[-1]["content"])
        return out


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tiny_cfg() -> Config:
    return load_config("tiny")


@pytest.fixture
def seeded_vault():
    """Create a temp vault with seed pages + core memory for bridge tests."""
    from rlm_kernel.seed import seed_vault
    from rlm_kernel.vault import LocalVault

    td = tempfile.TemporaryDirectory(prefix="rlm_int_vault_", ignore_cleanup_errors=True)
    vault = LocalVault(Path(td.name), init_git=False)
    seed_vault(vault)
    # Write core memory so the bridge has something to inject
    from rlm_kernel.memory import MemoryManager
    mgr = MemoryManager()
    mgr.write_core(vault, "Test instance — integration test.")
    yield vault
    td.cleanup()


@pytest.fixture
def bridge(seeded_vault):
    """KernelBridge over a seeded temp vault with rebuilt index."""
    from rlm_kernel.index import rebuild_index
    from rlm_kernel.repl_bridge import KernelBridge

    idx_path = seeded_vault.root / ".index" / "meta.sqlite"
    rebuild_index(seeded_vault, idx_path)
    return KernelBridge(seeded_vault, idx_path)


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestRootLoopIntegration:
    """End-to-end tests through RootLoop.run()."""

    def test_basic_completion_without_kernel(self, tiny_cfg):
        """(a) A full completion without kernel bridge."""
        backend = StubBackend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("What is the answer?", "Some context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_completion_with_kernel_bridge(self, tiny_cfg, bridge):
        """(b) A completion with KernelBridge backed by a seeded vault."""
        backend = StubBackend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=bridge)
        try:
            answer = loop.run("What is the answer?", "Some context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_turn_zero_probe_then_answer(self, tiny_cfg):
        """(c) Turn-0 probe, turn-1 answer-dict flow."""
        backend = StubBackend(responses=[
            # Turn 0 — probe
            "\n".join([
                "I'll probe the context first.",
                "```repl",
                "print(f'Context length: {len(context)}')",
                "```",
            ]),
            # Turn 1 — answer
            "\n".join([
                "Got it. Submitting.",
                "```repl",
                "answer['content'] = 'probed-and-answered'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Probe test", "Test context data")
        finally:
            loop.shutdown()

        assert answer == "probed-and-answered"

    def test_forced_finalization_path(self, tiny_cfg):
        """(d) Model never sets answer-dict → forced finalization."""
        # Return responses with no ```repl blocks and no answer-dict
        # After nudges are exhausted, forced finalization triggers
        backend = StubBackend(responses=[
            "The sky is blue.",   # no code block → nudge
            "I think 42.",        # no code block → nudge
            "Maybe 43.",          # nudge budget exhausted → error
            "Let me think...",    # consecutive errors → forced finalize
            "FINAL: best effort 42",
        ] * 3)  # enough for all turns
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Forced test", "Context")
        finally:
            loop.shutdown()

        assert answer is not None
        assert isinstance(answer, str)
        assert len(answer) > 0


class TestAnswerFinalizationGuards:
    """R6 — empty submissions are nudged, never silently swallowed.

    Before the fix, `answer['ready'] = True` with `content == ""` produced
    `final_answer = ""`, which the loop's truthiness check read as "not final",
    so the run continued without ever telling the model what went wrong. The
    nudge that existed for exactly this case (`NUDGE_EMPTY_ANSWER`) was never
    emitted anywhere.
    """

    def test_empty_submission_is_nudged_then_real_answer_wins(self, tiny_cfg):
        from rlm_local.templates import NUDGE_EMPTY_ANSWER

        backend = StubBackend(responses=[
            # Turn 0 — claims readiness with nothing in it.
            "\n".join([
                "Submitting now.",
                "```repl",
                "answer['content'] = ''",
                "answer['ready'] = True",
                "```",
            ]),
            # Turn 1 — the real thing.
            "\n".join([
                "Sorry — here it is.",
                "```repl",
                "answer['content'] = 'the real answer'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Empty answer test", "Some context")
        finally:
            loop.shutdown()

        assert answer == "the real answer"
        from rlm_local.templates import NUDGE_EMPTY_ANSWER
        assert any(NUDGE_EMPTY_ANSWER in m for m in backend.user_messages())

    def test_whitespace_only_submission_is_treated_as_empty(self, tiny_cfg):
        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "answer['content'] = '   \\n  '",
                "answer['ready'] = True",
                "```",
            ]),
            "\n".join([
                "```repl",
                "answer['content'] = 'actual'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Whitespace answer test", "Some context")
        finally:
            loop.shutdown()
        assert answer == "actual"

    def test_empty_submission_nudge_text_reaches_the_model(self, tiny_cfg):
        from rlm_local.templates import NUDGE_EMPTY_ANSWER

        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "answer['content'] = ''",
                "answer['ready'] = True",
                "```",
            ]),
            "\n".join([
                "```repl",
                "answer['content'] = 'fixed'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            loop.run("Nudge content test", "Some context")
        finally:
            loop.shutdown()

        nudges = [
            m for c in backend.calls for m in c["messages"]
            if m["role"] == "user" and NUDGE_EMPTY_ANSWER in m["content"]
        ]
        assert nudges, "the empty-answer nudge must be sent to the model"

    def test_repeated_empty_submissions_do_not_spin_forever(self, tiny_cfg):
        """Nudges are bounded by max_consecutive_nudges, then forced finalize."""
        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "answer['content'] = ''",
                "answer['ready'] = True",
                "```",
            ])
        ] * 50 + ["FINAL: gave up gracefully"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Spin test", "Some context")
        finally:
            loop.shutdown()

        # 1..max_turns turns, plus at most one forced-finalization call.
        assert len(backend.calls) <= tiny_cfg.max_turns + 2, (
            f"loop made {len(backend.calls)} model calls"
        )
        assert answer.strip() != ""

    def test_empty_answer_nudge_budget_is_respected(self, tiny_cfg):
        from rlm_local.templates import NUDGE_EMPTY_ANSWER

        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "answer['content'] = ''",
                "answer['ready'] = True",
                "```",
            ])
        ] * 50 + ["FINAL: done"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            loop.run("Nudge budget test", "Some context")
        finally:
            loop.shutdown()

        # The message history is cumulative, so count appended messages only.
        nudges = backend.count_appended(NUDGE_EMPTY_ANSWER)
        assert nudges <= tiny_cfg.max_consecutive_nudges, (
            f"{nudges} empty-answer nudges exceeds the budget "
            f"{tiny_cfg.max_consecutive_nudges}"
        )
        assert nudges >= 1


@pytest.fixture
def corpus_bridge(tmp_path: Path):
    """A real read-only bridge over a tiny corpus, for corpus runs.

    Classified and text-indexed as well as path-indexed, because a corpus run
    that cannot actually *search* is not the thing these tests are about — and
    under the served-citation rule (RO4, 2026-09-16) a run can only cite an
    address a helper returned, which needs a text index to exist.
    """
    from rlm_kernel.classify import classify_entries
    from rlm_kernel.corpus import CorpusBridge, CorpusIndex
    from rlm_kernel.mounts import LocalTreeMount

    root = tmp_path / "corpus"
    (root / "notes").mkdir(parents=True)
    # Exactly 21 bytes on purpose: the chunk address is therefore
    # `notes/song.txt#L0-21`, which is the address the tests below cite — and
    # under the served-citation rule a fixture whose served address and cited
    # address differ would test the refusal instead of the acceptance.
    (root / "notes" / "song.txt").write_text("Cuicani sang it first",
                                             encoding="utf-8")
    # A second searchable file holding none of the graded question's words: the
    # fixture for the absence-band rule, which needs a *real* hit that the search
    # labels `none`. Exactly 21 bytes again, so the address is
    # `notes/kettle.txt#L0-21`.
    (root / "notes" / "kettle.txt").write_text("The kettle boiled dry",
                                               encoding="utf-8")
    index_path = tmp_path / "derived" / "corpus.sqlite"
    index_path.parent.mkdir()
    mount = LocalTreeMount(root)
    idx = CorpusIndex.open_for(root, index_path)
    idx.build(mount)
    table = idx.classifications()
    table.ensure()
    classify_entries(mount, table)
    text_index = idx.text()
    text_index.ensure()
    text_index.add_text(
        raw=b"notes/song.txt", display="notes/song.txt", source_hash="h-song",
        text=(root / "notes" / "song.txt").read_bytes(),
    )
    text_index.add_text(
        raw=b"notes/kettle.txt", display="notes/kettle.txt",
        source_hash="h-kettle",
        text=(root / "notes" / "kettle.txt").read_bytes(),
    )
    b = CorpusBridge(mount=mount, index=idx)
    yield b
    b.close()


class TestCorpusUnsearchedNudge:
    """A corpus run that never touched the corpus is nudged, not accepted.

    Live run 3 answered "not mentioned in the corpus" after a single
    `print(len(context))`: no `corpus_search`, no `corpus_read`, nothing. The
    harness served every helper request, so it knows the difference between
    "searched and found nothing" and "never looked" — and only the second one is
    a lie about the corpus.
    """

    def test_a_submission_with_no_helper_call_is_refused_once(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNSEARCHED

        backend = StubBackend(responses=[
            # Turn 0 — the live-run-3 shape: submission with nothing behind it.
            "\n".join([
                "I checked the context.",
                "```repl",
                "print(len(context))",
                "answer['content'] = 'not mentioned in the corpus'",
                "answer['ready'] = True",
                "```",
            ]),
            # Turn 1 — a real search, then a cited answer. The citation is what
            # keeps this test about the *unsearched* guard: without it the answer
            # is refused for citing nothing and the assertions below would be
            # measuring the other guard.
            "\n".join([
                "```repl",
                "hits = corpus_search('Cuicani')",
                "print(hits)",
                "answer['content'] = 'found it\\nCitations: notes/song.txt#L0-21'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert answer.startswith("found it")
        assert backend.count_appended(NUDGE_CORPUS_UNSEARCHED) == 1

    def test_an_unsupported_claim_is_not_accepted_the_first_time(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """The unsearched answer must not become the run's final answer.

        The question is the one the cited passage answers: under the absence-band
        rule (2026-09-16) a citation whose hit the search labelled `weak`/`none` is
        refused, so a question and a citation that disagree are a different test.
        """
        unsearched = "\n".join([
            "```repl",
            "answer['content'] = 'nothing here'",
            "answer['ready'] = True",
            "```",
        ])
        searched = "\n".join([
            "```repl",
            "hits = corpus_search('Cuicani')",
            "print(hits)",
            "answer['content'] = ('the searched answer\\nCitations: '"
            " + hits[0].split()[0])",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[unsearched, searched])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()
        assert answer.startswith("the searched answer")

    def test_a_search_before_submitting_is_accepted_without_a_nudge(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """The nudge is evidence-based: one helper call and it never fires."""
        from rlm_local.templates import NUDGE_CORPUS_UNSEARCHED

        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "print(corpus_coverage())",
                # One helper call of any kind satisfies the unsearched guard, and
                # `corpus_coverage` serves no address — so the answer takes the
                # absence arm, which is exactly what that situation calls for.
                "answer['content'] = ('answered after looking: the corpus does not "
                "contain this.\\n[coverage: unknown]')",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert answer.startswith("answered after looking")
        assert backend.count_appended(NUDGE_CORPUS_UNSEARCHED) == 0
        # One model call means the first submission was the answer: no retry.
        assert len(backend.calls) == 1

    def test_without_a_corpus_the_nudge_does_not_exist(
        self, tiny_cfg,
    ) -> None:
        """A plain (non-corpus) run must not be told to search a corpus."""
        from rlm_local.templates import NUDGE_CORPUS_UNSEARCHED

        backend = StubBackend(responses=[
            "```repl\nanswer['content'] = 'no corpus here'\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert answer == "no corpus here"
        assert backend.count_appended(NUDGE_CORPUS_UNSEARCHED) == 0

    def test_the_nudge_budget_is_bounded(self, tiny_cfg, corpus_bridge) -> None:
        """A model that ignores the nudge forever must still terminate."""
        from rlm_local.templates import NUDGE_CORPUS_UNSEARCHED

        backend = StubBackend(responses=[
            "```repl\nanswer['content'] = 'still not looking'\nanswer['ready'] = True\n```",
        ] * 50 + ["FINAL: gave up"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        nudges = backend.count_appended(NUDGE_CORPUS_UNSEARCHED)
        assert 0 < nudges <= tiny_cfg.max_consecutive_nudges, (
            f"{nudges} corpus nudges exceeds the budget "
            f"{tiny_cfg.max_consecutive_nudges}"
        )
        assert answer.strip() != ""
        assert len(backend.calls) <= tiny_cfg.max_turns + 2


class TestCorpusCitationTelemetry:
    """RO4: whether a corpus answer carried its evidence is *measured*, not assumed.

    Owner call (2026-09-14), after the live rerun read three passages, printed
    seven addresses, and submitted an answer citing none of them: require
    citations in the prompt first, measure, and add a refusal only if the
    requirement does not take. So the harness records the fact and never blocks
    on it — two counters on the loop, and a `corpus_citation` guardrail event in
    the trajectory that an operator can count with `grep`.
    """

    def _logger(self, tmp_path: Path):
        from rlm_local.logger import TrajectoryLogger

        return TrajectoryLogger(tmp_path / "traj.jsonl")

    def _guardrails(self, logger) -> list[dict]:
        import json

        events = []
        for line in logger.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "guardrail":
                events.append(ev)
        return events

    def test_an_answer_with_no_address_is_recorded_as_uncited(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        """An *accepted* answer with no address is measured as uncited.

        The answer names coverage, which is the escape hatch: it is accepted even
        though it cites nothing, and the record says exactly that. The refusal
        path has its own tests; this one is about the measurement.
        """
        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "print(corpus_search('Cuicani'))",
                "answer['content'] = ('It is mentioned without quoting anything.\\n"
                "[coverage: 999 sources indexed (0.0% of the text files)]')",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        logger = self._logger(tmp_path)
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 1
        events = [e for e in self._guardrails(logger)
                  if e.get("guardrail") == "corpus_citation"]
        assert len(events) == 1, "one citation record per accepted answer"
        assert "answers_with_address=False" in events[0]["detail"]

    def test_an_answer_carrying_an_address_is_recorded_as_cited(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        backend = StubBackend(responses=[
            "\n".join([
                "```repl",
                "print(corpus_search('Cuicani'))",
                "answer['content'] = ('It is in the notes.\\n"
                "Citations: notes/song.txt#L0-21')",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        logger = self._logger(tmp_path)
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 0
        events = [e for e in self._guardrails(logger)
                  if e.get("guardrail") == "corpus_citation"]
        assert "answers_with_address=True" in events[0]["detail"]

    def test_a_run_without_a_corpus_records_nothing(
        self, tiny_cfg, tmp_path,
    ) -> None:
        backend = StubBackend(responses=[
            "```repl\nanswer['content'] = 'plain answer'\nanswer['ready'] = True\n```",
        ])
        logger = self._logger(tmp_path)
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None)
        try:
            loop.run("Question", "Some context")
        finally:
            loop.shutdown()

        assert loop.corpus_answers == 0
        assert [e for e in self._guardrails(logger)
                if e.get("guardrail") == "corpus_citation"] == []

    def test_the_forced_finalization_answer_is_measured_too(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        """A run that never converges still produces an answer worth counting."""
        backend = StubBackend(responses=[
            "```repl\nraise ValueError('never converges')\n```",
        ] * 40 + ["FINAL: best effort with no citation"])
        logger = self._logger(tmp_path)
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 1


class TestCorpusCitationGuard:
    """RO4: an answer that cites nothing is refused, unless it names coverage.

    The prompt-only phase was measured and failed: three consecutive live runs of
    `Qwen3.5-4B-Abliterated` searched, read up to five passages, printed addresses,
    and cited none of them (0 for 3 —
    `docs/20260915-0655-corpus-citation-compliance-measured.md`). The owner made
    the refusal conditional on exactly that, so the guard exists now.

    Its escape hatch is the other half of the instruction, and it is the reason a
    refusal cannot loop: an answer is acceptable when it cites an address **or**
    when it says the corpus does not hold the answer and gives its coverage. On a
    partly-indexed corpus "nothing citable" is the common case, not the exception.
    """

    def _searched(self, content: str) -> str:
        """A cell that searches first (so the unsearched guard stays quiet)."""
        return "\n".join([
            "```repl",
            "print(corpus_search('Cuicani'))",
            content,
            "```",
        ])

    def test_an_uncited_answer_is_refused_and_then_a_cited_one_wins(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[
            self._searched("answer['content'] = 'A prose answer with no citation.'\n"
                           "answer['ready'] = True"),
            self._searched("answer['content'] = 'Cited.\\n"
                           "Citations: notes/song.txt#L0-21'\n"
                           "answer['ready'] = True"),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 1
        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 0

    def test_an_answer_that_names_coverage_is_accepted_without_a_nudge(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """The escape hatch: "I looked, it is not there, here is how much I saw"."""
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[
            self._searched(
                "answer['content'] = ('The corpus does not answer this.\\n"
                "[coverage: 999 sources indexed (0.0% of the text files)]')\n"
                "answer['ready'] = True"
            ),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "does not answer" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 0
        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 1  # measured: no address was cited
        assert len(backend.calls) == 1

    def test_a_courtesy_final_line_is_guarded_too(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """`FINAL:` is a submission channel, so both corpus rules apply to it.

        A pure `FINAL:` line with no cell isolates this path: the run has not
        called a helper, so it is told to look first, and once it has looked an
        uncited final line is told to cite.
        """
        from rlm_local.templates import NUDGE_CORPUS_UNCITED, NUDGE_CORPUS_UNSEARCHED

        backend = StubBackend(responses=[
            "FINAL: a prose answer with no cell and no citation",
            self._searched("answer['content'] = 'Cited.\\n"
                           "Citations: notes/song.txt#L0-21'\n"
                           "answer['ready'] = True"),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNSEARCHED) == 1
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 0

    def test_an_uncited_final_line_after_a_search_is_refused(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[
            self._searched("print('probe')"),
            "FINAL: a prose answer, uncited",
            self._searched("answer['content'] = 'Cited.\\n"
                           "Citations: notes/song.txt#L0-21'\n"
                           "answer['ready'] = True"),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 1

    def test_the_refusal_budget_is_bounded(self, tiny_cfg, corpus_bridge) -> None:
        """A model that never cites anything must still terminate."""
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        stubborn = self._searched(
            "answer['content'] = 'still uncited'\nanswer['ready'] = True")
        backend = StubBackend(responses=[stubborn] * 50 + ["FINAL: gave up"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        nudges = backend.count_appended(NUDGE_CORPUS_UNCITED)
        assert 0 < nudges <= tiny_cfg.max_consecutive_nudges
        assert answer.strip() != ""
        # Forced finalization accepted the last uncited answer, and it is measured.
        assert loop.corpus_answers >= 1

    def test_without_a_corpus_an_uncited_answer_is_simply_an_answer(
        self, tiny_cfg,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[
            "```repl\nanswer['content'] = 'no corpus, no citations needed'\n"
            "answer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Question", "Some context")
        finally:
            loop.shutdown()

        assert answer == "no corpus, no citations needed"
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 0


class TestForcedFinalizationCarriesProvenance:
    """RO4: the answer that *is* delivered must carry its evidence (2026-09-16).

    Measured twice: on an unanswerable question the model explored for the whole
    turn budget and never submitted, so the answer arrived by forced finalization
    — uncited, and with no coverage line, while the citation guard (which refuses
    uncited *submissions*) never fired at all. The terminal path is the one place
    an answer is guaranteed to be delivered, so the corpus requirement has to be
    restated there. Refusing at that point would be worse than useless: it would
    turn a weak answer into no answer.
    """

    def _exhausted_backend(self) -> StubBackend:
        return StubBackend(responses=[
            "```repl\nraise ValueError('never converges')\n```",
        ] * 40 + ["The corpus does not contain this. [coverage: complete]"])

    def test_a_corpus_run_is_asked_for_its_evidence(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import FORCED_FINALIZATION_CORPUS_PROMPT

        backend = self._exhausted_backend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert FORCED_FINALIZATION_CORPUS_PROMPT in backend.user_messages()
        assert "Citations:" in FORCED_FINALIZATION_CORPUS_PROMPT
        assert "corpus_coverage()" in FORCED_FINALIZATION_CORPUS_PROMPT
        # The escape arm has to be in the terminal prompt too, or the requirement
        # is unsatisfiable on a question the corpus cannot answer.
        assert "does not contain" in FORCED_FINALIZATION_CORPUS_PROMPT
        # ...and it is the *last* user message, i.e. the answer was asked for it
        # after the loop gave up rather than before.
        assert backend.trailing_user_messages()[-1] == FORCED_FINALIZATION_CORPUS_PROMPT

    def test_a_run_without_a_corpus_keeps_the_plain_prompt(
        self, tiny_cfg,
    ) -> None:
        from rlm_local.templates import (
            FORCED_FINALIZATION_CORPUS_PROMPT,
            FORCED_FINALIZATION_PROMPT,
        )

        backend = self._exhausted_backend()
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            loop.run("Question", "Some context")
        finally:
            loop.shutdown()

        assert FORCED_FINALIZATION_PROMPT in backend.user_messages()
        assert FORCED_FINALIZATION_CORPUS_PROMPT not in backend.user_messages()


class TestCorpusLastTurnNudge:
    """RO4: the last turn is where "I did not find it" has to become sayable.

    Three live runs on a question the corpus cannot answer each spent their whole
    budget exploring and were answered by forced finalization — 5/8, 8/8, 8/8
    turns. The turn header already says `Turn 8/8.`, so what was missing was not
    information about the budget: it was permission to stop. The nudge is appended
    *before* the last turn's model call, so it costs no extra turn — it changes
    what the final turn is for.
    """

    def _stuck_until(self, responses: list[str]):
        cfg = load_config("tiny", max_turns=3)
        return cfg, StubBackend(responses=responses)

    def test_a_corpus_run_is_told_its_last_turn_is_for_answering(
        self, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_LAST_TURN

        probing = "```repl\nprint(corpus_search('Cuicani'))\n```"
        answering = ("```repl\nanswer['content'] = 'Not in the corpus.\\n"
                     "Citations: notes/song.txt#L0-21'\nanswer['ready'] = True\n```")
        cfg, backend = self._stuck_until([probing, probing, answering])
        loop = RootLoop(cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        expected = NUDGE_CORPUS_LAST_TURN.format(turn=3, max_turns=3)
        assert expected in backend.user_messages()
        # It arrives as the last instruction before the final turn's call, and
        # exactly once — a nudge that repeats every turn is a nudge the model
        # learns to ignore.
        assert backend.trailing_user_messages()[-1] == expected
        assert sum(1 for m in backend.user_messages() if m == expected) >= 1
        assert "#L0-21" in answer
        # Both arms are named, or the nudge would push a model with nothing to
        # cite into inventing a citation.
        assert "Citations:" in expected
        assert "does not contain" in expected

    def test_an_answer_before_the_last_turn_is_not_nudged(
        self, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_LAST_TURN

        cfg, backend = self._stuck_until([
            "```repl\nprint(corpus_search('Cuicani'))\n"
            "answer['content'] = 'early.\\nCitations: notes/song.txt#L0-21'\n"
            "answer['ready'] = True\n```",
        ])
        loop = RootLoop(cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert not any(NUDGE_CORPUS_LAST_TURN.format(turn=3, max_turns=3) == m
                       for m in backend.user_messages())

    def test_a_run_without_a_corpus_is_never_nudged(self) -> None:
        from rlm_local.templates import NUDGE_CORPUS_LAST_TURN

        cfg, backend = self._stuck_until([
            "```repl\nprint('probing')\n```",
            "```repl\nprint('probing again')\n```",
            "```repl\nanswer['content'] = 'plain'\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Question", "Some context")
        finally:
            loop.shutdown()

        assert answer == "plain"
        assert not any("last turn" in m for m in backend.user_messages())

    def test_a_corpus_run_that_never_looked_is_not_told_to_stop(
        self, corpus_bridge,
    ) -> None:
        """The unsearched guard owns that case; two conflicting nudges is worse
        than one."""
        from rlm_local.templates import NUDGE_CORPUS_LAST_TURN

        cfg, backend = self._stuck_until([
            "```repl\nprint(len(context))\n```",
            "```repl\nprint(len(context))\n```",
        ] + ["FINAL: gave up"])
        loop = RootLoop(cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert not any(NUDGE_CORPUS_LAST_TURN.format(turn=3, max_turns=3) == m
                       for m in backend.user_messages())


class TestCitationsMustBeServed:
    """RO4: a citation the harness never handed over is refused (2026-09-16).

    A 4-turn run printed no addresses at all — it read nothing — and still ended
    with a `Citations:` line naming an address nobody had served it. A
    well-formed fabricated citation is worse than no citation, because it looks
    checkable, so the requirement is now evidence: the parent serves every corpus
    helper call and remembers what it served, and a submitted citation must be a
    member of that set.
    """

    #: Cites the address the search actually returned — the realistic path.
    _CITE_WHAT_WAS_SERVED = "\n".join([
        "```repl",
        "hits = corpus_search('Cuicani')",
        "print(hits)",
        "answer['content'] = 'It is in the notes.\\nCitations: ' + hits[0].split()[0]",
        "answer['ready'] = True",
        "```",
    ])

    #: Cites a well-formed address that no helper ever returned.
    _CITE_AN_INVENTION = "\n".join([
        "```repl",
        "print(corpus_search('Cuicani'))",
        "answer['content'] = ('It is in the notes.\\n"
        "Citations: notes/song.txt#L4000-4100')",
        "answer['ready'] = True",
        "```",
    ])

    def test_a_fabricated_citation_is_refused_then_a_served_one_wins(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[
            self._CITE_AN_INVENTION,
            self._CITE_WHAT_WAS_SERVED,
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L4000-4100" not in answer, "the invention must not be the answer"
        assert "Citations:" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 1
        assert loop.corpus_answers == 1

    def test_a_served_citation_is_accepted_first_time(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        backend = StubBackend(responses=[self._CITE_WHAT_WAS_SERVED])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "Citations:" in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 0
        assert loop.corpus_answers == 1
        assert loop.corpus_answers_uncited == 0

    def test_naming_coverage_does_not_excuse_an_invented_address(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """The escape arm is for absence, not for a fabricated citation."""
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        invented_with_coverage = "\n".join([
            "```repl",
            "print(corpus_search('Cuicani'))",
            "answer['content'] = ('The corpus does not contain this.\\n"
            "[coverage: 999 sources indexed (0.0% of the text files)]\\n"
            "Citations: notes/song.txt#L4000-4100')",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[
            invented_with_coverage,
            self._CITE_WHAT_WAS_SERVED,
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L4000-4100" not in answer
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 1

    def test_the_refusal_records_how_many_addresses_were_unserved(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        from rlm_local.logger import TrajectoryLogger

        import json

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            self._CITE_AN_INVENTION,
            self._CITE_WHAT_WAS_SERVED,
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        guardrails = [json.loads(line) for line in
                      logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        refusals = [g for g in guardrails
                    if g.get("event") == "guardrail"
                    and g.get("guardrail") == "corpus_uncited"]
        assert len(refusals) == 1
        assert "unserved=1" in refusals[0]["detail"]


class TestAnAnswerMustRestOnAnAnsweringMatch:
    """RO4: a served address is evidence only if the passage behind it answers (2026-09-16).

    The relevance label was measured before it was enforced, and the measurement
    said the label alone changes nothing: the run's searches were all labelled, the
    best hit covered one word of a twelve-word question, the model was served
    `weak` eight times, and it cited the hits and submitted anyway
    (`docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md`). So the
    parent reads its own label back at submission: when *every* address an answer
    cites was served as a `weak` or `none` hit, the answer rests on passages the
    harness itself told the model do not answer the question, and it is refused
    once — with the absence arm still open, because that arm is the answer to a
    question the corpus does not hold.

    The band is only ever read from a hit's own header line, and an address with no
    band (one handed over by `corpus_read`, or by a question with no content words)
    never refuses anything: a check that cannot see the truth says `unknown`.
    """

    #: A question whose four content words appear nowhere in `notes/kettle.txt`.
    _UNANSWERABLE = "Who ratified the Lisbon protocol safeguards?"

    #: Searches something the corpus really holds, and cites it as if it answered.
    _CITE_THE_NON_ANSWERING_HIT = "\n".join([
        "```repl",
        "hits = corpus_search('kettle')",
        "print(hits)",
        "answer['content'] = ('The corpus says the kettle boiled dry.\\n"
        "Citations: ' + hits[0].split()[0])",
        "answer['ready'] = True",
        "```",
    ])

    #: The same search, and the answer the guard is trying to reach.
    _ABSENCE_WITH_COVERAGE = "\n".join([
        "```repl",
        "print(corpus_search('kettle'))",
        "answer['content'] = ('The corpus does not contain this.\\n' "
        "+ corpus_coverage())",
        "answer['ready'] = True",
        "```",
    ])

    def test_an_answer_resting_on_a_non_answering_hit_is_refused(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        from rlm_local.templates import NUDGE_CORPUS_WEAK_EVIDENCE

        backend = StubBackend(responses=[
            self._CITE_THE_NON_ANSWERING_HIT,
            self._ABSENCE_WITH_COVERAGE,
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run(self._UNANSWERABLE, "stub context")
        finally:
            loop.shutdown()

        assert "kettle" not in answer, "the refused claim must not be the answer"
        assert "does not contain" in answer
        assert backend.count_appended(NUDGE_CORPUS_WEAK_EVIDENCE) == 1
        assert loop.corpus_answers == 1

    def test_the_absence_arm_is_open_on_the_first_submission(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """Naming the near-miss as a near-miss, with coverage, is an answer.

        Refusing this would be a trap, not a guard: on a question the corpus does
        not hold, "here is the nearest passage and it is not the answer" is the
        truthful answer, and it is the one the nudge asks for.
        """
        from rlm_local.templates import NUDGE_CORPUS_WEAK_EVIDENCE

        names_it_as_a_near_miss = "\n".join([
            "```repl",
            "hits = corpus_search('kettle')",
            "answer['content'] = ('The corpus does not contain this; the closest "
            "passage is not an answer.\\nCitations: ' + hits[0].split()[0] + "
            "'\\n' + corpus_coverage())",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[names_it_as_a_near_miss])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run(self._UNANSWERABLE, "stub context")
        finally:
            loop.shutdown()

        assert "does not contain" in answer
        assert backend.count_appended(NUDGE_CORPUS_WEAK_EVIDENCE) == 0
        assert loop.corpus_answers == 1
        assert len(backend.calls) == 1

    def test_one_answering_citation_is_enough(self, tiny_cfg, corpus_bridge) -> None:
        """The best cited band decides, so a strong hit is not sunk by a weak one."""
        from rlm_local.templates import NUDGE_CORPUS_WEAK_EVIDENCE

        two_citations = "\n".join([
            "```repl",
            "near_misses = corpus_search('kettle')",
            "hits = corpus_search('Cuicani')",
            "answer['content'] = ('It is in the notes.\\nCitations: ' + "
            "hits[0].split()[0] + '; ' + near_misses[0].split()[0])",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[two_citations])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert backend.count_appended(NUDGE_CORPUS_WEAK_EVIDENCE) == 0
        assert loop.corpus_answers == 1

    def test_a_question_with_no_content_words_cannot_label_a_citation(
        self, tiny_cfg, corpus_bridge,
    ) -> None:
        """Every word a stopword means no hit carries a band: say unknown, accept."""
        from rlm_local.templates import NUDGE_CORPUS_WEAK_EVIDENCE

        cite_an_unlabelled_hit = "\n".join([
            "```repl",
            "hits = corpus_search('kettle')",
            "answer['content'] = ('Cited.\\nCitations: ' + hits[0].split()[0])",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[cite_an_unlabelled_hit])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("What is it?", "stub context")
        finally:
            loop.shutdown()

        assert "#L0-21" in answer
        assert backend.count_appended(NUDGE_CORPUS_WEAK_EVIDENCE) == 0
        assert loop.corpus_answers == 1

    def test_the_refusal_records_the_band_it_refused_on(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        """The instrument: a weak refusal is its own event, not an uncited one."""
        import json

        from rlm_local.logger import TrajectoryLogger
        from rlm_local.templates import NUDGE_CORPUS_UNCITED

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            self._CITE_THE_NON_ANSWERING_HIT,
            self._ABSENCE_WITH_COVERAGE,
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run(self._UNANSWERABLE, "stub context")
        finally:
            loop.shutdown()

        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        refusals = [e for e in events if e.get("event") == "guardrail"
                    and e.get("guardrail") == "corpus_weak_citation"]
        assert len(refusals) == 1
        assert "band=none" in refusals[0]["detail"]
        assert "addresses=1" in refusals[0]["detail"]
        # And it is not counted as an uncited refusal: the answer did cite.
        assert backend.count_appended(NUDGE_CORPUS_UNCITED) == 0

    def test_the_weak_refusal_budget_is_bounded(self, tiny_cfg, corpus_bridge) -> None:
        """A model that keeps citing its near-miss must still terminate."""
        from rlm_local.templates import NUDGE_CORPUS_WEAK_EVIDENCE

        backend = StubBackend(responses=[self._CITE_THE_NON_ANSWERING_HIT] * 50
                              + ["FINAL: gave up"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run(self._UNANSWERABLE, "stub context")
        finally:
            loop.shutdown()

        nudges = backend.count_appended(NUDGE_CORPUS_WEAK_EVIDENCE)
        assert 0 < nudges <= tiny_cfg.max_consecutive_nudges
        assert answer.strip() != ""


class TestARunRecordsWhatEachHelperServed:
    """The served result is structured data in the trajectory, not just counts.

    The owner's review of a run asks two questions a distribution cannot answer:
    *was this citation served?* and *did the passage behind it answer the
    question?* The parent serves every helper call, so the parent writes the
    addresses and their bands into the trajectory — which is what makes a rendered
    trace auditable rather than decorative (RO10, 2026-09-17).
    """

    def test_a_search_leaves_the_addresses_and_their_bands(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        import json

        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            "```repl\nprint(corpus_search('Cuicani'))\n```",
            "```repl\nanswer['content'] = ('in the notes\\n"
            "Citations: notes/song.txt#L0-21')\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        served = [e for e in events if e.get("event") == "corpus_served"]
        searches = [e for e in served if e["verb"] == "corpus_search"]
        assert searches, "a search must leave a structured record of what it served"
        assert searches[0]["query"] == "Cuicani"
        assert searches[0]["ok"] is True
        assert [a["address"] for a in searches[0]["addresses"]] == ["notes/song.txt#L0-21"]
        assert searches[0]["addresses"][0]["band"] == "strong"
        assert searches[0]["turn"] >= 1


class TestATraceOfARealRunIsAuditable:
    """The viewer, run end-to-end over a trajectory the harness actually wrote.

    A synthetic fixture proves the renderer; this proves the *pair* — that a run
    records enough for the page to answer "was this citation served, and did it
    answer the question?" without inference.
    """

    def test_the_page_carries_the_band_and_the_passage_behind_it(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        from rlm_local.logger import TrajectoryLogger
        from rlm_local.traceview import (
            collect_passages,
            read_trajectory,
            render_run_markdown,
            render_summary,
        )

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            "```repl\nprint(corpus_search('Cuicani'))\n```",
            "```repl\nanswer['content'] = ('It is in the notes.\\n"
            "Citations: notes/song.txt#L0-21')\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()
        assert "notes/song.txt#L0-21" in answer

        run = read_trajectory(logger.path)
        audit = run.audit()
        assert audit.complete, audit.notes
        assert audit.cited_answering == ["notes/song.txt#L0-21"]
        assert audit.cited_unserved == []

        page = render_run_markdown(run, collect_passages(run, corpus_bridge))
        assert "**strong**" in page
        assert "Cuicani sang it first" in page
        assert "corpus_search" in page

        # The summary is the form that may travel: counts, no question, no address.
        summary = render_summary(run)
        assert "audit=complete" in summary
        assert "answers=1" in summary
        assert "notes/song.txt" not in summary
        assert "Cuicani" not in summary


class TestInvalidPythonIsRetriedWithoutPenalty:
    """A cell that does not compile is a formatting failure, not a reasoning one.

    The owner's finding (2026-09-17): *"Sometimes the model fails to produce valid
    Python, those steps must be repeated without penalty until valid Python is
    generated."* Before this, a `SyntaxError` cost a turn **and** counted against
    the consecutive-error budget, so a small model's single worst habit could end a
    run that had done nothing wrong: the cell never ran, so nothing was attempted
    and nothing was learned.
    """

    _BROKEN = "```repl\nthis is not python at all\n```"
    _GOOD = "```repl\nanswer['content'] = 'fixed'\nanswer['ready'] = True\n```"

    @staticmethod
    def _events(logger) -> list[dict]:
        import json

        return [json.loads(line) for line in
                logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @classmethod
    def _retries(cls, logger) -> list[dict]:
        """The `syntax_retry` events — the trajectory is the record, not the
        backend's message list (which only ever holds the last call's messages,
        and whose nudge equality check would test the template's placeholders)."""
        return [e for e in cls._events(logger) if e.get("guardrail") == "syntax_retry"]

    def test_a_syntax_error_costs_no_turn(self, tmp_path: Path) -> None:
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        cfg = load_config("tiny", max_turns=3)
        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[self._BROKEN, self._GOOD])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert answer == "fixed"
        assert len(self._retries(logger)) == 1
        # The retry happened *inside* turn 1: three turns were available, and the
        # broken cell spent none of them.
        end = [e for e in self._events(logger) if e.get("event") == "end"]
        assert end and end[0]["turns_used"] == 1, end
        # And the model was actually told why, in its own message stream.
        told = [e for e in self._events(logger)
                if e.get("event") == "root_message" and e.get("role") == "user"
                and "is not valid Python" in str(e.get("content"))]
        assert told, "the model must be told the cell did not run"

    def test_the_syntax_retry_does_not_touch_the_error_budget(
        self, tmp_path: Path,
    ) -> None:
        """Otherwise three broken cells force finalization on a healthy run."""
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        cfg = load_config("tiny", max_turns=4)
        backend = StubBackend(responses=[self._BROKEN] * 3 + [self._GOOD])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert answer == "fixed"
        assert len(self._retries(logger)) == 3
        # A runtime error would have counted, and the third one would have tripped
        # the error budget and forced finalization.
        assert [e for e in self._events(logger)
                if e.get("guardrail") == "stderr" and "consecutive_errors=1" in
                str(e.get("detail"))] == []

    def test_a_model_that_never_writes_valid_python_still_terminates(
        self, tmp_path: Path,
    ) -> None:
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        cfg = load_config("tiny", max_turns=3, max_syntax_retries=2)
        backend = StubBackend(responses=[self._BROKEN] * 40 + ["FINAL: gave up"])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert len(self._retries(logger)) == 2, "exactly the retry budget, no more"
        gave_up = [e for e in self._events(logger)
                   if e.get("guardrail") == "syntax_giveup"]
        assert gave_up, "giving up must be recorded, not silent"
        assert answer.strip() != ""

    def test_a_runtime_error_still_counts(self, tmp_path: Path) -> None:
        """Only *not compiling* is free; a cell that ran and raised is not."""
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        cfg = load_config("tiny", max_turns=3)
        backend = StubBackend(responses=[
            "```repl\nraise ValueError('the cell ran and failed')\n```",
            self._GOOD,
        ])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert answer == "fixed"
        assert self._retries(logger) == []
        counted = [e for e in self._events(logger) if e.get("guardrail") == "stderr"]
        assert counted and "consecutive_errors=1" in counted[0]["detail"]


class TestATimedOutCellCostsOneTurn:
    """A timeout spends the turn it happened in, and exactly one (2026-09-17).

    Found by a probe at the real limits (`scripts/probe_cell_budget.py`, 60 s soft):
    one timed-out cell then ended a two-turn run — `turn_start=1` of 2, the scripted
    submission never executed, its text delivered by forced finalization instead. The
    cause was placement rather than arithmetic: the timeout branch advanced the turn
    counter *inside* the block loop, so the turn loop's own advance became a second
    one. With `max_turns=8` every timeout was stealing two turns, which means every
    recorded run that hit a timeout reported a turn count it never had.
    """

    @staticmethod
    def _events(logger) -> list[dict]:
        import json

        return [json.loads(line) for line in
                logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _run(self, tmp_path: Path):
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        # Three turns, two of which sleep. The second timeout is not padding: a cell
        # that times out leaves the *worker* still running it, so the next cell waits
        # behind the same sleep and dies on the same budget — which is what triggers
        # the sandbox's worker restart, and it is why a run that hits one timeout
        # usually hits two. The third turn then has a fresh worker and must run.
        # `cell_timeout_hard` equals the soft limit so there is no second limit to
        # reason about: this test is about the counter, not about extension.
        cfg = load_config("tiny", max_turns=3, cell_timeout=1.5,
                          cell_timeout_hard=1.5)
        backend = StubBackend(responses=[
            "```repl\nimport time; time.sleep(8)\n```",
            "```repl\nimport time; time.sleep(8)\n```",
            "```repl\nanswer['content'] = 'the last turn ran'\n"
            "answer['ready'] = True\n```",
        ])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "stub context")
        finally:
            loop.shutdown()
        return answer, self._events(logger)

    def test_the_last_turn_is_still_executed_after_timeouts(self, tmp_path: Path) -> None:
        answer, events = self._run(tmp_path)

        timeouts = [e for e in events if e.get("guardrail") == "cell_timeout"]
        assert timeouts, "cells must have hit their budget"
        starts = [e for e in events if e.get("event") == "turn_start"]
        assert len(starts) == 3, f"every turn must be entered, got {len(starts)}"
        end = next(e for e in events if e.get("event") == "end")
        assert end["turns_used"] == 3, end
        assert end["forced"] is False, "the final submission must have been executed"
        assert answer == "the last turn ran", answer

    def test_the_model_is_told_once_per_timeout(self, tmp_path: Path) -> None:
        _, events = self._run(tmp_path)

        timeouts = [e for e in events if e.get("guardrail") == "cell_timeout"]
        told = [e for e in events
                if e.get("event") == "root_message" and e.get("role") == "user"
                and "stopped after" in str(e.get("content"))]
        assert len(told) == len(timeouts) == 2, (len(told), len(timeouts))


class TestTurnAccounting:
    """`turns_used` must be the number of turns spent, and never more than the budget.

    A live run on 2026-09-17 reported `turns=9/8`: the explicit turn counter of the
    syntax-retry change sits at `max_turns` when the loop exhausts naturally and one
    lower when it breaks early, so `turn + 1` was wrong in one case and right by
    accident in the other. The count is now kept where turns are *entered*.
    """

    def _end(self, logger) -> dict:
        import json

        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return next(e for e in events if e.get("event") == "end")

    def test_exhausting_the_budget_reports_exactly_the_budget(self, tmp_path: Path) -> None:
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        cfg = load_config("tiny", max_turns=3)
        backend = StubBackend(responses=["```repl\nprint('probe')\n```"] * 20
                              + ["FINAL: gave up"])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert self._end(logger)["turns_used"] == 3

    def test_an_early_break_reports_the_turns_it_actually_spent(
        self, tmp_path: Path,
    ) -> None:
        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        cfg = load_config("tiny", max_turns=6, max_consecutive_errors=1)
        # Every cell raises, so the error budget breaks out on turn 2 of 6.
        backend = StubBackend(responses=[
            "```repl\nraise ValueError('boom')\n```",
            "```repl\nraise ValueError('boom again')\n```",
            "FINAL: gave up",
        ])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None)
        try:
            loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        assert self._end(logger)["turns_used"] == 2


class TestTheCellBudgetIsVisibleAndConfigurable:
    """The owner's finding: 60 s is too little on a loaded host, and a run that
    died on its budget must say so rather than looking like a bad answer.

    `cell_timeout` was profile-only (60 s on `tiny`/`laptop`, 120 s on
    `workstation`), with no flag and no way to see that a *timeout* — not the model
    — produced a failure. A `cell_timeout` guardrail event now names the budget and
    the last corpus helper the cell had called, which is what turns "it failed" into
    "`corpus_count` needed more than 60 s on this host".
    """

    def test_a_cell_that_dies_on_its_budget_is_recorded_with_its_budget_and_verb(
        self, tmp_path: Path, corpus_bridge,
    ) -> None:
        import json

        from rlm_local.config import load_config
        from rlm_local.logger import TrajectoryLogger

        cfg = load_config("tiny", max_turns=2, cell_timeout=0.5)
        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            "```repl\ncorpus_coverage()\nimport time; time.sleep(5)\n```",
            "```repl\nanswer['content'] = 'done'\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Question", "stub context")
        finally:
            loop.shutdown()

        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        timeouts = [e for e in events if e.get("guardrail") == "cell_timeout"]
        assert timeouts, "a cell that died on its budget must be recorded as such"
        assert "budget=0.5s" in timeouts[0]["detail"]
        # The verb names *what the cell was doing* when it ran out, which is the
        # whole diagnosis: a budget that is too low for one helper is a different
        # problem from a model that asks for the wrong thing.
        assert "last_helper=corpus_coverage" in timeouts[0]["detail"]
        # `>= 1` rather than `== 1`: the late result of an abandoned cell can
        # surface as a second timeout when the sandbox reads it, so the counter
        # bounds the *failures*, not the number of distinct cells. The event's
        # `block=` is what identifies a cell.
        assert loop.cell_timeouts >= 1

    def test_the_budget_is_a_cli_flag(self, tmp_path: Path) -> None:
        from rlm_local.cli import build_parser, ask_overrides

        context = tmp_path / "ctx.md"
        context.write_text("ctx", encoding="utf-8")
        args = build_parser().parse_args(
            ["ask", "q", "--context-file", str(context), "--cell-timeout", "240"])
        assert ask_overrides(args)["cell_timeout"] == 240.0

    def test_the_budget_can_come_from_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rlm_local.cli import build_parser, ask_overrides

        monkeypatch.setenv("RLM_CELL_TIMEOUT", "90")
        context = tmp_path / "ctx.md"
        context.write_text("ctx", encoding="utf-8")
        args = build_parser().parse_args(["ask", "q", "--context-file", str(context)])
        assert ask_overrides(args)["cell_timeout"] == 90.0


class TestSearchQualityIsLogged:
    """What a search served is a fact in the trajectory, not an inference (RO4).

    The relevance signal's first measurement could not say whether the model ever
    saw a `weak` match: the labels live inside tool results, the model printed none
    of them, and the transcript therefore did not contain them. The parent serves
    every helper call, so the parent reports the distribution — and these tests
    keep that report from silently disappearing.
    """

    def test_a_search_records_what_it_served(
        self, tiny_cfg, corpus_bridge, tmp_path,
    ) -> None:
        import json

        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            "```repl\nprint(corpus_search('Cuicani'))\n```",
            "```repl\nanswer['content'] = ('not in the corpus\\n"
            "[coverage: unknown]')\nanswer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            loop.run("Who is Cuicani?", "stub context")
        finally:
            loop.shutdown()

        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        quality = [e for e in events if e.get("event") == "guardrail"
                   and e.get("guardrail") == "corpus_search_quality"]
        assert quality, "a served search must leave a record of what it served"
        # The fixture's one hit covers the whole one-word question, so `strong` is
        # the honest label and it is what the event must carry.
        assert "strong=1" in quality[0]["detail"]
        assert "chars=" in quality[0]["detail"]


class TestAModelFailureEndsTheRunRatherThanDiscardingIt:
    """A transport failure must cost a turn, not the run (2026-09-16).

    A read timeout in the middle of a real corpus run propagated out of `run()`;
    the CLI exited 2 with `Error: The read operation timed out`, produced no answer,
    and left a trajectory with 49 events and **no `end` record** — a run that looks
    like absent data instead of a failure. Every run now ends in an answer string
    and an `end` event, whatever the model does.
    """

    def test_a_failing_turn_degrades_to_forced_finalization(
        self, tiny_cfg, tmp_path,
    ) -> None:
        import json

        from rlm_local.logger import TrajectoryLogger

        logger = TrajectoryLogger(tmp_path / "traj.jsonl")
        backend = StubBackend(responses=[
            # Turn 0 answers normally, so the run has a working model...
            "```repl\nprint('probing')\n```",
            # ...and then the router times out mid-run, which is the q2 case.
            TimeoutError("The read operation timed out"),
            "```repl\nanswer['content'] = 'answered after the timeout'\n"
            "answer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, logger=logger, kernel_bridge=None)
        try:
            answer = loop.run("Question", "Some context")
        finally:
            loop.shutdown()

        assert answer.strip() != ""
        assert loop.model_errors == 1
        events = [json.loads(line) for line in
                  logger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(e.get("event") == "end" for e in events), (
            "a run that survived a model failure must still record its end"
        )
        errors = [e for e in events if e.get("event") == "guardrail"
                  and e.get("guardrail") == "model_error"]
        assert errors, "the failure must be recorded, not swallowed"
        assert "TimeoutError" in errors[0]["detail"]

    def test_a_model_that_never_answers_propagates_instead_of_pretending(
        self, tiny_cfg,
    ) -> None:
        """The other half of the rule, and the half a real test caught: a *dead*
        server must be reported, not papered over with a placeholder and exit 0."""
        backend = StubBackend(responses=[TimeoutError("down")] * 6)
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            with pytest.raises(TimeoutError):
                loop.run("Question", "Some context")
        finally:
            loop.shutdown()


class TestStderrSelfCorrectionWiring:
    """R5 — §5.6 stage 4 must actually run: a traceback must engage the
    consecutive-error budget and hand the model a correction nudge."""

    def test_error_then_recovery_finishes_with_the_corrected_answer(self, tiny_cfg):
        from rlm_local.templates import NUDGE_STDERR_ERROR

        backend = StubBackend(responses=[
            # Turn 0 — a cell that raises.
            "\n".join([
                "Probing.",
                "```repl",
                "raise ValueError('boom')",
                "```",
            ]),
            # Turn 1 — corrected cell that submits.
            "\n".join([
                "Fixed the typo.",
                "```repl",
                "answer['content'] = 'recovered'",
                "answer['ready'] = True",
                "```",
            ]),
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Recovery test", "Some context")
        finally:
            loop.shutdown()

        assert answer == "recovered"
        nudges = [
            m["content"] for c in backend.calls for m in c["messages"]
            if m["role"] == "user" and "consecutive errors" in m["content"]
        ]
        assert nudges, "the stderr correction nudge must reach the model"
        assert "ValueError" in nudges[0]

    def test_clean_cell_resets_the_error_streak(self, tiny_cfg):
        """A cell that raises, then a clean cell, then success."""
        backend = StubBackend(responses=[
            "```repl\nraise RuntimeError('x')\n```",
            "```repl\nprint('clean')\n```",
            "```repl\nanswer['content'] = 'ok'; answer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Reset test", "Some context")
        finally:
            loop.shutdown()
        assert answer == "ok"

    def test_persistent_errors_reach_forced_finalization(self, tiny_cfg):
        """A model that never stops erroring must hit the error budget."""
        erroring = "```repl\nraise ValueError('always')\n```"
        backend = StubBackend(responses=[erroring] * 40 + ["FINAL: best effort"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Error budget test", "Some context")
        finally:
            loop.shutdown()

        assert answer.strip() != ""
        assert len(backend.calls) <= tiny_cfg.max_turns + 2

    def test_error_budget_is_bounded_by_max_consecutive_errors(self, tiny_cfg):
        erroring = "```repl\nraise ValueError('always')\n```"
        backend = StubBackend(responses=[erroring] * 40 + ["FINAL: best effort"])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            loop.run("Error budget bound test", "Some context")
        finally:
            loop.shutdown()
        # The loop must stop well before exhausting 40 scripted responses.
        assert len(backend.calls) < 40


class TestSubcallRepairJsonWiring:
    """R5 — `repair_json` must run on schema'd sub-call responses (§5.4)."""

    def test_schema_response_is_repair_parsed(self, tiny_cfg):
        from rlm_local.subcall_manager import SubcallManager

        class JsonBackend:
            def chat(self, messages, *, tier="sub", max_tokens=1024,
                     temperature=0.0, response_schema=None):
                return 'Here you go:\n```json\n{"answer": "1648"}\n```\nHope that helps!'

        mgr = SubcallManager(JsonBackend(), max_calls=5, max_chars=10000)
        try:
            out = mgr.llm_query("give me json", schema={"type": "object"})
            assert out == '{"answer": "1648"}', out
        finally:
            mgr.shutdown()

    def test_no_schema_leaves_response_untouched(self, tiny_cfg):
        from rlm_local.subcall_manager import SubcallManager

        raw = 'Here you go:\n```json\n{"answer": "1648"}\n```'

        class Backend:
            def chat(self, messages, *, tier="sub", max_tokens=1024,
                     temperature=0.0, response_schema=None):
                return raw

        mgr = SubcallManager(Backend(), max_calls=5, max_chars=10000)
        try:
            assert mgr.llm_query("give me json") == raw
        finally:
            mgr.shutdown()


class TestOutputCapPromise:
    """R10 — "the model is never lied to": the prompt's `{repl_cap}` and the
    enforced stdout cap must be the same number, by construction."""

    def test_prompt_cap_and_enforced_cap_agree(self):
        from rlm_local.config import load_config
        from rlm_local.templates import CELL_STDOUT_TRUNCATED

        cap = 37  # deliberately unlike any profile default
        cfg = load_config("tiny", repl_output_char_cap=cap)
        backend = StubBackend(responses=[
            "```repl\nprint('x' * 5000)\n```",
            "```repl\nanswer['content'] = 'done'; answer['ready'] = True\n```",
        ])
        loop = RootLoop(cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Cap test", "Some context")
        finally:
            loop.shutdown()
        assert answer == "done"

        system_prompt = backend.calls[0]["messages"][0]["content"]
        assert f"truncated to {cap} characters" in system_prompt, (
            "the prompt must advertise the cap that is actually enforced"
        )

        repl_messages = [m for m in backend.live_user_messages() if m.startswith("REPL output:")]
        assert repl_messages, "expected a REPL output message"
        body = repl_messages[0]
        assert "x" * cap in body
        assert "x" * (cap + 1) not in body, "more than the advertised cap leaked"
        assert CELL_STDOUT_TRUNCATED.format(cap=cap) in body

    def test_default_profile_cap_is_enforced_too(self, tiny_cfg):
        from rlm_local.templates import CELL_STDOUT_TRUNCATED

        cap = tiny_cfg.repl_output_char_cap
        backend = StubBackend(responses=[
            "```repl\nprint('z' * 20000)\n```",
            "```repl\nanswer['content'] = 'ok'; answer['ready'] = True\n```",
        ])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None)
        try:
            answer = loop.run("Default cap test", "Some context")
        finally:
            loop.shutdown()
        assert answer == "ok"
        body = [m for m in backend.live_user_messages() if m.startswith("REPL output:")][0]
        assert CELL_STDOUT_TRUNCATED.format(cap=cap) in body
        assert "z" * (cap + 1) not in body


class TestCompletionAPI:
    """End-to-end through the public completion() API."""

    def test_completion_without_kernel(self):
        """(a) completion() without bridge returns a string."""
        backend = StubBackend()
        answer = completion(
            "Test query", "Test context",
            config=load_config("tiny"), backend=backend,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0

    def test_completion_with_kernel_bridge(self, bridge):
        """(b) completion() with bridge returns a string."""
        backend = StubBackend()
        answer = completion(
            "Test query", "Test context",
            config=load_config("tiny"), backend=backend,
            kernel_bridge=bridge,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0
