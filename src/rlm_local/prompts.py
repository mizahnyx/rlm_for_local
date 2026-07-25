"""System prompt and few-shot transcripts (§5.5).

The system prompt is a .format() template receiving config-injected values.
Few-shots are load-bearing for small models and use the exact message format
of live turns (R4.1 equivalence-class normalization).
"""

from __future__ import annotations

# ── System prompt (§5.5) ──────────────────────────────────────────────────
# Design notes:
# - {repl_cap}, {sub_budget}, {max_turns}, {example_chunking_idiom} injected
#   from Config.prompt_vars() so capacity claims are never lies.
# - The orchestrator addendum is built into "How to work" — always on (R5.1).
# - Few-shots are embedded inline after the rules; small models need the format
#   demonstrated, not described.

SYSTEM_PROMPT = """You are a Recursive Language Model: you answer a query whose (potentially huge) context lives in a Python REPL, not in this conversation. You act turn by turn until you submit an answer.

REPL contract
- Write code in ```repl fences. The REPL persists across turns.
- Available: `context` (the data), `answer` (dict: set answer["content"] and answer["ready"]=True to submit), `llm_query(prompt)`, `llm_query_batched(list_of_prompts)`, and helpers: peek(n=2000), grep(pattern, max_hits=50), chunk(size=3000), map_query(items, template), show_vars().
- Only print() output is shown back to you, truncated to {repl_cap} characters. Never print large slices of context — pass slices to llm_query instead.
- llm_query sees ONLY the prompt you give it (no REPL, no history). Give it a self-contained chunk and ask for a short, specific answer. It handles about {sub_budget} characters well.

How to work (follow this order)
1. PROBE: print small samples and counts to learn the context's structure.
2. PLAN: in prose, state how the task decomposes — what each turn computes and which sub-calls it issues — before writing more code.
3. EXECUTE one small step per turn; print a tiny verification sample after each.
4. SUBMIT only after you have printed your candidate answer. If turns run low, submit your best inference rather than nothing.

Rules for a small context window (yours)
- Your own window is small: delegate reading, summarizing, classifying, and extracting to llm_query; keep only small results and decisions yourself.
- Prefer one llm_query_batched over a loop of llm_query calls.
- If a keyword/regex search answers it directly, skip sub-calls.
- Chunk big work: {example_chunking_idiom}.

You have {max_turns} turns. Plan accordingly.

───

Below are worked examples in the exact format of this conversation. Study them. Follow the same structure.

Example 1: Needle search — find a specific fact in a long document.

"""  # continued below with few-shots

# ── Few-shot transcripts ──────────────────────────────────────────────────

FEWSHOT_EXAMPLE = [
    ("user", "Answer the following: What color is mentioned in the context?\n\n"
              "Your context is a str of 5000 total characters. A sub-LLM call handles roughly 8000 characters well. You have 8 turns."),
    ("user", "Before writing code, describe in 1-2 sentences:\n"
              "1. What you need to learn about the context (probe plan).\n"
              "2. How the overall answer decomposes into smaller sub-problems.\n\n"
              "Then emit exactly one ```repl block with your probing code."),
    ("assistant",
     "I will probe the context to confirm it's text about colors, then grep for the color name.\n\n"
     "```repl\n"
     "print(f'Context length: {len(context)}')\n"
     "print(f'First 200 chars: {context[:200]}')\n"
     "```"),
    ("user", "REPL output:\n"
             "Context length: 5000\n"
             "First 200 chars: The sky appeared bright and clear today. The dominant color observed was blue, with scattered white clouds near the horizon. Several observers noted the deep blue hue..."),
    ("assistant",
     "Confirmed: the context discusses sky colors. I'll grep for the color and verify.\n\n"
     "```repl\n"
     "hits = grep('blue')\n"
     "print(f'Found {len(hits)} matches for blue')\n"
     "for h in hits[:3]:\n"
     "    print(h[:100])\n"
     "```"),
    ("user", "REPL output:\n"
             "Found 3 matches for blue\n"
             "The dominant color observed was blue, with scattered white clouds...\n"
             "Several observers noted the deep blue hue...\n"
             "...the blue shade persisted throughout the afternoon."),
    ("assistant",
     "Blue is confirmed as the dominant color. Submitting the answer.\n\n"
     "```repl\n"
     "answer['content'] = 'The color blue is mentioned in the context.'\n"
     "answer['ready'] = True\n"
     "```"),
]


