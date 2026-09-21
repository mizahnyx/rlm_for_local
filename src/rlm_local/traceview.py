"""Readable traces of a corpus run — the owner's instrument (RO10).

A trajectory is written for machines: a flat event stream of guardrail counters,
message payloads and cell results, ordered by when they happened. This module
turns one into two Markdown documents a human can *assess*: a page per run, and an
index over a directory of runs.

The owner's requirement (2026-09-17) is the design constraint: *"some sort of
readable by me traces … so I can browse and assess how much the process is
suitable to the task, which corpus citations are relevant in some scale."* Two
consequences follow, and both are load-bearing:

* **The page embeds the passage behind every cited and every served address.** A
  band label is the harness's judgement; a passage is the thing judged. Relevance
  cannot be assessed from `covers 1/4 (weak)` alone, so the renderer resolves each
  address through the read-only mount and puts the text on the page.
* **`render_summary` is the form that may travel.** It carries counts, never a
  question, an address or a quote, because the page itself is corpus-derived data:
  a consolidated list of what a private tree contains (AGENTS.md §1.9). The
  rendered directory is therefore refused inside the corpus root and written
  0600 in a 0700 directory outside it.

Neither the page nor the summary is a verdict. An audit is *partial* whenever the
trajectory cannot support it — a run recorded before the served-address
instrumentation, or a trajectory torn by a kill — and it says so rather than
reporting a clean result (AGENTS.md §1.8: a check that cannot see the truth says
"unknown").
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from rlm_kernel.mounts import assert_derived_outside_corpus
from rlm_kernel.textindex import ADDRESS_TOKEN_RE

#: A hit's bands, strongest first. `strong`/`partial` are the two a citation may
#: rest on; `weak`/`none` are the two the submission guard refuses when they are
#: an answer's *entire* evidence (RO4, 2026-09-17).
BAND_ORDER = ("strong", "partial", "weak", "none")
ANSWERING_BANDS = ("strong", "partial")

#: How much of a passage the page shows. Judging relevance needs the passage's
#: opening, not the whole file — and at this corpus's scale a page that embedded
#: whole files would be unreadable.
DEFAULT_PASSAGE_CHARS = 1200

#: The question is repeated in the index, shortened. The index is local-only by
#: construction (see the module docstring), so this is readability, not privacy.
QUESTION_IN_INDEX_CHARS = 90

#: User-role messages the root loop appends that are *not* harness interventions:
#: the turn header, and the echo of a cell's own output (which the page already
#: shows as a cell). Everything else a run was told is a nudge, and a nudge is the
#: most interesting thing on the page.
_TURN_HEADER_PREFIX = "Turn "
_REPL_ECHO_PREFIX = "REPL output:"


# ── The parsed run ─────────────────────────────────────────────────────────

@dataclass
class ServedAddress:
    """One address a helper handed the model, with the band and alias it came with.

    `alias` is the mnemonic the model was given for this passage (RO13) — the handle it
    was asked to cite instead of the address. It is on the page so a reader can see the
    mapping the run actually used, which is also what makes an alias in a cited answer
    legible without another lookup.
    """

    address: str
    band: str | None = None
    alias: str | None = None


@dataclass
class ServedCall:
    """One corpus helper call, as the parent served it."""

    turn: int
    verb: str
    query: str = ""
    addresses: list[ServedAddress] = field(default_factory=list)
    chars: int = 0
    ok: bool = True


@dataclass
class Turn:
    """One turn: what the model said, ran, and was told."""

    index: int
    max_turns: int | None = None
    model_said: str = ""
    cells: list[dict[str, Any]] = field(default_factory=list)
    guardrails: list[tuple[str, str]] = field(default_factory=list)
    nudges: list[str] = field(default_factory=list)
    served: list[ServedCall] = field(default_factory=list)


@dataclass
class CitationAudit:
    """What the answer cited, against what the harness served, and its band.

    Four buckets because they mean four different things: an answer resting on an
    answering passage; an answer resting on a passage the harness had already
    labelled as not answering it; a citation nobody served (fabrication); and
    evidence that was handed over and never used — which is the bucket that shows
    a run *leaving relevance on the table*, and the one no counter exposed before.

    A fifth list, `cited_unknown`, exists because the other four cannot always be
    filled honestly: a run recorded before the served-address instrumentation has no
    served set at all, and an empty set is *unknown*, not empty. Filing those
    citations under `cited_unserved` would report a fabrication the trajectory
    cannot support — which is exactly what the first summary pass over the 17
    recorded runs did, and why this bucket exists.
    """

    cited_answering: list[str] = field(default_factory=list)
    cited_non_answering: list[str] = field(default_factory=list)
    cited_unserved: list[str] = field(default_factory=list)
    cited_unknown: list[str] = field(default_factory=list)
    served_not_cited: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether the audit could see the whole run, or is saying so instead.

        Any note at all means the audit is limited: an empty served set, a
        citation with no band, a torn trajectory. A boolean that has to be
        recomputed from the wording of a note is a boolean that will disagree with
        it, which this project has already paid for once.
        """
        return not self.notes


