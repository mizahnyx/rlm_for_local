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

# Raised when a corpus answer's *entire* evidence is made of hits the search
# labelled `weak` or `none` — passages the harness itself told the model do not
# answer the question. The label alone was measured first and changed nothing: on
# 2026-09-16 a run was served a `weak` match eight times and cited the hits and
# submitted anyway (docs/20260916-2200-corpus-weak-labels-were-served-and-ignored.md).
# Same escape hatch as the uncited nudge, and for the same reason: on a question
# the corpus does not hold, "the corpus does not contain this, here is the
# coverage" is the truthful answer, so this cannot trap a run.
NUDGE_CORPUS_WEAK_EVIDENCE = (
    "Your answer rests on a passage that does not answer the question: every "
    "address you cited was a hit the search labelled `weak` or `none`, which means "
    "it covers too few of the question's words to be an answer. Submit the same "
    "answer again naming a passage the search called `strong` or `partial`, or — if "
    "there is none — say the corpus does not contain the answer and quote the line "
    "corpus_coverage() printed (it begins `[coverage:`). A weak match is a "
    "coincidence of wording, not evidence, and citing it does not make it one."
)

# Raised when a cell did not *compile*. The owner's finding (2026-09-17): small
# models fail to produce valid Python often enough that charging a turn for it ends
# runs that had done nothing wrong — the cell never ran, so nothing was attempted
# and nothing was learned. A syntax error therefore costs neither a turn nor the
# error budget, and the retry is bounded by `max_syntax_retries` so a model that
# cannot write Python at all still terminates.
NUDGE_SYNTAX_ERROR = (
    "That cell did not run: it is not valid Python, so the harness could not execute "
    "any part of it. This did not count against your turn budget. Send the cell again "
    "as valid Python — the same intent, corrected syntax. Common causes: a missing "
    "colon at the end of a `for`/`if`/`def` line, unbalanced quotes or brackets, and "
    "a paste that lost a line. The interpreter said:\n{error}"
)

# Raised when a cell exhausted its time budget. It names the budget and the helper,
# so an operator can tell a *harness* limit from a model failure — the distinction
# the owner asked for on 2026-09-17, after `corpus_count` died on a 60 s budget on a
# loaded host. `{helper}` is the last corpus helper the cell asked for, or `none`
# when it asked for nothing, which is what makes "raise the budget" and "fix the
# helper" different conclusions.
NUDGE_CELL_TIMEOUT = (
    "That cell was stopped after {timeout}s: it had not finished. The budget is a "
    "harness limit, not a judgement of your code — a large or loaded corpus can "
    "exceed it. Ask for less in one cell: bound the work (`limit=`, `k=`, `under=`), "
    "split it across cells, or use a cheaper helper. If the same call keeps timing "
    "out, say what you were asking for ({helper}) instead of retrying it."
)

