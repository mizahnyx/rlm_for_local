"""RLM harness for small local models — public API.

Usage:
    import rlm_local

    answer = rlm_local.completion(
        "What year was the Treaty of Westphalia signed?",
        "The Thirty Years' War (1618-1648) was one of the most destructive...",
        profile="laptop",
    )
"""

from __future__ import annotations

from typing import Any

from rlm_local.config import Config, load_config
from rlm_local.logger import TrajectoryLogger
from rlm_local.model_backend import HTTPModelBackend, ModelBackend
from rlm_local.root_loop import RootLoop


def completion(
    query: str,
    context: str | list[str],
    *,
    profile: str = "laptop",
    backend: ModelBackend | None = None,
    config: Config | None = None,
    logger: TrajectoryLogger | None = None,
    log_path: str | None = None,
    kernel_bridge: Any = None,
    corpus_bridge: Any = None,
    warning_sink: Any = None,
    **overrides: Any,
) -> str:
    """Run a single RLM completion.

    Args:
        query: The user's question or task description.
        context: The data to answer from. The root model never sees this
                 directly; it's available in the REPL as `context`.
        profile: Hardware profile name: "tiny", "laptop", or "workstation".
        backend: Optional pre-configured ModelBackend. Created automatically
                 from config if not provided.
        config: Optional pre-built Config. Created from profile if not provided.
        logger: Optional TrajectoryLogger. Created if log_path is given.
        log_path: Path for JSONL trajectory log. Creates a logger automatically.
        kernel_bridge: Optional KernelBridge for vault-aware operation (K1).
        corpus_bridge: Optional CorpusBridge for read-only corpus access (RO4).
                 When given, the REPL gains `corpus_find`, `corpus_list`,
                 `corpus_stat`, `corpus_read` and `corpus_count`, and the system
                 prompt says so.
        warning_sink: Optional callable that receives operator-facing warnings as
                 strings — currently the line announcing that a cell has been
                 granted its hard time limit. The CLI wires it to stderr.
        **overrides: Individual config value overrides (e.g. max_turns=10).

    Returns:
        The final answer string.
    """
    # Build config
    if config is None:
        config = load_config(profile, **overrides)

    # Build backend (track whether we created it)
    own_backend = False
    if backend is None:
        own_backend = True
        backend = HTTPModelBackend(
            root_endpoint=config.root_endpoint,
            sub_endpoint=config.sub_endpoint,
            root_model=config.root_model,
            sub_model=config.sub_model,
            verify=False,
            timeout=300.0,
        )

    # Build logger
    if logger is None and log_path:
        logger = TrajectoryLogger(log_path)

    loop = RootLoop(config, backend, logger, kernel_bridge=kernel_bridge,
                    corpus_bridge=corpus_bridge, warning_sink=warning_sink)
    try:
        return loop.run(query, context)
    finally:
        loop.shutdown()
        if own_backend and isinstance(backend, HTTPModelBackend):
            backend.close()