@dataclass
class AddressCorruption:
    """How the addresses an answer cites relate to the ones the harness served.

    Three outcomes, and the middle one is the point: a citation *one edit* from a
    served address is a copy slip that a repairable handle would have saved, whereas
    a citation near nothing is an invention. In the trajectory both are simply "not
    in the served set", which is why the size of the mnemonic prize was unknown until
    this existed (owner's finding, 2026-09-17).
    """

    exact: list[str] = field(default_factory=list)
    one_edit: list[tuple[str, str]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def cited(self) -> int:
        return len(self.exact) + len(self.one_edit) + len(self.unmatched)


def _one_edit_apart(a: str, b: str) -> bool:
    """Whether `a` becomes `b` with one insert, delete, substitution or *swap*.

    Deliberately one edit and not "close": the repair rule the mnemonic design
    proposes accepts a unique candidate within one edit, so counting anything looser
    would credit the harness with a rescue it would not have made. An **adjacent
    transposition counts as one** (Damerau, not plain Levenshtein), because swapping
    two neighbouring characters is the classic slip on a short token — `KQ7` typed as
    `K7Q` — and a repair rule that refused it would fail on the very corruption it
    exists for.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        mismatches = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(mismatches) == 1:
            return True
        if len(mismatches) == 2:
            i, j = mismatches
            return j == i + 1 and a[i] == b[j] and a[j] == b[i]
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


@dataclass
class RunTrace:
    """One trajectory, parsed into the shape a reader needs."""

    path: Path
    query: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    started_at: float | None = None
    elapsed_s: float | None = None
    turns_used: int | None = None
    forced: bool | None = None
    final_answer: str = ""
    turns: list[Turn] = field(default_factory=list)
    guardrails: list[tuple[int, str, str]] = field(default_factory=list)
    served: list[ServedCall] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    # ── Derived facts ─────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return str(self.config.get("root_model") or self.config.get("model") or "")

    @property
    def profile(self) -> str:
        return str(self.config.get("name") or self.config.get("profile") or "")

    @property
    def max_turns(self) -> int | None:
        for turn in self.turns:
            if turn.max_turns is not None:
                return turn.max_turns
        value = self.config.get("max_turns")
        return int(value) if isinstance(value, int) else None

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def first_helper_turn(self) -> int | None:
        """The turn in which this run first asked a corpus helper for anything.

        The owner's finding (2026-09-19), read off the first real question set: *"the
        model wastes turns realising that the search has to be done in the corpus"* —
        measured at 2 to 5 turns of a 5–6 turn budget before the corpus is touched. A
        metric nobody renders is a metric nobody improves, so it is on every summary
        line: `first_helper_turn=4` means the run spent three turns without asking the
        corpus anything.

        `None` means no helper call was recorded at all — which for a corpus run is a
        fact about a forced answer rather than a clean one, because a submission from a
        run that called nothing is refused. A call recorded on turn 0 is treated the
        same way: turn 0 is what the harness writes when it has no turn in hand, so it
        is *unknown* rather than first.
        """
        turns = [call.turn for call in self.served if call.turn > 0]
        return min(turns) if turns else None

    def guardrail_count(self, name: str) -> int:
        return sum(1 for _, guardrail, _ in self.guardrails if guardrail == name)

    def cited_addresses(self) -> list[str]:
        """Addresses the final answer cites, in the order they appear."""
        seen: list[str] = []
        for address in ADDRESS_TOKEN_RE.findall(self.final_answer or ""):
            if address not in seen:
                seen.append(address)
        return seen

    def band_by_address(self) -> dict[str, str]:
        """The strongest band each served address was handed over with."""
        bands: dict[str, str] = {}
        for call in self.served:
            for served in call.addresses:
                if served.band is None:
                    continue
                current = bands.get(served.address)
                if current is None or (BAND_ORDER.index(served.band)
                                       < BAND_ORDER.index(current)):
                    bands[served.address] = served.band
        return bands

    def served_addresses(self) -> list[str]:
        seen: list[str] = []
        for call in self.served:
            for served in call.addresses:
                if served.address not in seen:
                    seen.append(served.address)
        return seen

    def bands_served(self) -> dict[str, int]:
        """How many addresses were served at each band (per address, not per hit)."""
        counts: dict[str, int] = {}
        for band in self.band_by_address().values():
            counts[band] = counts.get(band, 0) + 1
        return counts

    def searches(self) -> list[ServedCall]:
        return [call for call in self.served if call.verb == "corpus_search"]

    def address_corruption(self) -> AddressCorruption:
        """Bucket each cited address by how far it is from what was served.

        Needs a served set, so a run recorded before the instrumentation returns
        every citation as unmatched — and the caller can tell, because the audit
        says the served set is unknown. The two are kept separate on purpose: an
        unverifiable run must not be read as a model that invents addresses.
        """
        served = self.served_addresses()
        result = AddressCorruption()
        for address in self.cited_addresses():
            if address in served:
                result.exact.append(address)
                continue
            near = [candidate for candidate in served
                    if _one_edit_apart(address, candidate)]
            if len(near) == 1:
                result.one_edit.append((address, near[0]))
            elif len(near) > 1:
                # More than one candidate is not a repairable slip; a repair would
                # have had to refuse here too, so it is counted as unmatched.
                result.unmatched.append(address)
            else:
                result.unmatched.append(address)
        return result

    def audit(self) -> CitationAudit:
        """The citation audit, with its own honesty about what it could not see."""
        bands = self.band_by_address()
        audit = CitationAudit()
        served = self.served_addresses()
        cited = self.cited_addresses()

        if not self.served:
            # An empty served set is *unknown*, not empty. Filing these citations
            # as unserved would report a fabrication the trajectory cannot support.
            audit.cited_unknown = list(cited)
            detail = (f"{len(cited)} cited address(es) " if cited else "nothing ")
            audit.notes.append(
                f"no served-address events in this trajectory, so {detail}could "
                "be checked against what a helper served: this run predates the "
                "corpus_served instrumentation, and its audit is partial rather "
                "than clean"
            )
            return audit

        for address in cited:
            if address not in served:
                audit.cited_unserved.append(address)
            elif bands.get(address) in ANSWERING_BANDS:
                audit.cited_answering.append(address)
            else:
                # A served address with no band is not evidence of a weak match;
                # it is evidence of *no judgement*, which is a different claim.
                audit.cited_non_answering.append(address)
        audit.served_not_cited = [a for a in served if a not in cited]

        if any(bands.get(a) is None for a in cited if a in served):
            audit.notes.append(
                "some cited addresses were served without a band (a corpus_read, "
                "or a question with no content words), so those citations are "
                "unknown rather than weak"
            )
        return audit


# ── Reading ────────────────────────────────────────────────────────────────

def collect_trajectories(path: str | Path) -> list[Path]:
    """Every trajectory at `path`: the file itself, or the `*.jsonl` in a directory."""
    path = Path(path)
    if path.is_dir():
        return sorted(path.glob("*.jsonl"))
    return [path]


def read_trajectory(path: str | Path) -> RunTrace:
    """Parse one trajectory.

    A torn final line is *reported*, not raised: the logger flushes per event, so a
    run killed mid-write leaves half a JSON object, and a viewer that crashed on
    exactly the run worth looking at would be useless when it is needed.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")

    run = RunTrace(path=path)
    torn = 0
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            torn += 1
            continue
        if not isinstance(event, dict):
            torn += 1
            continue
        _absorb(run, event)

    if torn:
        run.truncated = True
        run.notes.append(
            f"{torn} unreadable line(s) at the end of this trajectory — most "
            "likely a run that was interrupted mid-write: this is a truncated "
            "trajectory, so the last part of the run is missing from this page"
        )
    if run.elapsed_s is None:
        run.notes.append(
            "no `end` event in this trajectory: the run did not finish, so its "
            "outcome and elapsed time are unknown rather than zero"
        )
    return run


def _turn(run: RunTrace, index: int) -> Turn:
    for turn in run.turns:
        if turn.index == index:
            return turn
    turn = Turn(index=index)
    run.turns.append(turn)
    run.turns.sort(key=lambda t: t.index)
    return turn


def _absorb(run: RunTrace, event: dict[str, Any]) -> None:
    kind = event.get("event")

    if kind == "start":
        run.query = str(event.get("query") or "")
        run.config = dict(event.get("config") or {})
        run.started_at = event.get("timestamp")
    elif kind == "turn_start":
        turn = _turn(run, int(event.get("turn") or 0))
        turn.max_turns = event.get("max_turns")
    elif kind == "root_message":
        role = event.get("role")
        content = str(event.get("content") or "")
        turn = _turn(run, int(event.get("turn") or 0))
        if role == "assistant":
            turn.model_said += content
        elif not content.startswith((_TURN_HEADER_PREFIX, _REPL_ECHO_PREFIX)):
            turn.nudges.append(content)
    elif kind == "repl_result":
        turn = _turn(run, int(event.get("turn") or 0))
        turn.cells.append({
            "stdout": str(event.get("stdout") or ""),
            "stderr": str(event.get("stderr") or ""),
            "final_answer": event.get("final_answer"),
        })
    elif kind == "guardrail":
        name = str(event.get("guardrail") or "")
        detail = str(event.get("detail") or "")
        turn_number = int(event.get("turn") or 0)
        _turn(run, turn_number).guardrails.append((name, detail))
        run.guardrails.append((turn_number, name, detail))
    elif kind == "corpus_served":
        call = ServedCall(
            turn=int(event.get("turn") or 0),
            verb=str(event.get("verb") or ""),
            query=str(event.get("query") or ""),
            chars=int(event.get("chars") or 0),
            ok=bool(event.get("ok", True)),
            addresses=[
                ServedAddress(address=str(entry.get("address") or ""),
                              band=entry.get("band"),
                              alias=entry.get("alias"))
                for entry in (event.get("addresses") or [])
                if isinstance(entry, dict) and entry.get("address")
            ],
        )
        run.served.append(call)
        _turn(run, call.turn).served.append(call)
    elif kind == "end":
        run.elapsed_s = event.get("elapsed_s")
        run.turns_used = event.get("turns_used")
        run.forced = event.get("forced")
        run.final_answer = str(event.get("final_answer") or "")


# ── Markdown rendering ─────────────────────────────────────────────────────

def _fence(text: str, info: str = "") -> str:
    """A fenced block whose fence is longer than any run of backticks inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{info}\n{text}\n{ticks}"


def _cell(text: str) -> str:
    """One table cell: no newline, no stray pipe."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _stamp(seconds: float | None) -> str:
    if not seconds:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(seconds)))


