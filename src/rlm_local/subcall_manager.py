"""Sub-call manager (§5.4).

Owns concurrency, budgets, prompt hygiene, memoization, and schema fallback
for `llm_query` and `llm_query_batched`.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from rlm_local.model_backend import ModelBackend
from rlm_local.templates import (
    SUBCALL_CHAR_EXHAUSTED,
    SUBCALL_COUNT_EXHAUSTED,
    SUBCALL_OVERSIZE_WARNING,
)


class SubcallManager:
    """Manages sub-LLM calls: budgets, concurrency, memoization, sizing."""

    def __init__(
        self,
        backend: ModelBackend,
        *,
        max_concurrent: int = 2,
        max_calls: int = 60,
        max_chars: int = 4_000_000,
        prompt_char_budget: int = 16000,
        context_total_chars: int = 0,
        shortcut_warn_fraction: float = 0.60,
        sub_model: str = "",
    ) -> None:
        self._backend = backend
        self._max_concurrent = max_concurrent
        self._max_calls = max_calls
        self._max_chars = max_chars
        self._prompt_char_budget = prompt_char_budget
        self._context_total_chars = context_total_chars
        self._shortcut_warn_fraction = shortcut_warn_fraction

        # Budget tracking
        self._calls_used = 0
        self._chars_used = 0
        self._lock = threading.Lock()

        # Memoization cache: prompt_hash -> response
        self._cache: dict[str, str] = {}

        # Thread pool for concurrent sub-calls
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)

    # ── Public API (called from REPL) ──────────────────────────────────────

    def llm_query(self, prompt: str, *, schema: dict[str, Any] | None = None) -> str:
        """One-shot sub-call. No history, no system prompt.

        Returns the response text, or an error string on budget exhaustion.
        """
        return self._do_call(prompt, schema)

    def llm_query_batched(
        self, prompts: list[str], *, schema: dict[str, Any] | None = None,
    ) -> list[str]:
        """Batched sub-calls with bounded parallelism (§5.4).

        Order-preserving. Per-item errors returned as strings.
        """
        futures: list[Future[str]] = []
        for prompt in prompts:
            futures.append(self._executor.submit(self._do_call, prompt, schema))

        results: list[str] = []
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                results.append(f"Error: {e}")
        return results

    # ── Internals ─────────────────────────────────────────────────────────

    def _do_call(self, prompt: str, schema: dict[str, Any] | None) -> str:
        """Execute one sub-call with budget checks, memoization, and sizing warnings."""
        prompt_len = len(prompt)

        # Budget check
        with self._lock:
            if self._calls_used >= self._max_calls:
                return SUBCALL_COUNT_EXHAUSTED.format(
                    used=self._calls_used, max_subcalls=self._max_calls,
                )
            if self._chars_used + prompt_len > self._max_chars:
                return SUBCALL_CHAR_EXHAUSTED
            self._calls_used += 1
            self._chars_used += prompt_len

        # Memoization check
        cache_key = hashlib.sha256(prompt.encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Size warning (non-blocking)
        warnings: list[str] = []
        if prompt_len > self._prompt_char_budget:
            warnings.append(SUBCALL_OVERSIZE_WARNING.format(
                size=prompt_len, budget=self._prompt_char_budget,
            ))

        # Anti-shortcut check (R5.3)
        if self._context_total_chars > 0 and self._shortcut_warn_fraction > 0:
            pct = prompt_len / self._context_total_chars
            if pct > self._shortcut_warn_fraction:
                from rlm_local.templates import SHORTCUT_WARNING
                warnings.append(SHORTCUT_WARNING.format(pct=pct * 100))

        # Schema fallback: if schema given, try structured output; otherwise
        # append a prose instruction (the server may or may not honor it)
        use_schema = schema
        extra_prompt = ""
        if schema is not None:
            # We pass the schema to the backend, but also add prose fallback
            extra_prompt = f"\n\nRespond with valid JSON matching this schema: {schema}"
            # Some servers don't support response_schema; strip it if needed
            # For now we pass it; the backend handles it

        full_prompt = prompt + extra_prompt

        try:
            response = self._backend.chat(
                [{"role": "user", "content": full_prompt}],
                tier="sub",
                max_tokens=1024,
                temperature=0.0,
                response_schema=use_schema,
            )
        except Exception as e:
            response = f"Error: {e}"

        # Cache the result
        self._cache[cache_key] = response
        return response

    # ── Budget queries ────────────────────────────────────────────────────

    @property
    def calls_used(self) -> int:
        return self._calls_used

    @property
    def chars_used(self) -> int:
        return self._chars_used

    @property
    def calls_remaining(self) -> int:
        return max(0, self._max_calls - self._calls_used)

    @property
    def cache_hits(self) -> int:
        return len(self._cache)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
