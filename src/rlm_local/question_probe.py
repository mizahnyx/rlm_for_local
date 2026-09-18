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


def _slug(name: str) -> str:
    """A file-name-safe version of an id, so a question can name its trajectory."""
    cleaned = "".join(
        ch if (ch.isalnum() or ch in "-_") else "-" for ch in name.strip().lower()
    )
    return cleaned.strip("-") or "question"


def parse_questions(text: str) -> list[Question]:
    """A question set from text: one per line, `#` comments, `id<TAB>question`.

    Deliberately the simplest format that carries a name and a question, because the
    point of a local question file is that the owner amends it between runs. A bare
    question gets `q1`, `q2`, … in file order; an id is slugged so it can name a file.
    """
    questions: list[Question] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            name, _, body = line.partition("\t")
            name, body = name.strip(), body.strip()
        else:
            name, body = "", line
        if not body:
            continue
        questions.append(Question(id=_slug(name or f"q{len(questions) + 1}"),
                                  question=body))
    return questions


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
    lines = ["# The question set this probe run used. One question per line;",
             "# `id<TAB>question` names the trajectory. Corpus-derived: keep it here.",
             ""]
    lines += [f"{question.id}\t{question.question}" for question in questions]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class QuestionSet:
    """A parsed set plus where it came from, for the probe's own reporting."""

    questions: list[Question] = field(default_factory=list)
    source: str = "built-in"
