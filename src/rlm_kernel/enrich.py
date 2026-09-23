"""Selective enrichment: which documents are worth a generation pass (RO6).

RO6's arithmetic, measured (`docs/20260923-1700-ro6-the-value-set-measured.md`): a 500-token
summary of a 10 KB document is ~5–10 minutes on this host, so summarising the ~2.88M text files is
1–1.9 **years**, while the set retrieval has ever reached for is 180 *addresses* and the set an
answer has ever *cited* is 13. Selective enrichment is therefore affordable today, and the
binding constraint is having a value signal rather than a faster model — this module is that
signal, taken from what the harness already records instead of from a heuristic.

Two properties are deliberate:

* **Usage is evidence, not a guess.** A document that an answer cited is worth more than one that
  a search merely served, and both are counted rather than inferred from size or type.
* **It counts documents, not addresses.** One file served at three byte ranges is one document;
  the first measurement counted addresses and so overstated the set. A plan of *documents* is what
  a generation pass consumes.
"""

from __future__ import annotations

import collections
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from rlm_kernel.textindex import provenance_class

#: An address is `path#L<start>-<end>`; the document is everything before the fragment.
_FRAGMENT = re.compile(r"#L\d+-\d+$")
#: Addresses found inside *text* (a citation in an answer). Deliberately space-free: the first
#: version allowed spaces and so let the prose before an address become part of the path — it
#: turned "The answer is in docs/one.txt#L0-99" into a candidate named
#: "The answer is in docs/one.txt", which is a document that does not exist, and a wrong
#: candidate is worse than a missing one. The cost is that a citation to a path *containing a
#: space* is not recognised here; that under-ranks such a document rather than inventing one, and
#: the document is still in the plan if a search ever served it — the served set comes from
#: structured events, where spaces are exact.
_ANY_ADDRESS = re.compile(r"[^\s\"'\[\],()]+#L\d+-\d+")

#: Minutes of inference one summary costs on this host — the measured range RO6 is built on.
MINUTES_PER_SUMMARY = (5.0, 10.0)


@dataclass(frozen=True)
class Candidate:
    """One document, with the evidence for enriching it."""

    source: str
    provenance: str
    served: int
    cited: int

    @property
    def rank_key(self) -> tuple[int, int, str]:
        """Cited first, then served, then the path — deterministic at every level.

        A cited document outranks a merely-served one *however often* the latter was served: a
        citation is a person's question having been answered, and a serve is a search having been
        run. The path breaks ties so the order cannot drift between runs, which is what makes a
        plan reproducible.
        """
        return (-self.cited, -self.served, self.source)


def read_usage(paths: Iterable[Path]) -> tuple[collections.Counter, collections.Counter]:
    """The served and cited *documents* recorded in a set of trajectories.

    Served addresses come from the `corpus_served` events (the parent records every helper
    result); cited ones from the citation event's detail and from the final answer text. Both are
    reduced to documents here, so a file reached at several byte ranges is one candidate.
    """
    served: collections.Counter[str] = collections.Counter()
    cited: collections.Counter[str] = collections.Counter()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if '"corpus_served"' not in text and "#L" not in text:
            # Nothing here recorded usage. The old guard also required either a `corpus_served`
            # event or the literal `Citations:` — so an answer that cited an address inline,
            # without that word, made the whole trajectory invisible to this reader. The test
            # for prose-before-an-address caught it by producing exactly that file.
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = str(row.get("event") or "")
            if event == "corpus_served":
                for entry in row.get("addresses") or []:
                    _count(served, str(entry.get("address") or ""))
            elif event == "guardrail" and str(row.get("guardrail")) == "corpus_citation":
                for found in _ANY_ADDRESS.findall(str(row.get("detail") or "")):
                    _count(cited, found)
            elif event in ("end", "answer"):
                for found in _ANY_ADDRESS.findall(str(row.get("final_answer") or "")):
                    _count(cited, found)
    return served, cited


def _count(counter: collections.Counter, address: str) -> None:
    document = _FRAGMENT.sub("", address.strip())
    if document:
        counter[document] += 1


