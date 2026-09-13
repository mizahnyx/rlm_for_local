"""Stage 1: classify every file by what is actually in it (RO1/RO3).

The path index knows names and sizes. It does not know whether a `.dat` is a
database, a video or a text file — and Stage 0 showed how much that matters:
1,074,728 files with unknown extensions (138.7 GiB) and 486,287 with no extension
at all (52.5 GiB) are ~191 GiB whose nature no extension can tell. This pass reads
the **head of each file** (default 8 KiB, bounded, `O_RDONLY`, through the
read-only mount) and records what it finds in a table beside the path index:

* `kind` — `text`, `binary`, `archive`, `media`, `database`, `document`, `empty`
  or `unreadable`;
* `encoding` — `utf-8`, `cp1252`, or nothing;
* `head_hash` — SHA-256 of the head plus the file size, which is an exact content
  hash for any file smaller than the head window (1.49M files are under 1 KiB) and
  an approximate one above it;
* what was read, so a later pass with a different window can redo it.

**Read-only.** Nothing here writes to the corpus: reads go through
`LocalTreeMount.open_readonly`, which is `os.open(path, os.O_RDONLY)`, bounded,
and contained. The output is derived state and lives in the index, outside the
corpus.

**Resumable.** The pass commits in batches and skips what is already classified,
so a run killed after three hours resumes where it stopped — the same discipline
the index build was given after it lost a whole walk.

**Aggregates only.** `report()` returns counts, byte totals and a dedup estimate;
no path is ever in it, because this output is meant to be quotable off the corpus
machine (`AGENTS.md` §1.9). A test enforces that.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from rlm_kernel.mounts import Entry, LocalTreeMount, ReadOnlyViolation

#: Bumped when the sniffing rules change, so old rows can be redone deliberately
#: rather than trusted.
SNIFF_VERSION = "1"

#: Default read window for the sniff. `file(1)`-style detection needs very little,
#: and every byte here is multiplied by 4.28M files.
DEFAULT_SNIFF_BYTES = 8192
DEFAULT_HASH_BYTES = 65_536
DEFAULT_BATCH_SIZE = 2_000

TEXT = "text"
BINARY = "binary"
ARCHIVE = "archive"
MEDIA = "media"
DATABASE = "database"
DOCUMENT = "document"
EMPTY = "empty"
UNREADABLE = "unreadable"

KINDS = (TEXT, ARCHIVE, MEDIA, DATABASE, DOCUMENT, BINARY, EMPTY, UNREADABLE)

#: Strong magics: at least four bytes and specific enough to win over the text
#: test. Checked before decoding, because a container's head is not text.
_STRONG_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", ARCHIVE), (b"PK\x05\x06", ARCHIVE), (b"PK\x07\x08", ARCHIVE),
    (b"7z\xbc\xaf\x27\x1c", ARCHIVE),
    (b"Rar!\x1a\x07", ARCHIVE),
    (b"\x1f\x8b\x08", ARCHIVE),
    (b"BZh9", ARCHIVE), (b"BZh8", ARCHIVE),
    (b"\xfd7zXZ\x00", ARCHIVE),
    (b"\x28\xb5\x2f\xfd", ARCHIVE),
    (b"\x04\x22\x4d\x18", ARCHIVE),
    (b"\x89PNG\r\n\x1a\n", MEDIA),
    (b"GIF87a", MEDIA), (b"GIF89a", MEDIA),
    (b"RIFF", MEDIA),
    (b"OggS", MEDIA),
    (b"fLaC", MEDIA),
    (b"\x1aE\xdf\xa3", MEDIA),
    (b"\x00\x00\x01\xba", MEDIA), (b"\x00\x00\x01\xb3", MEDIA),
    (b"FLV\x01", MEDIA),
    (b"ID3", MEDIA),
    (b"SQLite format 3\x00", DATABASE),
    (b"\x00\x01\x00\x00Standard Jet DB", DATABASE),
    (b"\x00\x01\x00\x00Standard ACE DB", DATABASE),
    (b"%PDF-", DOCUMENT),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", DOCUMENT),
    (b"-----BEGIN PGP", TEXT),
    (b"-----BEGIN CERTIFICATE", TEXT),
)

#: Weak magics: two or three bytes. Only consulted once the text test has failed,
#: so a file that merely starts with "BM" is not called an image.
_WEAK_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", MEDIA),
    (b"BM", MEDIA),
    (b"II*\x00", MEDIA), (b"MM\x00*", MEDIA),
    (b"{\\rtf", DOCUMENT),
    (b"MZ", BINARY),
    (b"\x7fELF", BINARY),
)

_ALLOWED_CONTROL = frozenset(b"\t\n\r\f\b\x1b")


@dataclass(frozen=True)
class SniffResult:
    kind: str
    encoding: str | None

    def as_row(self) -> tuple[str, str | None]:
        return self.kind, self.encoding


def _control_ratio(data: bytes) -> float:
    bad = sum(1 for b in data if b < 0x20 and b not in _ALLOWED_CONTROL)
    return bad / len(data) if data else 0.0


#: A short file with a handful of control bytes is still text — an ANSI escape or
#: a BEL in a log line is not a binary format. The ratio alone would call a 33-byte
#: log with two escapes binary, which is exactly the kind of false verdict that
#: makes a classifier untrustworthy.
_CONTROL_ALLOWANCE = 4


def _looks_textual(data: bytes) -> bool:
    bad = sum(1 for b in data if b < 0x20 and b not in _ALLOWED_CONTROL)
    return bad <= _CONTROL_ALLOWANCE or _control_ratio(data) < 0.01


def _printable_ratio(data: bytes) -> float:
    good = sum(1 for b in data if 0x20 <= b <= 0x7E or 0xA0 <= b or b in _ALLOWED_CONTROL)
    return good / len(data) if data else 0.0


def sniff(data: bytes) -> SniffResult:
    """What is this? Magic first, then decodability, then default to binary.

    The order matters and is deliberate: a container's head is not text, so strong
    magics are checked before decoding; short magics are checked *after*, so a
    plain sentence beginning "BM" is still text.
    """
    if not data:
        return SniffResult(EMPTY, None)

    for magic, kind in _STRONG_MAGIC:
        if data.startswith(magic):
            return SniffResult(kind, None)

    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if _looks_textual(data):
                return SniffResult(TEXT, "utf-8")

    for magic, kind in _WEAK_MAGIC:
        if data.startswith(magic):
            return SniffResult(kind, None)

    if b"\x00" not in data and _printable_ratio(data) > 0.95 and _looks_textual(data):
        # Not UTF-8, but printable: the owner's corpus is Spanish and English, so
        # this is usually legacy single-byte text — the encodings old CDs carry.
        for encoding in ("cp1252", "latin-1"):
            try:
                data.decode(encoding)
            except UnicodeDecodeError:
                continue
            return SniffResult(TEXT, encoding)

    return SniffResult(BINARY, None)


def head_hash(data: bytes, size: int) -> str:
    """SHA-256 over the head plus the size.

    The size is included so that two files sharing a long prefix but differing in
    length are not called duplicates. For any file no larger than the read window
    this is the exact content hash (and 1.49M files here are under 1 KiB).
    """
    digest = hashlib.sha256()
    digest.update(data)
    digest.update(b"\x00")
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


@dataclass
class ClassifyStats:
    """What the pass did. Aggregates only — no path is ever collected here."""

    files_seen: int = 0
    classified: int = 0
    skipped: int = 0
    unreadable: int = 0
    bytes_read: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    def note(self, kind: str) -> None:
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1


class ClassificationTable:
    """The `classification` table: its schema, its writes, its aggregates.

    Lives beside the path index rather than inside it. The path index's layout
    rule is "a version change drops the table and rebuilds", and rebuilding it
    costs 95 minutes on the real corpus — so an additive table like this one must
    *not* be able to trigger that. It carries its own version key instead.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS classification (
                raw          BLOB PRIMARY KEY,
                kind         TEXT NOT NULL,
                encoding     TEXT,
                head_hash    TEXT NOT NULL DEFAULT '',
                hash_mode    TEXT NOT NULL DEFAULT 'head',
                sniff_bytes  INTEGER NOT NULL DEFAULT 0,
                note         TEXT,
                classified_at REAL NOT NULL,
                sniff_version TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS classification_kind
                ON classification(kind);
            CREATE INDEX IF NOT EXISTS classification_hash
                ON classification(head_hash);
            """
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("classification_version", SNIFF_VERSION),
        )
        self._conn.commit()

    def version(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'classification_version'"
        ).fetchone()
        return None if row is None else str(row[0])

    def pending(
        self, *, limit: int | None = None, redo: bool = False,
    ) -> list[tuple[bytes, int]]:
        """Files still to classify: `(raw path bytes, size)`.

        Only files: a symlink is a link, not content, and a directory has none.
        """
        sql = (
            "SELECT e.raw, e.size FROM entries e"
            " LEFT JOIN classification c ON c.raw = e.raw"
            " WHERE e.kind = 'file'"
        )
        if not redo:
            sql += " AND c.raw IS NULL"
        sql += " ORDER BY e.raw"
        if limit is not None:
            sql += " LIMIT ?"
            params: tuple[Any, ...] = (int(limit),) if limit is not None else ()
        else:
            params = ()
        return [(bytes(raw), int(size)) for raw, size in self._conn.execute(sql, params)]

    def record(self, rows: Iterable[tuple[Any, ...]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT OR REPLACE INTO classification"
            " (raw, kind, encoding, head_hash, hash_mode, sniff_bytes, note,"
            "  classified_at, sniff_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def count(self, *, version: str | None = None) -> int:
        if version is None:
            row = self._conn.execute("SELECT COUNT(*) FROM classification").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM classification WHERE sniff_version = ?",
                (version,),
            ).fetchone()
        return int(row[0]) if row else 0

    def report(self) -> dict[str, Any]:
        """Counts, bytes and a dedup estimate. Never a path."""
        by_kind: dict[str, dict[str, int]] = {}
        for kind, n, size in self._conn.execute(
            "SELECT c.kind, COUNT(*), COALESCE(SUM(e.size), 0)"
            " FROM classification c JOIN entries e ON e.raw = c.raw"
            " GROUP BY c.kind ORDER BY c.kind"
        ):
            by_kind[str(kind)] = {"files": int(n), "bytes": int(size)}

        by_encoding: dict[str, int] = {}
        for encoding, n in self._conn.execute(
            "SELECT COALESCE(encoding, '(none)'), COUNT(*) FROM classification"
            " GROUP BY 1 ORDER BY 2 DESC"
        ):
            by_encoding[str(encoding)] = int(n)

        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT head_hash) FROM classification"
            " WHERE head_hash <> ''"
        ).fetchone()
        hashed_files = int(row[0]) if row else 0
        distinct_hashes = int(row[1]) if row else 0

        dup_bytes = 0
        for total, smallest in self._conn.execute(
            "SELECT SUM(e.size), MIN(e.size) FROM classification c"
            " JOIN entries e ON e.raw = c.raw"
            " WHERE c.head_hash <> ''"
            " GROUP BY c.head_hash HAVING COUNT(*) > 1"
        ):
            dup_bytes += int(total) - int(smallest)

        unreadable = by_kind.get(UNREADABLE, {}).get("files", 0)
        return {
            "classified": sum(v["files"] for v in by_kind.values()),
            "by_kind": by_kind,
            "by_encoding": by_encoding,
            "hashed_files": hashed_files,
            "distinct_hashes": distinct_hashes,
            "duplicate_bytes": dup_bytes,
            "unreadable": unreadable,
            "classification_version": self.version(),
        }


def classify_one(
    mount: LocalTreeMount,
    rel: str,
    size: int,
    *,
    sniff_bytes: int = DEFAULT_SNIFF_BYTES,
    hash_bytes: int = DEFAULT_HASH_BYTES,
    hash_mode: str = "head",
) -> tuple[SniffResult, str, str, int]:
    """Read one file's head and describe it.

    The read is bounded by `max(sniff_bytes, hash_bytes)` — never the file's size
    — so a 200 GiB VM image costs the same as a 4-byte file. `hash_mode="none"`
    skips hashing; `"full"` is deliberately not implemented here: it would read
    every byte of 1,010 GiB in a pass whose job is to be cheap, and it belongs in
    a separate decision (see the roadmap's RO3 note).
    """
    want = sniff_bytes
    if hash_mode == "head":
        want = max(sniff_bytes, hash_bytes)
    data = mount.read_bytes(rel, max_bytes=want)
    result = sniff(data)
    digest = head_hash(data, size) if hash_mode == "head" else ""
    return result, digest, hash_mode, len(data)


def classify_entries(
    mount: LocalTreeMount,
    table: ClassificationTable,
    *,
    sniff_bytes: int = DEFAULT_SNIFF_BYTES,
    hash_bytes: int = DEFAULT_HASH_BYTES,
    hash_mode: str = "head",
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    redo: bool = False,
    progress: Callable[[ClassifyStats], None] | None = None,
    progress_every: int = 50_000,
) -> ClassifyStats:
    """Run the classification pass, resumably.

    Rows are committed per batch, so an interrupted run keeps its work and the
    next run picks up the remainder. `limit` exists for smoke tests and for
    pilots; the real pass runs unbounded.
    """
    stats = ClassifyStats()
    pending = table.pending(limit=limit, redo=redo)
    stats.skipped = 0 if redo else max(
        0, _file_count(table) - len(pending)
    )
    rows: list[tuple[Any, ...]] = []
    now = time.time()
    for raw, size in pending:
        rel = raw.decode("utf-8", "surrogateescape")
        stats.files_seen += 1
        try:
            result, digest, mode, read = classify_one(
                mount, rel, size, sniff_bytes=sniff_bytes, hash_bytes=hash_bytes,
                hash_mode=hash_mode,
            )
            note = None
        except ReadOnlyViolation as e:
            result, digest, mode, read = SniffResult(UNREADABLE, None), "", "none", 0
            note = type(e).__name__
            stats.unreadable += 1
        except OSError as e:  # pragma: no cover - environment failure
            result, digest, mode, read = SniffResult(UNREADABLE, None), "", "none", 0
            note = type(e).__name__
            stats.unreadable += 1
        stats.bytes_read += read
        stats.note(result.kind)
        stats.classified += 1
        rows.append((
            raw, result.kind, result.encoding, digest, mode, read, note,
            now, SNIFF_VERSION,
        ))
        if len(rows) >= batch_size:
            table.record(rows)
            rows.clear()
            if progress is not None:
                progress(stats)
    if rows:
        table.record(rows)
    if progress is not None:
        progress(stats)
    return stats


def _file_count(table: ClassificationTable) -> int:
    row = table._conn.execute(  # noqa: SLF001 - same package, one aggregate
        "SELECT COUNT(*) FROM entries WHERE kind = 'file'"
    ).fetchone()
    return int(row[0]) if row else 0


def format_report(report: dict[str, Any], *, total_files: int, total_bytes: int,
                  stats: ClassifyStats | None = None) -> str:
    """The human-readable aggregate report. Contains no path by construction."""

    def human(n: float) -> str:
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if abs(n) < 1024 or unit == "TiB":
                return f"{n:,.1f} {unit}"
            n /= 1024
        return f"{n:,.1f} TiB"

    lines: list[str] = []
    if stats is not None:
        lines.append(
            f"this pass: {stats.files_seen:,} files read, "
            f"{human(stats.bytes_read)} read off the corpus, "
            f"{stats.unreadable:,} unreadable"
        )
    lines.append(f"classified: {report['classified']:,} of {total_files:,} files")
    lines.append("")
    lines.append(f"{'kind':<12}{'files':>14}{'%files':>9}{'bytes':>16}{'%bytes':>9}")
    for kind, info in sorted(report["by_kind"].items(),
                             key=lambda kv: -kv[1]["bytes"]):
        lines.append(
            f"{kind:<12}{info['files']:>14,}"
            f"{100 * info['files'] / max(1, total_files):>8.1f}%"
            f"{human(info['bytes']):>16}"
            f"{100 * info['bytes'] / max(1, total_bytes):>8.1f}%"
        )
    lines.append("")
    lines.append("encodings: " + ", ".join(
        f"{k}={v:,}" for k, v in report["by_encoding"].items()
    ))
    dup_bytes = report["duplicate_bytes"]
    dup_factor = (
        report["hashed_files"] / report["distinct_hashes"]
        if report["distinct_hashes"] else 0.0
    )
    lines.append(
        f"dedup (head+size hash): {report['hashed_files']:,} files -> "
        f"{report['distinct_hashes']:,} distinct, factor {dup_factor:.2f}x; "
        f"{human(dup_bytes)} of file bytes are copies of something else"
    )
    lines.append(f"sniff version: {report['classification_version']}")
    return "\n".join(lines)