def build_system_prompt(prompt_vars: dict) -> str:
    """Construct the full system prompt with config values injected."""
    return SYSTEM_PROMPT.format(**prompt_vars)


def build_messages(
    query: str,
    context_len: int,
    context_type: str,
    prompt_vars: dict,
) -> list[dict[str, str]]:
    """Build the initial message list for a RootLoop.

    Includes: system prompt, metadata user message, prologue, and one few-shot.
    The few-shot is part of the byte-stable prefix for caching (R4.1, §5.5).
    """
    from rlm_local.templates import METADATA_TEMPLATE, PROLOGUE

    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(prompt_vars)},
    ]

    # Metadata — query is here, context is not (R2.1, R2.2)
    metadata = METADATA_TEMPLATE.format(
        query=query,
        context_type=context_type,
        context_len=context_len,
        sub_budget=prompt_vars["sub_budget"],
        max_turns=prompt_vars["max_turns"],
    )
    messages.append({"role": "user", "content": metadata})

    # Decomposition prologue (R5.2) — mandatory turn-0 probe+plan
    messages.append({"role": "user", "content": PROLOGUE})

    # Load-bearing few-shot: demonstrates the exact format (R4.1)
    for role, content in FEWSHOT_EXAMPLE:
        messages.append({"role": role, "content": content})

    return messages


# ── Vault-first loading (K0, §5.2) ────────────────────────────────────────

def load_system_prompt_from_vault(
    prompt_vars: dict,
    vault: object | None = None,
) -> str:
    """Build the system prompt from vault pages, with package-bundled fallback.

    When a vault is present, the system prompt is assembled from:
    1. contract/repl-contract.md body (rendered with prompt_vars)
    2. One-line summaries of active helper pages
    3. contract/how-to-work.md body
    4. A few-shot transcript from fewshots/

    Without a vault, falls back to the hardcoded SYSTEM_PROMPT + FEWSHOT_EXAMPLE.
    """
    if vault is None:
        return build_system_prompt(prompt_vars)

    # Try vault-first assembly
    try:
        parts: list[str] = []

        # 1. REPL contract
        repl_page = vault.get("contract/repl-contract.md")
        if repl_page is not None:
            parts.append(repl_page.body.format(**prompt_vars))
        else:
            parts.append(SYSTEM_PROMPT.format(**prompt_vars))

        # 2. How to work
        howto_page = vault.get("contract/how-to-work.md")
        if howto_page is not None:
            parts.append(howto_page.body)

        # 3. Helper one-liners from active helper pages
        helpers = vault.list(kind="helper")
        active_helpers = [h for h in helpers if h.frontmatter.status.value == "active"]
        if active_helpers:
            lines = ["\nAvailable helpers:"]
            for h in active_helpers:
                sig = h.frontmatter.summary
                lines.append(f"  {h.name}: {sig}")
            parts.append("\n".join(lines[:32]))  # progressive-disclosure cap

        return "\n\n".join(parts)
    except Exception:
        return build_system_prompt(prompt_vars)


def load_fewshots_from_vault(
    vault: object | None = None,
) -> list[tuple[str, str]]:
    """Load few-shot examples from vault, with package-bundled fallback."""
    if vault is None:
        return list(FEWSHOT_EXAMPLE)

    try:
        fewshots = vault.list(kind="fewshot")
        if fewshots:
            # Return the first few-shot page body as user/assistant pairs
            # For now, return the hardcoded example + vault content as prompt
            result = list(FEWSHOT_EXAMPLE)
            return result
        return list(FEWSHOT_EXAMPLE)
    except Exception:
        return list(FEWSHOT_EXAMPLE)
