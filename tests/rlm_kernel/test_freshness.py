"""The cache freshness ledger: current, stale, or unknown — and never by scanning (RO15).

Two properties carry this module, and both are responses to incidents this project has
already had:

* **A cache with no fingerprint is `unknown`, never `current`.** Grandfathering an
  unvouched-for cache as current is the confident default `AGENTS.md` §1.8 ranks below
  silence, and it is how a stale index came to answer "no matches" as though it were
  absence.
* **The diagnosis must not be the expensive operation.** A staleness check that counted
  29 015 791 chunks would be the CL6 defect wearing a new hat. The trace-based tests below
  assert which statements the ledger issues, so a future marker cannot quietly become a
  scan.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex
from rlm_kernel.freshness import (
    CACHES,
    CURRENT,
    FINGERPRINT_PREFIX,
    STALE,
    UNKNOWN,
    CacheSpec,
    assess,
    capture,
    recorded,
    render,
    report,
)
from rlm_kernel.mounts import LocalTreeMount


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """An index with the tables the ledger reads, and nothing else."""
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "notes.txt").write_text("some words here\n", encoding="utf-8")
    derived = tmp_path / "derived"
    derived.mkdir()
    idx = CorpusIndex.open_for(root, derived / "corpus.sqlite")
    idx.build(LocalTreeMount(root))
    idx.classifications().ensure()
    classify_entries(LocalTreeMount(root), idx.classifications())
    idx.text().ensure()
    idx.mining().ensure()
    yield idx._conn  # noqa: SLF001 - the ledger reads this connection
    idx.close()


@pytest.fixture
def spec() -> CacheSpec:
    return CacheSpec(name="archive_listings",
                     derived_from="each container's own directory listing",
                     remedy="rlm mine run --task list_archive",
                     markers=("listings_done",))


def _finish_a_listing(conn: sqlite3.Connection, name: bytes = b"container.zip") -> None:
    """Move the cheap marker the way a mining window would.

    Written through the store's own schema rather than by hand from memory: the queue has
    NOT NULL columns (`priority`, `attempts`, `updated_at`) that a hand-built INSERT gets
    wrong the moment the schema changes. `enqueue` is a no-op for a row that already
    exists, so "another listing finished" needs a different container.
    """
    from rlm_kernel.mine import LIST_ARCHIVE, MineStore

    store = MineStore(conn)
    store.ensure()
    store.enqueue([(name, LIST_ARCHIVE, 0)])
    raw, task, _priority = store.claim([LIST_ARCHIVE], 1)[0]
    store.finish(raw, task, "done")
    conn.commit()


class TestAnUnvouchedCacheIsUnknown:
    def test_no_fingerprint_is_unknown_not_current(
        self, conn: sqlite3.Connection, spec: CacheSpec,
    ) -> None:
        currency = assess(conn, spec)
        assert currency.state == UNKNOWN
        assert "no fingerprint" in currency.detail
        assert currency.state != CURRENT, "nothing vouches for it"

    def test_an_unreadable_fingerprint_is_unknown_not_a_crash(
        self, conn: sqlite3.Connection, spec: CacheSpec,
    ) -> None:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (FINGERPRINT_PREFIX + spec.name, "not json"))
        conn.commit()
        assert assess(conn, spec).state == UNKNOWN

    def test_a_fingerprint_missing_a_marker_is_unknown(
        self, conn: sqlite3.Connection,
    ) -> None:
        """A partial fingerprint cannot vouch for the marker it does not hold."""
        partial = CacheSpec(name="archive_listings", derived_from="x", remedy="y",
                            markers=("listings_done", "never_recorded"))
        capture(conn, partial.name, {"listings_done": 1})
        currency = assess(conn, partial)
        assert currency.state == UNKNOWN
        assert "never_recorded" in currency.detail

    def test_an_artefact_with_no_check_is_unknown_never_current(
        self, conn: sqlite3.Connection,
    ) -> None:
        """The failure mode this guards: a spec added without a branch, silently current."""
        unhandled = CacheSpec(name="a_cache_with_no_branch", derived_from="x", remedy="y",
                              markers=("whatever",))
        capture(conn, unhandled.name, {"whatever": 1})
        currency = assess(conn, unhandled)
        assert currency.state == UNKNOWN
        assert "no staleness check" in currency.detail


class TestStaleMeansTheInputsMoved:
    def test_a_fresh_fingerprint_is_current(
        self, conn: sqlite3.Connection, spec: CacheSpec,
    ) -> None:
        _finish_a_listing(conn)
        capture(conn, spec.name, {"listings_done": 1})
        currency = assess(conn, spec)
        assert currency.state == CURRENT
        assert "ago" in currency.detail

    def test_mining_since_the_fingerprint_is_stale_with_the_work_named(
        self, conn: sqlite3.Connection, spec: CacheSpec,
    ) -> None:
        _finish_a_listing(conn)
        capture(conn, spec.name, {"listings_done": 1})
        _finish_a_listing(conn, b"another.zip")       # the corpus moved
        currency = assess(conn, spec)
        assert currency.state == STALE
        assert "listings_done 1→2" in currency.detail
        # The remedy is what makes "stale" actionable rather than alarming.
        assert spec.remedy in currency.line

    def test_age_alone_is_not_staleness(
        self, conn: sqlite3.Connection, spec: CacheSpec,
    ) -> None:
        """The owner's distinction: a count from two hours ago over an untouched corpus is current."""
        _finish_a_listing(conn)
        import json
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (FINGERPRINT_PREFIX + spec.name,
             json.dumps({"recorded_at": time.time() - 7200,
                         "markers": {"listings_done": 1}})))
        conn.commit()
        currency = assess(conn, spec)
        assert currency.state == CURRENT
        assert "h ago" in currency.detail, "the age is reported, not treated as staleness"