# Raised before the *last* turn of a corpus run that has looked but not answered.
# The turn header already says `Turn 8/8.`, so what is missing is not information
# about the budget but permission to stop: three live runs on a question the
# corpus cannot answer each explored to the end (5/8, 8/8, 8/8 turns) and were
# answered by forced finalization. Appended before the last call, so it costs no
# extra turn — it changes what the final turn is for. Both arms are named, or a
# model with nothing to cite is pushed into inventing one.
NUDGE_CORPUS_LAST_TURN = (
    "This is your last turn (turn {turn} of {max_turns}). Stop searching and "
    "submit now. If what you read answers the question, submit it with the "
    "addresses in a final `Citations:` line. If it does not, submit an answer "
    "that says the corpus does not contain this and quote the coverage line from "
    "corpus_coverage() — 'I did not find it' is a complete answer, and a run that "
    "ends without submitting loses everything it had learned."
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

# The corpus variant of the same request (§5.5, RO4). The terminal answer is the
# one guaranteed to be delivered, so it is where the corpus's provenance
# requirement has to be restated: two live runs on an unanswerable question spent
# the whole turn budget exploring, never submitted, and were answered here — both
# times uncited, with the citation guard (which refuses uncited *submissions*)
# never firing at all. Both arms are named because a question the corpus cannot
# answer has to remain answerable: cite what you read, or say the corpus does not
# contain it and quote its coverage.
FORCED_FINALIZATION_CORPUS_PROMPT = (
    "Based on everything you have learned so far, provide your best final "
    "answer now, in plain text. This run had a read-only corpus, so end your "
    "answer with a `Citations:` line naming the addresses you actually read — "
    "`Citations: <path>#L<start>-<end>` — or, if you did not read anything that "
    "answers the question, say that the corpus does not contain the answer and "
    "quote the line corpus_coverage() printed (it begins `[coverage:`). An "
    "answer that does neither cannot be checked, and it will be recorded as such."
)

# Terminal placeholders — the harness must never return an empty string as an
# answer, and these are the only places that decide what to say instead.
NO_ANSWER_PRODUCED = "(No answer produced)"
FINALIZATION_FAILED = "(No answer produced — forced finalization failed)"

# ---------------------------------------------------------------------------
# Cell limits (§5.5, R1.3)
# ---------------------------------------------------------------------------

CELL_TIMEOUT_ERROR = "Error: cell exceeded the {timeout}s time limit."
# The second, hard limit (owner, 2026-09-17). The soft limit above *signals*: a cell
# that is demonstrably working is allowed to continue. This one stops it, and it
# reads differently on purpose — "exceeded the 60.0s time limit" would blame the
# wrong budget for a cell that was granted 1200 s and still did not finish.
CELL_HARD_TIMEOUT_ERROR = (
    "Error: cell exceeded the hard time limit of {timeout}s and was stopped."
)
# The operator line for an extension. A run that grants a cell twenty minutes must
# say so while it happens, not only in the trajectory afterwards.
CELL_EXTENDED_WARNING = (
    "[rlm] a cell has been running for {elapsed:.0f}s (soft limit {soft:g}s) and is "
    "doing work — {helper} — so it is allowed up to {hard:g}s. This is a "
    "time-consuming operation on this host, not a hang."
)
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
# Emitted by the worker when a cell does not compile. It is a *worker* message
# because that is where the interpreter error is caught, and the parent keys off
# the `syntax_error` flag the worker sets alongside it rather than parsing this
# text (a message the parent string-matches is a message that breaks when reworded).
WORKER_CELL_SYNTAX_ERROR = (
    "SyntaxError: this cell is not valid Python, so none of it ran. The "
    "interpreter said: {error}"
)

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
# A call with a keyword the helper does not take used to raise Python's own TypeError
# into the cell's stderr, where a small model can do nothing with it. Measured
# 2026-09-20 on the second question set: question 1 spent two of its six turns on
# `corpus_search(..., limit=5)` — the helper takes `k=` — and the traceback named the
# internal wrapper, not the helper the model had called.
WORKER_CORPUS_BAD_ARGUMENTS = (
    "Error: {helper}() does not take those arguments ({error}). It takes: {takes}. "
    "Nothing was run; call it again with the parameters above."
)

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
    "cell_syntax_error": WORKER_CELL_SYNTAX_ERROR,
    "corpus_no_matches": WORKER_CORPUS_NO_MATCHES,
    "corpus_no_entries": WORKER_CORPUS_NO_ENTRIES,
    "corpus_not_found": WORKER_CORPUS_NOT_FOUND,
    "corpus_read_failed": WORKER_CORPUS_READ_FAILED,
    "corpus_count_failed": WORKER_CORPUS_COUNT_FAILED,
    "corpus_search_failed": WORKER_CORPUS_SEARCH_FAILED,
    "corpus_bad_arguments": WORKER_CORPUS_BAD_ARGUMENTS,
}

