"""Real questions against the corpus, reported as aggregates (2026-09-18).

Why this exists
---------------
The cell-budget probe (`scripts/probe_cell_budget.py`) measures a *mechanism*: a cell
that sleeps for 75 seconds. The owner's point is that a mechanism is not the task. The
traces that say whether this harness can navigate a 4.28M-file corpus are the traces of
**real questions**, asked by a real model, answered (or refused) from real passages.

What it does
------------
`parse_questions` reads a question set; `run_question` runs one question through the
harness against the read-only corpus and writes one trajectory; `render_line` reports
that run as one aggregate line, using the *same* renderer as `rlm trace summary` so the
operator reads one vocabulary everywhere.

Privacy is structural, not a promise
------------------------------------
Two rules, both enforced here and both tested:

* **The probe prints aggregates only.** Turns, timeouts, extensions, helper calls,
  citation counts, refusals, wall clock — never the answer, never a passage, never an
  address. A probe whose stdout carried an answer would be a probe that leaks the corpus
  into whatever window is reading it (`AGENTS.md` §1.9).
* **A question set lives beside the corpus.** A question devised from a passage is
  corpus-derived, so question files belong in `~/rlm-derived/questions/` on the machine
  holding the corpus. What may live in *this* repository is `DEFAULT_QUESTIONS`: the three
  questions about the corpus's own aggregates — counts, coverage, kinds — which reveal
  nothing about its contents.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rlm_local.logger import TrajectoryLogger
from rlm_local.root_loop import RootLoop
from rlm_local.templates import CORPUS_CONTEXT_STUB
from rlm_local.traceview import read_trajectory, render_summary

#: Questions that may live in this repository: they ask about the corpus's own
#: aggregates, never about its contents. Everything else belongs beside the corpus.
DEFAULT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("how-many-entries",
     "How many files and how many directories does the corpus contain?"),
    ("how-much-is-indexed",
     "How much of the corpus has been indexed for text search so far?"),
    ("what-kinds-of-material",
     "What kinds of material does this corpus hold, in aggregate? Quote the "
     "coverage line you used."),
)


@dataclass(frozen=True)
class Question:
    """One question with the stable name its trajectory is written under."""

    id: str
    question: str


@dataclass
class QuestionRun:
    """What one question produced: where its trajectory is, and how to read it."""

    question: Question
    trajectory: Path
    seconds: float
    summary: str
    error: str | None = None


def select_questions(
    questions: list[Question], only: str | None, *, source: str,
) -> list[Question]:
    """The subset `--only` asks for, or a refusal that says what the set holds.

    `--only` matching nothing is nearly always a *missing* `--questions` rather than a
    typo: the built-in set is three questions about aggregates, so an operator who has
    written their own set and forgotten the flag gets an empty result that looks like a
    broken tool. (Measured 2026-09-19: the first attempt to re-run one of the owner's
    questions did exactly that and printed `No questions to run.`)

    So the refusal names the set it searched and, when the set is a file, the ids that
    file holds. Those ids are the operator's own labels, printed on the machine that
    holds the corpus, which is where they already live — and the alternative, a message
    saying only "nothing matched", sends them looking for a bug in the filter.
    """
    if not only:
        return list(questions)
    wanted = only.lower()
    matched = [q for q in questions if wanted in q.id.lower()]
    if matched:
        return matched
    available = ", ".join(q.id for q in questions) or "(the set is empty)"
    raise ValueError(
        f"no question id in {source} matches {only!r}. Ids in this set: {available}. "
        "If your own set is a file, pass it with --questions PATH."
    )


def _slug(name: str) -> str:
    """A file-name-safe version of an id, so a question can name its trajectory."""
    cleaned = "".join(
        ch if (ch.isalnum() or ch in "-_") else "-" for ch in name.strip().lower()
    )
    return cleaned.strip("-") or "question"


#: The most likely hand-editing mistake: an id separated from its question by spaces
#: instead of a tab. The shape is narrow on purpose — a slug containing `-` or `_`,
#: then two or more spaces — because a *question* may legitimately begin with a short
#: lowercase word, and refusing those would be worse than the mistake it prevents.
_SPACE_SEPARATED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*[-_][a-z0-9_-]*\s{2,}\S")


def _unique(identifier: str, seen: set[str], reserved: set[str]) -> str:
    """Make room for a repeated id instead of letting two questions share one file.

    Ids name the trajectories (`<id>.jsonl`) and `run_question` truncates before it
    writes, so two questions with the same id would silently keep only the second
    one's evidence. A numeric suffix is visible in the file name and in the probe's
    own line, which is the point: a lost trajectory must not be quiet.

    `reserved` holds the ids written explicitly in the file. A suffix invented here
    must not take one of them — an id the owner wrote down keeps its name, and the
    duplicate is the one that moves.
    """
    if identifier not in seen:
        return identifier
    suffix = 2
    while (candidate := f"{identifier}-{suffix}") in seen or candidate in reserved:
        suffix += 1
    return candidate


def parse_questions(text: str) -> list[Question]:
    """A question set from text: one per line, `#` comments, `id<TAB>question`.

    Deliberately the simplest format that carries a name and a question, because the
    point of a local question file is that the owner amends it between runs. Three
    rules, each of which exists because the alternative loses evidence:

    * A bare question gets `q1`, `q2`, … in file order.
    * An id is **slugged** so it can name a file, and **kept unique** so two questions
      cannot land on one trajectory.
    * A TAB separates id from question. A line shaped like `some-id  Question?` — a
      slug, spaces, text — is **refused**, because reading it as a question would file
      it under an automatic id and hide the mistake until someone noticed that a
      trajectory was named `q7`.

    Raises:
        ValueError: for a line shaped like a space-separated id.
    """
    questions: list[Question] = []
    seen: set[str] = set()
    # Explicit ids are reserved before anything is auto-named, so an id the owner
    # wrote down is never displaced by a suffix invented for a duplicate.
    reserved = {
        _slug(line.partition("\t")[0].strip())
        for line in (raw.strip() for raw in text.splitlines())
        if line and not line.startswith("#") and "\t" in line
        and line.partition("\t")[0].strip()
    }
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            name, _, body = line.partition("\t")
            name, body = name.strip(), body.strip()
        else:
            if _SPACE_SEPARATED_ID_RE.match(line):
                raise ValueError(
                    "this line looks like an id separated from its question by "
                    f"spaces: {line[:60]!r}. Separate them with a TAB — "
                    "`id<TAB>question` — or make the whole line the question (it "
                    "then gets an automatic id)."
                )
            name, body = "", line
        if not body:
            continue
        identifier = _unique(_slug(name or f"q{len(questions) + 1}"), seen, reserved)
        seen.add(identifier)
        questions.append(Question(id=identifier, question=body))
    return questions


def example_question_set_text() -> str:
    """A valid question set, with the format stated in its own header.

    Written by `scripts/run_question_probe.py --example`, and parsed by a test, so the
    documented format and the parser cannot drift apart: an example that no longer
    parses would be worse than no example at all.
    """
    lines = [
        "# A question file for: scripts/run_question_probe.py --questions THIS_FILE",
        "#",
        "# * One question per line. Blank lines and lines beginning with # are ignored.",
        "# * `id<TAB>question` — the separator is a TAB, not spaces — names the",
        "#   trajectory the question writes (`<id>.jsonl`).",
        "# * A line with no TAB is the whole question, and gets an automatic id:",
        "#   q1, q2, … in file order.",
        "# * Ids are slugged (lower case; anything that is not a letter, digit, dash or",
        "#   underscore becomes a dash) and kept unique, because an id is a file name:",
        "#   `../../escape/me` becomes `escape-me`, and a repeated id gains a numeric",
        "#   suffix (`dup`, then `dup-2`) — unless that name is written explicitly",
        "#   further down, in which case the duplicate is the one that moves.",
        "# * Keep a set devised from passages where the corpus is — it is corpus-derived",
        "#   (AGENTS.md §1.9). The three questions below ask about the corpus's own",
        "#   aggregates, which is why they may live in the repository. Replace them.",
        "",
    ]
    lines += [f"{name}\t{text}" for name, text in DEFAULT_QUESTIONS]
    lines += [
        "",
        "# A bare question, for comparison. Uncommented it would run as `q4`, because",
        "# three ids are already taken:",
        "# How many of the corpus's files are images?",
        "#",
        "# Runs are sequential and each question gets its own trajectory, so a set is",
        "# re-runnable: the probe copies the set it used into --out-dir as",
        "# `questions.txt`, beside the trajectories it produced.",
    ]
    return "\n".join(lines) + "\n"


def run_question(
    question: Question,
    *,
    config: Any,
    backend: Any,
    corpus_bridge: Any,
    out_dir: Path,
    warning_sink: Any = None,
) -> QuestionRun:
    """Run one question, write its trajectory, and read the run back as counts.

    A question that *fails* must not take the probe with it: six questions are a
    measurement, and a transport error on the fourth is a fact about the run rather
    than a reason to lose the other five. The failure is recorded on the `QuestionRun`
    and the caller prints it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{question.id}.jsonl"
    # Truncate: the logger appends, so a second probe run would otherwise leave two
    # runs' events in one file and the page would show executions that never happened.
    path.unlink(missing_ok=True)
    logger = TrajectoryLogger(path)
    loop = RootLoop(config, backend, logger=logger, kernel_bridge=None,
                    corpus_bridge=corpus_bridge, warning_sink=warning_sink)
    started = time.monotonic()
    error: str | None = None
    try:
        loop.run(question.question, CORPUS_CONTEXT_STUB)
    except Exception as e:  # a dead server, a torn socket: record it and continue
        error = f"{type(e).__name__}: {e}"
    finally:
        loop.shutdown()
    seconds = time.monotonic() - started
    summary = render_summary(read_trajectory(path))
    return QuestionRun(question=question, trajectory=path, seconds=seconds,
                       summary=summary, error=error)