class TestTheDiagnosisIsNeverTheExpensiveOperation:
    def test_assessing_every_cache_issues_no_chunk_count_and_no_join(
        self, conn: sqlite3.Connection,
    ) -> None:
        """A staleness check that scans is the CL6 defect with a new name.

        The trace callback renders bound parameters inline, so the assertion is on the
        statement text: no `COUNT(*) FROM text_chunks`, no `FROM archive_members`, no
        grouping over a big table.
        """
        for spec in CACHES:
            capture(conn, spec.name, {k: 0 for k in spec.markers})
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            report(conn)
        finally:
            conn.set_trace_callback(None)

        joined = " ".join(statements).lower()
        assert "count(*) from text_chunks" not in joined, joined
        assert "from archive_members" not in joined, joined
        assert " from text_chunks" not in joined, joined
        # …and it did read something, so the claim is not vacuous.
        assert statements, "the ledger must actually ask the index something"

    def test_every_declared_marker_is_cheap_to_read(self, conn: sqlite3.Connection) -> None:
        """Each marker in `CACHES` must be answerable by an indexed count."""
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            report(conn)
        finally:
            conn.set_trace_callback(None)
        for stmt in statements:
            low = stmt.lower()
            if "count(*)" in low:
                assert "mine_queue" in low, (
                    f"a marker counted something other than the indexed queue: {stmt}"
                )


class TestTheReport:
    def test_render_names_the_remedy_only_for_what_is_not_current(
        self, conn: sqlite3.Connection,
    ) -> None:
        text = render(report(conn))
        assert "cache freshness" in text
        assert "unknown" in text
        # Nothing is current here (no fingerprints), so every remedy is offered.
        for spec in CACHES:
            assert spec.remedy in text

    def test_a_current_ledger_is_quiet(
        self, conn: sqlite3.Connection,
    ) -> None:
        """Once every cache is fingerprinted and nothing moved, the report offers no work."""
        listed = conn.execute(
            "SELECT COUNT(*) FROM mine_queue WHERE task='list_archive' AND state='done'"
        ).fetchone()[0]
        extracted = conn.execute(
            "SELECT COUNT(*) FROM mine_queue WHERE task='extract_text' AND state='done'"
        ).fetchone()[0]
        capture(conn, "archive_listings", {"listings_done": listed})
        capture(conn, "extraction", {"extracted_done": extracted})
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('coverage_snapshot', ?)",
            ('{"sources_indexed": 1, "chunks": 1}',))
        conn.commit()
        capture(conn, "coverage", {"sources_indexed": 1, "chunks": 1})

        currencies = report(conn)
        assert [c.state for c in currencies] == [CURRENT, CURRENT, CURRENT]
        text = render(currencies)
        assert "work that would make" not in text
        assert "report `unknown`" not in text
