"""The text index: what is *inside* the corpus, addressable and citable (RO3).

The path index knows names; the classification knows kinds. This knows words — and
it is what turns the corpus from something you can list into something you can ask.

Four decisions from the owner shape it, and each one is visible in the schema:

* **Chunks are addresses, not text.** A chunk row records `(source, byte_start,
  byte_end)` and nothing else; the FTS table is **contentless** (`content=''`), so
  the words exist once — in the file, or in the derived cache — and search results
  are reconstructed by reading the range. A citation that cannot be re-read is not
  a citation, so `read()` is the only way text comes back out.
* **Derived text is labelled.** A VLM description or an OCR page is not a quote.
  Chunks carry `derived` and `engine`, and the wiki has to keep showing that
  difference (`AGENTS.md` §1.9 and the I6 rule in the scope conversation).
* **Vendored code is indexed but ranked down, with a counter.** Excluding it would
  hide matches silently; including it unranked would drown the owner's own
  material. So it is indexed, filtered out of the default results, and the number
  of matches hidden by that filter is *reported*.
* **Spanish and English.** `unicode61` with `remove_diacritics 2`, so `Maria`
  finds `María` — which matters when the corpus is a Spanish-language backup.

Coverage is part of the interface, not an afterthought: a search that cannot see
un-indexed files must say so rather than answer "no matches" (the same rule that
made the marker proof and the partial index honest).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

TEXTINDEX_VERSION = "1"

#: Target and hard maximum chunk sizes in *bytes*. The target keeps a chunk around
#: a paragraph or two; the maximum stops one pathological line (a minified `.js`, a
#: single-line JSON dump) from becoming a chunk nobody can use.
DEFAULT_CHUNK_BYTES = 1_200
MAX_CHUNK_BYTES = 8_000

#: Path fragments that mean "this came with something else". Versioned here, in
#: code, because the ranking rule has to give the same answer in six months.
VENDORED_MARKERS: tuple[str, ...] = (
    "node_modules/", "site-packages/", "dist-packages/", "/.git/", "/.svn/",
    "/vendor/", "/vendors/", "/third_party/", "/thirdparty/", "/external/",
    "/bower_components/", "/.venv/", "/venv/", "/dist/", "/build/", "/target/",
    "/__pycache__/", "/.tox/", "/.cache/", "/pods/", "/carthage/",
    "/packages/", "/.gradle/", "/.m2/", "/.npm/", "/.cargo/", "/.rustup/",
    "/appdata/", "/program files", "/windows/", "/usr/lib/", "/usr/share/",
)

ORIGIN_FILE = "file"
ORIGIN_CACHE = "cache"

#: `path#L<byte_start>-<byte_end>`, the one citable form. Parsed by
#: `TextIndex.find_chunk`, which is what lets a printed hit be handed straight
#: back to `corpus_read` instead of being re-parsed by whoever read it — the step
#: a small model got wrong the first time it was asked to use a hit.
ADDRESS_RE = re.compile(r"^(?P<source>.+?)#L(?P<start>\d+)-(?P<end>\d+)$")

#: The same shape, unanchored: does this text cite *any* address? Used to measure
#: whether a corpus answer carried its evidence (RO4), where the answer is prose
#: with addresses somewhere inside it rather than an address on its own. It is a
#: shape check, not a validator — whether a cited address resolves is the index's
#: business, and answering that here would make an unindexed citation look like a
#: fabricated one.
ADDRESS_IN_TEXT_RE = re.compile(r"#L\d+-\d+")

#: A *whole* address as it appears inside prose or a printed hit: `path#L<a>-<b>`.
#: `ADDRESS_IN_TEXT_RE` above finds the offset half, which is enough to notice that
#: something looks like an address but not enough to compare two of them — and
#: comparing is the point: the harness records what each helper served and requires
#: a submitted citation to be a member of that set (RO4, 2026-09-16). Trailing
#: punctuation is excluded by construction (`\d+` ends the match), and the path is
#: the run of non-delimiter characters before the `#`, so a printed hit line yields
#: exactly its address token.
ADDRESS_TOKEN_RE = re.compile(r"[^\s`\"'()\[\]<>]+#L\d+-\d+")

#: Where the published coverage snapshot lives in the index's `meta` table. Two
#: keys rather than one so a reader can report the snapshot's age: the numbers are
#: computed by whoever can afford the scan, and staleness is the only price.
COVERAGE_SNAPSHOT_KEY = "coverage_snapshot"
COVERAGE_PUBLISHED_AT_KEY = "coverage_published_at"


def is_vendored(display_path: str) -> bool:
    """Whether a path looks like it arrived with something else.

    A ranking hint, never a deletion: matches inside these paths are counted and
    reported when they are filtered out.
    """
    lowered = "/" + display_path.lower().lstrip("/")
    return any(marker in lowered for marker in VENDORED_MARKERS)


def chunk_ranges(
    data: bytes,
    *,
    target: int = DEFAULT_CHUNK_BYTES,
    maximum: int = MAX_CHUNK_BYTES,
) -> list[tuple[int, int]]:
    """Split bytes into `(start, end)` ranges on newline boundaries.

    Byte offsets, not character offsets: the corpus has 98 names that are not even
    valid UTF-8, and its files are not all valid UTF-8 either. A byte range is the
    only address that can always be re-read. Whitespace-only chunks are dropped —
    they cost a row and a posting list and can never match anything useful.
    """
    if not data:
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    cursor = 0
    length = len(data)
    while cursor < length:
        newline = data.find(b"\n", cursor)
        line_end = length if newline == -1 else newline + 1
        # A single over-long line is split at the maximum rather than kept whole.
        while line_end - start > maximum:
            ranges.append((start, start + maximum))
            start += maximum
        if line_end - start >= target:
            ranges.append((start, line_end))
            start = line_end
        cursor = line_end
    if start < length:
        ranges.append((start, length))
    return [
        (s, e) for s, e in ranges
        if data[s:e].strip()
    ]


@dataclass(frozen=True)
class Hit:
    """One matching chunk. It carries an address, never the text itself."""

    chunk_id: int
    source: str
    origin: str
    source_hash: str
    byte_start: int
    byte_end: int
    derived: bool
    engine: str | None
    vendored: bool
    score: float
    cache_task: str | None = None
    cache_key: str | None = None

    @property
    def address(self) -> str:
        """The citable form: `source#L<byte_start>-<byte_end>`.

        Archive members and cache entries use the same shape, so a citation always
        says where in what, and never depends on the reader knowing which store it
        came from.
        """
        return f"{self.source}#L{self.byte_start}-{self.byte_end}"


@dataclass
class SearchResult:
    hits: list[Hit]
    hidden_vendored: int
    coverage_note: str | None = None


class TextIndex:
    """The chunk table and its contentless FTS5 companion."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS text_chunks (
                id          INTEGER PRIMARY KEY,
                source      BLOB NOT NULL,
                display     TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                origin      TEXT NOT NULL,
                cache_task  TEXT,
                cache_key   TEXT,
                byte_start  INTEGER NOT NULL,
                byte_end    INTEGER NOT NULL,
                derived     INTEGER NOT NULL DEFAULT 0,
                engine      TEXT,
                vendored    INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS text_chunks_source
                ON text_chunks(source);
            CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(
                body,
                content='',
                tokenize="unicode61 remove_diacritics 2"
            );
            """
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("textindex_version", TEXTINDEX_VERSION),
        )
        self._conn.commit()

    def version(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'textindex_version'"
        ).fetchone()
        return None if row is None else str(row[0])

    # ── Indexing ──────────────────────────────────────────────────────────

    def has_source(self, raw: bytes, *, origin: str = ORIGIN_FILE) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM text_chunks WHERE source = ? AND origin = ? LIMIT 1",
            (raw, origin),
        ).fetchone()
        return row is not None

    def add_text(
        self,
        *,
        raw: bytes,
        display: str,
        source_hash: str,
        text: bytes,
        origin: str = ORIGIN_FILE,
        cache_task: str | None = None,
        cache_key: str | None = None,
        derived: bool = False,
        engine: str | None = None,
        replace: bool = False,
    ) -> int:
        """Index one source's text. Returns the number of chunks written.

        Idempotent by default: a source already present under the same origin is
        skipped, which is what makes re-running a batch free. `replace` drops the
        old chunks first (used when a derived artefact is regenerated).
        """
        if replace:
            self.drop_source(raw, origin=origin)
        elif self.has_source(raw, origin=origin):
            return 0
        ranges = chunk_ranges(text)
        if not ranges:
            return 0
        vendored = 1 if is_vendored(display) else 0
        cursor = self._conn.cursor()
        for start, end in ranges:
            cursor.execute(
                "INSERT INTO text_chunks (source, display, source_hash, origin,"
                " cache_task, cache_key, byte_start, byte_end, derived, engine,"
                " vendored) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (raw, display, source_hash, origin, cache_task, cache_key,
                 start, end, 1 if derived else 0, engine, vendored),
            )
            cursor.execute(
                "INSERT INTO text_fts (rowid, body) VALUES (?, ?)",
                (cursor.lastrowid, text[start:end].decode("utf-8", "replace")),
            )
        self._conn.commit()
        return len(ranges)

    def drop_source(self, raw: bytes, *, origin: str | None = None) -> int:
        """Remove a source's chunks. The FTS table is contentless, so its rows go
        with a rebuild rather than a delete — `reset` is the honest way to redo a
        whole index."""
        if origin is None:
            cursor = self._conn.execute(
                "DELETE FROM text_chunks WHERE source = ?", (raw,)
            )
        else:
            cursor = self._conn.execute(
                "DELETE FROM text_chunks WHERE source = ? AND origin = ?", (raw, origin)
            )
        self._conn.commit()
        return int(cursor.rowcount)

    def reset(self) -> None:
        """Drop both tables and rebuild empty. The FTS companion is contentless, so
        a partial delete would leave postings behind; a reset cannot."""
        self._conn.executescript(
            "DROP TABLE IF EXISTS text_fts; DROP TABLE IF EXISTS text_chunks;"
        )
        self._conn.commit()
        self.ensure()

    # ── Searching ─────────────────────────────────────────────────────────

    @staticmethod
    def _match_expression(query: str) -> str | None:
        """Turn a user query into an FTS5 expression, safely.

        Terms are wrapped in double quotes, so FTS5 syntax the user did not intend
        (a stray `*`, `NEAR`, a bare `-`) cannot become an operator or a syntax
        error. All terms must appear: for a corpus of needles, an AND is the
        honest default, and a query that is too narrow says so by returning
        nothing rather than by returning noise.
        """
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return None
        return " AND ".join(f'"{term}"' for term in terms[:16])

    def search(
        self, query: str, *, k: int = 8, include_vendored: bool = False,
        derived_only: bool = False,
    ) -> SearchResult:
        expression = self._match_expression(query)
        if expression is None:
            return SearchResult(hits=[], hidden_vendored=0)
        clauses = ["text_fts MATCH ?"]
        params: list[Any] = [expression]
        if not include_vendored:
            clauses.append("c.vendored = 0")
        if derived_only:
            clauses.append("c.derived = 1")
        sql = (
            "SELECT c.id, c.source, c.display, c.source_hash, c.origin, c.byte_start,"
            " c.byte_end, c.derived, c.engine, c.vendored, c.cache_task, c.cache_key,"
            " bm25(text_fts) AS score"
            " FROM text_fts JOIN text_chunks c ON c.id = text_fts.rowid"
            f" WHERE {' AND '.join(clauses)}"
            " ORDER BY score LIMIT ?"
        )
        rows = self._conn.execute(sql, (*params, max(1, int(k)))).fetchall()
        hits = [
            Hit(
                chunk_id=int(r[0]), source=str(r[2]), origin=str(r[4]),
                source_hash=str(r[3]), byte_start=int(r[5]), byte_end=int(r[6]),
                derived=bool(r[7]), engine=r[8], vendored=bool(r[9]),
                score=float(r[12]), cache_task=r[10], cache_key=r[11],
            )
            for r in rows
        ]
        hidden = 0
        if not include_vendored:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM text_fts JOIN text_chunks c"
                " ON c.id = text_fts.rowid WHERE text_fts MATCH ? AND c.vendored = 1",
                (expression,),
            ).fetchone()
            hidden = int(row[0]) if row else 0
        return SearchResult(hits=hits, hidden_vendored=hidden)

    # ── Reading a hit back ────────────────────────────────────────────────

    def read(
        self,
        hit: Hit,
        *,
        mount: LocalTreeMount | None = None,
        cache_root: Path | None = None,
    ) -> str:
        """The chunk's text, read from where it lives.

        A citation is only real if it can be re-read, so this is the *only* way
        text comes back out of the index — and it fails loudly rather than
        returning a plausible empty string.
        """
        if hit.origin == ORIGIN_CACHE:
            if cache_root is None or not hit.cache_task or not hit.cache_key:
                raise ReadOnlyViolation("cache root or key missing for a derived chunk")
            path = (Path(cache_root) / hit.cache_task / hit.cache_key[:2]
                    / f"{hit.cache_key}.txt")
            try:
                data = path.read_bytes()
            except OSError as e:
                raise ReadOnlyViolation(f"derived text is gone: {e}") from e
        else:
            if mount is None:
                raise ReadOnlyViolation("mount required for a source chunk")
            rel = hit.source.encode("utf-8", "surrogateescape").decode(
                "utf-8", "surrogateescape"
            )
            with mount.open_readonly(rel) as handle:
                handle.seek(hit.byte_start)
                data = handle.read(hit.byte_end - hit.byte_start)
        return data.decode("utf-8", "replace")

    def find_chunk(self, address: str) -> Hit | None:
        """The chunk an address names, or None if it is not indexed.

        This is what turns a printed hit into something a caller can *use*: the
        address already says which store holds the text (a file or a derivation
        cache), so nothing downstream has to guess or re-parse.
        """
        match = ADDRESS_RE.match((address or "").strip())
        if match is None:
            return None
        source = match.group("source")
        start = int(match.group("start"))
        end = int(match.group("end"))
        row = self._conn.execute(
            "SELECT id, source, display, source_hash, origin, byte_start, byte_end,"
            " derived, engine, vendored, cache_task, cache_key FROM text_chunks"
            " WHERE display = ? AND byte_start = ? AND byte_end = ? LIMIT 1",
            (source, start, end),
        ).fetchone()
        if row is None:
            return None
        return Hit(
            chunk_id=int(row[0]), source=str(row[2]), origin=str(row[4]),
            source_hash=str(row[3]), byte_start=int(row[5]), byte_end=int(row[6]),
            derived=bool(row[7]), engine=row[8], vendored=bool(row[9]),
            score=0.0, cache_task=row[10], cache_key=row[11],
        )

    def read_address(
        self,
        address: str,
        *,
        mount: LocalTreeMount | None = None,
        cache_root: Path | None = None,
    ) -> str | None:
        """Read an address, or None when it names nothing indexed."""
        hit = self.find_chunk(address)
        if hit is None:
            return None
        return self.read(hit, mount=mount, cache_root=cache_root)

    # ── Coverage and stats ────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT source), COALESCE(SUM(byte_end"
            " - byte_start), 0), COALESCE(SUM(derived), 0) FROM text_chunks"
        ).fetchone()
        chunks, sources, indexed_bytes, derived = (row or (0, 0, 0, 0))
        return {
            "chunks": int(chunks),
            "sources": int(sources),
            "indexed_bytes": int(indexed_bytes),
            "derived_chunks": int(derived),
            "version": self.version(),
        }

    def coverage(self) -> dict[str, Any]:
        """What has been indexed against what the map says exists.

        The numbers a search has to quote when it finds nothing: "no matches"
        means nothing without "of the 2.9M text files, 12% are indexed".
        """
        text_files = self._count(
            "SELECT COUNT(*) FROM classification WHERE kind = 'text'"
        )
        documents = self._count(
            "SELECT COUNT(*) FROM classification WHERE kind IN ('document', 'archive')"
        )
        extracted = self._count(
            "SELECT COUNT(*) FROM mine_queue WHERE task = 'extract_text'"
            " AND state = 'done'"
        )
        needs_ocr = self._count(
            "SELECT COUNT(*) FROM mine_queue WHERE task = 'extract_text'"
            " AND note = 'needs_ocr'"
        )
        stats = self.stats()
        indexed = stats["sources"]
        return {
            "text_files_in_map": text_files,
            "documents_in_map": documents,
            "documents_extracted": extracted,
            "documents_needing_ocr": needs_ocr,
            "sources_indexed": indexed,
            "text_coverage": (indexed / text_files) if text_files else 0.0,
            "chunks": stats["chunks"],
            "indexed_bytes": stats["indexed_bytes"],
        }

    def _count(self, sql: str, params: Iterable[Any] = ()) -> int:
        try:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row else 0

    # ── The published coverage snapshot ───────────────────────────────────

    def publish_coverage(self, coverage: dict[str, Any]) -> None:
        """Store a *computed* coverage snapshot for the search path to quote.

        Measured on the real index on 2026-09-15: `search` returns in 0.2 s while
        `COUNT(*) FROM text_chunks` takes **972 s** and `COUNT(DISTINCT source)`
        and the classification counts each run past 45 s. `corpus_search` and
        `corpus_coverage` both called `coverage()`, so every corpus cell blew the
        REPL's 120 s cell limit, the worker was killed and restarted four times,
        and a live run ended after two hours with
        `(No answer produced — forced finalization failed)`.

        So the slow process computes and this path stores: the numbers are always
        *computed*, never accumulated, which means a snapshot cannot drift away
        from the index it describes — it can only be old, and
        `published_coverage` reports its age so a reader can say so out loud.
        """
        self._set_meta(COVERAGE_SNAPSHOT_KEY,
                       json.dumps(dict(coverage), sort_keys=True))
        self._set_meta(COVERAGE_PUBLISHED_AT_KEY, repr(time.time()))

    def published_coverage(self) -> dict[str, Any] | None:
        """The last published snapshot, or None when nobody has published one.

        None is what the search path must say "unknown" for: reporting zero
        instead would be a confident wrong answer about how much of the corpus
        was searched, which is the failure this project ranks below silence.
        """
        raw = self._get_meta(COVERAGE_SNAPSHOT_KEY)
        if raw is None:
            return None
        try:
            coverage = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(coverage, dict):
            return None
        published = self._get_meta(COVERAGE_PUBLISHED_AT_KEY)
        try:
            coverage["published_at"] = (float(published)
                                        if published is not None else None)
        except (TypeError, ValueError):
            coverage["published_at"] = None
        return coverage

    def _get_meta(self, key: str) -> str | None:
        """Read one meta value, or None — including when the table is missing."""
        try:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error:
            return None
        return None if row is None else str(row[0])

    def _set_meta(self, key: str, value: str) -> None:
        """Write one meta value, creating the table if this database lacks it."""
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()


def coverage_note(coverage: dict[str, Any]) -> str:
    """The sentence a search result must carry when it is incomplete.

    A published snapshot carries `published_at`, and an old one says how old it
    is: "5 sources indexed" from twenty minutes ago is a different claim from the
    same words measured a moment ago, and the difference is the whole reason the
    snapshot exists.
    """
    pct = 100 * float(coverage.get("text_coverage", 0.0))
    if pct >= 99.5:
        return ""
    age_suffix = ""
    published = coverage.get("published_at")
    if isinstance(published, (int, float)):
        age = max(0.0, time.time() - float(published))
        if age >= 60:
            age_suffix = f" (snapshot {int(age // 60)} min ago)"
    return (
        f"[coverage: {coverage.get('sources_indexed', 0):,} sources indexed "
        f"({pct:.1f}% of the {coverage.get('text_files_in_map', 0):,} text files; "
        f"{coverage.get('documents_extracted', 0):,} documents extracted, "
        f"{coverage.get('documents_needing_ocr', 0):,} awaiting OCR)]{age_suffix}"
    )


def format_hits(result: SearchResult, texts: list[str], *, width: int = 200) -> str:
    """The operator's view of a search: address, labels, and a snippet."""
    lines: list[str] = []
    for hit, text in zip(result.hits, texts):
        labels = [hit.origin]
        if hit.derived:
            labels.append(f"derived:{hit.engine or 'unknown'}")
        if hit.vendored:
            labels.append("vendored")
        snippet = re.sub(r"\s+", " ", text).strip()[:width]
        lines.append(f"{hit.address}  [{', '.join(labels)}]")
        lines.append(f"    {snippet}")
    if result.hidden_vendored:
        lines.append(
            f"[{result.hidden_vendored:,} further matches hidden by the vendored "
            "filter; search again with include_vendored=True]"
        )
    if result.coverage_note:
        lines.append(result.coverage_note)
    if not result.hits:
        lines.append("(no matches)")
    return "\n".join(lines)


def now() -> float:
    return time.time()