def rank_candidates(
    served: collections.Counter, cited: collections.Counter,
    *, only: Iterable[str] | None = None,
) -> list[Candidate]:
    """Every document the recorded usage reached, best first.

    `only` restricts the set — `{"cited"}` for the thirteen that answered a question,
    `{"served", "cited"}` for everything retrieval has touched. The default is everything,
    because the plan is a ranking and the caller decides how far down it to spend.
    """
    wanted = set(only) if only is not None else {"served", "cited"}
    documents = set()
    if "served" in wanted:
        documents |= set(served)
    if "cited" in wanted:
        documents |= set(cited)
    return sorted(
        (Candidate(source=doc, provenance=provenance_class(doc),
                   served=int(served.get(doc, 0)), cited=int(cited.get(doc, 0)))
         for doc in documents),
        key=lambda candidate: candidate.rank_key,
    )


def render_plan(candidates: list[Candidate]) -> str:
    """The plan as aggregate lines — counts, classes and the arithmetic, never a path."""
    if not candidates:
        return "no candidates: nothing has been served or cited yet"
    by_class: collections.Counter[str] = collections.Counter(c.provenance for c in candidates)
    cited = sum(1 for c in candidates if c.cited)
    low, high = MINUTES_PER_SUMMARY
    lines = [
        f"{len(candidates)} document(s) to consider; {cited} of them cited by an answer",
        f"  by class: {dict(by_class.most_common())}",
        f"  cost at {low:g}-{high:g} min each: {len(candidates) * low / 60:.1f}-"
        f"{len(candidates) * high / 60:.1f} hours of inference",
    ]
    if cited:
        lines.append(f"  the cited set alone: {cited} document(s), "
                     f"{cited * low / 60:.1f}-{cited * high / 60:.1f} hours")
    return "\n".join(lines)


def write_plan(candidates: list[Candidate], path: Path) -> None:
    """Write the ranked plan beside the corpus, 0600 — it names corpus paths.

    Local-only by construction: the plan is a list of the owner's files, so it lives where the
    corpus lives and never travels (`AGENTS.md` §1.9). What may travel is `render_plan`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# rank\tsource\tserved\tcited\tprovenance"]
    for position, candidate in enumerate(candidates, 1):
        lines.append(f"{position}\t{candidate.source}\t{candidate.served}\t{candidate.cited}\t"
                     f"{candidate.provenance}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - a filesystem that refuses the mode
        pass


def iter_trajectories(root: Path) -> Iterator[Path]:
    """Every trajectory under `root`, skipping rendered pages."""
    for path in sorted(Path(root).rglob("*.jsonl")):
        if "traces" not in path.parts:
            yield path


def _as_count(value: str) -> int:
    """A plan's count field, or 0 when it is not a number.

    A malformed count must not raise inside a run that is about to spend hours of inference: the
    row keeps its document and loses only its evidence, and the caller is told how many rows were
    dropped in total by `read_plan_with_drops`.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_plan_with_drops(path: Path) -> tuple[list[Candidate], int]:
    """Read a plan written by `write_plan`; return the candidates and how many rows were dropped.

    Header and blank lines are not rows. A row without all five tab-separated fields is dropped
    and **counted** rather than guessed at, because a plan is the list of documents a generation
    pass will spend time on: a silently shortened list is a plan nobody can audit.
    """
    candidates: list[Candidate] = []
    dropped = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            dropped += 1
            continue
        _rank, source, served, cited, provenance = fields[:5]
        if not source:
            dropped += 1
            continue
        candidates.append(
            Candidate(
                source=source,
                provenance=provenance or provenance_class(source),
                served=_as_count(served),
                cited=_as_count(cited),
            )
        )
    return candidates, dropped


def read_plan(path: Path) -> list[Candidate]:
    """The candidates in a plan, ignoring how many rows were dropped — see
    `read_plan_with_drops` when the difference matters, which it does for a run that spends
    inference."""
    return read_plan_with_drops(path)[0]


def select_documents(
    candidates: Iterable[Candidate], *, limit: int | None = None, cited_only: bool = False,
) -> list[Candidate]:
    """The top of the ranking to spend inference on — the value set, narrowed on request.

    `cited_only` keeps the documents an answer actually cited (13 of them, measured on
    2026-09-23), which is the cheapest defensible set; the default is everything retrieval has
    reached. The ranking is already cited-first, and this **does not re-rank**: the order is the
    evidence, and sorting it again here would quietly disagree with the plan a reader is looking
    at. `limit=None` means all of it, which is a deliberate choice by the caller and never a
    default that spends a night of inference by accident.
    """
    chosen = [candidate for candidate in candidates if candidate.cited] if cited_only \
        else list(candidates)
    if limit is not None:
        chosen = chosen[: max(0, int(limit))]
    return chosen
