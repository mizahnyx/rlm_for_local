"""RootLoop — the heart of the RLM harness (§5.5).

Orchestrates the turn-by-turn interaction between root model, REPL, and sub-calls.
Implements turn accounting, termination, forced finalization, and the full
message layout with byte-stable prefix for prompt caching.
"""

from __future__ import annotations

from typing import Any

from rlm_local.config import Config
from rlm_local.context_store import Context, ContextStore, _InMemoryContext
from rlm_local.logger import TrajectoryLogger
from rlm_local.model_backend import ModelBackend
from rlm_local.parser import ParseResult, Parser
from rlm_local.prompts import build_messages
from rlm_local.repl import REPLResult, REPLSandbox
from rlm_local.subcall_manager import SubcallManager
from rlm_local.templates import (
    FORCED_FINALIZATION_PROMPT,
    REPL_BLOCK_LABEL,
    REPL_RESULT_TEMPLATE,
    TURN_HEADER,
    TURN_ZERO_SAFEGUARD,
)


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
    ) -> None:
        self._config = config
        self._backend = backend
        self._logger = logger
        self._kernel_bridge = kernel_bridge

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
            spill_threshold=profile.context_spill_threshold,
        )
        ctx_handle = self._context_store.ingest(context)

        # Subcall manager
        self._subcall_mgr = SubcallManager(
            self._backend,
            max_concurrent=profile.max_concurrent_subcalls,
            max_calls=profile.max_subcalls,
            max_chars=profile.max_subcall_chars,
            prompt_char_budget=profile.sub_prompt_char_budget,
            context_total_chars=context_len,
            shortcut_warn_fraction=profile.shortcut_warn_fraction,
        )

        # Parser (D1: restored — was accidentally deleted in kernel edit)
        self._parser = Parser(
            max_consecutive_nudges=profile.max_consecutive_nudges,
            max_consecutive_errors=profile.max_consecutive_errors,
        )

        # REPL
        self._repl = REPLSandbox(
            cell_timeout=profile.cell_timeout,
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
            _fewshots = load_fewshots_from_vault(self._kernel_bridge.vault)

        messages = build_messages(
            query, context_len, _context_type, prompt_vars,
            system_prompt=_system_prompt, fewshots=_fewshots,
        )

        # ── Start REPL with context and helpers ───────────────────────────
        self._repl._kernel_bridge = self._kernel_bridge
        self._repl.start(ctx_handle, self._subcall_mgr, definitions=definitions)

        # ── Main loop ─────────────────────────────────────────────────────
        final_answer: str | None = None
        max_turns = profile.max_turns
        turn: int = -1  # 0-indexed internally; displayed as turn 1..N

        for turn in range(max_turns):
            display_turn = turn + 1

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

            # ── Get root model response ───────────────────────────────────
            root_text = self._backend.chat(
                messages,
                tier="root",
                max_tokens=3000 if turn == 0 else 1500,
                temperature=0.0,
            )

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
                final_answer = result.final_answer
                break

            # ── Execute blocks in REPL ────────────────────────────────────
            if not result.blocks:
                # No blocks and no final answer — nudge should've caught this
                # If we got here, the parser is out of nudges; forced finalize
                break

            for bi, block in enumerate(result.blocks):
                # Check for answer-in-block (parser detects answer dict usage)
                content, ready = self._parser.check_answer_in_block(block)

                repl_result = self._repl.execute(block)

                if self._logger:
                    self._logger.log_repl_result(
                        display_turn,
                        repl_result.stdout,
                        repl_result.stderr,
                        repl_result.final_answer,
                        repl_result.warnings,
                    )

                # Check for answer['ready'] = True from REPL state
                if repl_result.final_answer is not None:
                    final_answer = repl_result.final_answer
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

            if final_answer:
                break

            # ── Error budget exceeded? ────────────────────────────────────
            if self._parser.consecutive_errors > profile.max_consecutive_errors:
                break

        # ── Forced finalization ────────────────────────────────────────────
        if final_answer is None:
            messages.append({"role": "user", "content": FORCED_FINALIZATION_PROMPT})
            if self._logger:
                self._logger.log_root_message("user", FORCED_FINALIZATION_PROMPT)

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
                final_match = __import__('re').search(r"^FINAL:\s*(.+)$", final_text, __import__('re').MULTILINE)
                if final_match:
                    final_answer = final_match.group(1).strip()
                else:
                    final_answer = final_text.strip()
            except Exception:
                final_answer = "(No answer produced — forced finalization failed)"

            if self._logger:
                self._logger.log_end(
                    final_answer or "",
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

        return final_answer or "(No answer produced)"

    def shutdown(self) -> None:
        """Clean up all resources."""
        if self._repl:
            self._repl.shutdown()
        if self._subcall_mgr:
            self._subcall_mgr.shutdown()
        if self._context_store:
            self._context_store.cleanup()
