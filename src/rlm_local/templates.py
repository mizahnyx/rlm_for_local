"""Templated harness messages — R4.1: every string the harness emits is frozen here.

All messages use .format() with kwargs so they compose cleanly without
accidental str.format collisions with code blocks containing braces.

When a kernel vault is available, templates load from contract/templates/
pages first, falling back to these package-bundled defaults (§5.2, K0).
"""


from __future__ import annotations

# ---------------------------------------------------------------------------
# Root loop / turn messages
# ---------------------------------------------------------------------------

METADATA_TEMPLATE = (
    "Answer the following: {query}\n\n"
    "Your context is a {context_type} of {context_len} total characters. "
    "A sub-LLM call handles roughly {sub_budget} characters well. "
    "You have {max_turns} turns."
)

PROLOGUE = (
    "Before writing code, describe in 1-2 sentences:\n"
    "1. What you need to learn about the context (probe plan).\n"
    "2. How the overall answer decomposes into smaller sub-problems.\n\n"
    "Then emit exactly one ```repl block with your probing code."
)

TURN_HEADER = "Turn {turn}/{max_turns}."

TURN_ZERO_SAFEGUARD = (
    "You have not inspected the context yet. "
    "Probe it first with peek() or grep(); do NOT finalize."
)

REPL_RESULT_TEMPLATE = "REPL output:{block_label}\n{stdout}{stderr}"
REPL_BLOCK_LABEL = " (block {n})"

# ---------------------------------------------------------------------------
# Parser / guardrail retry nudges (§5.6)
# ---------------------------------------------------------------------------

NUDGE_NO_BLOCK = (
    "No ```repl block found. "
    "Emit exactly one ```repl block with your next step."
)

NUDGE_EMPTY_ANSWER = (
    "answer[\"ready\"] was set to True but answer[\"content\"] is empty. "
    "Populate answer[\"content\"] with your result before setting ready=True."
)

NUDGE_NARRATION = (
    "You described code without emitting it. "
    "Emit exactly one ```repl block containing the code you intend to run."
)

# ---------------------------------------------------------------------------
# Sub-call budget / warning messages (§5.4)
# ---------------------------------------------------------------------------

SUBCALL_OVERSIZE_WARNING = (
    "[WARNING] This sub-call prompt ({size} chars) exceeds the recommended "
    "{budget}-char budget. Quality may degrade."
)

SUBCALL_COUNT_EXHAUSTED = (
    "Error: sub-call budget exhausted ({used}/{max_subcalls} calls used). "
    "Finalize with what you have."
)

SUBCALL_CHAR_EXHAUSTED = (
    "Error: sub-call character budget exhausted. Finalize with what you have."
)

# ---------------------------------------------------------------------------
# Anti-shortcut warning (§5.3, R5.3)
# ---------------------------------------------------------------------------

SHORTCUT_WARNING = (
    "[WARNING] This sub-call received {pct:.0f}% of the full context. "
    "Consider decomposing into smaller chunks for better results."
)

# ---------------------------------------------------------------------------
# Forced finalization (§5.5)
# ---------------------------------------------------------------------------

FORCED_FINALIZATION_PROMPT = (
    "Based on everything you have learned so far, provide your best final "
    "answer now. Summarize your findings in plain text."
)

# ---------------------------------------------------------------------------
# Cell limits
# ---------------------------------------------------------------------------

CELL_TIMEOUT_ERROR = "Error: cell exceeded the {timeout}s time limit."
CELL_STDOUT_TRUNCATED = "\n[... output truncated to {cap} characters ...]"

# ---------------------------------------------------------------------------
# REPL environment boot messages
# ---------------------------------------------------------------------------

REPL_READY = "REPL ready."
REPL_FINAL_ANSWER = "Final answer submitted."

# ── Vault-first loading (K0) ──────────────────────────────────────────────

# Map of template constant names to their contract page paths.
_TEMPLATE_PAGE_MAP: dict[str, str] = {
    "METADATA_TEMPLATE": "contract/templates/metadata.md",
    "PROLOGUE": "contract/templates/prologue.md",
    "TURN_HEADER": "contract/templates/turn-header.md",
    "TURN_ZERO_SAFEGUARD": "contract/templates/turn-zero-safeguard.md",
    "REPL_RESULT_TEMPLATE": "contract/templates/repl-result.md",
    "REPL_BLOCK_LABEL": "contract/templates/repl-block-label.md",
    "NUDGE_NO_BLOCK": "contract/templates/nudge-no-block.md",
    "NUDGE_EMPTY_ANSWER": "contract/templates/nudge-empty-answer.md",
    "NUDGE_NARRATION": "contract/templates/nudge-narration.md",
    "SUBCALL_OVERSIZE_WARNING": "contract/templates/subcall-oversize-warning.md",
    "SUBCALL_COUNT_EXHAUSTED": "contract/templates/subcall-count-exhausted.md",
    "SUBCALL_CHAR_EXHAUSTED": "contract/templates/subcall-char-exhausted.md",
    "SHORTCUT_WARNING": "contract/templates/shortcut-warning.md",
    "FORCED_FINALIZATION_PROMPT": "contract/templates/forced-finalization.md",
    "CELL_TIMEOUT_ERROR": "contract/templates/cell-timeout-error.md",
    "CELL_STDOUT_TRUNCATED": "contract/templates/cell-stdout-truncated.md",
}


def load_template(name: str, vault: object | None = None) -> str:
    """Load a template string, vault-first with package fallback.

    Args:
        name: The constant name (e.g. "METADATA_TEMPLATE").
        vault: Optional VaultStore-compatible object. If provided and the
               corresponding page exists, its body is returned.

    Returns:
        The template string — from vault if available, otherwise the hardcoded
        package-bundled default.
    """
    import sys as _sys
    _mod = _sys.modules[__name__]

    # Try vault first
    if vault is not None:
        page_path = _TEMPLATE_PAGE_MAP.get(name)
        if page_path:
            try:
                page = vault.get(page_path)
                if page is not None:
                    return page.body.strip()
            except Exception:
                pass

    # Fallback: package-bundled constant
    return getattr(_mod, name, "")
