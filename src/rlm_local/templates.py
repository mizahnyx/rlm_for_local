"""Templated harness messages — R4.1: every string the harness emits is frozen here.

All messages use .format() with kwargs so they compose cleanly without
accidental str.format collisions with code blocks containing braces.

When a kernel vault is available, templates load from contract/templates/
pages first, falling back to these package-bundled defaults (§5.2, K0).

`tests/test_templates.py::test_no_dead_templates` enforces that every constant
defined here is actually referenced by `src/rlm_local/` — a template that no
code path can emit is a lie about what the harness says.
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

NUDGE_STDERR_ERROR = (
    "Your last cell raised {error_kind} "
    "({errors}/{max_errors} consecutive errors). "
    "Read the traceback above, fix the cause, and emit exactly one ```repl block."
)

# Raised when a corpus run tries to submit without having called a single corpus
# helper. The harness knows this for a fact — the parent process serves every
# helper request — so the nudge is evidence, not a guess. The third live run
# answered "not mentioned in the corpus" after one `print(len(context))`, which
# is how that looked from the outside.
NUDGE_CORPUS_UNSEARCHED = (
    "You have not searched the corpus, so you cannot have answered a question "
    "about it — `context` is a placeholder, not the data. Emit exactly one "
    "```repl block that calls corpus_search(\"<the key terms of the question>\") "
    "and prints the hits, then read the best hit with corpus_read before you "
    "submit. If the search finds nothing, call corpus_coverage() and say so."
)

# Raised when a corpus run submits an answer that cites nothing and says nothing
# about coverage. The prompt-only version of this rule was measured and failed —
# three consecutive live runs of the 4B laptop model searched, read up to five
# passages, printed the addresses, and cited none of them — so the requirement is
# enforced once now, with the escape hatch the instruction already names.
NUDGE_CORPUS_UNCITED = (
    "Your answer cites nothing, so it cannot be checked. Submit the same answer "
    "again with a final `Citations:` line naming the addresses you actually "
    "read — `Citations: <path>#L<start>-<end>; <path>#L<start>-<end>`. If you "
    "did not read anything that answers the question, say that instead and quote "
    "the coverage line from corpus_coverage(): 'the corpus does not contain "
    "this' is an acceptable answer, an uncited claim about it is not."
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

# Terminal placeholders — the harness must never return an empty string as an
# answer, and these are the only places that decide what to say instead.
NO_ANSWER_PRODUCED = "(No answer produced)"
FINALIZATION_FAILED = "(No answer produced — forced finalization failed)"

# ---------------------------------------------------------------------------
# Cell limits (§5.5, R1.3)
# ---------------------------------------------------------------------------

CELL_TIMEOUT_ERROR = "Error: cell exceeded the {timeout}s time limit."
CELL_STDOUT_TRUNCATED = "\n[... output truncated to {cap} characters ...]"
# stderr keeps head and tail (the traceback ends in the useful line), so the
# elision is reported in the middle.
CELL_STDERR_TRUNCATED = "\n[... {elided} characters of stderr elided ...]\n"
REPL_WORKER_RESTARTED = (
    "Error: the REPL worker did not respond and was restarted. "
    "All REPL variables (including any partial results) were lost."
)

# ---------------------------------------------------------------------------
# Worker-side messages (R4.1, CL3)
# ---------------------------------------------------------------------------
# These are emitted by the sandboxed worker program, which is a string in
# `repl.py`; they are defined here and injected into that program so the
# harness's message layer has one home. Two of them are also emitted on the
# harness side of the same protocol (`search`, `propose`), where the worker and
# `repl_bridge` must agree word for word.

WORKER_INVALID_REGEX = "Error: invalid regex: {error}"
WORKER_NO_HARNESS_RESPONSE = "Error: no response from harness"
WORKER_SEARCH_NO_RESULTS = "(no results)"
WORKER_PROPOSE_FAILED = "Error: propose failed"

# Corpus helpers (RO4). These are the worker-side defaults, used when the harness
# answers with nothing at all — the real answers are formatted by
# `rlm_kernel.corpus.CorpusBridge`, which owns the corpus's own vocabulary
# ("no such path", "refused", "truncated"). Each one is the honest worker-side
# equivalent of "the harness told me nothing", never a fake success.
WORKER_CORPUS_NO_MATCHES = "(no corpus paths matched)"
WORKER_CORPUS_NO_ENTRIES = "(no corpus entries)"
WORKER_CORPUS_NOT_FOUND = "Error: no such path in the corpus: {rel}"
WORKER_CORPUS_READ_FAILED = "Error: corpus read failed: {rel}"
WORKER_CORPUS_COUNT_FAILED = "Error: corpus count failed"
WORKER_CORPUS_SEARCH_FAILED = "Error: corpus text search failed"

# ---------------------------------------------------------------------------
# Corpus runs (RO4)
# ---------------------------------------------------------------------------
# A corpus question has no `context` string: the data is a file tree that a cell
# reaches through the corpus_* helpers. Saying so is load-bearing — a model given
# an empty context will otherwise try to read the corpus out of `context`, and
# this corpus is far too large for any cell to walk.
CORPUS_CONTEXT_STUB = (
    "The data for this task is a large read-only corpus, not a string. "
    "`context` is NOT the corpus: reach it with corpus_search (words inside "
    "files), corpus_find (paths, and members inside archives), corpus_list, "
    "corpus_stat, corpus_read and corpus_count. Never walk the tree from a cell."
)

WORKER_MESSAGES: dict[str, str] = {
    "invalid_regex": WORKER_INVALID_REGEX,
    "no_harness_response": WORKER_NO_HARNESS_RESPONSE,
    "search_no_results": WORKER_SEARCH_NO_RESULTS,
    "propose_failed": WORKER_PROPOSE_FAILED,
    "corpus_no_matches": WORKER_CORPUS_NO_MATCHES,
    "corpus_no_entries": WORKER_CORPUS_NO_ENTRIES,
    "corpus_not_found": WORKER_CORPUS_NOT_FOUND,
    "corpus_read_failed": WORKER_CORPUS_READ_FAILED,
    "corpus_count_failed": WORKER_CORPUS_COUNT_FAILED,
    "corpus_search_failed": WORKER_CORPUS_SEARCH_FAILED,
}