def render_line(run: QuestionRun) -> str:
    """The aggregate line for one question — and *only* aggregates.

    `render_summary` carries no question, no address and no quote (`rlm trace
    summary` is documented as the form safe to paste anywhere); this adds the wall
    clock and, when the run failed, the failure. The answer itself stays in the
    trajectory, beside the corpus.
    """
    line = f"{run.question.id}: wall={run.seconds:.0f}s {run.summary}"
    if run.error:
        line += f" error={run.error}"
    return line


def write_question_set(questions: list[Question], path: Path) -> None:
    """Keep the set that was run next to the trajectories it produced.

    A measurement is only re-runnable if the questions are on the page with it: the
    trajectory says what the harness did, and this says what it was asked.
    """
    lines = [
        "# The question set this probe run used.",
        "# One question per line; `id<TAB>question` (a TAB) names the trajectory, and an",
        "# id is slugged and kept unique. A line with no TAB is the whole question.",
        "# Corpus-derived: keep this file where the corpus is (AGENTS.md §1.9).",
        "",
    ]
    lines += [f"{question.id}\t{question.question}" for question in questions]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class QuestionSet:
    """A parsed set plus where it came from, for the probe's own reporting."""

    questions: list[Question] = field(default_factory=list)
    source: str = "built-in"
