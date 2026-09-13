"""Corpus access for the harness: a path index and read-only tool handlers (RO3/RO4).

The owner's requirement is that the corpus is *only* ever touched through the
harness (AGENTS.md §1.9). This module is the harness's half of that: the two
things a model cell is allowed to do with a corpus, and nothing else.

**`CorpusIndex` — names, not contents.** One streaming walk writes a SQLite table
of paths (`path`, `parent`, `name`, `kind`, `size`, `mtime`) and every later
question is a query against it. This is what makes the corpus answerable at all:
the tree holds millions of entries, a single walk of it took 33 minutes on
`lunacode`, so a helper that walked per call would be useless, and a model told
to "look around" would burn its whole budget on the first turn. The index reads
**no file contents** — it never opens a file, only the directory entries — which
is both the cheap half of RO3 and a property a test enforces with a spy mount
whose read methods raise.

The index is *derived state*: it lives outside the corpus, and
`assert_derived_outside_corpus` refuses to open it if it does not.

**`CorpusBridge` — the REPL handlers.** `corpus_find`, `corpus_list`,
`corpus_stat`, `corpus_read` and `corpus_count` are answered here, in the parent
process, through `LocalTreeMount`. The worker never touches the corpus itself:
it asks over the socket protocol it already uses for `search` and `propose`.
That placement is deliberate — the read-only enforcement is in one process, next
to the type that has no write verb, rather than spread across sandboxed cells.

Reads are bounded and paths are relative. A helper result is text that goes into
a model's context window, so a 10 GB file must not be able to arrive by naming
it, and an absolute path must not be able to escape the mount.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from rlm_kernel.mounts import (
    Entry,
    LocalTreeMount,
    ReadOnlyViolation,
    assert_derived_outside_corpus,
)

#: Hard bounds. A tool result is read into a small model's context window, so the
#: caps are part of the interface, not caller politeness: `FIND_LIMIT_MAX` keeps a
#: single call from returning a million paths, and `READ_BYTES_MAX` keeps a file
#: name from being a way to pull a whole disk into a prompt.
FIND_LIMIT_MAX = 200
LIST_LIMIT_MAX = 200
READ_BYTES_MAX = 200_000
DEFAULT_FIND_LIMIT = 20
DEFAULT_LIST_LIMIT = 50
DEFAULT_READ_BYTES = 20_000

_SCHEMA_VERSION = "1"


_SCHEMA_VERSION = "2"

#: The one layout this code can read. Bumped whenever it changes; the index is a
#: cache of the corpus, so a stale layout is dropped and rebuilt rather than
#: migrated (`_ensure_schema`).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    raw    BLOB PRIMARY KEY,
    path   TEXT NOT NULL,
    parent TEXT NOT NULL,
    name   TEXT NOT NULL,
    kind   TEXT NOT NULL,
    size   INTEGER NOT NULL,
    mtime  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_parent ON entries(parent);
CREATE INDEX IF NOT EXISTS entries_name ON entries(name);
CREATE INDEX IF NOT EXISTS entries_path ON entries(path);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def path_text(rel: str) -> str:
    """The stored, always-encodable form of a path.

    A backup contains file names that are not valid UTF-8 (Linux permits any
    bytes but `/` and NUL), and Python hands those back from `scandir` with
    surrogate escapes (`'\\udced'`). SQLite encodes TEXT as UTF-8 and refuses
    lone surrogates outright — which is how the first real build of this index
    died 75,000 entries in, after the unit tests (all ASCII names) passed.

    So a path is stored twice: exactly, as bytes (`raw`), and as this
    surrogate-free text (`path`) for display and search. `CorpusIndex.raw_for`
    recovers the exact bytes, which is how a damaged name stays *readable*
    instead of merely visible.
    """
    return rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def path_bytes(rel: str) -> bytes:
    """The exact bytes of a path, whatever its encoding."""
    return rel.encode("utf-8", "surrogateescape")


#: One statement, used by both the batch and the tail insert.
_INSERT = (
    "INSERT OR REPLACE INTO entries"
    " (raw, path, parent, name, kind, size, mtime) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

#: The secondary indexes. Dropped for the duration of a bulk load and rebuilt in
#: one pass at the end: maintaining three B-trees per row insertion costs a small
#: multiple of the whole walk (measured on the owner's corpus, where the first
#: full build took ~100 minutes), while recreating them afterwards is one sort.
_SECONDARY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entries_parent ON entries(parent)",
    "CREATE INDEX IF NOT EXISTS entries_name ON entries(name)",
    "CREATE INDEX IF NOT EXISTS entries_path ON entries(path)",
)
_DROP_SECONDARY_INDEXES = (
    "DROP INDEX IF EXISTS entries_parent",
    "DROP INDEX IF EXISTS entries_name",
    "DROP INDEX IF EXISTS entries_path",
)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class CorpusIndex:
    """A queryable table of corpus *paths*. No file contents, ever.

    Built by one pass over the mount, read by every later helper call. The
    index file is derived state and must live outside the corpus root.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn = _connect(self._path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the schema, or drop a stale one and rebuild from scratch.

        There is nothing to migrate: the corpus is the source of truth and this
        table is a cache of it, so a layout change costs one re-walk rather than
        a migration script that has to be right.
        """
        self._conn.executescript(_SCHEMA)
        existing = {
            k: v for k, v in self._conn.execute("SELECT key, value FROM meta")
        }
        found = existing.get("schema_version")
        if found is not None and found != _SCHEMA_VERSION:
            self._conn.executescript("DROP TABLE IF EXISTS entries;")
            self._conn.executescript(_SCHEMA)
        self._set_meta("schema_version", _SCHEMA_VERSION)
        self._conn.commit()

    @classmethod
    def open_for(
        cls, corpus_root: str | Path, index_path: str | Path,
    ) -> "CorpusIndex":
        """Open (creating if needed) an index that is *outside* the corpus.

        Layer 3 of the read-only guarantee, applied where the index is opened
        rather than trusted to the caller: an index inside the corpus root is a
        hard failure, not a warning.
        """
        assert_derived_outside_corpus(corpus_root, index_path)
        return cls(index_path)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "CorpusIndex":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── Building ──────────────────────────────────────────────────────────

    def build(
        self,
        mount: LocalTreeMount,
        *,
        progress: Callable[[int], None] | None = None,
        progress_every: int | None = None,
        batch_size: int = 5_000,
    ) -> int:
        """Write the path index from one streaming walk of ``mount``.

        A rebuild replaces the previous contents: the corpus is the source of
        truth and the index is a cache of it.

        **Progress is checkpointed, and completeness is recorded.** Each batch is
        committed and the running count is written to `meta`, so a walk killed
        after an hour (this happened on the first real one, which lost nothing
        only because it died before the first commit) leaves a *usable* and
        *honest* partial index: `complete` stays `"0"`, and every tool result
        built on it says the index is incomplete. A partial index quietly
        answering "no matches" would be the worst failure this project knows —
        a confident wrong answer.

        Only directory entries are read. No file is opened, so an unreadable,
        damaged or enormous file costs nothing here.

        `progress_every` gates the callback by *entries*, not by batches: a walk
        of millions of files at the default batch size would otherwise emit about
        a thousand lines, which is a progress report nobody reads — and, the first
        time this ran, a `--progress-every` flag that claimed to control the
        interval and did not.
        """
        rows: list[tuple[bytes, str, str, str, str, int, float]] = []
        written = 0
        damaged = 0
        reported = 0

        def report(count: int, *, final: bool = False) -> None:
            nonlocal reported
            if progress is None or count == reported:
                return
            if not final and progress_every and count - reported < progress_every:
                return
            reported = count
            progress(count)

        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM entries")
        self._set_meta("complete", "0")
        self._set_meta("entries", "0")
        self._conn.commit()
        for statement in _DROP_SECONDARY_INDEXES:
            cursor.execute(statement)
        self._conn.commit()
        try:
            for entry in mount.iter_entries():
                path = entry.rel
                shown = path_text(path)
                if shown != path:
                    # A name that is not valid UTF-8. Counted so the aggregate
                    # record can say how much of the corpus is in this state.
                    damaged += 1
                parent, _, name = shown.rpartition("/")
                rows.append(
                    (path_bytes(path), shown, parent, name, entry.kind, entry.size,
                     entry.mtime)
                )
                if len(rows) >= batch_size:
                    cursor.executemany(_INSERT, rows)
                    written += len(rows)
                    rows.clear()
                    self._set_meta("entries", str(written))
                    self._conn.commit()
                    report(written)
            if rows:
                cursor.executemany(_INSERT, rows)
                written += len(rows)
                self._set_meta("entries", str(written))
                self._conn.commit()
        finally:
            # The indexes come back whatever happened: an index-less table still
            # answers queries, just by scanning, and leaving it that way would be
            # a silent performance trap for every later caller.
            for statement in _SECONDARY_INDEXES:
                cursor.execute(statement)
            self._conn.commit()
        self._set_meta("corpus_root", str(mount.root))
        self._set_meta("built_at", f"{time.time():.3f}")
        self._set_meta("schema_version", _SCHEMA_VERSION)
        self._set_meta("entries", str(written))
        # Aggregates only, per AGENTS.md 1.9: how many names are not valid
        # UTF-8 is a fact about the corpus that is safe to report anywhere.
        self._set_meta("paths_not_utf8", str(damaged))
        self._set_meta("complete", "1")
        self._conn.commit()
        report(written, final=True)
        return written

    def is_complete(self) -> bool:
        """Whether the last build ran to the end of the corpus.

        An index with no `complete` key at all predates the flag; it is treated
        as complete because the build that made it either finished or left a
        database that its own version could not have queried anyway.
        """
        return self.meta().get("complete", "1") == "1"

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )

    def meta(self) -> dict[str, str]:
        return {k: v for k, v in self._conn.execute("SELECT key, value FROM meta")}

    def is_empty(self) -> bool:
        row = self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()
        return not row or row[0] == 0

    # ── Querying ──────────────────────────────────────────────────────────

    def count(self, *, kind: str | None = None, under: str = "") -> int:
        sql = "SELECT COUNT(*) FROM entries"
        where, params = self._where(kind=kind, under=under)
        row = self._conn.execute(sql + where, params).fetchone()
        return int(row[0]) if row else 0

    def bytes_total(self, *, kind: str | None = None, under: str = "") -> int:
        """Sum of sizes. Files only unless a kind is named, since summing a
        directory's own size says nothing about what is in it."""
        sql = "SELECT COALESCE(SUM(size), 0) FROM entries"
        where, params = self._where(kind=kind if kind is not None else "file", under=under)
        row = self._conn.execute(sql + where, params).fetchone()
        return int(row[0]) if row else 0

    def summary(self) -> dict[str, Any]:
        """Aggregates only: counts and byte totals, no paths.

        This is the shape that is safe to print, mail or commit (AGENTS.md
        §1.9: aggregates may travel, identifiers may not).
        """
        by_kind: dict[str, int] = {}
        for kind, n in self._conn.execute(
            "SELECT kind, COUNT(*) FROM entries GROUP BY kind ORDER BY kind"
        ):
            by_kind[str(kind)] = int(n)
        total_bytes = self._conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM entries WHERE kind = 'file'"
        ).fetchone()
        out: dict[str, Any] = {
            "entries": sum(by_kind.values()),
            "by_kind": by_kind,
            "file_bytes": int(total_bytes[0]) if total_bytes else 0,
            "meta": self.meta(),
        }
        return out

    def _where(self, *, kind: str | None = None, under: str = "") -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if under:
            # A range scan on the primary key, not `LIKE 'prefix%'`: the range
            # form uses the index whatever the collation is.
            prefix = under.rstrip("/") + "/"
            clauses.append("path >= ? AND path < ?")
            params.extend([prefix, prefix + "\uffff"])
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def stat(self, rel: str) -> Entry | None:
        row = self._conn.execute(
            "SELECT path, kind, size, mtime FROM entries WHERE path = ?",
            (rel.rstrip("/"),),
        ).fetchone()
        return _entry(row)

    def raw_for(self, rel: str) -> bytes | None:
        """The exact bytes of a stored path, or None if absent or ambiguous.

        This is what makes a name that is not valid UTF-8 *usable*: the display
        form has its damaged bytes replaced, so opening it directly fails, and
        the original bytes are the only thing that names the file.
        """
        rows = self._conn.execute(
            "SELECT raw FROM entries WHERE path = ? LIMIT 2", (rel.rstrip("/"),)
        ).fetchall()
        if len(rows) != 1:
            return None
        return bytes(rows[0][0])

    def list_dir(self, rel: str = "", *, limit: int = DEFAULT_LIST_LIMIT) -> list[Entry]:
        """One directory level, from the index. Never a walk."""
        parent = rel.strip("/")
        rows = self._conn.execute(
            "SELECT path, kind, size, mtime FROM entries WHERE parent = ?"
            " ORDER BY kind, name LIMIT ?",
            (parent, _bounded(limit, LIST_LIMIT_MAX)),
        ).fetchall()
        return [e for e in (_entry(r) for r in rows) if e is not None]

    def find(
        self,
        query: str,
        *,
        limit: int = DEFAULT_FIND_LIMIT,
        kind: str | None = None,
        under: str = "",
        order: str = "name",
    ) -> list[Entry]:
        """Substring search over names (or over full paths when `query` has a `/`).

        Matching on *names* by default is what a person means by "find the file
        called something": a query of `budget` should not be answered with every
        path that happens to sit under a directory containing that word. When the
        query contains a slash the search moves to full paths, which is how you
        ask for `notes/2021`.

        `%` and `_` are escaped, so a query cannot turn into a wildcard that
        "matches" the entire corpus.
        """
        needle = query.strip().strip("/")
        if not needle:
            return []
        column = "path" if "/" in needle else "name"
        clauses = [f"{column} LIKE ? ESCAPE '\\'"]
        params: list[Any] = [f"%{_escape_like(needle)}%"]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if under:
            prefix = under.strip("/") + "/"
            clauses.append("path >= ? AND path < ?")
            params.extend([prefix, prefix + "\uffff"])
        order_sql = "size DESC, path" if order == "size" else "length(name), path"
        params.append(_bounded(limit, FIND_LIMIT_MAX))
        rows = self._conn.execute(
            "SELECT path, kind, size, mtime FROM entries"
            f" WHERE {' AND '.join(clauses)} ORDER BY {order_sql} LIMIT ?",
            params,
        ).fetchall()
        return [e for e in (_entry(r) for r in rows) if e is not None]


