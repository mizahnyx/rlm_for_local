"""System prompt and few-shot transcripts (§5.5).

The system prompt is a .format() template receiving config-injected values.
Few-shots are load-bearing for small models and use the exact message format
of live turns (R4.1 equivalence-class normalization).
"""

from __future__ import annotations

import re

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

# ── Example 2: voluntary submission (R25.6) ───────────────────────────────
# The recorded P4 follow-up: the model was scoring 0/15 on "answer-dict
# submission" because it described its answer in prose and let forced
# finalization carry the run. Example 1 *does* end in a submission, but it
# arrives after three turns of narration; this example makes the point
# unmissable — a short probe, then submit *immediately*, in the same turn the
# evidence is verified, without being asked.

EXAMPLE_2_HEADER = "Example 2: Submit the moment the evidence is verified."

FEWSHOT_SUBMISSION_EXAMPLE = [
    ("user", "Answer the following: What is the vault access code?\n\n"
             "Your context is a str of 120000 total characters. A sub-LLM call "
             "handles roughly 8000 characters well. You have 8 turns."),
    ("assistant",
     "One step is enough: grep the context for the code, then submit it in the "
     "same turn. I will not narrate the answer instead of submitting it.\n\n"
     "```repl\n"
     "hits = grep('access code')\n"
     "print(f'Found {len(hits)} matches')\n"
     "print(hits[0] if hits else 'none')\n"
     "```"),
    ("user", "REPL output:\n"
             "Found 1 matches\n"
             "The vault access code is KX-2210 and it rotates every quarter."),
    ("assistant",
     "The evidence is verified, so I submit now — this is the step that ends the "
     "task. Note that submitting means executing code that sets `answer`; writing "
     "the answer as prose leaves the task unfinished and the harness has to force "
     "a finalization.\n\n"
     "```repl\n"
     "answer['content'] = 'The vault access code is KX-2210.'\n"
     "answer['ready'] = True\n"
     "```"),
]

FEWSHOT_EXAMPLE = FEWSHOT_EXAMPLE + [
    ("user", EXAMPLE_2_HEADER)
] + FEWSHOT_SUBMISSION_EXAMPLE


def build_system_prompt(prompt_vars: dict) -> str:
    """Construct the full system prompt with config values injected."""
    return SYSTEM_PROMPT.format(**prompt_vars)


# ── Corpus helpers (RO4) ──────────────────────────────────────────────────
# Appended to the system prompt by the root loop when a corpus is configured,
# in packaged *and* vault-assembled prompt paths alike. Not a `prompt_vars` slot
# on purpose: the section is runtime state about this run (is there a corpus? is
# there an index?), not a hardware-profile value, and `Config.prompt_vars()` is
# checked against the SYSTEM_PROMPT template key-for-key.
CORPUS_SECTION_HEADER = "The corpus (read-only)"
CORPUS_SECTION_LINES = (
    "- A large read-only file tree is available: {root}.",
    "- corpus_find(query, limit=20, kind=None, under=\"\") — search the corpus"
    " path index. Results are relative paths. A query with a '/' searches full"
    " paths; without one it searches file names.",
    "- corpus_list(rel=\"\", limit=50) — one directory level (not the subtree).",
    "- corpus_stat(rel) — kind, size and mtime of one path.",
    "- corpus_read(rel, max_bytes=20000) — read one file, bounded. Long files are"
    " truncated and say so.",
    "- corpus_count(kind=None, under=\"\") — counts and byte totals without"
    " listing anything.",
    "- Never walk the corpus from a cell, and never build a list of every path:"
    " it holds millions of files and a single walk takes tens of minutes. Search"
    " with corpus_find, count with corpus_count.",
    "- The corpus is read-only: nothing you run can or may change it.",
)
CORPUS_SECTION_NO_INDEX = (
    "- No path index is built yet, so corpus_find and corpus_count will say so."
    " corpus_list, corpus_stat and corpus_read work regardless."
)


def corpus_helpers_section(root: str, has_index: bool) -> str:
    """The corpus capability block, with the helper names the worker defines.

    `tests/test_corpus_repl.py` checks these names against the worker's actual
    namespace, because a prompt that advertises a helper the worker does not
    define is a lie the model will act on.
    """
    lines = [CORPUS_SECTION_HEADER]
    lines.extend(line.format(root=root) if "{root}" in line else line
                 for line in CORPUS_SECTION_LINES)
    if not has_index:
        lines.append(CORPUS_SECTION_NO_INDEX)
    return "\n".join(lines)


