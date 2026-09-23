"""Mining the corpus: a map-driven queue, hash-keyed caches, windowed runs (RO3+).

The corpus is not read once and summarised; it is **mined**, batch by batch, in
whatever windows the owner has. That framing decides the shape of this module:

* **The map decides the work.** Stage 1 classified every file (`text`, `archive`,
  `media`, `document`, `binary`, …) and hashed its head. `plan_queue` reads that
  map and enqueues exactly the tasks each file needs — nothing is discovered by
  walking, and nothing is queued twice.
* **Everything derived is cached by content hash.** A VLM description, an ASR
  transcript and an extracted document all land under
  `~/rlm-derived/cache/<task>/<hash>.{json,txt}`, keyed by the *source's* content
  hash — so the 2.09x duplication in this corpus is paid for once, and re-running
  a batch costs nothing.
* **A run is a window, not a job.** `run_queue` takes a time budget, an optional
  deadline, an item cap and a pause file, checks them between items, and commits
  after each one. A run stopped at any moment loses at most the item in flight,
  which is what makes "the laptop is mine except when I want it" workable.
* **One model at a time, and one worker at a time.** The CLI takes an exclusive
  lock; a second miner refuses to start rather than fighting for the same 15 GiB.
* **Reads go through the mount.** No task opens a corpus file itself: they are
  handed the read-only mount, so containment, `O_RDONLY` and the derived-state
  boundary keep holding (AGENTS.md §1.8). A test scans this module for a builtin
  `open(` to keep it that way.
* **Output is aggregates.** `status()` and `TaskOutcome.note` carry counts and
  engine names, never a path (AGENTS.md §1.9).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation
# The origin label for derived text, shared with the text index so a citation from
# a cache entry and a citation from a file cannot drift apart.
from rlm_kernel.textindex import ORIGIN_CACHE

MINING_VERSION = "1"

#: Task names. Ordered here by the sequence they are built in, not by priority.
LIST_ARCHIVE = "list_archive"
EXTRACT_TEXT = "extract_text"
INDEX_TEXT = "index_text"
OCR_PAGE = "ocr_page"
VLM_DESCRIBE = "vlm_describe"
ASR_TRANSCRIBE = "asr_transcribe"
SUMMARISE = "summarise"
SYNTHESISE = "synthesise"

ALL_TASKS = (LIST_ARCHIVE, EXTRACT_TEXT, INDEX_TEXT, OCR_PAGE, VLM_DESCRIBE,
             ASR_TRANSCRIBE, SUMMARISE, SYNTHESISE)

#: Implemented so far. The rest are queued names with no handler yet, and
#: `run_queue` reports them as skipped rather than pretending to do them.
IMPLEMENTED_TASKS = (LIST_ARCHIVE, EXTRACT_TEXT, INDEX_TEXT)

PENDING = "pending"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

#: Where a *deliberate* count of the member table is published, so that no cheap path has
#: to scan for it. See `published_member_count`.
MEMBER_COUNT_KEY = "archive_members_published"

#: The one way to count member rows. `COUNT(member)` rather than `COUNT(*)` on purpose:
#: `member` is `NOT NULL`, so the two agree exactly, and both read the same covering index
#: — but SQLite answers a *bare* `COUNT(*)` from the b-tree without stepping the VM, so the
#: progress handler a window heartbeats with is blind to it (measured: 0 pulses vs 600 for
#: 200k rows, `scripts/probe_count_forms.py`). A count the heartbeat cannot see is a count
#: during which the lock goes stale while the worker is demonstrably working.
MEMBER_COUNT_SQL = "SELECT COUNT(member) FROM archive_members"

#: Priority bands. Lower runs first; the bands are the mining order the owner
#: confirmed: prose, then containers, then media, then everything else.
PRIORITY_EXTRACT_DOCUMENT = 10
PRIORITY_INDEX_TEXT = 20
PRIORITY_LIST_ARCHIVE = 30
PRIORITY_OCR_PAGE = 50
PRIORITY_VLM_DESCRIBE = 60
PRIORITY_ASR_TRANSCRIBE = 70
PRIORITY_SUMMARISE = 90

#: Cap on members read from one container. A single 26 GiB `.jar` can hold tens of
#: thousands; listing is meant to be cheap and bounded, not exhaustive at any cost.
MAX_MEMBERS = 20_000

#: Cap on how much of one text file is read for indexing. Text files here are
#: overwhelmingly small (1.49M are under a KiB); the cap exists so one
#: pathological log cannot pull a gigabyte into memory mid-window.
MAX_INDEX_BYTES = 32 * 1024 * 1024

#: Extensions handled by `extract_text` through a text-layer engine (no OCR).
PDF_EXTENSIONS = (".pdf",)
#: Zip-based office/document formats: text lives in known XML members.
ZIP_DOCUMENT_EXTENSIONS = (
    ".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt",
    ".ods", ".odp", ".epub", ".fodt", ".odg", ".xps",
)
#: What `plan_queue` treats as a document, whatever the sniff called it.
DOCUMENT_SUFFIXES = (*PDF_EXTENSIONS, *ZIP_DOCUMENT_EXTENSIONS)
#: Members inside those containers that carry the words.
ZIP_TEXT_MEMBERS = (
    "word/document.xml", "xl/sharedStrings.xml", "xl/worksheets/",
    "ppt/slides/", "ppt/notesSlides/", "content.xml", "OEBPS/", "EPUB/",
    "index.xml",
)


class MiningError(RuntimeError):
    """A task failed in a way worth recording against the queue row."""


@dataclass(frozen=True)
class TaskOutcome:
    """What a task did, in terms the queue and the report understand."""

    state: str
    note: str | None = None
    cache_key: str | None = None
    chars: int = 0


@dataclass
class MineStats:
    """Aggregates for one run. No path ever enters this object."""

    processed: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    cache_hits: int = 0
    by_task: dict[str, int] = field(default_factory=dict)

    def note(self, task: str, state: str) -> None:
        self.processed += 1
        self.by_task[task] = self.by_task.get(task, 0) + 1
        if state == DONE:
            self.done += 1
        elif state == FAILED:
            self.failed += 1
        else:
            self.skipped += 1


@dataclass
class MineRun:
    """The result of a windowed run: what happened and why it stopped."""

    stats: MineStats
    stop_reason: str
    seconds: float


# ── The queue and its tables ──────────────────────────────────────────────

class MineStore:
    """The mining tables: work to do, and what the map has been mined into.

    Separate from the path index and the classification table on purpose: the
    path index rebuilds by dropping itself on a layout change (95 minutes), so
    additive tables carry their own version keys and never trigger that.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mine_queue (
                raw        BLOB NOT NULL,
                task       TEXT NOT NULL,
                priority   INTEGER NOT NULL,
                state      TEXT NOT NULL,
                attempts   INTEGER NOT NULL DEFAULT 0,
                note       TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (raw, task)
            );
            -- The claim index carries `raw` as its last column on purpose. The
            -- claim is `WHERE state='pending' AND task IN (...) ORDER BY
            -- priority, raw LIMIT 1`: without `raw` in the index, SQLite has to
            -- sort every pending row to find the smallest one — *per item*. On
            -- the real corpus that turned 2.88M queued files into 0.7 files a
            -- second at 95% CPU (612 files in 14 minutes) while looking exactly
            -- like a slow disk. With it, the row is an index seek.
            CREATE INDEX IF NOT EXISTS mine_queue_claim
                ON mine_queue(task, state, priority, raw);
            DROP INDEX IF EXISTS mine_queue_pending;
            CREATE TABLE IF NOT EXISTS archive_members (
                container BLOB NOT NULL,
                member    TEXT NOT NULL,
                size      INTEGER NOT NULL,
                kind      TEXT NOT NULL,
                PRIMARY KEY (container, member)
            );
            CREATE INDEX IF NOT EXISTS archive_members_member
                ON archive_members(member);
            """
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("mining_version", MINING_VERSION),
        )
        self._conn.commit()

    # ── Enqueueing ────────────────────────────────────────────────────────

    def enqueue(self, rows: Iterable[tuple[bytes, str, int]]) -> int:
        """Add `(raw, task, priority)` rows. Re-enqueueing is a no-op."""
        rows = list(rows)
        if not rows:
            return 0
        now = time.time()
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO mine_queue"
            " (raw, task, priority, state, attempts, updated_at)"
            " VALUES (?, ?, ?, ?, 0, ?)",
            [(raw, task, priority, PENDING, now) for raw, task, priority in rows],
        )
        self._conn.commit()
        return self._conn.total_changes - before

    # ── Claiming and finishing ────────────────────────────────────────────

    def claim(self, tasks: Iterable[str], limit: int) -> list[tuple[bytes, str, int]]:
        """The next `(raw, task, priority)` to work on, soonest priority first."""
        names = list(tasks)
        if not names:
            return []
        placeholders = ",".join("?" * len(names))
        rows = self._conn.execute(
            f"SELECT raw, task, priority FROM mine_queue"
            f" WHERE state = ? AND task IN ({placeholders})"
            f" ORDER BY priority, raw LIMIT ?",
            (PENDING, *names, int(limit)),
        ).fetchall()
        return [(bytes(raw), str(task), int(priority)) for raw, task, priority in rows]

    def finish(self, raw: bytes, task: str, state: str, note: str | None = None) -> None:
        self._conn.execute(
            "UPDATE mine_queue SET state = ?, note = ?, attempts = attempts + 1,"
            " updated_at = ? WHERE raw = ? AND task = ?",
            (state, note, time.time(), raw, task),
        )
        self._conn.commit()

    def reset_failed(self, task: str | None = None) -> int:
        """Put failed rows back in the queue — the `retry` verb of the scraper."""
        if task is None:
            cursor = self._conn.execute(
                "UPDATE mine_queue SET state = ? WHERE state = ?", (PENDING, FAILED)
            )
        else:
            cursor = self._conn.execute(
                "UPDATE mine_queue SET state = ? WHERE state = ? AND task = ?",
                (PENDING, FAILED, task),
            )
        self._conn.commit()
        return int(cursor.rowcount)

    def reset_skipped(self, task: str, note: str) -> int:
        """Put `skipped` rows back in the queue, for one task and one note (2026-09-21).

        A skip is a *judgement at the time it was made* — "no engine claims this" — and a
        judgement can be superseded by the harness learning to do the thing. That happened:
        content routing (`task_list_archive`) and a wider extension list made 12 448
        extensionless containers and 52 mis-named archives listable, and every one of them
        was recorded `skipped/no_listing_engine`, which is a terminal state no verb could
        revisit. `retry` resets `failed` rows only, and `enqueue` is a no-op for a row that
        already exists, so without this the work the change unlocked would never run.

        Scoped by task **and** note on purpose: a skip is re-opened for a *reason*, and an
        unscoped reset would re-run skips whose reason still stands.
        """
        cursor = self._conn.execute(
            "UPDATE mine_queue SET state = ?, note = NULL, updated_at = ?"
            " WHERE state = ? AND task = ? AND note = ?",
            (PENDING, time.time(), SKIPPED, task, note),
        )
        self._conn.commit()
        return int(cursor.rowcount)

    # ── Members (what an archive listing produced) ────────────────────────

    def add_members(self, container: bytes, members: Iterable[tuple[str, int, str]]) -> int:
        rows = [(container, member, size, kind) for member, size, kind in members]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT OR REPLACE INTO archive_members (container, member, size, kind)"
            " VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def member_count(self) -> int:
        """The exact number of member rows — by scanning the whole table.

        Exact, and slow for exactly that reason: on the real index (~30M rows) this is
        the query that held a window open for an hour **after** its work was committed
        and its lock released (measured 2026-09-22,
        `docs/20260922-0855-the-window-that-counted-instead-of-finishing.md`). Nothing
        on a window's closing path may call it. Read `published_member_count` instead.
        """
        row = self._conn.execute(MEMBER_COUNT_SQL).fetchone()
        return int(row[0]) if row else 0

    def published_member_count(self) -> int | None:
        """The member count a deliberate publication recorded, or None.

        Never scans: a status line that counts a 30M-row table is how a finished window
        looks hung, and a window that would rather not pay for the count says "unknown"
        instead of a number it did not measure (AGENTS.md §1.8).
        """
        return published_member_count(self._conn)

    def has_members(self) -> bool:
        """Whether any archive listing has been recorded here.

        A corpus that has only been indexed (never mined) has no such table, and
        the search path must ask rather than assume.
        """
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_members'"
        ).fetchone()
        return row is not None

    def find_members(
        self, query: str, *, limit: int = 20,
    ) -> list[tuple[str, str, int, str]]:
        """Members whose *name* matches, as `(container, member, size, kind)`.

        This is the "archives are directories" half of the design: a member cannot
        be read without extracting it, but it can be found by name — and a hit
        tells the caller exactly which container to open.
        """
        needle = (query or "").strip().strip("/")
        if not needle:
            return []
        rows = self._conn.execute(
            "SELECT container, member, size, kind FROM archive_members"
            " WHERE member LIKE ? ESCAPE '\\'"
            " ORDER BY length(member), member LIMIT ?",
            (f"%{_escape_like(needle)}%", max(1, int(limit))),
        ).fetchall()
        out: list[tuple[str, str, int, str]] = []
        for container, member, size, kind in rows:
            display = bytes(container).decode("utf-8", "replace")
            out.append((display, str(member), int(size), str(kind)))
        return out

    # ── Reporting ─────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        by_task: dict[str, dict[str, int]] = {}
        for task, state, n in self._conn.execute(
            "SELECT task, state, COUNT(*) FROM mine_queue GROUP BY task, state"
        ):
            by_task.setdefault(str(task), {})[str(state)] = int(n)
        return {
            "queued_by_task": by_task,
            "archive_members": self.published_member_count(),
            "mining_version": MINING_VERSION,
        }


# ── The derivation cache ──────────────────────────────────────────────────

class DerivationCache:
    """Content-hash-keyed storage for everything a task derives.

    The key is the *source's* hash, so two byte-identical files share one
    derivation — which matters here: the corpus duplicates itself 2.09x within
    text alone. The metadata deliberately records no path: the queue row already
    says which file asked for the work, and a cache that names files is a cache
    that cannot be shared between duplicates.
    """

    def __init__(self, root: str | Path, task: str) -> None:
        self._root = Path(root) / task

    def key(self, source_hash: str, params: str = "") -> str:
        digest = hashlib.sha256()
        digest.update(source_hash.encode("ascii", "replace"))
        digest.update(b"\x00")
        digest.update(MINING_VERSION.encode("ascii"))
        digest.update(b"\x00")
        digest.update(params.encode("utf-8"))
        return digest.hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        shard = key[:2]
        return (self._root / shard / f"{key}.json", self._root / shard / f"{key}.txt")

    def has(self, key: str) -> bool:
        meta, _ = self._paths(key)
        return meta.exists()

    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        meta_path, text_path = self._paths(key)
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            text = text_path.read_text(encoding="utf-8", errors="replace") \
                if text_path.exists() else ""
        except (OSError, ValueError):
            return None
        return text, meta

    def put(self, key: str, text: str, meta: dict[str, Any]) -> None:
        """Write an entry atomically, or not at all.

        The text lands first and the metadata second, each through a temporary file and
        `os.replace`, and `has()` looks for the *metadata* — so a reader sees either a
        complete entry or none. A truncated `.txt` with metadata beside it would be served
        as the passage, which is the one failure this project ranks below saying nothing.
        The temporaries are removed on every path, including the failing one, so nothing is
        left behind that could be mistaken for an entry, and the write is refused outright
        when the filesystem cannot take it (owner call, 2026-09-22).
        """
        meta_path, text_path = self._paths(key)
        _require_space(meta_path.parent)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**meta, "task": self._root.name, "mining_version": MINING_VERSION,
                   "cached_at": time.time(), "chars": len(text)}
        tmp_text = text_path.with_name(text_path.name + ".tmp")
        tmp_meta = meta_path.with_name(meta_path.name + ".tmp")
        try:
            tmp_text.write_text(text, encoding="utf-8")
            os.chmod(tmp_text, 0o600)
            os.replace(tmp_text, text_path)
            tmp_meta.write_text(json.dumps(payload, indent=2, sort_keys=True),
                                encoding="utf-8")
            os.chmod(tmp_meta, 0o600)
            os.replace(tmp_meta, meta_path)
        finally:
            for leftover in (tmp_text, tmp_meta):
                try:
                    leftover.unlink()
                except OSError:
                    pass

    def stats(self) -> dict[str, int]:
        entries = 0
        chars = 0
        if self._root.exists():
            for path in self._root.rglob("*.json"):
                entries += 1
        if self._root.exists():
            for path in self._root.rglob("*.txt"):
                try:
                    chars += path.stat().st_size
                except OSError:
                    continue
        return {"entries": entries, "text_bytes": chars}


# ── Planning: the map decides the work ────────────────────────────────────

def plan_queue(store: MineStore, *, limit: int | None = None) -> dict[str, int]:
    """Enqueue tasks from the Stage 1 map. Idempotent.

    Only files the map says exist are considered, and only tasks that make sense
    for their kind: a `text` file needs no extraction (its bytes *are* the text),
    an `archive` needs listing, a `document` needs a text layer or, failing that,
    OCR.
    """
    enqueued = 0
    counts: dict[str, int] = {}

    def collect(task: str, priority: int, where: str, params: tuple[Any, ...]) -> None:
        """Enqueue every map row matching `where`, in batches.

        Batched because the biggest class here is 2.88M text files: collecting
        them into one list first would cost a few hundred MB of tuples for no
        benefit, and the insert is idempotent anyway.
        """
        nonlocal enqueued
        sql = (
            "SELECT e.raw FROM entries e JOIN classification c ON c.raw = e.raw"
            f" WHERE {where}"
        )
        cursor = store._conn.execute(sql, params)
        counts[task] = 0
        while True:
            batch = cursor.fetchmany(50_000)
            if not batch:
                break
            rows = [(bytes(raw), task, priority) for (raw,) in batch]
            counts[task] += len(rows)
            enqueued += store.enqueue(rows)

    # Documents are chosen by *name*, not by sniffed kind, and the reason is
    # concrete: a `.docx` is a zip container, so the sniff (correctly) calls it an
    # archive. The extension is what says whether the bytes hold words to extract
    # or members to list, so extraction claims the document extensions and the
    # listing task takes the rest.
    doc_like = " OR ".join("lower(e.name) LIKE ?" for _ in DOCUMENT_SUFFIXES)
    patterns = tuple(f"%{ext}" for ext in DOCUMENT_SUFFIXES)
    # `.rar` is claimed for *both* tasks: listing records its members, and extraction is
    # what makes a member's text readable (RO14 serves it from the container's own
    # extraction entry). Every other archive is listed and nothing more, which is why the
    # extraction claim is wider than the listing exclusion.
    extract_like = " OR ".join(
        "lower(e.name) LIKE ?" for _ in (*DOCUMENT_SUFFIXES, *LIBARCHIVE_DOCUMENT_EXTENSIONS))
    extract_patterns = tuple(
        f"%{ext}" for ext in (*DOCUMENT_SUFFIXES, *LIBARCHIVE_DOCUMENT_EXTENSIONS))
    collect(
        EXTRACT_TEXT, PRIORITY_EXTRACT_DOCUMENT,
        f"c.kind IN ('document', 'archive') AND ({extract_like})", extract_patterns,
    )
    collect(
        LIST_ARCHIVE, PRIORITY_LIST_ARCHIVE,
        f"c.kind = 'archive' AND NOT ({doc_like})", patterns,
    )
    # Plain text needs no conversion — its bytes are the text — so it is the one
    # class that is indexed rather than extracted, and it is the largest by far.
    collect(INDEX_TEXT, PRIORITY_INDEX_TEXT, "c.kind = 'text'", ())
    return {"enqueued": enqueued, "considered": counts}


# ── Tasks ─────────────────────────────────────────────────────────────────

@dataclass
class TaskContext:
    """What a task is given: the read-only mount, the cache, and the store."""

    mount: LocalTreeMount
    store: MineStore
    cache_root: Path
    engines: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = field(
        default_factory=dict
    )
    """Injected engines by name — `pdf`, `zip_document`, `libarchive_members`,
    `libarchive_text`, `summarise`. A task falls back to its own built-in when its name is
    absent; `summarise` has no built-in and reports `no_summarise_engine` rather than
    inventing a description. An engine whose output is cached under a derivation key should
    carry `engine_tag` (the model that produced it): the tag is part of the key, so without
    one a second model is served the first model's work in silence.
    """
    text_index: Any = None
    """The `TextIndex`, when one is attached. Tasks that produce or find text
    feed it; a run without one still extracts, and says so rather than pretending
    to have indexed anything."""

    def cache_for(self, task: str) -> DerivationCache:
        return DerivationCache(self.cache_root, task)


def _zip_members(handle: io.BufferedReader) -> list[tuple[str, int, str]]:
    members: list[tuple[str, int, str]] = []
    with zipfile.ZipFile(handle) as archive:
        for info in archive.infolist():
            if len(members) >= MAX_MEMBERS:
                break
            kind = "dir" if info.is_dir() else "file"
            members.append((info.filename, int(info.file_size), kind))
    return members


def _tar_members(handle: io.BufferedReader) -> list[tuple[str, int, str]]:
    members: list[tuple[str, int, str]] = []
    with tarfile.open(fileobj=handle, mode="r|*") as archive:
        for info in archive:
            if len(members) >= MAX_MEMBERS:
                break
            kind = "dir" if info.isdir() else ("symlink" if info.issym() else "file")
            members.append((info.name, int(info.size), kind))
    return members


def _gzip_single_member(handle: io.BufferedReader, name: str) -> list[tuple[str, int, str]]:
    # A bare `.gz`/`.zst`/`.xz` has one member with no name of its own.
    handle.read(0)
    return [(name.rsplit("/", 1)[-1].rsplit(".", 1)[0], 0, "file")]


def task_list_archive(ctx: TaskContext, rel: str, size: int, source_hash: str) -> TaskOutcome:
    """List a container's members and record them for search.

    Reading is bounded twice: by `MAX_MEMBERS` per container and by the mount's
    containment rules. A container that cannot be parsed is a `failed` row with
    the exception class as its note — never an aborted run, because a mining pass
    over 80,000 containers will certainly meet damaged ones.

    The cache holds the listing as TSV (`member<TAB>size<TAB>kind`) so a cache hit
    restores the members table *exactly*, not as a list of names with no sizes.
    """
    cache = ctx.cache_for(LIST_ARCHIVE)
    key = cache.key(source_hash, params=f"members<={MAX_MEMBERS}")
    if cache.has(key):
        text, meta = cache.get(key) or ("", {})
        members = _parse_member_tsv(text)
        ctx.store.add_members(_bytes_of(rel), members)
        return TaskOutcome(DONE, "cache", key, len(members))

    lower = rel.lower()
    # The extension decides when it names a format; when it does not, the bytes do. Both
    # directions were measured on this corpus: 52 files named `.rar` are zip archives, and
    # 12 448 containers with no extension at all are gzip or zip. A name is a hint the file
    # can contradict, and read-only it costs one bounded read to ask.
    engine = None
    if lower.endswith(ZIP_CONTAINER_EXTENSIONS):
        engine = "zip"
    elif lower.endswith(TAR_CONTAINER_EXTENSIONS):
        engine = "tar"
    elif lower.endswith(SINGLE_STREAM_EXTENSIONS):
        engine = "stream"
    else:
        try:
            with ctx.mount.open_readonly(rel, max_bytes=CONTAINER_SNIFF_BYTES) as probe:
                by_content = _container_by_content(probe)
        except (OSError, ReadOnlyViolation) as e:
            return TaskOutcome(FAILED, type(e).__name__)
        if by_content is None:
            return TaskOutcome(SKIPPED, "no_listing_engine")
        engine = {"zip": "zip", "tar": "tar", "rar": "libarchive"}.get(by_content, "stream")

    try:
        if engine == "zip":
            with ctx.mount.open_readonly(rel) as handle:
                members = _zip_members(handle)
        elif engine == "tar":
            with ctx.mount.open_readonly(rel) as handle:
                members = _tar_members(handle)
        elif engine == "libarchive":
            lister = ctx.engines.get("libarchive_members") or _libarchive_members
            members = lister(ctx.mount, rel)
        else:
            with ctx.mount.open_readonly(rel, max_bytes=1) as handle:
                members = _gzip_single_member(handle, rel)
    except LibarchiveError as e:
        # The class only: libarchive quotes the file it was reading, and `note` travels.
        return TaskOutcome(FAILED, f"libarchive:{e}")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError,
            ReadOnlyViolation, ValueError) as e:
        return TaskOutcome(FAILED, type(e).__name__)

    text = _member_tsv(members)
    meta = {
        "members": len(members),
        "declared_bytes": sum(size_ for _, size_, _ in members),
        "kinds": _kind_counts(members),
    }
    try:
        cache.put(key, text, meta)
        ctx.store.add_members(_bytes_of(rel), members)
    except (OSError, sqlite3.Error) as e:
        # Hardware and space conditions land here: a full disk, a read-only or failing
        # filesystem. The item is recorded failed — the work is simply not done yet — and
        # `DerivationCache.put` guarantees nothing partial was published (owner call,
        # 2026-09-22). The listing itself is already in hand, so a retry is cheap.
        return TaskOutcome(FAILED, type(e).__name__)
    return TaskOutcome(DONE, None, key, len(members))


def _docx_text(handle: io.BufferedReader) -> tuple[str, dict[str, Any]]:
    parts: list[str] = []
    with zipfile.ZipFile(handle) as archive:
        names = archive.namelist()
        wanted = [
            name for name in names
            if any(name.endswith(member) or name.startswith(member)
                   for member in ZIP_TEXT_MEMBERS)
        ]
        for name in sorted(wanted)[:500]:
            try:
                raw = archive.read(name)
            except (KeyError, zipfile.BadZipFile, OSError):
                continue
            parts.append(_xml_to_text(raw))
    text = "\n".join(part for part in parts if part.strip())
    return text, {"members_read": len(wanted), "engine": "zip+xml"}


def _xml_to_text(raw: bytes) -> str:
    import re

    decoded = raw.decode("utf-8", errors="replace")
    # Tags out, entities in: enough for search, and no XML dependency.
    decoded = re.sub(r"<[^>]+>", " ", decoded)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&apos;", "'")):
        decoded = decoded.replace(entity, char)
    return re.sub(r"\s+", " ", decoded).strip()


def _pdftotext(handle: io.BufferedReader) -> tuple[str, dict[str, Any]]:
    """Text layer via poppler. Empty output means the pages are images."""
    proc = subprocess.run(
        ["pdftotext", "-layout", "-", "-"],
        stdin=handle, capture_output=True, timeout=300,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    return text, {"engine": "pdftotext", "returncode": proc.returncode}


def task_extract_text(ctx: TaskContext, rel: str, size: int, source_hash: str) -> TaskOutcome:
    """Pull the words out of a document, or say that OCR is needed.

    The owner's rule: prefer a real converter, fall back to OCR only when there
    is nothing to convert. An empty text layer is therefore not a failure — it is
    a `skipped` row noting `needs_ocr`, which is the signal that queues the VLM
    task later instead of guessing here.
    """
    cache = ctx.cache_for(EXTRACT_TEXT)
    key = cache.key(source_hash)
    if cache.has(key):
        text, meta = cache.get(key) or ("", {})
        return TaskOutcome(DONE, "cache", key, len(text))

    lower = rel.lower()
    engine = ctx.engines.get("pdf") if lower.endswith(PDF_EXTENSIONS) else None
    if lower.endswith(PDF_EXTENSIONS):
        engine = engine or _pdftotext
        try:
            text, meta = engine(ctx.mount.open_readonly(rel))
        except (OSError, subprocess.SubprocessError, ReadOnlyViolation) as e:
            return TaskOutcome(FAILED, type(e).__name__)
    elif lower.endswith(ZIP_DOCUMENT_EXTENSIONS):
        engine = ctx.engines.get("zip_document") or _docx_text
        try:
            text, meta = engine(ctx.mount.open_readonly(rel))
        except (OSError, zipfile.BadZipFile, ReadOnlyViolation) as e:
            return TaskOutcome(FAILED, type(e).__name__)
    elif lower.endswith(LIBARCHIVE_DOCUMENT_EXTENSIONS):
        # A container is extracted so that its *members* can be read: RO14 serves a member
        # from the container's own extraction-cache entry, so without this a `.rar` could
        # be searched by member name and never opened.
        engine = ctx.engines.get("libarchive_text") or _libarchive_text
        try:
            text, meta = engine(ctx.mount, rel)
        except (OSError, ReadOnlyViolation, RuntimeError) as e:
            return TaskOutcome(FAILED, type(e).__name__)
    else:
        return TaskOutcome(SKIPPED, "no_extraction_engine")

    if not text.strip():
        # An archive with no text members is not a document awaiting OCR, and saying so
        # would queue it for a vision pass that has nothing to look at.
        note = ("no_text_members" if lower.endswith(LIBARCHIVE_DOCUMENT_EXTENSIONS)
                else "needs_ocr")
        return TaskOutcome(SKIPPED, note, key, 0)
    try:
        cache.put(key, text, meta)
    except (OSError, sqlite3.Error) as e:
        # Same guard as the listing path: a disk that cannot take the write is a recorded
        # failure, and never a half-written entry a reader would serve as the passage.
        return TaskOutcome(FAILED, type(e).__name__)
    # A derived document goes into the text index straight away: the words are
    # already in hand, and the chunk rows point at the cache entry rather than at
    # the container, because the container cannot be read without extracting it
    # again.
    if ctx.text_index is not None:
        try:
            ctx.text_index.add_text(
                raw=_bytes_of(rel), display=_index_display(rel), source_hash=source_hash,
                text=text.encode("utf-8"), origin=ORIGIN_CACHE,
                cache_task=EXTRACT_TEXT, cache_key=key, derived=True,
                engine=str(meta.get("engine", "")), replace=True,
            )
        except (UnicodeEncodeError, sqlite3.Error) as e:
            # The extraction is cached and useful; only the index row failed. Reported so
            # the item is counted rather than silently half-done.
            return TaskOutcome(FAILED, type(e).__name__, key, len(text))
    return TaskOutcome(DONE, None, key, len(text))


def _index_display(rel: str) -> str:
    """The display a text chunk stores for a path, in a form SQLite can hold (RO20).

    `text_chunks.display` is TEXT, and SQLite encodes TEXT as UTF-8 and refuses a lone
    surrogate outright — which is how the first repair pass died six seconds in, and how
    `mine_queue` came to hold a handful of `UnicodeEncodeError` rows for names that are not
    valid UTF-8 (Python hands those back from `scandir` with surrogate escapes).

    The path index already solved this: `CorpusIndex` stores a path twice, exactly as bytes
    in `raw` and as this surrogate-free text in `path`, and `raw_for(display)` recovers the
    bytes. Using the same rendering here means a chunk's display is the *same string*
    `entries.path` holds for that file, which is what keeps `raw_for` resolving it. A
    rendering of its own — `backslashreplace`, say — would be unique but would not match
    `entries.path`, and a display the path index does not recognise resolves to `None`, so
    the read path would answer "no such path" for a file that is sitting right there.
    """
    # Imported here rather than at module scope: `corpus.py` imports this module lazily
    # for the same reason, and the two must not become a cycle.
    from rlm_kernel.corpus import path_text

    return path_text(rel)


#: A summary is a *description* of a document, not a copy of it, so its input is capped far
#: below an index pass: past roughly 32 KiB another kilobyte of the document stops changing
#: the description, and on this host every kilobyte is paid for at ~6.6 tok/s of prompt. The
#: output bound is the same shape of decision one level down — `MAX_CONTAINER_TEXT_BYTES` is
#: what a reader may see, this is what a description may say (RO6: "a vendored `.jar` should
#: probably only be described by a single line"), and 400 tokens is already generous for a
#: line. The value is part of the cache key, so raising it re-describes rather than reuses.
MAX_SUMMARY_INPUT_BYTES = 32 * 1024
SUMMARY_MAX_TOKENS = 400

#: Below this a document *is* its own description, and the floor is a **value** judgement, not
#: a cost one. That distinction was measured rather than assumed, and the first version of
#: this comment got it wrong: at these bounds a small document is not much cheaper than a
#: large one — a short reply is bounded by decode at ~3.0 tok/s while 512 bytes of prompt
#: costs ~2 s at ~6.6 tok/s — so a floor defended as a saving would be defended falsely. It
#: says instead that describing five hundred bytes costs more attention than they are worth
#: (`docs/20260923-1800-ro6-the-handler-and-the-arithmetic-that-bounds-it.md`).
MIN_SUMMARY_INPUT_BYTES = 512


def _engine_tag(engine: Any) -> str:
    """The identity of an injected engine, as it belongs in a derivation key.

    A description written by one model must never be served as another's. Configure the
    summariser to a second model and, without this, every document the first model already
    described would be a **cache hit** — the run would look like a success, the summaries
    would be the old model's, and nothing would go red. That is the same silent substitution
    the mnemonic aliases exist to prevent, one layer down: a wrong answer that arrives fast.

    So the engine carries its own identity (`engine_tag`, set by whoever builds it) and the
    key names it. An engine without one is `default`, which is the honest answer — from here
    two untagged engines are indistinguishable, and pretending otherwise would be a
    different costume on the same failure.
    """
    tag = getattr(engine, "engine_tag", "")
    return str(tag) if tag else "default"


def _index_summary(
    ctx: TaskContext, rel: str, source_hash: str, key: str, summary: str, engine_name: str,
) -> TaskOutcome:
    """Store a description in the text index, under the **cache** origin.

    `ORIGIN_CACHE` is this repository's convention for a derived artefact, and here it is what
    makes the description exist at all: `add_text` is idempotent on `(raw, origin)` and defaults
    to the *file* origin, so a description of a document the mining windows had already indexed
    as plain text was silently dropped — and then recorded as an empty reply. Measured
    2026-09-23: two descriptions were paid for, cached, and never stored, and the queue said
    `empty_summary` about both. A lie in the queue about work that did happen is worse than a
    failure, because nothing goes red.

    A reply too short to become a chunk is not an error either: the description happened and is
    cached, so it is a `done` row carrying the reason rather than a skip that suggests nothing
    occurred.
    """
    if ctx.text_index is None:
        return TaskOutcome(DONE, "not_indexed")
    try:
        chunks = ctx.text_index.add_text(
            raw=_bytes_of(rel),
            display=_index_display(rel),
            source_hash=source_hash,
            text=summary.encode("utf-8"),
            origin=ORIGIN_CACHE,
            cache_task=SUMMARISE,
            cache_key=key,
            derived=True,
            engine=engine_name,
        )
    except (UnicodeEncodeError, sqlite3.Error) as e:
        return TaskOutcome(FAILED, type(e).__name__)
    if chunks:
        return TaskOutcome(DONE, None, None, chunks)
    # Nothing written, and the description was not empty (that is rejected before we get here):
    # the only way `add_text` returns nothing for non-empty text is that this `(raw, origin)`
    # pair is already stored, so say that rather than inventing a second explanation.
    return TaskOutcome(DONE, "already_indexed")


def task_summarise(ctx: TaskContext, rel: str, size: int, source_hash: str) -> TaskOutcome:
    """Describe one document, so the corpus can be *navigated* rather than read (RO6).

    The model is **injected**, never imported: the engine is `ctx.engines["summarise"]`, a
    callable `(text, max_tokens) -> (summary, meta)`, because the kernel has no model by
    construction and a kernel that reached for one could not be tested without it. No engine
    means a skip *with a reason*, which is the honest answer — never a fabricated
    description, and never a silent zero.

    A summary already paid for is a cache hit and costs no model call; the reply is indexed
    as **derived** text under the cache origin, so `corpus_search` can find the description
    while the document's own chunks stay its own. A cache hit still *ensures* the description
    is findable, because the index can be rebuilt or an earlier version of this task can have
    failed to store it: a description that was paid for and cannot be searched for is the
    failure this path exists to prevent. Indexing is skipped when there is no text index, but
    *the summary is still made and cached*.
    """
    engine = ctx.engines.get("summarise")
    if engine is None:
        return TaskOutcome(SKIPPED, "no_summarise_engine")
    cache = DerivationCache(ctx.cache_root, SUMMARISE)
    key = cache.key(source_hash, params=f"summary<=v1,400tok,{_engine_tag(engine)}")
    if cache.has(key):
        cached = cache.get(key)
        if cached is not None:
            summary, meta = cached
            outcome = _index_summary(ctx, rel, source_hash, key, summary,
                                    str((meta or {}).get("engine", "cache")))
            if outcome.state == FAILED:
                return outcome
        return TaskOutcome(DONE, "cache")
    if size < MIN_SUMMARY_INPUT_BYTES:
        return TaskOutcome(SKIPPED, "too_short_to_summarise")
    try:
        with ctx.mount.open_readonly(rel, max_bytes=MAX_SUMMARY_INPUT_BYTES) as handle:
            data = handle.read()
    except (OSError, ReadOnlyViolation) as e:
        return TaskOutcome(FAILED, type(e).__name__)
    # A description tolerates a decoding loss; an *address* does not, which is why this is
    # the one place bytes become text without the index's own decoder. Mislabelled bytes
    # cost a slightly worse sentence here, and nothing at all downstream.
    text = data.decode("utf-8", "replace")
    try:
        summary, meta = engine(text, SUMMARY_MAX_TOKENS)
    except Exception as e:  # noqa: BLE001 — one document must never end a pass (RO19/RO20)
        return TaskOutcome(FAILED, type(e).__name__)
    summary = (summary or "").strip()
    if not summary:
        # An empty reply is a model failure, not a description: caching it would turn one
        # bad minute into a permanent claim that the document says nothing.
        return TaskOutcome(SKIPPED, "empty_summary")
    cache.put(
        key,
        summary,
        {
            "source": source_hash,
            "engine": str((meta or {}).get("engine", "summarise")),
            "input_bytes": len(data),
            "max_tokens": SUMMARY_MAX_TOKENS,
        },
    )
    if ctx.text_index is None:
        return TaskOutcome(DONE, "not_indexed")
    return _index_summary(ctx, rel, source_hash, key, summary,
                          str((meta or {}).get("engine", "summarise")))


def task_index_text(ctx: TaskContext, rel: str, size: int, source_hash: str) -> TaskOutcome:
    """Index a plain text file's own bytes.

    These files need no conversion: their bytes *are* the text, so this is the
    cheapest task in the queue and the one that covers most of the corpus
    (2,882,822 files). The read is capped at `MAX_INDEX_BYTES`, and a file that
    hit the cap says `truncated` rather than pretending to be fully indexed.
    """
    if ctx.text_index is None:
        return TaskOutcome(SKIPPED, "no_text_index")
    raw = _bytes_of(rel)
    if ctx.text_index.has_source(raw):
        return TaskOutcome(DONE, "cache")
    try:
        with ctx.mount.open_readonly(rel, max_bytes=MAX_INDEX_BYTES) as handle:
            data = handle.read()
    except (OSError, ReadOnlyViolation) as e:
        return TaskOutcome(FAILED, type(e).__name__)
    try:
        chunks = ctx.text_index.add_text(
            raw=raw, display=_index_display(rel), source_hash=source_hash, text=data,
        )
    except (UnicodeEncodeError, sqlite3.Error) as e:
        # One unstorable item is a recorded failure, never the end of a pass (RO19's
        # repair pass learned the same lesson, and RO20's `index_text 11 failed` is what
        # the old behaviour left behind: a truncated run with no tail to count).
        return TaskOutcome(FAILED, type(e).__name__)
    if not chunks:
        return TaskOutcome(SKIPPED, "no_text")
    note = "truncated" if size > len(data) else None
    return TaskOutcome(DONE, note, None, chunks)


TASK_HANDLERS: dict[str, Callable[[TaskContext, str, int, str], TaskOutcome]] = {
    LIST_ARCHIVE: task_list_archive,
    EXTRACT_TEXT: task_extract_text,
    INDEX_TEXT: task_index_text,
    # Deliberately in the table but **not** in `IMPLEMENTED_TASKS`: a handler exists, but a
    # default mining window must not pick summarisation up until an engine is wired and its
    # cost measured — `mine plan` still counts it as unimplemented, which is the truthful
    # description of a task no window can yet be asked to run.
    SUMMARISE: task_summarise,
}


#: Container extensions, grouped by the engine that can open them. Named constants rather
#: than literals at the call site, because the list is a *policy* about what counts as a
#: container and it grew by measurement (RO15 2026-09-21): of 84 361 containers the queue
#: knew, 19 446 had no engine that claimed them, and the head of that list was shapes the
#: zip engine already handles under a different suffix.
#:
#: So this is not one list but three, and the distinction is which engine parses it:
ZIP_CONTAINER_EXTENSIONS = (
    ".zip", ".jar", ".whl", ".apk", ".xpi", ".crx", ".vsix",
    ".epub", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    # Zip-shaped archives the same engine already opens, added by measurement. `.aar`,
    # `.war` and `.ear` are the Java/Android build outputs beside the 38 197 `.jar` this
    # corpus already lists; `.nupkg` is the .NET one. None needs a new dependency — only
    # the extension list was narrow.
    ".aar", ".war", ".ear", ".nupkg", ".jmod", ".egg", ".deb", ".rpm",
)
TAR_CONTAINER_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz",
                            ".txz", ".tar.zst")
SINGLE_STREAM_EXTENSIONS = (".gz", ".bz2", ".xz", ".zst")

#: How much of a container to read to decide what it is. Enough for a tar's `ustar` marker
#: at byte 257 and a zip's first local header; small enough that probing costs a read no
#: larger than a chunk.
CONTAINER_SNIFF_BYTES = 600

#: How far into a compressed stream to look for a tar header, once the outer codec is known.
#: A tar's `ustar` sits at decompressed offset 257, so this only has to clear that.
WRAPPED_TAR_PROBE_BYTES = 1_024

#: Magic numbers that name a container format, checked in order. A *claim* about a format,
#: not a heuristic: a byte pattern that is not here stays `no_listing_engine` rather than
#: being tried against every parser, which would turn 19 446 recorded skips into recorded
#: failures and tell a reader less.
CONTAINER_MAGIC: tuple[tuple[str, tuple[bytes, ...]], ...] = (
    ("zip", (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")),
    # RAR is routed by *content*, never by its name, and the reason is measured: 52 of the
    # corpus's 136 `.rar` are zips wearing that name, and content routing already lists
    # them through the zip engine. Claiming the extension would send those to libarchive
    # (which would cope) and would make the name override the bytes, which is the rule this
    # table exists to keep. RAR4 and RAR5 both: `Rar!\x1a\x07\x00` and the RAR5 form.
    ("rar", (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")),
    ("gzip", (b"\x1f\x8b",)),
    ("bzip2", (b"BZh",)),
    ("xz", (b"\xfd7zXZ\x00",)),
    ("zstd", (b"\x28\xb5\x2f\xfd",)),
)

#: Where libarchive lives. Present on the corpus host and already used for `.tar`; the
#: extraction engine streams members to stdout, so no temporary directory is ever created.
BSDTAR = "bsdtar"

#: Extensions claimed for extraction by name, because their `kind` is `archive` and the
#: extraction task is what makes their members *readable* (RO14 serves a member from the
#: container's own extraction-cache entry). `.rar` is here rather than in
#: `ZIP_CONTAINER_EXTENSIONS` deliberately: it is a *listing* extension only by content.
LIBARCHIVE_DOCUMENT_EXTENSIONS = (".rar",)

#: Members inside a libarchive container whose text is worth extracting. A judgement, and a
#: narrow one: the alternative — reading every member and asking whether it decodes — spends
#: a container's whole budget on things like `.png` payloads that decode to noise.
LIBARCHIVE_TEXT_MEMBER_EXTENSIONS = (
    ".txt", ".text", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".xml",
    ".html", ".htm", ".xhtml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".sql",
    ".js", ".mjs", ".cjs", ".ts", ".py", ".rb", ".pl", ".php", ".java", ".kt", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".swift", ".sh", ".bash", ".zsh", ".bat",
    ".ps1", ".lua", ".r", ".m", ".tex", ".svg", ".gitignore", ".properties",
)

#: How much member text one container may contribute, and how many members are read. Bounds
#: the work, not the enumeration: every member is still listed (the owner asked for that);
#: this only caps what is *extracted* into the cache.
MAX_CONTAINER_TEXT_BYTES = 8 * 1024 * 1024
MAX_CONTAINER_TEXT_MEMBERS = 2_000

#: Free space a cache write requires before it is attempted. A write that cannot finish
#: leaves a truncated entry, and a truncated entry is worse than a miss: a reader would
#: serve it as the passage. So the write is refused up front, the item is recorded failed,
#: and the work is simply not done yet.
MIN_FREE_BYTES = 256 * 1024 * 1024


class NoSpaceLeftError(OSError):
    """Raised instead of attempting a write the disk cannot take."""


class LibarchiveError(RuntimeError):
    """`bsdtar` refused the container. The message is a *class*, never text it printed.

    libarchive's own diagnostics quote the file it was reading, so they are classified
    here and the class is what reaches the queue's `note` column. `note` is derived state
    that travels; a path is not.
    """


def _require_space(root: Path) -> None:
    """Refuse a cache write when the filesystem cannot take it (owner call, 2026-09-22)."""
    try:
        free = shutil.disk_usage(root if Path(root).exists() else Path(root).anchor).free
    except OSError:  # pragma: no cover - a filesystem that cannot be asked
        return
    if free < MIN_FREE_BYTES:
        raise NoSpaceLeftError(28, "not enough free space for a cache write")


def _libarchive_class(stderr: bytes) -> str:
    """A class for a `bsdtar` failure, from its message, without ever echoing it."""
    low = stderr.decode("utf-8", "replace").lower()
    if "passphrase" in low or "password" in low or "encrypt" in low:
        return "encrypted"
    if "solid" in low:
        return "solid"
    if "unexpected end" in low or "truncated" in low or "damaged" in low:
        return "truncated"
    if "unsupported" in low or "not supported" in low:
        return "unsupported"
    if "unrecognized" in low or "malformed" in low:
        return "unrecognized-format"
    return "libarchive-error"


def _libarchive_run(mount: LocalTreeMount, rel: str, flags: list[str],
                    members: Iterable[str] = (), *, timeout: float = 900.0) -> bytes:
    """Run `bsdtar` over the mount's *descriptor*, never over a path.

    libarchive needs a seekable input and the mount is the only thing allowed to open a
    corpus file, so the mount's descriptor is passed through (`/dev/fd/N` plus `pass_fds`).
    Measured on 2026-09-22: four RARs listed this way (1, 6 859, 3 857 and 41 members), and
    the verbose form parsed 10 800 of 10 800 rows.

    **The archive goes immediately after the flags**, because bsdtar's grammar is
    `bsdtar <flags> archive [members]`. The first version appended it last, so an extraction
    read the *member name* as the archive: the live run read none of the 3 215 text members
    it had just listed, and the empty result was reported as `no_text_members` — a wrong
    answer shaped exactly like a fact about the archives.

    The child's stderr is classified into a class rather than propagated: it quotes the file
    it was reading, and `note` travels.
    """
    with mount.open_readonly(rel) as handle:
        fd = handle.fileno()
        argv = [BSDTAR, *flags, f"/dev/fd/{fd}", *members]
        try:
            run = subprocess.run(argv, pass_fds=(fd,), capture_output=True, timeout=timeout)
        except FileNotFoundError as e:  # no bsdtar on this host
            raise LibarchiveError("no-libarchive") from e
        except subprocess.TimeoutExpired as e:
            raise LibarchiveError("timeout") from e
    if run.returncode != 0:
        raise LibarchiveError(_libarchive_class(run.stderr))
    return run.stdout


def _libarchive_members(
    mount: LocalTreeMount, rel: str,
) -> list[tuple[str, int, str]]:
    """List a container's members through libarchive: `(member, size, kind)`.

    The verbose form carries the sizes `archive_members` needs, and its layout was
    measured rather than assumed: 10 800 of 10 800 rows split into exactly nine fields with
    the size at index 4. A row that does not match is **skipped**, not guessed — a member
    with an invented size is a wrong number in a table that travels.
    """
    stdout = _libarchive_run(mount, rel, ["-tvf"])
    members: list[tuple[str, int, str]] = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(None, 8)
        if len(parts) != 9 or not parts[4].isdigit():
            continue
        mode, name = parts[0], parts[8].strip()
        kind = "dir" if mode.startswith("d") else ("link" if mode.startswith("l") else "file")
        if kind == "link":
            # The verbose form renders a symlink as `name -> target`; the member is `name`,
            # and storing the arrow text would invent a member that is not in the archive.
            name = name.split(" -> ", 1)[0].strip()
        if not name:
            continue
        members.append((name, int(parts[4]), kind))
        if len(members) >= MAX_MEMBERS:
            break
    return members


def _member_is_text(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(LIBARCHIVE_TEXT_MEMBER_EXTENSIONS)


def _libarchive_text(mount: LocalTreeMount, rel: str) -> tuple[str, dict[str, Any]]:
    """Extract the text of a container's members, one member at a time, to stdout.

    Each member is streamed with `-xO`, so **no temporary file is ever created** — which is
    the whole of the cleanup story for extraction, and the reason the owner's "clean up
    what you extracted" is satisfied by construction rather than by a sweeper.

    Bounded twice: by `MAX_CONTAINER_TEXT_MEMBERS` and by `MAX_CONTAINER_TEXT_BYTES`, and a
    container that hits either says `truncated` in its metadata rather than presenting a
    partial extraction as the whole.
    """
    members = _libarchive_members(mount, rel)
    parts: list[str] = []
    used = 0
    read = 0
    skipped: list[str] = []
    truncated = False
    for name, size, kind in members:
        if kind != "file" or not _member_is_text(name):
            continue
        if read >= MAX_CONTAINER_TEXT_MEMBERS or used >= MAX_CONTAINER_TEXT_BYTES:
            truncated = True
            break
        remaining = MAX_CONTAINER_TEXT_BYTES - used
        try:
            raw = _libarchive_run(mount, rel, ["-xOf"], [name], timeout=600.0)
        except LibarchiveError as e:
            # One unreadable member does not end the container: it is recorded as skipped
            # and the rest are still read, the same rule the listing task follows for a
            # damaged container.
            skipped.append(str(e))
            continue
        if len(raw) > remaining:
            raw = raw[:remaining]
            truncated = True
        parts.append(f"===== {name} =====\n" + raw.decode("utf-8", "replace"))
        used += len(raw)
        read += 1
    if read == 0 and skipped:
        # Every text member failed to read. Reporting that as "no text members" would be a
        # fact-shaped lie about the archive: the truth is that the extraction did not work,
        # and the first live run of this code did exactly that — 129 containers recorded
        # `no_text_members` while 3 215 members each failed with the archive in the wrong
        # argument position. An empty result from *no candidates* is a fact; an empty result
        # from *every candidate failing* is a failure.
        raise LibarchiveError(f"all-members-unreadable:{len(skipped)}")
    text = "\n".join(parts)
    meta = {
        "engine": "libarchive",
        "members_listed": len(members),
        "members_read": read,
        "declared_bytes": used,
        "skipped_members": len(skipped),
        "truncated": truncated,
    }
    return text, meta


def _wrapped_tar(handle: io.BufferedReader, head: bytes) -> bool:
    """Whether a compressed stream's payload is a tar, not just a single stream.

    A `.tgz`'s gzip wraps a tar, so its members are the tar's rather than one opaque blob.
    The marker cannot be seen in the compressed head — `ustar` lives at offset 257 of the
    **decompressed** bytes — so a bounded prefix is decompressed, and only the codecs the
    outer head implies are tried. Every failure to decompress answers `False`: a stream that
    cannot be read this far is reported as the stream it is, which is the honest shallow
    description rather than a guess about what it holds.
    """
    import bz2
    import lzma
    import zlib

    payload: bytes | None = None
    try:
        if head.startswith(b"\x1f\x8b"):
            payload = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(
                head, WRAPPED_TAR_PROBE_BYTES)
        elif head.startswith(b"BZh"):
            payload = bz2.BZ2Decompressor().decompress(head, WRAPPED_TAR_PROBE_BYTES)
        elif head.startswith(b"\xfd7zXZ\x00"):
            payload = lzma.LZMADecompressor().decompress(head, WRAPPED_TAR_PROBE_BYTES)
    except Exception:  # noqa: BLE001 - an unreadable wrapper is not a tar
        return False
    return bool(payload) and len(payload) > 262 and payload[257:262] == b"ustar"


def _container_by_content(handle: io.BufferedReader) -> str | None:
    """Which engine a container's own bytes claim, or None.

    `zip`/`gzip`/`bzip2`/`xz`/`zstd` are *stream* formats — one member, or a directory for
    zip — while `tar` is the shape that commonly appears **inside** one of them: a `.tgz`'s
    gzip wraps a tar, and its members are the tar's, not one opaque blob. So a compressed
    stream whose decompressed head carries tar's `ustar` marker is a tar container, and the
    marker is there precisely because the tar reader wrote it.
    """
    head = handle.read(CONTAINER_SNIFF_BYTES)
    for name, magics in CONTAINER_MAGIC:
        if any(head.startswith(magic) for magic in magics):
            if name in ("gzip", "bzip2", "xz") and _wrapped_tar(handle, head):
                return "tar"
            return name
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    return None


def _kind_counts(members: Iterable[tuple[str, int, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, _, kind in members:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _bytes_of(rel: str) -> bytes:
    return rel.encode("utf-8", "surrogateescape")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _member_tsv(members: Iterable[tuple[str, int, str]]) -> str:
    return "\n".join(f"{name}\t{size}\t{kind}" for name, size, kind in members)


def _parse_member_tsv(text: str) -> list[tuple[str, int, str]]:
    """Inverse of `_member_tsv`, tolerant of a hand-inspected file."""
    out: list[tuple[str, int, str]] = []
    for line in text.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        size = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        kind = parts[2] if len(parts) > 2 else "file"
        out.append((name, size, kind))
    return out


# ── The windowed worker ───────────────────────────────────────────────────

def source_hash_for(conn: sqlite3.Connection, raw: bytes) -> str:
    row = conn.execute(
        "SELECT head_hash FROM classification WHERE raw = ?", (raw,)
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def run_queue(
    *,
    store: MineStore,
    conn: sqlite3.Connection,
    mount: LocalTreeMount,
    cache_root: Path,
    tasks: Iterable[str] = IMPLEMENTED_TASKS,
    engines: dict[str, Any] | None = None,
    budget_seconds: float | None = None,
    deadline: float | None = None,
    max_items: int | None = None,
    pause_file: Path | None = None,
    lock_file: Path | None = None,
    text_index: Any = None,
    coverage_scan: bool = True,
    progress: Callable[[MineStats], None] | None = None,
    progress_every: int = 100,
    now: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> MineRun:
    """Process queued items until a budget, a deadline, a pause or the queue ends.

    Every item commits when it lands, so the answer to "stop now" is: the item in
    flight is the most that can be lost. The stop reason is returned as text so a
    window that ended because the owner came back is distinguishable from one that
    ran out of work.

    **The budget bounds the items, not the closing work.** A window checks the clock
    between items, so the trailing coverage publication runs past it — measured
    2026-09-22, a 25-minute window took 76 minutes. `coverage_scan=False` drops that
    publication's expensive half for a window in a chain; the cheap fingerprints are
    recorded either way.
    """
    started = now()
    stats = MineStats()
    ctx = TaskContext(mount=mount, store=store, cache_root=Path(cache_root),
                      engines=dict(engines or {}), text_index=text_index)
    wanted = [task for task in tasks]
    stop_reason = "queue_empty"

    while True:
        if pause_file is not None and Path(pause_file).exists():
            stop_reason = "paused"
            break
        if budget_seconds is not None and now() - started >= budget_seconds:
            stop_reason = "budget"
            break
        if deadline is not None and wall_clock() >= deadline:
            stop_reason = "deadline"
            break
        if max_items is not None and stats.processed >= max_items:
            stop_reason = "max_items"
            break

        batch = store.claim(wanted, limit=1)
        if not batch:
            stop_reason = "queue_empty"
            break
        raw, task, _priority = batch[0]
        rel = raw.decode("utf-8", "surrogateescape")
        handler = TASK_HANDLERS.get(task)
        if handler is None:
            store.finish(raw, task, SKIPPED, "no_handler")
            stats.note(task, SKIPPED)
            continue

        source_hash = source_hash_for(conn, raw)
        if not source_hash:
            store.finish(raw, task, SKIPPED, "unclassified")
            stats.note(task, SKIPPED)
            continue

        size = _size_for(conn, raw)
        try:
            outcome = handler(ctx, rel, size, source_hash)
        except Exception as e:  # pragma: no cover - defensive
            outcome = TaskOutcome(FAILED, type(e).__name__)
        if outcome.note == "cache":
            stats.cache_hits += 1
        store.finish(raw, task, outcome.state, outcome.note)
        stats.note(task, outcome.state)
        if lock_file is not None:
            refresh_lock(lock_file)
        if progress is not None and stats.processed % max(1, progress_every) == 0:
            progress(stats)

    if progress is not None:
        progress(stats)
    # Leave a coverage snapshot behind: the search path reads it instead of
    # counting 25M chunks inside a 120 s cell (see `publish_coverage_snapshot`).
    # The heartbeat rides along, because this is the phase that outlives the item
    # loop: without it the lock goes stale while the worker is demonstrably working.
    def heartbeat() -> None:
        if lock_file is not None:
            refresh_lock(lock_file)

    publish_coverage_snapshot(conn, expensive=coverage_scan, on_progress=heartbeat)
    return MineRun(stats=stats, stop_reason=stop_reason, seconds=now() - started)


def _size_for(conn: sqlite3.Connection, raw: bytes) -> int:
    row = conn.execute("SELECT size FROM entries WHERE raw = ?", (raw,)).fetchone()
    return int(row[0]) if row else 0


def publish_coverage_snapshot(
    conn: sqlite3.Connection,
    *,
    expensive: bool = True,
    on_progress: Callable[[], None] | None = None,
) -> bool:
    """Compute and store the coverage a search will quote. Best effort, slow.

    The scan behind this takes ~16 minutes on the real index (25M chunks), which
    is why a *search* never does it: a corpus cell has 120 s and dies, which is
    how a live run burned two hours and answered nothing. A mining window is hours
    long and already slow, so it pays this price once — at the start when nothing
    has been published, and at the end, so the snapshot a finished window leaves
    behind describes the index that window produced.

    **`expensive=False` skips the whole-index scan** and records only the cheap
    fingerprints, for a *chained* window that should pay for the scan once rather
    than once per window. Measured 2026-09-22: a 25-minute window that listed 7 437
    containers then spent ~51 minutes here, cold, so a chain of six windows would
    have spent five hours counting to produce snapshots nobody read in between. A
    skipped scan publishes no coverage fingerprint, because claiming a number was
    current when it was not measured is the one thing this must never do.

    `on_progress` is called, rate-limited, from *inside* the scan: the window's lock
    heartbeats per item (`refresh_lock`), so the long non-item phases are exactly
    where a live worker starts to look dead and a second one takes its lock.

    When it does scan, it also publishes the member count, so that `mine status` can
    quote a number instead of counting a 30M-row table itself.

    Never raises: a window that cannot publish coverage must still finish its work.
    """
    try:
        from rlm_kernel.freshness import capture

        if expensive:
            from rlm_kernel.textindex import TextIndex

            text = TextIndex(conn)
            text.ensure()
            with _pulse(conn, on_progress):
                coverage = text.coverage()
                members = _count_members(conn)
            text.publish_coverage(coverage)
            record_member_count(conn, members)
            # RO15: the snapshot is the moment the text index's numbers are known, so this is
            # also the moment to fingerprint the caches derived from it. `freshness.capture`
            # is best effort and never raises: a window that cannot record a fingerprint still
            # finishes its work, and the cache then honestly reports `unknown`.
            capture(conn, "coverage", {
                "sources_indexed": coverage.get("sources_indexed"),
                "chunks": coverage.get("chunks"),
            })
        counts = {task: int(conn.execute(
            "SELECT COUNT(*) FROM mine_queue WHERE task = ? AND state = 'done'",
            (task,)).fetchone()[0]) for task in (LIST_ARCHIVE, EXTRACT_TEXT)}
        capture(conn, "archive_listings", {"listings_done": counts[LIST_ARCHIVE]})
        capture(conn, "extraction", {"extracted_done": counts[EXTRACT_TEXT]})
        return True
    except Exception:  # pragma: no cover - defensive
        return False


@contextmanager
def _pulse(
    conn: sqlite3.Connection,
    on_progress: Callable[[], None] | None,
    *,
    every: int = 1_000,
    min_seconds: float = 30.0,
) -> Iterator[None]:
    """Call `on_progress`, rate-limited, from inside one long SQLite statement.

    `set_progress_handler` is the only hook that runs *during* a single scan, which is
    what a heartbeat needs — a lock touched only between items goes stale during the
    ~16-minute coverage count and the next worker takes it while this one is still
    working. The handler is called every `every` VM steps and must return 0; returning
    non-zero would abort the statement it is watching, which is how the first sketch of
    this turned the count into an exception.
    """
    if on_progress is None:
        yield
        return
    last = [0.0]

    def handler() -> int:
        now = time.monotonic()
        if now - last[0] >= min_seconds:
            last[0] = now
            on_progress()
        return 0

    conn.set_progress_handler(handler, every)
    try:
        yield
    finally:
        conn.set_progress_handler(None, 0)


def _count_members(conn: sqlite3.Connection) -> int:
    """Count the member rows, in a form a heartbeat can see. 0 when there is no table."""
    try:
        row = conn.execute(MEMBER_COUNT_SQL).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def record_member_count(conn: sqlite3.Connection, members: int) -> None:
    """Publish a member count measured now, for every cheap reader to quote."""
    try:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (MEMBER_COUNT_KEY, str(int(members))))
        conn.commit()
    except sqlite3.Error:  # pragma: no cover - a meta table that cannot be written
        pass


def published_member_count(conn: sqlite3.Connection) -> int | None:
    """The published member count, or None. Issues no statement over `archive_members`."""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?",
                           (MEMBER_COUNT_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None



# ── The single-worker lock ────────────────────────────────────────────────

#: A lock whose heartbeat is older than this is stale and may be taken over.
DEFAULT_LOCK_STALE_SECONDS = 300.0


def acquire_lock(lock_file: Path, *, stale_after: float = DEFAULT_LOCK_STALE_SECONDS,
                 now: Callable[[], float] = time.time) -> int | None:
    """Take the mining lock, or return None if another worker holds it.

    One miner at a time is not tidiness: the router on this box serves 4B models
    from 15 GiB of RAM, and a second heavy consumer is how the machine starts
    swapping.

    **Liveness is decided by a heartbeat, never by probing the pid.** The worker
    refreshes this file as it works (`refresh_lock`), so a lock that has not been
    touched for `stale_after` seconds belongs to a dead or stopped worker and can
    be taken over. The obvious alternative — checking whether the recorded pid
    exists — is what this used to do, via `os.kill(pid, 0)`, and that is a
    footgun: on POSIX signal 0 is a harmless probe, but on Windows Python's
    `os.kill` calls `TerminateProcess`, so the "probe" **kills the process it is
    asking about**. It killed this project's own test run, and the harness that
    was running it, before anyone noticed. The pid stays in the file as
    information; nothing here acts on it. A guard test scans this module and
    fails if `os.kill` ever comes back.
    """
    lock_file = Path(lock_file)
    if lock_file.exists():
        try:
            age = now() - lock_file.stat().st_mtime
        except OSError:
            age = None
        if age is not None and age < stale_after:
            return None
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(f"{os.getpid()} {time.time():.0f}\n", encoding="utf-8")
    return os.getpid()


def refresh_lock(lock_file: Path) -> None:
    """Say "still working" so a long run is not mistaken for a dead one."""
    try:
        Path(lock_file).touch()
    except OSError:
        pass


def release_lock(lock_file: Path) -> None:
    try:
        Path(lock_file).unlink()
    except OSError:
        pass


def _members_line(members: int | None) -> str:
    """The member count for a status line, or an honest `unknown`.

    Never counts: `mine status` used to scan ~30M rows to print this, which is how a
    window that had already committed all of its work looked like a hung one for an
    hour. `rlm corpus counters --refresh` — or a window run without
    `--no-coverage-scan` — is the deliberate way to produce the number.
    """
    if members is None:
        return ("unknown (counting them scans the whole table; publish one with "
                "`rlm corpus counters --refresh`)")
    return f"{members:,}"


def format_status(status: dict[str, Any], *, cache: dict[str, int] | None = None) -> str:
    lines = ["mining queue:"]
    for task, states in sorted(status["queued_by_task"].items()):
        parts = ", ".join(f"{state}={n:,}" for state, n in sorted(states.items()))
        lines.append(f"  {task:<16}{parts}")
    if not status["queued_by_task"]:
        lines.append("  (empty — run `rlm mine plan` to enqueue from the map)")
    lines.append(f"archive members recorded: {_members_line(status['archive_members'])}")
    if cache is not None:
        lines.append(f"cache: {cache['entries']:,} entries, "
                     f"{cache['text_bytes'] / 1024 / 1024:.1f} MiB of derived text")
    return "\n".join(lines)