def _bounded(value: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return maximum
    if value <= 0:
        return 1
    return min(value, maximum)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _entry(row: Iterable[Any] | None) -> Entry | None:
    if row is None:
        return None
    path, kind, size, mtime = row
    return Entry(rel=str(path), kind=str(kind), size=int(size), mode=0, mtime=float(mtime))


# ── Formatting: what a model cell actually sees ───────────────────────────
# These are tool results rather than harness prose, which is why they are
# formatted here and not in `rlm_local/templates.py` — the same rule the
# existing vault bridge follows for `search`/`propose`.

CORPUS_NOT_FOUND = "Error: no such path in the corpus: {rel!r}"
CORPUS_REFUSED = "Error: refused (outside the corpus, or not readable): {rel!r}"
CORPUS_NO_INDEX = (
    "Error: no corpus path index is built or configured, so this search cannot "
    "run. corpus_list/corpus_stat/corpus_read still work. (An operator builds "
    "the index with `rlm corpus index`.)"
)
CORPUS_READ_TRUNCATED = "\n[... truncated: {shown} of {total} bytes shown ...]"
CORPUS_NOT_A_FILE = "Error: not a regular file in the corpus: {rel!r}"
CORPUS_READ_ERROR = "Error: could not read {rel!r}: {error}"
CORPUS_NO_MATCHES = "(no corpus paths matched)"
CORPUS_INCOMPLETE = (
    "[warning: the path index is INCOMPLETE — {entries} entries as of its last "
    "checkpoint, so results may be missing. Rebuild it with `rlm corpus index`.]"
)
CORPUS_INCOMPLETE_INDEX_REPORT = (
    "\n[incomplete index: {entries} entries as of the last checkpoint]"
)
CORPUS_SUMMARY_LINE = (
    "{count} entries under {scope} ({files} files, {dirs} directories, "
    "{bytes} bytes in files)"
)


@dataclass
class CorpusBridge:
    """Answers the REPL's corpus verbs. Read-only, bounded, index-backed.

    Holds the mount (for reads and listings) and, when one has been built, the
    path index (for search and counting). The index is optional on purpose: the
    corpus is answerable without one — just slowly, and only where the model
    already knows to look.
    """

    mount: LocalTreeMount
    index: CorpusIndex | None = None

    @classmethod
    def open_for(
        cls,
        corpus_root: str | Path,
        index_path: str | Path | None = None,
    ) -> "CorpusBridge":
        """Mount a corpus, and attach its index when one has been built.

        A missing index file is not an error — it is the ordinary state before
        `rlm corpus index` has run, and the helpers say so rather than pretending
        they searched. An index path *inside* the corpus is an error (layer 3).
        """
        mount = LocalTreeMount(corpus_root)
        index: CorpusIndex | None = None
        if index_path is not None:
            candidate = Path(index_path)
            assert_derived_outside_corpus(mount.root, candidate)
            if candidate.exists():
                index = CorpusIndex(candidate)
        return cls(mount=mount, index=index)

    def close(self) -> None:
        if self.index is not None:
            self.index.close()

    # ── Handlers (called by REPLSandbox._handle_request) ──────────────────

    def _refusal(self, rel: str) -> str | None:
        """The refusal for a path that leaves the corpus, or None.

        Checked before existence, on purpose: `mount.exists()` is `False` for a
        path that escapes *and* for a path that is merely missing, so asking it
        first would report "no such path" for the one case that deserves to be
        named as refused.
        """
        if self.mount.is_contained(rel):
            return None
        return CORPUS_REFUSED.format(rel=rel)

    def _readable(self, rel: str) -> str | None:
        """The path to hand the mount, or None if nothing there can be opened.

        Usually the caller's own string. The exception is a name that is not
        valid UTF-8: the index stores a display form with the damaged bytes
        replaced, and that display form does not name the file on disk. When the
        index knows the exact bytes, they are recovered here — otherwise a
        damaged name would be visible in a search result and permanently
        unreadable, which is the worst of both.
        """
        try:
            if self.mount.exists(rel):
                return rel
        except ReadOnlyViolation:
            return None
        if self.index is None:
            return None
        raw = self.index.raw_for(rel)
        if raw is None:
            return None
        recovered = raw.decode("utf-8", "surrogateescape")
        try:
            if self.mount.exists(recovered):
                return recovered
        except ReadOnlyViolation:
            return None
        return None

    def handle_find(
        self,
        query: str,
        limit: int = DEFAULT_FIND_LIMIT,
        kind: str | None = None,
        under: str = "",
    ) -> str:
        if self.index is None:
            return CORPUS_NO_INDEX
        hits = self.index.find(query, limit=limit, kind=kind, under=under)
        if not hits:
            return self._honest(CORPUS_NO_MATCHES)
        return self._honest(
            "\n".join(_format_entry(e) for e in hits)
            + _total_hint(self.index.count(kind=kind, under=under), len(hits))
        )

    def _honest(self, text: str) -> str:
        """Prefix a result from an incomplete index with the fact that it is one.

        A partial index answering "no matches" is a confident wrong answer, which
        is the failure this project treats as worse than no answer at all.
        """
        if self.index is None or self.index.is_complete():
            return text
        entries = self.index.meta().get("entries", "?")
        return CORPUS_INCOMPLETE.format(entries=entries) + "\n" + text

    def handle_list(self, rel: str = "", limit: int = DEFAULT_LIST_LIMIT) -> str:
        """One directory level, from the index when there is one, else the mount.

        The mount fallback is a single `scandir` of one directory — bounded work
        — which is why it is allowed where a walk is not.
        """
        if self.index is not None:
            if rel.strip("/") and self.index.stat(rel) is None:
                return CORPUS_NOT_FOUND.format(rel=rel)
            hits = self.index.list_dir(rel, limit=limit)
            if not hits:
                return CORPUS_NOT_FOUND.format(rel=rel)
            return "\n".join(_format_entry(e) for e in hits)
        refusal = self._refusal(rel)
        if refusal is not None:
            return refusal
        target = self._readable(rel)
        if target is None:
            return CORPUS_NOT_FOUND.format(rel=rel)
        try:
            if rel.strip("/") and self.mount.stat(target).kind != "dir":
                return CORPUS_NOT_FOUND.format(rel=rel)
            shown = self.mount.iter_children(
                target, max_entries=_bounded(limit, LIST_LIMIT_MAX)
            )
            hits = list(shown)
            if not hits:
                return CORPUS_NOT_FOUND.format(rel=rel)
            return "\n".join(_format_entry(e) for e in hits)
        except ReadOnlyViolation as e:
            return CORPUS_REFUSED.format(rel=rel) + f" ({e})"

    def handle_stat(self, rel: str) -> str:
        refusal = self._refusal(rel)
        if refusal is not None:
            return refusal
        if self.index is not None:
            entry = self.index.stat(rel)
        else:
            # A missing path and an escaping path are different answers: the
            # former is "nothing there", the latter is "refused".
            target = self._readable(rel)
            if target is None:
                return CORPUS_NOT_FOUND.format(rel=rel)
            try:
                entry = self.mount.stat(target)
            except ReadOnlyViolation as e:
                return CORPUS_REFUSED.format(rel=rel) + f" ({e})"
        if entry is None:
            return CORPUS_NOT_FOUND.format(rel=rel)
        return (
            f"{entry.rel}\n  kind: {entry.kind}\n  size: {entry.size} bytes\n"
            f"  mtime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry.mtime))}"
        )

    def handle_read(self, rel: str, max_bytes: int = DEFAULT_READ_BYTES) -> str:
        """Read a bounded slice of one file. The only content access there is."""
        cap = _bounded(max_bytes, READ_BYTES_MAX)
        refusal = self._refusal(rel)
        if refusal is not None:
            return refusal
        target = self._readable(rel)
        if target is None:
            return CORPUS_NOT_FOUND.format(rel=rel)
        try:
            size = self.mount.stat(target)
        except ReadOnlyViolation as e:
            return CORPUS_REFUSED.format(rel=rel) + f" ({e})"
        if size.kind == "dir":
            return CORPUS_NOT_A_FILE.format(rel=rel)
        try:
            data = self.mount.read_bytes(target, max_bytes=cap)
        except ReadOnlyViolation as e:
            return CORPUS_READ_ERROR.format(rel=rel, error=e)
        text = data.decode("utf-8", errors="replace")
        # The header names the path the *caller* used: `size.rel` may contain
        # surrogate escapes for a name that is not valid UTF-8, and a surrogate
        # cannot be printed (or JSON-round-tripped through a cell) without
        # raising. The bytes are what opens the file; the display form is what
        # travels.
        header = f"# {rel} ({size.size} bytes)"
        if size.size > len(data):
            return header + "\n" + text + CORPUS_READ_TRUNCATED.format(
                shown=len(data), total=size.size
            )
        return header + "\n" + text

    def handle_count(self, *, kind: str | None = None, under: str = "") -> str:
        if self.index is None:
            return CORPUS_NO_INDEX
        return self._honest(count_report(self.index, kind=kind, under=under))


def count_report(
    index: CorpusIndex, *, kind: str | None = None, under: str = "",
) -> str:
    """The one-line aggregate answer. Needs no mount: it is index arithmetic.

    Split out of the bridge so the CLI can answer "how many" without opening a
    mount — which matters because `rlm corpus count` must work on a machine that
    is reading an index built somewhere else.
    """
    scope = under or "the whole corpus"
    return CORPUS_SUMMARY_LINE.format(
        count=index.count(kind=kind, under=under),
        scope=scope + (f" (kind={kind})" if kind else ""),
        files=index.count(kind="file", under=under),
        dirs=index.count(kind="dir", under=under),
        bytes=index.bytes_total(kind=None, under=under),
    )


def _format_entry(entry: Entry) -> str:
    marker = {"dir": "/", "symlink": "@", "file": ""}.get(entry.kind, "?")
    return f"{entry.rel}{marker}  [{entry.kind}, {entry.size} bytes]"


def _total_hint(total: int, shown: int) -> str:
    if total > shown:
        return f"\n[... {shown} of {total} matching paths shown; narrow the query ...]"
    return ""


def index_report(index: CorpusIndex) -> str:
    """An aggregates-only description of an index, safe to print anywhere."""
    return json.dumps(index.summary(), indent=2, sort_keys=True)
