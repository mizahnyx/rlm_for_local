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
    """A ModelBackend that returns scripted responses in sequence."""

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
            return self.responses.pop(0)
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
    """A real read-only bridge over a tiny corpus, for corpus runs."""
    from rlm_kernel.corpus import CorpusBridge, CorpusIndex
    from rlm_kernel.mounts import LocalTreeMount

    root = tmp_path / "corpus"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "song.txt").write_text("Cuicani sang it first.\n",
                                             encoding="utf-8")
    index_path = tmp_path / "derived" / "corpus.sqlite"
    index_path.parent.mkdir()
    idx = CorpusIndex.open_for(root, index_path)
    idx.build(LocalTreeMount(root))
    b = CorpusBridge(mount=LocalTreeMount(root), index=idx)
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
        """The unsearched answer must not become the run's final answer."""
        unsearched = "\n".join([
            "```repl",
            "answer['content'] = 'nothing here'",
            "answer['ready'] = True",
            "```",
        ])
        searched = "\n".join([
            "```repl",
            "print(corpus_find('song'))",
            "answer['content'] = 'the searched answer\\n"
            "Citations: notes/song.txt#L0-21'",
            "answer['ready'] = True",
            "```",
        ])
        backend = StubBackend(responses=[unsearched, searched])
        loop = RootLoop(tiny_cfg, backend, kernel_bridge=None,
                        corpus_bridge=corpus_bridge)
        try:
            answer = loop.run("Question", "stub context")
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
                "answer['content'] = ('answered after looking\\n"
                "Citations: notes/song.txt#L0-21')",
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
