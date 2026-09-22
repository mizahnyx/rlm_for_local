#!/usr/bin/env python3
"""Which counting queries can a heartbeat see?

A mining window holds a lock whose liveness is decided by a **heartbeat**, and the
window's closing scan refreshes it from inside the statement with
`sqlite3.set_progress_handler` (`rlm_kernel.mine._pulse`). That handler runs per VM
step, which makes one query shape a trap:

    SELECT COUNT(*) FROM t          -- 0 pulses: SQLite answers it from the b-tree
    SELECT COUNT(x) FROM t          -- pulses: the expression is evaluated per row
    SELECT SUM(x) FROM t            -- pulses
    SELECT COUNT(DISTINCT x) FROM t -- pulses

Measured 2026-09-22 while diagnosing a window that spent an hour after its work was
done (`docs/20260922-0855-the-window-that-counted-instead-of-finishing.md`). The
consequence is in `mine.MEMBER_COUNT_SQL`: the member count counts a **column**, so
that the count the closing scan publishes is one the heartbeat can cover. A count the
heartbeat cannot see is a count during which the lock goes stale while the worker is
demonstrably working — and the next worker takes it.

Run it directly; it prints a table and reads nothing but its own in-memory database:

    uv run python scripts/probe_count_forms.py
"""

from __future__ import annotations

import sqlite3
import time

ROWS = 200_000
#: Every this many VM steps the handler is called; small enough that a scan of ROWS
#: rows cannot hide from it.
EVERY = 1_000

QUERIES = (
    "SELECT COUNT(*) FROM many",
    "SELECT COUNT(x) FROM many",
    "SELECT SUM(x) FROM many",
    "SELECT COUNT(*) FROM many WHERE x >= 0",
    "SELECT COUNT(DISTINCT y) FROM many",
)


def build() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE many (x INTEGER, y TEXT)")
    conn.executemany("INSERT INTO many VALUES (?, ?)",
                     [(i, f"value-{i % 97}") for i in range(ROWS)])
    conn.commit()
    return conn


def measure(conn: sqlite3.Connection, sql: str) -> tuple[int, float]:
    """Pulses the progress handler fires for `sql`, and how long it took."""
    seen = [0]

    def handler() -> int:
        seen[0] += 1
        return 0

    conn.set_progress_handler(handler, EVERY)
    started = time.perf_counter()
    try:
        conn.execute(sql).fetchone()
    finally:
        conn.set_progress_handler(None, 0)
    return seen[0], (time.perf_counter() - started) * 1000.0


def main() -> int:
    conn = build()
    print(f"{ROWS:,} rows; progress handler every {EVERY:,} VM steps\n")
    print(f"{'query':<44} {'pulses':>8} {'ms':>9}  heartbeat")
    invisible = 0
    for sql in QUERIES:
        pulses, ms = measure(conn, sql)
        verdict = "sees it" if pulses else "BLIND"
        invisible += 0 if pulses else 1
        print(f"{sql:<44} {pulses:>8,} {ms:>9.1f}  {verdict}")
    print()
    if invisible:
        print(f"{invisible} query shape(s) cannot be covered by a progress-handler "
              "heartbeat - do not put them on a path whose lock must stay fresh.")
    else:  # pragma: no cover - would mean SQLite changed under us
        print("Every shape steps the VM; the heartbeat covers all of them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
