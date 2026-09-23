"""The summariser engine (RO6): one document in, one description out.

Wired from the CLI, never from the kernel. The kernel takes a callable it is handed
(`ctx.engines["summarise"]`) and does not import this module, which is what keeps a model
optional in every kernel test and keeps "the kernel has no model" a fact rather than a promise.

It defaults to **the same model and endpoint as every other command** — the profile's root tier,
`RLM_MODEL` / `RLM_ENDPOINT`, `--model` / `--endpoint` — and takes a different one per
invocation, because the question at the decision gate is not only what the usual model costs but
whether another model's description is worth its own cost.

The model is part of the engine's `engine_tag`, which the kernel folds into the derivation key
(`mine._engine_tag`). Without that, pointing the summariser at a second model would serve the
first model's descriptions as cache hits: the run would succeed and every word of it would be
from the wrong model.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from rlm_local.model_backend import ModelBackend
from rlm_local.summary_metrics import CHARS_PER_TOKEN, measure
from rlm_local.templates import SUMMARY_PROMPT, SUMMARY_SYSTEM

#: Published host throughput (`AGENTS.md` §4), and the reason the timeout below is computed
#: rather than chosen: ~6.6 tok/s prompt, ~3.0 tok/s decode, one model at a time.
PROMPT_TOKENS_PER_SECOND = 6.6
DECODE_TOKENS_PER_SECOND = 3.0

#: Room left on top of the modelled cost: the throughput above is a published average, and a box
#: that is also mining — or swapping, which this one does — is slower.
SUMMARY_TIMEOUT_SAFETY = 1.5

#: The HTTP client's own default, named so the difference is visible where it is argued about.
BACKEND_DEFAULT_TIMEOUT = 300.0


def summarise_timeout_seconds(max_input_bytes: int, max_tokens: int) -> float:
    """Seconds one description may take: the input cap's prompt plus the output bound's decode.

    This exists because two reasonable bounds turned out to contradict each other, and the
    contradiction cost a real run. The kernel caps a summary's input at 32 KiB, which at ~6.6
    tok/s prompt and ~4 characters per token is **~21 minutes** of processing before the first
    output token — while `HTTPModelBackend` defaults to a 300 s timeout and retries twice. So the
    largest documents the handler will ever send are exactly the ones guaranteed to die.

    Measured 2026-09-23: the first live call raised `ReadTimeout` after **901.9 s** on a
    30 838-character document — three 300 s attempts. It was diagnosable at all only because the
    engine logs a failing call as a row rather than as an absence.

    The timeout is therefore *derived* from the cap and the output bound (×the safety factor),
    and `tests/test_summarise_engine.py` fails if the two ever drift apart again.
    """
    prompt_seconds = (max_input_bytes / CHARS_PER_TOKEN) / PROMPT_TOKENS_PER_SECOND
    decode_seconds = max_tokens / DECODE_TOKENS_PER_SECOND
    return round((prompt_seconds + decode_seconds) * SUMMARY_TIMEOUT_SAFETY, 1)


def append_record(path: Path, record: dict[str, Any]) -> None:
    """Append one metrics record as a JSON line, creating the file 0600.

    `os.open` rather than a text-mode append, because the mode must be set **at creation**: a
    metrics log written world-readable once keeps that mode for every later write, and this log
    describes someone's private files even though it quotes none of them.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(handle, line.encode("utf-8"))
    finally:
        os.close(handle)


def engine_tag_for(model: str, endpoint: str | None = None) -> str:
    """A summariser's identity in a derivation key: the model, and where it was served.

    The endpoint belongs in the tag because two servers can offer the same model name with
    different weights or quantisation, and a description from one is not the other's.
    """
    host = urlsplit(endpoint).netloc if endpoint else ""
    return f"{model}@{host}" if host else model


def make_summarise_engine(
    backend: ModelBackend,
    *,
    model: str,
    endpoint: str | None = None,
    log_path: Path | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    tag: str | None = None,
) -> Callable[..., tuple[str, dict[str, Any]]]:
    """Build the callable `task_summarise` expects, carrying its own identity.

    The returned engine is called as `engine(text, max_tokens) -> (summary, meta)`. Every call —
    including one that raised — appends a metrics record when `log_path` is given, so a failure
    is a row in the log rather than an absence from it.

    `max_tokens=None` means the kernel's own bound (`SUMMARY_MAX_TOKENS`), imported lazily so
    that this module does not depend on the kernel at import time.
    """
    if max_tokens is None:
        from rlm_kernel.mine import SUMMARY_MAX_TOKENS

        max_tokens = SUMMARY_MAX_TOKENS
    resolved_temperature = float(temperature)
    resolved_tag = tag or engine_tag_for(model, endpoint)

    def engine(text: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        started = time.monotonic()
        try:
            summary = backend.chat(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM},
                    {
                        "role": "user",
                        "content": SUMMARY_PROMPT.format(document=text, max_tokens=max_tokens),
                    },
                ],
                tier="root",
                max_tokens=max_tokens,
                temperature=resolved_temperature,
            )
        except Exception as exc:
            # Log before re-raising: the kernel records `RuntimeError` as a failed item, and the
            # metrics log should show the same event from the model's side — a model that fails
            # every call and a model that is never called look identical without this.
            if log_path is not None:
                append_record(
                    log_path,
                    measure(
                        model=resolved_tag,
                        seconds=time.monotonic() - started,
                        source=text,
                        summary="",
                        max_tokens=max_tokens,
                        error=type(exc).__name__,
                    ),
                )
            raise
        seconds = time.monotonic() - started
        record = measure(
            model=resolved_tag,
            seconds=seconds,
            source=text,
            summary=summary,
            max_tokens=max_tokens,
        )
        if log_path is not None:
            append_record(log_path, record)
        return summary, {
            "engine": resolved_tag,
            "model": model,
            "seconds": record["seconds"],
            "input_chars": record["input_chars"],
            "output_chars": record["output_chars"],
            "estimated_output_tokens": record["estimated_output_tokens"],
            "groundedness": record["groundedness"],
        }

    engine.engine_tag = resolved_tag  # type: ignore[attr-defined]
    return engine
