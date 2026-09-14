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
import sqlite3
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

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
            CREATE INDEX IF NOT EXISTS mine_queue_pending
                ON mine_queue(task, state, priority);
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
        row = self._conn.execute("SELECT COUNT(*) FROM archive_members").fetchone()
        return int(row[0]) if row else 0

    # ── Reporting ─────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        by_task: dict[str, dict[str, int]] = {}
        for task, state, n in self._conn.execute(
            "SELECT task, state, COUNT(*) FROM mine_queue GROUP BY task, state"
        ):
            by_task.setdefault(str(task), {})[str(state)] = int(n)
        return {
            "queued_by_task": by_task,
            "archive_members": self.member_count(),
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
        meta_path, text_path = self._paths(key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        payload = {**meta, "task": self._root.name, "mining_version": MINING_VERSION,
                   "cached_at": time.time(), "chars": len(text)}
        meta_path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                             encoding="utf-8")
        os.chmod(meta_path, 0o600)
        os.chmod(text_path, 0o600)

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
    collect(
        EXTRACT_TEXT, PRIORITY_EXTRACT_DOCUMENT,
        f"c.kind IN ('document', 'archive') AND ({doc_like})", patterns,
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
    try:
        if lower.endswith((".zip", ".jar", ".whl", ".apk", ".xpi", ".crx", ".vsix",
                           ".epub", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")):
            with ctx.mount.open_readonly(rel) as handle:
                members = _zip_members(handle)
        elif lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz",
                             ".txz", ".tar.zst")):
            with ctx.mount.open_readonly(rel) as handle:
                members = _tar_members(handle)
        elif lower.endswith((".gz", ".bz2", ".xz", ".zst")):
            with ctx.mount.open_readonly(rel, max_bytes=1) as handle:
                members = _gzip_single_member(handle, rel)
        else:
            return TaskOutcome(SKIPPED, "no_listing_engine")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError,
            ReadOnlyViolation, ValueError) as e:
        return TaskOutcome(FAILED, type(e).__name__)

    text = _member_tsv(members)
    meta = {
        "members": len(members),
        "declared_bytes": sum(size_ for _, size_, _ in members),
        "kinds": _kind_counts(members),
    }
    cache.put(key, text, meta)
    ctx.store.add_members(_bytes_of(rel), members)
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
    else:
        return TaskOutcome(SKIPPED, "no_extraction_engine")

    if not text.strip():
        return TaskOutcome(SKIPPED, "needs_ocr", key, 0)
    cache.put(key, text, meta)
    # A derived document goes into the text index straight away: the words are
    # already in hand, and the chunk rows point at the cache entry rather than at
    # the container, because the container cannot be read without extracting it
    # again.
    if ctx.text_index is not None:
        ctx.text_index.add_text(
            raw=_bytes_of(rel), display=rel, source_hash=source_hash,
            text=text.encode("utf-8"), origin=ORIGIN_CACHE,
            cache_task=EXTRACT_TEXT, cache_key=key, derived=True,
            engine=str(meta.get("engine", "")), replace=True,
        )
    return TaskOutcome(DONE, None, key, len(text))


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
    chunks = ctx.text_index.add_text(
        raw=raw, display=rel, source_hash=source_hash, text=data,
    )
    if not chunks:
        return TaskOutcome(SKIPPED, "no_text")
    note = "truncated" if size > len(data) else None
    return TaskOutcome(DONE, note, None, chunks)


TASK_HANDLERS: dict[str, Callable[[TaskContext, str, int, str], TaskOutcome]] = {
    LIST_ARCHIVE: task_list_archive,
    EXTRACT_TEXT: task_extract_text,
    INDEX_TEXT: task_index_text,
}


def _kind_counts(members: Iterable[tuple[str, int, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _, _, kind in members:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _bytes_of(rel: str) -> bytes:
    return rel.encode("utf-8", "surrogateescape")


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
    budget_seconds: float | None = None,
    deadline: float | None = None,
    max_items: int | None = None,
    pause_file: Path | None = None,
    lock_file: Path | None = None,
    text_index: Any = None,
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
    """
    started = now()
    stats = MineStats()
    ctx = TaskContext(mount=mount, store=store, cache_root=Path(cache_root),
                      text_index=text_index)
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
    return MineRun(stats=stats, stop_reason=stop_reason, seconds=now() - started)


def _size_for(conn: sqlite3.Connection, raw: bytes) -> int:
    row = conn.execute("SELECT size FROM entries WHERE raw = ?", (raw,)).fetchone()
    return int(row[0]) if row else 0


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


def format_status(status: dict[str, Any], *, cache: dict[str, int] | None = None) -> str:
    lines = ["mining queue:"]
    for task, states in sorted(status["queued_by_task"].items()):
        parts = ", ".join(f"{state}={n:,}" for state, n in sorted(states.items()))
        lines.append(f"  {task:<16}{parts}")
    if not status["queued_by_task"]:
        lines.append("  (empty — run `rlm mine plan` to enqueue from the map)")
    lines.append(f"archive members recorded: {status['archive_members']:,}")
    if cache is not None:
        lines.append(f"cache: {cache['entries']:,} entries, "
                     f"{cache['text_bytes'] / 1024 / 1024:.1f} MiB of derived text")
    return "\n".join(lines)