def build_messages(
    query: str,
    context_len: int,
    context_type: str,
    prompt_vars: dict,
    *,
    system_prompt: str | None = None,
    fewshots: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build the initial message list for a RootLoop.

    Includes: system prompt, metadata user message, prologue, and one few-shot.
    The few-shot is part of the byte-stable prefix for caching (R4.1, §5.5).

    Args:
        system_prompt: Optional override for the system prompt. If None,
            the hardcoded SYSTEM_PROMPT (config-injected) is used.
        fewshots: Optional override for few-shot examples. If None,
            the hardcoded FEWSHOT_EXAMPLE is used.
    """
    from rlm_local.templates import METADATA_TEMPLATE, PROLOGUE

    sp = system_prompt if system_prompt is not None else build_system_prompt(prompt_vars)
    fs = fewshots if fewshots is not None else list(FEWSHOT_EXAMPLE)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": sp},
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
    for role, content in fs:
        messages.append({"role": role, "content": content})

    return messages


# ── Vault-first loading (K0, §5.2) ────────────────────────────────────────

CONTRACT_PAGE_PATH = "contract/repl-contract.md"
HOW_TO_WORK_PAGE_PATH = "contract/how-to-work.md"

#: Contract pages whose body is rendered with `profile.prompt_vars()`.
#:
#: Named here because it is a *rule*, not an implementation detail: a page on this
#: list must use only the slots the runtime actually fills, or assembly raises
#: `KeyError` (which is caught, so the page is silently ignored and the packaged
#: prompt is used instead). The gate's slot-subset check (CL4) reads this list
#: rather than guessing — which is what keeps it from rejecting the template
#: pages, whose slots belong to `templates.py` and are never filled from
#: `prompt_vars`.
FORMATTED_CONTRACT_PATHS: tuple[str, ...] = (CONTRACT_PAGE_PATH,)


def load_system_prompt_from_vault(
    prompt_vars: dict,
    vault: object | None = None,
    bridge: object | None = None,
) -> str:
    """Build the system prompt from vault pages, with package-bundled fallback.

    When a vault is present, the system prompt is assembled from:
    1. contract/repl-contract.md body (rendered with prompt_vars)
    2. One-line summaries of active helper pages
    3. contract/how-to-work.md body
    4. A few-shot transcript from fewshot/ (R8: one active vault page, if it
       fits the prompt budget; see load_fewshots_from_vault)

    Args:
        bridge: Optional KernelBridge for index-backed helper listing (C1).
            When provided, active helpers are listed via SQL index query
            instead of full vault walk.

    Without a vault, falls back to the hardcoded SYSTEM_PROMPT + FEWSHOT_EXAMPLE.
    """
    if vault is None:
        return build_system_prompt(prompt_vars)

    # Try vault-first assembly
    try:
        parts: list[str] = []

        # 1. REPL contract — the one page rendered with prompt_vars
        # (FORMATTED_CONTRACT_PATHS): a slot it uses that the runtime does not
        # fill would raise here, and the fallback below would hide it.
        repl_page = vault.get(CONTRACT_PAGE_PATH)
        if repl_page is not None:
            parts.append(repl_page.body.format(**prompt_vars))
        else:
            parts.append(SYSTEM_PROMPT.format(**prompt_vars))

        # 2. How to work — appended verbatim, so its braces are literal text
        howto_page = vault.get(HOW_TO_WORK_PAGE_PATH)
        if howto_page is not None:
            parts.append(howto_page.body)

        # 3. Helper one-liners — one source of truth (DG9): the bridge's
        # index-backed listing when a bridge is available, a vault walk when it
        # is not. Both apply the same "active only, capped" rule.
        if bridge is not None:
            helper_lines = bridge.get_helper_summaries()
        else:
            helpers = vault.list(kind="helper")
            helper_lines = [
                f"  {h.name}: {h.frontmatter.summary}"
                for h in helpers
                if h.frontmatter.status.value == "active"
            ][:30]

        if helper_lines:
            parts.append("\n".join(["\nAvailable helpers:"] + helper_lines))

        return "\n\n".join(parts)
    except Exception:
        return build_system_prompt(prompt_vars)


def parse_fewshot_body(body: str) -> list[tuple[str, str]]:
    """Turn a vault few-shot page body into (role, content) message pairs (R8).

    Two shapes are recognised, both of which this repository actually writes:

    1. ``## Query`` + ``## Answer`` — written by ``rlm_kernel.seed`` and by
       ``rlm_kernel.optimize.bootstrap_fewshots``. Yields one user/assistant
       pair.
    2. One or more ``## Example`` / ``## Examples`` sections, each optionally
       split by a ``### Assistant`` (or ``### Response`` / ``### Answer``)
       sub-heading into the user turn and the assistant turn. Yields one pair
       per section.

    Anything else yields ``[]``. Guessing at a transcript the page does not
    actually contain would put invented turns in the prompt.
    """
    sections = _sections(body)
    if not sections:
        return []

    by_title = {title: content for title, content in sections}
    if by_title.get("query") and by_title.get("answer"):
        return [("user", by_title["query"]), ("assistant", by_title["answer"])]

    pairs: list[tuple[str, str]] = []
    for title, content in sections:
        if not title.startswith("example"):
            continue
        split = _split_example(content)
        if split is not None:
            pairs.extend(split)
    return pairs


# A `## Heading` line starts a new section.
_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# Within an example section, the assistant turn starts at one of these.
_ASSISTANT_HEADING_RE = re.compile(
    r"^###[ \t]*(?:assistant|response|answer)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
# ...and the user turn, when it is labelled at all.
_USER_HEADING_RE = re.compile(
    r"^###[ \t]*(?:user|query|question|prompt)[ \t]*$", re.MULTILINE | re.IGNORECASE
)


def _sections(body: str) -> list[tuple[str, str]]:
    """Split a body on `## ` headings into (lowercased title, content) pairs."""
    matches = list(_SECTION_RE.finditer(body))
    out: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out.append((match.group(1).strip().lower(), body[match.end():end].strip()))
    return out


def _split_example(content: str) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Split one `## Example` section into ((user...), (assistant...)), or None."""
    assistant = _ASSISTANT_HEADING_RE.search(content)
    if assistant is None:
        return None
    user_part = content[: assistant.start()]
    assistant_part = content[assistant.end():]
    user_heading = _USER_HEADING_RE.search(user_part)
    if user_heading is not None:
        user_part = user_part[user_heading.end():]
    user_text = user_part.strip()
    assistant_text = assistant_part.strip()
    if not user_text or not assistant_text:
        return None
    return ("user", user_text), ("assistant", assistant_text)


def load_fewshots_from_vault(
    vault: object | None = None,
    prompt_char_budget: int | None = None,
) -> list[tuple[str, str]]:
    """Load few-shot examples from the vault, with package-bundled fallback (R8).

    The builtin example is always first (it is load-bearing for small models and
    defines the conversation shape). At most **one** active vault few-shot is
    then appended, and only if it fits the prompt budget: a pair whose combined
    length exceeds ``prompt_char_budget / 4`` is skipped, because it would crowd
    out real work in the root window.

    Args:
        vault: A VaultStore, or None for the package-bundled example only.
        prompt_char_budget: The sub-prompt character budget the pair must fit
            within a quarter of. Defaults to the `laptop` profile's budget.

    Anything unrecognised or unreachable degrades to the bundled example — this
    function must never be able to break a run.
    """
    if vault is None:
        return list(FEWSHOT_EXAMPLE)

    if prompt_char_budget is None:
        from rlm_local.config import PROFILES

        prompt_char_budget = PROFILES["laptop"].sub_prompt_char_budget
    max_pair_chars = max(0, prompt_char_budget // 4)

    try:
        pages = vault.list(kind="fewshot")
    except Exception:
        return list(FEWSHOT_EXAMPLE)

    result = list(FEWSHOT_EXAMPLE)
    for page in pages:
        try:
            status = page.frontmatter.status.value
        except Exception:
            continue
        if status != "active":
            continue
        pairs = parse_fewshot_body(page.body or "")
        if not pairs:
            continue
        if sum(len(content) for _, content in pairs) > max_pair_chars:
            continue
        result.extend(pairs)
        break  # at most one vault few-shot
    return result
