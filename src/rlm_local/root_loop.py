"""RootLoop — the heart of the RLM harness (§5.5).

Orchestrates the turn-by-turn interaction between root model, REPL, and sub-calls.
Implements turn accounting, termination, forced finalization, and the full
message layout with byte-stable prefix for prompt caching.
"""

from __future__ import annotations

import re
from typing import Any

from rlm_local.config import Config
from rlm_local.context_store import ContextStore
from rlm_local.logger import TrajectoryLogger
from rlm_local.model_backend import ModelBackend
from rlm_local.parser import FINAL_LINE_RE, Parser
from rlm_local.prompts import build_messages
from rlm_local.repl import BAND_ORDER, REPLSandbox
from rlm_local.subcall_manager import SubcallManager
from rlm_local.templates import (
    FINALIZATION_FAILED,
    FORCED_FINALIZATION_CORPUS_PROMPT,
    FORCED_FINALIZATION_PROMPT,
    NO_ANSWER_PRODUCED,
    NUDGE_CORPUS_LAST_TURN,
    NUDGE_CORPUS_UNCITED,
    NUDGE_CORPUS_WEAK_EVIDENCE,
    NUDGE_CORPUS_UNSEARCHED,
    NUDGE_EMPTY_ANSWER,
    REPL_BLOCK_LABEL,
    REPL_RESULT_TEMPLATE,
    TURN_HEADER,
    TURN_ZERO_SAFEGUARD,
)

# The address shape, imported from the module that owns the citation grammar
# rather than re-spelled here: a second copy of `#L\d+-\d+` is how the harness
# would come to disagree with the corpus about what an address is. The *token*
# form is the one that carries the path: the offsets alone cannot be compared
# against what a helper served, and comparing is the whole point (RO4).
from rlm_kernel.textindex import ADDRESS_TOKEN_RE

#: The word a corpus answer must contain when it has no address to offer. It is
#: how both sides mark "I looked and the corpus does not have this": the prompt
#: says to quote `corpus_coverage()`, and that helper's own output starts
#: `[coverage: …`. Matching the word rather than the full line keeps the guard
#: from demanding a format the model was never given.
COVERAGE_MARKER = "coverage"


