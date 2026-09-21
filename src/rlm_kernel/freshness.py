"""The cache freshness ledger: is each derived artefact current? (RO15)

Every derived thing in this system — the path index, the classification, the text index, the
published counts, the archive listings, the extraction cache — is a cache over the corpus.
Until this module, each one was discovered to be behind by the operation that tripped over
it: a slow read, a wrong number, a wasted mining window. The owner's framing is the design:

> *"We will probably need to add some form of 'to do list' of operations that would make all
> the caches actual with respect to the corpus."*

So for each artefact this answers four questions: what it is derived from, whether it is
**current** (not "how old" — whether its inputs changed since it was built), what operation
would make it current, and how much work that is.

Two rules keep it honest, and both come from the project's own history:

* **A cache with no fingerprint says `unknown`, never `current`.** A cache built before
  fingerprints existed, or by a writer that did not record one, cannot be vouched for.
  Grandfathering it as current is the confident default this project ranks below silence
  (`AGENTS.md` §1.8).
* **The ledger never answers by scanning a big table.** A staleness check that counts 29M
  chunks is the CL6 defect wearing a new hat — it would make the *diagnosis* the expensive
  operation. So a check either reads a cheap aggregate (a snapshot's own numbers against the
  live ones) or reports `unknown` with the reason, and the reason names the command that
  would settle it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

#: Where a fingerprint lives in the index's `meta` table. One row per artefact, holding the
#: markers at the moment that artefact was last built.
FINGERPRINT_PREFIX = "freshness:"

#: The three answers a cache can give about its own currency. `unknown` is not a failure
#: state — it is what a cache says when it cannot see the truth.
CURRENT = "current"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class CacheSpec:
    """One derived artefact: what it is for, and what would make it current."""

    name: str
    derived_from: str
    remedy: str
    #: The markers `capture` records and `assess` compares. Named here so the report can
    #: say what it looked at rather than just what it concluded.
    markers: tuple[str, ...]


#: The artefacts the ledger covers, in the order a reader wants them: the caches a *read*
#: depends on first, then the ones that only affect what a count or a listing says.
#:
#: Every marker below is an **indexed count** (`COUNT(*)` on a task/state pair in
#: `mine_queue`, or a value already stored in the coverage snapshot). That is deliberate:
#: a staleness check that counted chunks or grouped over `archive_members` would make the
#: diagnosis the expensive operation, which is the CL6 defect with a new name. Where a
#: cheap check does not exist, the artefact is left out until one does, rather than
#: answered by a scan.
CACHES: tuple[CacheSpec, ...] = (
    CacheSpec(
        name="coverage",
        derived_from="the text index, summarised",
        remedy="rlm corpus counters --refresh, or the end of a mining window",
        markers=("sources_indexed", "chunks"),
    ),
    CacheSpec(
        name="archive_listings",
        derived_from="each container's own directory listing",
        remedy="rlm mine run --task list_archive",
        markers=("listings_done",),
    ),
    CacheSpec(
        name="extraction",
        derived_from="a container's members, extracted on demand",
        remedy="rlm mine run --task extract_text (the container a miss named)",
        markers=("extracted_done",),
    ),
)


@dataclass
class Currency:
    """What the ledger concluded about one cache, and how it can say so."""

    spec: CacheSpec
    state: str
    detail: str = ""
    work: str = ""
    recorded_at: float | None = None
    markers: dict[str, Any] = field(default_factory=dict)

    @property
    def line(self) -> str:
        """One report line: name, state, and the remedy when it is not current."""
        parts = [f"{self.spec.name:18} {self.state}"]
        if self.detail:
            parts.append(self.detail)
        if self.state != CURRENT:
            parts.append(f"— {self.spec.remedy}")
        return "  ".join(parts)


def capture(conn: sqlite3.Connection, name: str, markers: dict[str, Any]) -> None:
    """Record an artefact's input markers at the moment it was built.

    Called by the writer, not by the reader: the whole point is that the fingerprint is
    taken when the cache is known to be current, so that a later read can compare. Best
    effort — a cache that cannot record its markers is a cache that reports `unknown`,
    which is the safe direction.
    """
    payload = {"recorded_at": time.time(), "markers": markers}
    try:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (FINGERPRINT_PREFIX + name, json.dumps(payload)),
        )
        conn.commit()
    except sqlite3.Error:  # pragma: no cover - a meta table that cannot be written
        pass


def recorded(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """The stored fingerprint for an artefact, or None when there is none."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (FINGERPRINT_PREFIX + name,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def _live_coverage(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The published coverage snapshot's own numbers, without recomputing them.

    Reading the snapshot is one row; recomputing it is the ~16-minute scan this project
    already refuses to put on a read path (CL6). So the comparison here is between two
    *stored* summaries, not between a summary and a scan.
    """
    from rlm_kernel.textindex import TextIndex

    try:
        return TextIndex(conn).published_coverage()
    except Exception:  # pragma: no cover - a corpus without the text tables
        return None


def _live_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """The markers that are cheap to read: two indexed counts over `mine_queue`.

    `(task, state)` pairs, each one an index seek rather than a scan — which is what makes
    "is this cache current?" a question a report may ask. A cache whose only honest check
    is expensive is not in `CACHES` yet; see the note there.
    """
    out: dict[str, int] = {}
    for marker, task in (("listings_done", "list_archive"),
                         ("extracted_done", "extract_text")):
        try:
            out[marker] = int(conn.execute(
                "SELECT COUNT(*) FROM mine_queue WHERE task = ? AND state = 'done'",
                (task,)).fetchone()[0])
        except sqlite3.Error:  # pragma: no cover - a corpus without the mining tables
            out[marker] = 0
    return out


def assess(conn: sqlite3.Connection, spec: CacheSpec) -> Currency:
    """Whether one artefact is current, stale, or unknown — and never by scanning."""
    fingerprint = recorded(conn, spec.name)
    if fingerprint is None:
        return Currency(
            spec, UNKNOWN,
            detail=(f"no fingerprint recorded, so nothing vouches for it being current "
                    f"({spec.derived_from})"),
        )

    stored = fingerprint.get("markers") or {}
    recorded_at = fingerprint.get("recorded_at")

    # A fingerprint that does not hold every marker this cache is judged on cannot vouch
    # for the ones it lacks. Checked once, for every artefact, so a marker added to a spec
    # without a capture to match it reports unknown rather than passing by default.
    missing = [k for k in spec.markers if k not in stored]
    if missing:
        return Currency(spec, UNKNOWN,
                        detail=f"the fingerprint does not hold {', '.join(missing)}",
                        recorded_at=recorded_at, markers=stored)

    if spec.name == "coverage":
        live = _live_coverage(conn)
        if live is None:
            return Currency(spec, UNKNOWN,
                            detail="no coverage snapshot has been published",
                            recorded_at=recorded_at)
        # Two stored summaries compared: the fingerprint taken when the snapshot was
        # published, against the snapshot itself. Neither costs a scan.
        drifted = [k for k in spec.markers
                   if k in live and live.get(k) != stored.get(k)]
        if drifted:
            delta = ", ".join(f"{k} {stored.get(k)}→{live.get(k)}" for k in drifted)
            return Currency(spec, STALE, detail=f"the text index moved: {delta}",
                            work=delta, recorded_at=recorded_at, markers=stored)
        return Currency(spec, CURRENT, detail=f"as of {_ago(recorded_at)}",
                        recorded_at=recorded_at, markers=stored)

    if spec.name == "archive_listings":
        # The remedy this cache's staleness implies is *mining*, so its marker moves when
        # mining moves: a listing completed since the fingerprint was taken is work this
        # snapshot no longer describes.
        live = _live_counts(conn)
        if stored.get("listings_done") != live.get("listings_done"):
            delta = f"listings_done {stored.get('listings_done')}→{live.get('listings_done')}"
            return Currency(spec, STALE, detail=f"mining moved: {delta}", work=delta,
                            recorded_at=recorded_at, markers=stored)
        return Currency(spec, CURRENT, detail=f"as of {_ago(recorded_at)}",
                        recorded_at=recorded_at, markers=stored)

    if spec.name == "extraction":
        live = _live_counts(conn)
        if stored.get("extracted_done") != live.get("extracted_done"):
            delta = f"extracted_done {stored.get('extracted_done')}→{live.get('extracted_done')}"
            return Currency(spec, STALE, detail=f"mining moved: {delta}", work=delta,
                            recorded_at=recorded_at, markers=stored)
        return Currency(spec, CURRENT, detail=f"as of {_ago(recorded_at)}",
                        recorded_at=recorded_at, markers=stored)

    # An artefact with a spec but no branch here would silently report current, which is
    # the one answer this module must not invent.
    return Currency(spec, UNKNOWN,
                    detail=f"no staleness check is implemented for {spec.name!r}",
                    recorded_at=recorded_at, markers=stored)


def _ago(when: float | None) -> str:
    if not when:
        return "an unknown time"
    seconds = max(0.0, time.time() - float(when))
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def report(conn: sqlite3.Connection, *, caches: tuple[CacheSpec, ...] = CACHES) -> list[Currency]:
    """The ledger: one assessment per artefact, in reading order."""
    return [assess(conn, spec) for spec in caches]


def render(currencies: list[Currency]) -> str:
    """The report as text. Aggregates and states only: no path, no passage."""
    lines = ["cache freshness (derived state against the corpus)"]
    for currency in currencies:
        lines.append("  " + currency.line)
    stale = [c for c in currencies if c.state == STALE]
    unknown = [c for c in currencies if c.state == UNKNOWN]
    if stale:
        lines.append("")
        lines.append("  work that would make the stale caches current:")
        for currency in stale:
            lines.append(f"    {currency.spec.name}: {currency.spec.remedy}")
    if unknown:
        lines.append("")
        lines.append(f"  {len(unknown)} cache(s) report `unknown`, which is not `current`: "
                     "no fingerprint vouches for them.")
    return "\n".join(lines)