def _outcome(run: RunTrace) -> str:
    if run.forced is None:
        return "**unknown** — no `end` event (the run did not finish)"
    if run.forced:
        return (f"**forced finalization** after {run.turns_used} turn(s) — the "
                "model never submitted, so the harness asked for the terminal "
                "answer")
    return f"**voluntary submission** at turn {run.turns_used}"


def render_run_markdown(
    run: RunTrace,
    passages: dict[str, str] | None = None,
    *,
    max_passage: int = DEFAULT_PASSAGE_CHARS,
) -> str:
    """The page for one run: header, counts, citation audit, turn by turn."""
    passages = passages or {}
    audit = run.audit()
    corruption = run.address_corruption()
    bands = run.bands_served()

    lines: list[str] = []
    lines.append(f"# Trace — {run.path.name}")
    lines.append("")
    lines.append(f"**Question:** {run.query or '(not recorded)'}")
    lines.append("")
    lines.append(
        f"**Model:** {run.model or '(not recorded)'} · "
        f"**Profile:** {run.profile or '(not recorded)'} · "
        f"**Max turns:** {run.max_turns if run.max_turns is not None else '?'}"
    )
    lines.append("")
    lines.append(f"**Outcome:** {_outcome(run)}")
    lines.append("")
    elapsed = f"{run.elapsed_s:.1f} s" if isinstance(run.elapsed_s, (int, float)) else "unknown"
    lines.append(f"**Started:** {_stamp(run.started_at)} · **Elapsed:** {elapsed}")
    lines.append("")
    if run.notes:
        lines.append("> **Read this first**")
        lines.extend(f"> - {note}" for note in run.notes)
        lines.append("")

    # ── Counts ────────────────────────────────────────────────────────────
    lines.append("## Counts")
    lines.append("")
    lines.append("| fact | value |")
    lines.append("|---|---|")
    lines.append(f"| turns used | {run.turns_used if run.turns_used is not None else 'unknown'} |")
    lines.append(f"| answers recorded | {run.guardrail_count('corpus_citation')} |")
    lines.append(f"| refusals: nothing checkable | {run.guardrail_count('corpus_uncited')} |")
    lines.append(f"| refusals: weak/none citation | {run.guardrail_count('corpus_weak_citation')} |")
    lines.append(f"| searches served | {len(run.searches())} |")
    lines.append(f"| helper calls served | {len(run.served)} |")
    lines.append(f"| model errors | {run.guardrail_count('model_error')} |")
    lines.append("| bands served (per address) | "
                 + (", ".join(f"{name}={count}" for name, count in sorted(bands.items()))
                    or "nothing labelled") + " |")
    lines.append("")

    # ── Citation audit ────────────────────────────────────────────────────
    lines.append("## Citation audit")
    lines.append("")
    lines.append(
        "Every address the final answer cites, against what the harness served and "
        "the band it served it with — so relevance can be read off the passage "
        "rather than inferred from the label."
    )
    lines.append("")
    for heading, addresses, explain in (
        ("Cited, and the passage answers the question",
         audit.cited_answering,
         "the strongest evidence class: the search said `strong`/`partial` and the "
         "answer used it"),
        ("Cited, but the passage does not answer it",
         audit.cited_non_answering,
         "the search had already labelled this `weak`/`none` (or gave it no band): "
         "this is the failure the absence-band rule refuses"),
        ("Cited, but no helper ever served it",
         audit.cited_unserved,
         "a citation pointing at nothing — a fabrication, and refused outright"),
        ("Cited with one character out (a repairable slip)",
         [f"`{got}` — one edit from `{near}`" for got, near in corruption.one_edit],
         "what a repairable handle would have rescued: the model meant a passage it "
         "had been served, and today this reads as an invention"),
        ("Cited, but whether a helper served it is unknown",
         audit.cited_unknown,
         "this run predates the served-address instrumentation, so its citations "
         "cannot be checked either way: `unknown` is not a finding of fabrication"),
        ("Served, and never cited",
         audit.served_not_cited,
         "evidence the run was handed and did not use: the clearest signal of a run "
         "leaving relevance on the table"),
    ):
        lines.append(f"### {heading} ({len(addresses)})")
        lines.append("")
        if not addresses:
            lines.append("_none_")
            lines.append("")
            continue
        lines.append(f"_{explain}_")
        lines.append("")
        for address in addresses:
            lines.extend(_address_block(run, address, passages, max_passage))
        lines.append("")
    if audit.notes:
        lines.append("**Audit limits**")
        lines.append("")
        lines.extend(f"- {note}" for note in audit.notes)
        lines.append("")

    # ── Turn by turn ──────────────────────────────────────────────────────
    lines.append("## Turn by turn")
    lines.append("")
    for turn in run.turns:
        header = f"### Turn {turn.index}"
        if turn.max_turns:
            header += f"/{turn.max_turns}"
        lines.append(header)
        lines.append("")
        if turn.guardrails:
            lines.append("**Guardrails**")
            lines.append("")
            lines.extend(f"- `{name}` — {detail}" for name, detail in turn.guardrails)
            lines.append("")
        if turn.model_said:
            lines.append("**Model said**")
            lines.append("")
            lines.append(_fence(turn.model_said))
            lines.append("")
        for number, cell in enumerate(turn.cells, start=1):
            declared = cell.get("final_answer")
            lines.append(f"**Cell {number}**"
                         + ("" if declared is None else " (declared a submission)"))
            lines.append("")
            if cell.get("stdout"):
                lines.append(_fence(str(cell["stdout"]), "text"))
                lines.append("")
            if cell.get("stderr"):
                lines.append("stderr:")
                lines.append("")
                lines.append(_fence(str(cell["stderr"]), "text"))
                lines.append("")
            if not cell.get("stdout") and not cell.get("stderr"):
                lines.append("_(no output)_")
                lines.append("")
        if turn.served:
            lines.append("**Served to the model**")
            lines.append("")
            lines.append("| verb | query | alias | address | band |")
            lines.append("|---|---|---|---|---|")
            for call in turn.served:
                if not call.addresses:
                    lines.append(f"| `{call.verb}` | {_cell(call.query)} | | "
                                 f"_(nothing served; ok={call.ok})_ | |")
                    continue
                for served in call.addresses:
                    band = f"**{served.band}**" if served.band else "_(no band)_"
                    alias = f"`{served.alias}`" if served.alias else "—"
                    lines.append(f"| `{call.verb}` | {_cell(call.query)} | {alias} | "
                                 f"`{served.address}` | {band} |")
            lines.append("")
        for nudge in turn.nudges:
            lines.append("**The harness interrupted**")
            lines.append("")
            lines.append(_fence(nudge, "text"))
            lines.append("")
        lines.append("---")
        lines.append("")

    # ── The answer ────────────────────────────────────────────────────────
    lines.append("## The final answer")
    lines.append("")
    lines.append(_fence(run.final_answer or "(no answer in this trajectory)", "text"))
    lines.append("")
    return "\n".join(lines)


