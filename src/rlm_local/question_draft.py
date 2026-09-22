"""Draft a question set from prose passages, without the passages leaving the machine.

The owner's evaluation loop needs questions that a person would actually ask of this
corpus, and `AGENTS.md` §1.9 puts corpus prose out of reach of anything that travels: not
into the repository, not into a session transcript, not into a commit message. So the
questions are drafted *where the corpus is*, by a local model, one passage at a time, and
what travels back is a count.

Two consequences are stated rather than hidden:

* **The drafter is a model, so the questions are a model's idea of a question.** The set is
  written beside the corpus for the owner to read, edit or discard; nothing in this module
  decides that a drafted question is a good one.
* **The confound is real and is recorded, not removed.** If the same model drafts and
  answers, a low citation rate may say more about the drafter than the harness. Using a
  *different* model for drafting is the mitigation, and the operator picks it; the drafting
  model is written into the set's header so a reader can see which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rlm_local.question_probe import Question
from rlm_local.templates import (
    DRAFT_QUESTION_PROMPT,
    DRAFT_QUESTION_SYSTEM,
    DRAFT_QUESTION_UNUSABLE,
)

#: Opening words that introduce a question rather than being one. A model told "no preamble"
#: still sometimes answers "Question: …", and stripping that is cheaper than another call.
_LEAD_INS = ("question:", "pregunta:", "q:", "1.", "1)", "2.", "2)")

#: Decoration a model adds around a line: bullets, emphasis, quotes, headings.
_DECORATION = "`*_#> \t\"'-\u2022\u00b7"

#: Patterns that mean the reply is commentary. A draft that begins with one of these is
#: dropped rather than shipped as a question: "I cannot answer that" is not a probe.
_COMMENTARY = re.compile(
    r"^(i (cannot|can't|can not|am unable|don't|do not|apologize|apologise|need|would)|"
    r"as an ai|lo siento|no puedo|no es posible|sorry[,.]|unfortunately|"
    r"the passage (does not|doesn't)|this passage (does not|doesn't))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Draft:
    """One drafted question and the address it came from (the address stays local)."""

    identifier: str
    question: str
    address: str
    draft_model: str
    usable: bool


def clean_question(reply: str) -> str:
    """The question inside a model reply, or an empty string if there is not one.

    Deliberately strict and deliberately dumb: one line, lead-ins removed, no markdown
    decoration, must end in a question mark, must not read as commentary. A question the
    probe cannot use is a question that wastes a whole run, and a run costs tens of
    minutes — so the cheap filter lives here and the expensive one is the operator reading
    the set.
    """
    if not reply or not reply.strip():
        return ""
    for raw_line in reply.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Decoration and lead-ins interleave — `- Pregunta: **¿Qué pasó?**` needs both, in
        # either order — so strip until the line stops changing, bounded so a pathological
        # reply cannot spin here.
        for _ in range(4):
            before = line
            line = line.strip(_DECORATION)
            lowered = line.lower()
            for lead in _LEAD_INS:
                if lowered.startswith(lead):
                    line = line[len(lead):].strip()
                    break
            if line == before:
                break
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if _COMMENTARY.match(line):
            return ""
        if line.endswith("?") or line.endswith("¿") or line.endswith("？"):
            return line
        # A single line that is not a question: keep looking, then give up rather than
        # inventing a question mark for a statement.
        continue
    return ""


def draft_question(backend: object, passage: str, *, draft_model: str = "",
                   identifier: str = "", address: str = "", max_tokens: int = 160,
                   passage_chars: int = 3_000) -> Draft:
    """One passage, one call, one question — or a `Draft` marked unusable."""
    prompt = DRAFT_QUESTION_PROMPT.replace(
        "{passage}", (passage or "")[:passage_chars].strip())
    reply = backend.chat(  # type: ignore[attr-defined]
        [{"role": "system", "content": DRAFT_QUESTION_SYSTEM},
         {"role": "user", "content": prompt}],
        tier="root", max_tokens=max_tokens, temperature=0.2,
    )
    question = clean_question(reply)
    return Draft(
        identifier=identifier,
        question=question or DRAFT_QUESTION_UNUSABLE,
        address=address,
        draft_model=draft_model,
        usable=bool(question),
    )


def as_questions(drafts: list[Draft]) -> list[Question]:
    """The usable drafts as the probe's own `Question` rows."""
    return [Question(id=d.identifier, question=d.question) for d in drafts if d.usable]


def write_sources(drafts: list[Draft], path: Path) -> None:
    """`id<TAB>address<TAB>usable` beside the corpus — never into the repository.

    The address is corpus-derived, so this file is written where the corpus is, mode 0600,
    and is what lets the owner see which passage a question came from and disagree with it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# id\taddress\tusable\n"
        + "".join(f"{d.identifier}\t{d.address}\t{'yes' if d.usable else 'no'}\n"
                  for d in drafts),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - a filesystem that refuses the mode
        pass