class RootLoop:
    """Manages one completion(query, context) invocation.

    Lifecycle:
        loop = RootLoop(config, backend, logger)
        answer = loop.run(query, context)
        loop.shutdown()
    """

    def __init__(
        self,
        config: Config,
        backend: ModelBackend,
        logger: TrajectoryLogger | None = None,
        kernel_bridge: Any = None,
        corpus_bridge: Any = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self._logger = logger
        self._kernel_bridge = kernel_bridge
        # RO4: the read-only corpus handlers, when a corpus is configured. Kept
        # separate from the kernel bridge because a corpus run needs no vault:
        # "answer questions about a file tree" is its own capability.
        self._corpus_bridge = corpus_bridge
        # RO4 provenance telemetry: how many answers a corpus run produced, and
        # how many of them carried no address. Measured, never enforced — see
        # `_record_citations` for why the owner's call was to measure first.
        self.corpus_answers = 0
        self.corpus_answers_uncited = 0
        #: How many model calls raised instead of answering. A run that survives a
        #: transport failure records it here and in the trajectory, so a degraded
        #: run is visible rather than looking like a normal one (2026-09-16).
        self.model_errors = 0
        #: Whether the model has ever answered in this run. A failure before that
        #: is a dead server and propagates; a failure after it is a timeout and the
        #: run finishes (2026-09-16).
        self._model_ok = False

        # Subsystem instances (created fresh per run)
        self._parser: Parser | None = None
        self._repl: REPLSandbox | None = None
        self._subcall_mgr: SubcallManager | None = None
        self._context_store: ContextStore | None = None

    def run(self, query: str, context: str | list[str]) -> str:
        """Execute a completion.

        Args:
            query: The user's question/task.
            context: The data to answer from (never seen by the root model directly).

        Returns:
            The final answer string.
        """
        # ── Setup ─────────────────────────────────────────────────────────
        prompt_vars = self._config.prompt_vars()
        profile = self._config.profile
        # Every operating value is read through the Config, not the frozen
        # Profile: `Config.__getattr__` layers caller overrides over the
        # profile, and the prompt is built from `prompt_vars()` which does the
        # same. Reading `profile.X` here would let an override change what the
        # model is told while the harness kept the default (R10).
        cfg = self._config

        # Determine context type and length
        if isinstance(context, list):
            context_type = f"list of {len(context)} items"
            context_len = sum(len(str(c)) for c in context)
        else:
            context_type = "str"
            context_len = len(str(context))

        if self._logger:
            self._logger.log_start(
                query, context_len,
                {**profile.to_dict(), **self._config.overrides},
            )

        # Ingest context
        self._context_store = ContextStore(
            spill_threshold=cfg.context_spill_threshold,
        )
        ctx_handle = self._context_store.ingest(context)

        # Subcall manager
        self._subcall_mgr = SubcallManager(
            self._backend,
            max_concurrent=cfg.max_concurrent_subcalls,
            max_calls=cfg.max_subcalls,
            max_chars=cfg.max_subcall_chars,
            prompt_char_budget=cfg.sub_prompt_char_budget,
            context_total_chars=context_len,
            shortcut_warn_fraction=cfg.shortcut_warn_fraction,
        )

        # Parser (D1: restored — was accidentally deleted in kernel edit)
        self._parser = Parser(
            max_consecutive_nudges=cfg.max_consecutive_nudges,
            max_consecutive_errors=cfg.max_consecutive_errors,
        )

        # REPL
        # R10: the cap passed here is exactly the `{repl_cap}` the prompt
        # promises — both come from the same Config attribute, so template text
        # and behaviour cannot drift apart.
        self._repl = REPLSandbox(
            cell_timeout=cfg.cell_timeout,
            stdout_cap=cfg.repl_output_char_cap,
        )

        # K1: Get helper definitions and core-memory from kernel bridge
        definitions = None
        core_memory_summary = None
        if self._kernel_bridge:
            definitions = self._kernel_bridge.get_helper_definitions()
            core_memory_summary = self._kernel_bridge.get_core_memory_summary()

        # Add core-memory to metadata if available
        _context_type = context_type
        if core_memory_summary:
            _context_type = f"{context_type}\n\nCore memory: {core_memory_summary}"

        # Build initial messages (byte-stable prefix)
        # D4: thread vault-assembled system prompt through build_messages
        _system_prompt: str | None = None
        _fewshots: list | None = None
        if self._kernel_bridge:
            from rlm_local.prompts import load_system_prompt_from_vault, load_fewshots_from_vault
            _system_prompt = load_system_prompt_from_vault(prompt_vars, self._kernel_bridge.vault, bridge=self._kernel_bridge)
            _fewshots = load_fewshots_from_vault(
                self._kernel_bridge.vault,
                prompt_char_budget=cfg.sub_prompt_char_budget,
            )

        # RO4: a configured corpus must be advertised, and in *both* prompt
        # paths — the packaged SYSTEM_PROMPT and a vault-assembled one. Appending
        # here (rather than inside a template) is what makes that uniform: a
        # vault whose contract page predates this feature would otherwise leave
        # the model uninformed about helpers that its cells can call.
        if self._corpus_bridge is not None:
            from rlm_local.prompts import build_system_prompt, corpus_helpers_section

            # The run's question, so a hit's match-quality label measures the
            # passage against what was asked rather than against the AND search that
            # found it. Set here rather than only in the CLI, because every entry
            # point (CLI, web console, `completion()`, tests) comes through here.
            self._corpus_bridge.question = query
            if _system_prompt is None:
                _system_prompt = build_system_prompt(prompt_vars)
            mount = getattr(self._corpus_bridge, "mount", None)
            _system_prompt += "\n\n" + corpus_helpers_section(
                root=str(mount.root) if mount is not None else "(configured)",
                has_index=getattr(self._corpus_bridge, "index", None) is not None,
            )

        messages = build_messages(
            query, context_len, _context_type, prompt_vars,
            system_prompt=_system_prompt, fewshots=_fewshots,
        )

        # ── Start REPL with context and helpers ───────────────────────────
        self._repl._kernel_bridge = self._kernel_bridge
        self._repl._corpus_bridge = self._corpus_bridge
        # What each search *served*, so the trajectory records what the model was
        # shown rather than the harness inferring it from the code (RO4).
        self._repl._corpus_quality_logger = self._log_search_quality
        self._repl.start(ctx_handle, self._subcall_mgr, definitions=definitions)

        # ── Main loop ─────────────────────────────────────────────────────
        final_answer: str | None = None
        max_turns = cfg.max_turns
        turn: int = -1  # 0-indexed internally; displayed as turn 1..N
        # R6: an empty submission is a mistake, not an answer. It is nudged at
        # most `max_consecutive_nudges` times before forced finalization takes
        # over, so a model stuck in a submit-empty loop cannot burn the turn
        # budget in silence.
        empty_answer_nudges = 0
        # A corpus run's submission only means something if the corpus was
        # actually searched; counted separately from empty-submission nudges.
        corpus_nudges = 0
        # ...and an answer only means something if it can be checked: one nudge
        # budget for uncited answers, shared by every channel they arrive on.
        corpus_uncited_nudges = 0

        for turn in range(max_turns):
            display_turn = turn + 1
            # The quality callback fires while a cell runs, so it needs to know
            # which turn that is without being passed one.
            self._current_turn = display_turn

            # Turn header
            turn_header = TURN_HEADER.format(
                turn=display_turn, max_turns=max_turns,
            )
            if turn == 0:
                turn_header += "\n" + TURN_ZERO_SAFEGUARD

            messages.append({"role": "user", "content": turn_header})

            if self._logger:
                self._logger.log_turn_start(display_turn, max_turns)
                self._logger.log_root_message("user", turn_header)

            # The last turn of a corpus run that has looked but not answered is
            # for answering. The header already carries `Turn N/M`, so what is
            # missing is permission rather than information: three live runs on a
            # question the corpus cannot answer explored to the end (5/8, 8/8,
            # 8/8 turns) and were answered by forced finalization instead.
            # Appended before the final call, so it costs no turn of its own.
            if (self._corpus_bridge is not None
                    and self._repl is not None
                    and self._repl.corpus_calls
                    and turn == max_turns - 1):
                last_turn_nudge = NUDGE_CORPUS_LAST_TURN.format(
                    turn=display_turn, max_turns=max_turns,
                )
                messages.append({"role": "user", "content": last_turn_nudge})
                if self._logger:
                    self._logger.log_guardrail(
                        display_turn, "corpus_last_turn",
                        f"last turn reached with {self._repl.corpus_calls} "
                        f"corpus helper call(s) and no submission",
                    )
                    self._logger.log_root_message("user", last_turn_nudge)

            # ── Get root model response ───────────────────────────────────
            # A transport failure here must not discard the run. Measured
            # 2026-09-16: a read timeout during a turn propagated out of `run()`,
            # the CLI exited 2 with `Error: The read operation timed out`, no answer
            # was produced, and the trajectory ended after 49 events with **no
            # `end` record** — so the run looked like absent data rather than a
            # failure. Breaking into forced finalization instead keeps every run
            # ending in an answer string and an `end` event, and the forced call is
            # already guarded (a failing one yields FINALIZATION_FAILED).
            try:
                root_text = self._backend.chat(
                    messages,
                    tier="root",
                    max_tokens=3000 if turn == 0 else 1500,
                    temperature=0.0,
                )
            except Exception as e:
                self.model_errors += 1
                if self._logger:
                    self._logger.log_guardrail(
                        display_turn, "model_error",
                        f"{type(e).__name__}: {e}"[:200],
                    )
                # A failure *after* the model has answered is a transient timeout,
                # and the run is worth finishing. A failure on the first call means
                # there was never a working model: that propagates, because the CLI
                # then reports it and exits non-zero, whereas a placeholder answer
                # with exit 0 would tell an operator the run succeeded. `test_chat`
                # pins that half, and it caught this over-correction.
                if not self._model_ok:
                    raise
                break

            self._model_ok = True
            messages.append({"role": "assistant", "content": root_text})
            if self._logger:
                self._logger.log_root_message("assistant", root_text)

            # ── Parse ─────────────────────────────────────────────────────
            result = self._parser.parse(root_text, turn=turn)

            if self._logger:
                self._logger.log_guardrail(
                    display_turn, "parse",
                    f"blocks={len(result.blocks)} final_answer={result.final_answer is not None} "
                    f"nudge={result.nudge is not None} warnings={result.warnings}",
                )

            # Handle nudge (retry)
            if result.nudge:
                messages.append({"role": "user", "content": result.nudge})
                if self._logger:
                    self._logger.log_root_message("user", result.nudge)
                continue

            # Handle courtesy FINAL:
            if result.final_answer is not None:
                # RO4: `FINAL:` is a submission channel, so both corpus rules
                # apply here — the run must have looked, and the answer must be
                # checkable. Budget exhausted means the answer is taken: a final
                # line the model chose to write is still an answer, and
                # `_record_citations` measures it either way.
                if (self._corpus_bridge is not None
                        and self._repl is not None
                        and not self._repl.corpus_calls
                        and corpus_nudges < cfg.max_consecutive_nudges):
                    corpus_nudges += 1
                    if self._logger:
                        self._logger.log_guardrail(
                            display_turn, "corpus_unsearched",
                            f"FINAL: line with no corpus helper call "
                            f"(nudges={corpus_nudges}/{cfg.max_consecutive_nudges})",
                        )
                    messages.append({"role": "user", "content": NUDGE_CORPUS_UNSEARCHED})
                    if self._logger:
                        self._logger.log_root_message("user", NUDGE_CORPUS_UNSEARCHED)
                    continue
                refusal = self._refusal_reason(result.final_answer)
                if refusal and corpus_uncited_nudges < cfg.max_consecutive_nudges:
                    corpus_uncited_nudges += 1
                    self._log_uncited(display_turn, corpus_uncited_nudges, cfg,
                                      answer=result.final_answer, reason=refusal)
                    nudge = (NUDGE_CORPUS_WEAK_EVIDENCE if refusal == "weak"
                             else NUDGE_CORPUS_UNCITED)
                    messages.append({"role": "user", "content": nudge})
                    if self._logger:
                        self._logger.log_root_message("user", nudge)
                    continue
                final_answer = result.final_answer
                self._record_citations(display_turn, final_answer)
                break

            # ── Execute blocks in REPL ────────────────────────────────────
            if not result.blocks:
                # No blocks and no final answer — nudge should've caught this
                # If we got here, the parser is out of nudges; forced finalize
                break

            stderr_nudge: str | None = None
            empty_submission = False
            corpus_unsearched = False
            corpus_uncited = False
            corpus_uncited_reason = ""

            for bi, block in enumerate(result.blocks):
                # Static read of the block: does it *declare* a submission, and
                # what literal content does it carry? Advisory only — the
                # runtime value is authoritative (e.g. `answer['content'] = x`).
                static_content, declares_ready = self._parser.check_answer_in_block(block)

                repl_result = self._repl.execute(block)

                if self._logger:
                    self._logger.log_repl_result(
                        display_turn,
                        repl_result.stdout,
                        repl_result.stderr,
                        repl_result.final_answer,
                        repl_result.warnings,
                        repl_result.answer_state,
                        repl_result.scaffold_repaired,
                    )

                # ── §5.6 stage 4: stderr self-correction (R5) ─────────────
                if repl_result.stderr and repl_result.stderr.strip():
                    err_result = self._parser.parse_stderr(root_text, repl_result.stderr)
                    if err_result is not None and err_result.nudge:
                        stderr_nudge = err_result.nudge
                    if self._logger:
                        self._logger.log_guardrail(
                            display_turn, "stderr",
                            f"consecutive_errors={self._parser.consecutive_errors} "
                            f"nudge={stderr_nudge is not None}",
                        )
                else:
                    self._parser.reset_errors()

                # ── Termination: answer['ready'] = True (R6) ──────────────
                # `is not None`, never truthiness: an empty string is a
                # *submission*, and it is handled as its own case below.
                if repl_result.final_answer is not None:
                    if repl_result.final_answer.strip() == "":
                        empty_submission = True
                        empty_answer_nudges += 1
                        if self._logger:
                            self._logger.log_guardrail(
                                display_turn, "empty_answer",
                                f"block={bi + 1} declares_ready={declares_ready} "
                                f"static_content={static_content!r} "
                                f"nudges={empty_answer_nudges}/{cfg.max_consecutive_nudges}",
                            )
                        break
                    # A corpus run that never touched the corpus cannot have
                    # answered a question about it, and the harness *knows*
                    # whether it did: the parent sees every helper request. The
                    # third live run submitted "not mentioned in the corpus" after
                    # a single `print(len(context))` — no search, no read. So this
                    # is a nudge driven by evidence, not by hope. The flag is
                    # handled with `empty_submission` after the block loop, for the
                    # same reason: the response declared itself ready, so nothing
                    # after this block should still execute.
                    if (self._corpus_bridge is not None
                            and self._repl is not None
                            and not self._repl.corpus_calls):
                        corpus_unsearched = True
                        corpus_nudges += 1
                        if self._logger:
                            self._logger.log_guardrail(
                                display_turn, "corpus_unsearched",
                                f"block={bi + 1} submission with no corpus helper "
                                f"call (nudges={corpus_nudges}/"
                                f"{cfg.max_consecutive_nudges})",
                            )
                        break
                    # RO4: an answer about the corpus that cites nothing and says
                    # nothing about coverage is refused once, with the escape
                    # hatch named in the nudge. Handled with the other flags after
                    # the block loop, for the same reason: a declaring block ends
                    # the response.
                    refusal = self._refusal_reason(repl_result.final_answer)
                    if refusal:
                        corpus_uncited = True
                        corpus_uncited_reason = refusal
                        corpus_uncited_nudges += 1
                        self._log_uncited(display_turn, corpus_uncited_nudges, cfg,
                                          answer=repl_result.final_answer, block=bi,
                                          reason=refusal)
                        break
                    final_answer = repl_result.final_answer
                    self._record_citations(display_turn, final_answer)
                    break

                # Build templated REPL output message
                block_label = ""
                if len(result.blocks) > 1:
                    block_label = REPL_BLOCK_LABEL.format(n=bi + 1)

                repl_msg = REPL_RESULT_TEMPLATE.format(
                    block_label=block_label,
                    stdout=repl_result.stdout or "(no output)",
                    stderr=("\nSTDERR:\n" + repl_result.stderr) if repl_result.stderr else "",
                )

                messages.append({"role": "user", "content": repl_msg})
                if self._logger:
                    self._logger.log_root_message("user", repl_msg)

            if final_answer is not None:
                break

            # R6: tell the model its submission was empty instead of silently
            # spinning. Counted against max_consecutive_nudges.
            if empty_submission:
                if empty_answer_nudges <= cfg.max_consecutive_nudges:
                    messages.append({"role": "user", "content": NUDGE_EMPTY_ANSWER})
                    if self._logger:
                        self._logger.log_root_message("user", NUDGE_EMPTY_ANSWER)
                    continue
                # Nudge budget exhausted — fall through to forced finalization.
                break

            # RO4: the same treatment for a corpus run that submitted without ever
            # asking the corpus anything. The model is told to look, and the turn
            # is spent doing it; once the budget is exhausted the loop falls
            # through to forced finalization rather than spinning.
            if corpus_unsearched:
                if corpus_nudges <= cfg.max_consecutive_nudges:
                    messages.append({"role": "user",
                                     "content": NUDGE_CORPUS_UNSEARCHED})
                    if self._logger:
                        self._logger.log_root_message("user",
                                                      NUDGE_CORPUS_UNSEARCHED)
                    continue
                break

            # RO4: an answer that cites nothing and names no coverage is told to
            # fix exactly that, once per nudge of its own budget. The escape hatch
            # travels in the nudge, so a truthful "the corpus does not have this,
            # here is the coverage" is always available and the guard cannot trap
            # a run on a 34%-indexed corpus.
            if corpus_uncited:
                if corpus_uncited_nudges <= cfg.max_consecutive_nudges:
                    nudge = (NUDGE_CORPUS_WEAK_EVIDENCE
                             if corpus_uncited_reason == "weak"
                             else NUDGE_CORPUS_UNCITED)
                    messages.append({"role": "user", "content": nudge})
                    if self._logger:
                        self._logger.log_root_message("user", nudge)
                    continue
                break

            # R5: hand the model the traceback-derived correction nudge.
            if stderr_nudge:
                messages.append({"role": "user", "content": stderr_nudge})
                if self._logger:
                    self._logger.log_root_message("user", stderr_nudge)

            # ── Error budget exceeded? ────────────────────────────────────
            if self._parser.consecutive_errors > cfg.max_consecutive_errors:
                break

        # ── Forced finalization ────────────────────────────────────────────
        if final_answer is None:
            # The terminal answer is the one guaranteed to be delivered, so a
            # corpus run is asked for its evidence here. Two live runs on an
            # unanswerable question spent the whole turn budget exploring, never
            # submitted, and were answered from this prompt — uncited both times,
            # because the citation guard only ever sees submissions. Refusing here
            # would be worse than useless; asking is what this path allows.
            forced_prompt = (
                FORCED_FINALIZATION_CORPUS_PROMPT
                if self._corpus_bridge is not None
                else FORCED_FINALIZATION_PROMPT
            )
            messages.append({"role": "user", "content": forced_prompt})
            if self._logger:
                self._logger.log_root_message("user", forced_prompt)

            try:
                final_text = self._backend.chat(
                    messages,
                    tier="root",
                    max_tokens=2000,
                    temperature=0.0,
                )
                messages.append({"role": "assistant", "content": final_text})
                if self._logger:
                    self._logger.log_root_message("assistant", final_text)

                # Try one last courtesy FINAL: parse
                final_match = FINAL_LINE_RE.search(final_text)
                if final_match:
                    final_answer = final_match.group(1).strip()
                else:
                    final_answer = final_text.strip()
            except Exception:
                final_answer = FINALIZATION_FAILED

            # RO4: a forced answer is still an answer, and a corpus run's forced
            # answer is exactly the one most likely to be uncited.
            self._record_citations(turn + 1, final_answer)

            if self._logger:
                self._logger.log_end(
                    final_answer if final_answer is not None else "",
                    turn + 1,
                    self._subcall_mgr.calls_used if self._subcall_mgr else 0,
                    forced=True,
                )
        else:
            if self._logger:
                self._logger.log_end(
                    final_answer,
                    turn + 1,
                    self._subcall_mgr.calls_used if self._subcall_mgr else 0,
                    forced=False,
                )

        if final_answer is None or final_answer == "":
            return NO_ANSWER_PRODUCED
        return final_answer

    def _unserved_citations(self, answer: str | None) -> set[str]:
        """Addresses the answer cites that no helper ever handed over (RO4).

        A citation is evidence only if the harness served it: the parent answers
        every corpus helper call, so it knows the set. On 2026-09-16 a four-turn
        run read nothing, printed nothing, and still ended with a `Citations:` line
        naming an address nobody had served it — a fabricated citation, which a
        pattern match accepted. A citation that points at nothing is a confident
        wrong answer wearing a receipt, and this project ranks those below silence.
        """
        addresses = set(ADDRESS_TOKEN_RE.findall(answer or ""))
        if not addresses:
            return set()
        served = set(getattr(self._repl, "corpus_addresses_served", set()) or set())
        return addresses - served

    def _cites_only_unanswering_evidence(self, addresses: set[str]) -> bool:
        """Whether *every* address an answer cites was served as a weak hit (RO4).

        The band comes from the sandbox, which reads it off the hit's own header
        line. Three rules keep this from becoming a trap:

        * **The best cited band decides.** One `strong` or `partial` citation is
          enough; only an answer whose entire evidence is `weak`/`none` rests on
          nothing.
        * **An unlabelled address is not a verdict.** `corpus_read` hands over a
          passage without judging it, and a question with no content words cannot
          be matched against anything, so an address with no recorded band makes
          this return False. AGENTS.md §1.8: a check that cannot see the truth says
          `unknown`, and unknown never refuses.
        * **Absence is still an answer.** The caller only refuses when the answer
          also offers no coverage; "the corpus does not contain this" remains the
          accepted route for a question the corpus does not hold.
        """
        bands = getattr(self._repl, "corpus_address_bands", None) or {}
        seen = [bands.get(address) for address in addresses]
        if not seen or any(band is None for band in seen):
            return False
        return all(band in ("weak", "none") for band in seen)

    def _refusal_reason(self, answer: str | None) -> str | None:
        """Why this corpus answer must be sent back, or `None` if it may stand (RO4).

        The rule, and the only rule: this is what both submission channels ask.

        Refused when it cites an address the harness never served — no escape arm
        excuses a fabricated citation — when every address it cites was served as a
        `weak`/`none` hit and it offers no coverage, and otherwise when it offers
        neither a served address nor a coverage statement. That coverage arm is the
        real escape hatch, and it is why this cannot loop: "the corpus does not
        contain this, here is the coverage" is a truthful answer, and on a
        partly-indexed corpus it is the common one. A refusal a truthful run can
        always satisfy is a guard; one that cannot is a trap.

        The *reason* is returned rather than a boolean because the two failures need
        different nudges: a fabricated citation is a lie about the harness, while a
        citation that was served but does not answer the question is a misreading the
        model can fix by citing better evidence or by saying the corpus does not
        contain the answer.
        """
        if self._corpus_bridge is None:
            return None
        text = answer or ""
        if self._unserved_citations(text):
            return "unserved"
        addresses = set(ADDRESS_TOKEN_RE.findall(text))
        if not addresses:
            return None if COVERAGE_MARKER in text.lower() else "uncited"
        if (self._cites_only_unanswering_evidence(addresses)
                and COVERAGE_MARKER not in text.lower()):
            return "weak"
        return None

    def _log_uncited(self, turn: int, nudges: int, cfg: Config, answer: str = "",
                     block: int | None = None, reason: str = "") -> None:
        """Record one refusal, so the guard's own hit rate is measurable."""
        if not self._logger:
            return
        where = f"block={block + 1} " if block is not None else "FINAL: "
        unserved = len(self._unserved_citations(answer))
        if reason == "weak":
            bands = getattr(self._repl, "corpus_address_bands", None) or {}
            addresses = set(ADDRESS_TOKEN_RE.findall(answer or ""))
            best = min((bands.get(a, "unknown") for a in addresses),
                       key=lambda b: BAND_ORDER.index(b) if b in BAND_ORDER else 99,
                       default="unknown")
            self._logger.log_guardrail(
                turn, "corpus_weak_citation",
                f"{where}answer refused: every cited address was served weak "
                f"(band={best} addresses={len(addresses)} "
                f"nudges={nudges}/{cfg.max_consecutive_nudges})",
            )
            return
        why = (f"{unserved} address(es) no helper served" if unserved
               else "no address and no coverage")
        self._logger.log_guardrail(
            turn, "corpus_uncited",
            f"{where}answer refused: {why} (unserved={unserved} "
            f"nudges={nudges}/{cfg.max_consecutive_nudges})",
        )

    def _log_search_quality(self, distribution: dict[str, int],
                            served_chars: int) -> None:
        """Record what a search served, so "did the model see a weak match?" is a
        fact in the trajectory and not an inference from the code (RO4).

        The labels live inside tool results; a run that prints none of them leaves
        no trace of what it was shown, which is exactly the state the first
        measurement of the relevance signal found itself in.
        """
        if not self._logger:
            return
        labels = " ".join(f"{name}={count}" for name, count in
                          sorted(distribution.items())) or "no_quality_labels"
        self._logger.log_guardrail(
            getattr(self, "_current_turn", 0), "corpus_search_quality",
            f"served {labels} chars={served_chars}",
        )

    def _record_citations(self, turn: int, answer: str | None) -> None:
        """Note whether a corpus answer carried the evidence it used (RO4).

        Every answer that is *accepted* is counted here, whether or not it cited
        anything: `corpus_answers` / `corpus_answers_uncited` on the loop, and one
        `corpus_citation` guardrail event with `answers_with_address=True|False`
        that an operator can tally with `grep -c`. A run with no corpus records
        nothing.

        The check is for *an address somewhere in the answer*, not for the
        `Citations:` line: an answer that quotes the addresses it read is grounded
        even if it formats them differently, whereas a line saying `Citations:`
        with nothing checkable after it is not. Refused answers never reach here —
        they are recorded by `_log_uncited` and re-attempted.
        """
        if self._corpus_bridge is None:
            return
        text = answer or ""
        cited = bool(ADDRESS_TOKEN_RE.search(text))
        self.corpus_answers += 1
        if not cited:
            self.corpus_answers_uncited += 1
        if self._logger:
            self._logger.log_guardrail(
                turn, "corpus_citation",
                f"answers_with_address={cited} chars={len(text)}",
            )

    def shutdown(self) -> None:
        """Clean up all resources."""
        if self._repl:
            self._repl.shutdown()
        if self._subcall_mgr:
            self._subcall_mgr.shutdown()
        if self._context_store:
            self._context_store.cleanup()