def _address_block(run: RunTrace, address: str,
                   passages: dict[str, str],
                   max_passage: int) -> list[str]:
    """One audited address: its alias, band, who served it, and the passage itself."""
    bands = run.band_by_address()
    band = bands.get(address)
    servers = [call for call in run.served
               if any(s.address == address for s in call.addresses)]
    if servers:
        who = "; ".join(f"`{call.verb}` (turn {call.turn}, query `{call.query}`)"
                        for call in servers)
    else:
        who = "no helper"
    label = f"**{band}**" if band else "no band (unknown)"
    # RO13: the alias beside the address, so a reader can connect the handle the model
    # wrote to the passage it means without re-deriving the mapping from the events.
    alias = next((s.alias for call in servers for s in call.addresses
                  if s.address == address and s.alias), None)
    handle = f" (alias `{alias}`)" if alias else ""
    out = [f"- `{address}`{handle} — {label}, served by {who}"]
    passage = passages.get(address)
    if passage:
        if len(passage) > max_passage:
            passage = passage[:max_passage] + "\n…[truncated for this page]"
        out.append("")
        out.extend(f"  > {line}" if line else "  >" for line in passage.split("\n"))
        out.append("")
    elif passages:
        out.append("")
        out.append("  > _(passage not resolved for this page)_")
        out.append("")
    return out


def render_summary(run: RunTrace) -> str:
    """One line of counts — the only output of this module that may travel.

    No question, no address, no quote: it is the form an operator can paste into a
    session or a commit message while the page stays beside the corpus
    (AGENTS.md §1.9).
    """
    audit = run.audit()
    corruption = run.address_corruption()
    bands = ", ".join(f"{name}={count}" for name, count in sorted(run.bands_served().items()))
    elapsed = (f"{run.elapsed_s:.1f}s" if isinstance(run.elapsed_s, (int, float))
               else "unknown")
    return (
        f"{run.path.name}: turns={run.turns_used if run.turns_used is not None else '?'}"
        f"/{run.max_turns if run.max_turns is not None else '?'}"
        f" forced={run.forced} elapsed={elapsed}"
        f" first_helper_turn="
        f"{run.first_helper_turn if run.first_helper_turn is not None else 'none'}"
        f" answers={run.guardrail_count('corpus_citation')}"
        f" cited_answering={len(audit.cited_answering)}"
        f" cited_non_answering={len(audit.cited_non_answering)}"
        f" cited_unserved={len(audit.cited_unserved)}"
        f" cited_unknown={len(audit.cited_unknown)}"
        f" cited_exact={len(corruption.exact)}"
        f" cited_one_edit={len(corruption.one_edit)}"
        f" cited_unmatched={len(corruption.unmatched)}"
        f" served_not_cited={len(audit.served_not_cited)}"
        f" refusals_uncited={run.guardrail_count('corpus_uncited')}"
        f" refusals_weak={run.guardrail_count('corpus_weak_citation')}"
        f" searches={len(run.searches())}"
        f" bands={bands or 'none'}"
        f" audit={'complete' if audit.complete else 'partial'}"
    )


def render_index_markdown(runs: Iterable[tuple[RunTrace, str]]) -> str:
    """The index over a directory of rendered runs.

    `runs` is `(run, page filename)` pairs, in the order they were rendered.
    """
    runs = list(runs)
    lines = [
        "# Corpus run traces",
        "",
        f"{len(runs)} run(s). One page per run; each page carries the question, the "
        "turn-by-turn transcript, the citation audit, and the passage behind every "
        "cited or served address.",
        "",
        "| started | question | model | turns | outcome | answers | refusals | searches | bands | page |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run, page in runs:
        question = run.query or "(not recorded)"
        if len(question) > QUESTION_IN_INDEX_CHARS:
            question = question[:QUESTION_IN_INDEX_CHARS - 1] + "…"
        outcome = ("unknown" if run.forced is None
                   else ("forced" if run.forced else "voluntary"))
        refusals = (run.guardrail_count("corpus_uncited")
                    + run.guardrail_count("corpus_weak_citation"))
        bands = ", ".join(f"{name}={count}"
                          for name, count in sorted(run.bands_served().items())) or "—"
        lines.append(
            f"| {_stamp(run.started_at)} | {_cell(question)} | {_cell(run.model or '—')} "
            f"| {run.turns_used if run.turns_used is not None else '?'}"
            f"/{run.max_turns if run.max_turns is not None else '?'}"
            f" | {outcome} | {run.guardrail_count('corpus_citation')} | {refusals} "
            f"| {len(run.searches())} | {_cell(bands)} | [{page}]({page}) |"
        )
    lines.append("")
    lines.append(
        "Generated by `rlm trace render`. **This directory contains corpus text** "
        "(the passages behind cited and served addresses): it is derived data and "
        "belongs beside the corpus, not in a session, a commit or a repository "
        "(AGENTS.md §1.9)."
    )
    lines.append("")
    return "\n".join(lines)


# ── Passages and the directory ─────────────────────────────────────────────

def collect_passages(run: RunTrace, bridge: Any,
                     *, max_bytes: int = DEFAULT_PASSAGE_CHARS) -> dict[str, str]:
    """Resolve every cited and served address to its passage through the mount.

    Read-only by construction: the bridge has no write verb, and a failure to
    resolve is recorded *as text on the page* rather than raised, because a page
    that says "this address could not be re-read" is still a usable page.

    **A container member is not read.** `arch.zip!member.txt` names no file on
    disk, so the exact path bytes cannot be resolved and the text index falls back
    to a filter on an unindexed column — measured at *over 150 s for one address*
    on the complete index (2026-09-17), the same scan RO11 fixed for file-backed
    addresses. Rendering must not be able to hang for hours on one address, so
    those are marked on the page instead, with the reason. The alternative —
    reading them and hoping — is a viewer that stops working the moment someone
    cites a passage inside an archive.
    """
    wanted = list(run.cited_addresses())
    for address in run.served_addresses():
        if address not in wanted:
            wanted.append(address)
    passages: dict[str, str] = {}
    for address in wanted:
        display = address.rsplit("#L", 1)[0]
        resolvable = None
        try:
            resolvable = bridge.index.raw_for(display) if bridge.index else None
        except Exception:  # pragma: no cover - defensive
            resolvable = None
        if resolvable is None:
            passages[address] = (
                "(passage not embedded: this address names no file on disk — a "
                "container member or a derivation — so its lookup has no index and "
                "reading it would scan the chunk table; see roadmap RO11)"
            )
            continue
        try:
            passages[address] = str(bridge.handle_read(address, max_bytes=max_bytes))
        except Exception as e:  # pragma: no cover - defensive
            passages[address] = f"(could not re-read this address: {type(e).__name__}: {e})"
    return passages


def _page_name(run: RunTrace) -> str:
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(run.started_at)) \
        if run.started_at else "00000000-0000"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", run.stem).strip("-") or "run"
    return f"{stamp}-{safe}.md"


def render_traces(
    paths: Iterable[str | Path],
    out_dir: str | Path,
    *,
    corpus_root: str | Path | None = None,
    passages: dict[str, dict[str, str]] | None = None,
    max_passage: int = DEFAULT_PASSAGE_CHARS,
) -> list[Path]:
    """Render one Markdown page per trajectory plus `index.md`.

    `passages` maps a trajectory path (as a string) to its address→passage map;
    `collect_passages` builds one from a corpus bridge. The pages contain corpus
    text and are written 0600 inside a 0700 directory, which is refused outright
    when it sits inside the corpus root (AGENTS.md §1.8, layer 3).
    """
    out_dir = Path(out_dir)
    if corpus_root is not None:
        assert_derived_outside_corpus(corpus_root, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _lock_down(out_dir, 0o700)

    rendered: list[tuple[RunTrace, str]] = []
    written: list[Path] = []
    for path in paths:
        run = read_trajectory(path)
        page = _page_name(run)
        run_passages = (passages or {}).get(str(path)) or (passages or {}).get(run.path.name) or {}
        target = out_dir / page
        target.write_text(
            render_run_markdown(run, run_passages, max_passage=max_passage),
            encoding="utf-8",
        )
        _lock_down(target, 0o600)
        rendered.append((run, page))
        written.append(target)

    index = out_dir / "index.md"
    index.write_text(render_index_markdown(rendered), encoding="utf-8")
    _lock_down(index, 0o600)
    written.append(index)
    return written


def _lock_down(path: Path, mode: int) -> None:
    """Best effort: the page holds corpus text, so it is owner-only."""
    try:
        os.chmod(path, mode)
    except OSError:  # pragma: no cover - filesystem-dependent
        pass
