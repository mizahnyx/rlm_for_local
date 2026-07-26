"""SQLite index with FTS5 — derived, rebuildable from vault pages (§4).

The index is always rebuildable from markdown (the source of truth).
Tables:
  pages(id, path, kind, name, title, summary, hash, version, status, updated)
  links(src, dst) — wikilinks between pages
  tags(page_id, tag)
  fts_pages — FTS5 external-content table over (title, summary, body)
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from rlm_kernel.schema import Page, PageKind
from rlm_kernel.vault import VaultStore


WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    hash TEXT,
    idx_hash TEXT,
    fts_rowid INTEGER,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    updated TEXT
);

CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    PRIMARY KEY (src, dst)
);

CREATE TABLE IF NOT EXISTS tags (
    page_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (page_id, tag)
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_pages USING fts5(
    path,
    kind,
    name,
    title,
    summary,
    body
);
"""


class Index:
    """SQLite-backed full-text and metadata index over vault pages."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._run_migrations()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema ────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA_SQL)
        # Set user_version so migrations know they're already applied (W1)
        self.conn.execute("PRAGMA user_version = 2")
        self.conn.commit()
    def _checkpoint(self) -> None:
        """Checkpoint WAL after bulk writes (F4)."""
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    _MIGRATIONS: list[tuple[int, str]] = [
        (2, "ALTER TABLE pages ADD COLUMN fts_rowid INTEGER"),
    ]

    def _run_migrations(self) -> None:
        """Apply pending schema migrations (W1).

        Only runs if the database has been initialized (pages table exists).
        Fresh databases get schema via _ensure_schema's CREATE TABLE IF NOT EXISTS.
        """
        # Guard: skip if database hasn't been initialized yet
        table_check = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pages'"
        ).fetchone()
        if table_check is None:
            return

        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for target_version, sql in self._MIGRATIONS:
            if current < target_version:
                self._conn.execute(sql)
                self._conn.execute(f"PRAGMA user_version = {target_version}")
                current = target_version
        self._conn.commit()

    # ── Build / rebuild ───────────────────────────────────────────────────

    def build(self, vault: VaultStore) -> None:
        """Full rebuild from vault pages. Idempotent.

        Uses for_update=False — skips per-page DELETEs since tables are
        emptied up front. This avoids the FTS5 full-scan quadratic (F1).
        """
        self._ensure_schema()

        # Clear existing data — all at once, no per-page DELETEs needed
        self.conn.execute("DELETE FROM fts_pages")
        self.conn.execute("DELETE FROM tags")
        self.conn.execute("DELETE FROM links")
        self.conn.execute("DELETE FROM pages")

        pages = vault.list()
        for page in pages:
            self._index_page(page, for_update=False)
        self.conn.commit()
        self._checkpoint()
    def _index_page(self, page: Page, for_update: bool = False) -> None:
        """Insert or replace a single page in the index.

        Args:
            page: The page to index.
            for_update: If True, this is an incremental update — per-page
                DELETEs use rowid-keyed access (O(1) for FTS). If False
                (fresh build), all tables were emptied up front so
                per-page DELETEs are skipped entirely (F1 — avoids the
                FTS5 full-scan quadratic).
        """
        fm = page.frontmatter

        # On updates, clean up old data using indexed access paths
        if for_update:
            old = self.conn.execute(
                "SELECT id, fts_rowid FROM pages WHERE path = ?", (page.path,)
            ).fetchone()
            if old:
                if old["id"] != fm.id:
                    self.conn.execute("DELETE FROM tags WHERE page_id = ?", (old["id"],))
                self.conn.execute("DELETE FROM tags WHERE page_id = ?", (fm.id,))
                self.conn.execute("DELETE FROM links WHERE src = ?", (page.path,))
                # F2: rowid-keyed FTS DELETE — indexed in FTS5, O(1)
                if old["fts_rowid"] is not None:
                    self.conn.execute(
                        "DELETE FROM fts_pages WHERE rowid = ?", (old["fts_rowid"],)
                    )

        # Insert/update pages row
        self.conn.execute(
            """INSERT OR REPLACE INTO pages (id, path, kind, name, title, summary,
               hash, idx_hash, fts_rowid, version, status, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fm.id, page.path, fm.kind.value, fm.name, fm.title,
                fm.summary, fm.hash, page.content_hash, None, fm.version,
                fm.status.value,
                fm.updated.isoformat() if fm.updated else None,
            ),
        )
        # Tags
        for tag in fm.tags:
            self.conn.execute(
                "INSERT OR IGNORE INTO tags (page_id, tag) VALUES (?, ?)",
                (fm.id, tag),
            )
        # Links
        for link_text in WIKILINK_PATTERN.findall(page.body):
            target = link_text.split("|")[0].split("#")[0].strip()
            if target:
                self.conn.execute(
                    "INSERT OR IGNORE INTO links (src, dst) VALUES (?, ?)",
                    (page.path, target),
                )
        # FTS insert — capture rowid for future rowid-keyed DELETEs (F2)
        cursor = self.conn.execute(
            """INSERT INTO fts_pages (path, kind, name, title, summary, body)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (page.path, fm.kind.value, fm.name, fm.title,
             fm.summary, page.body),
        )
        # Store FTS rowid for O(1) updates
        self.conn.execute(
            "UPDATE pages SET fts_rowid = ? WHERE path = ?",
            (cursor.lastrowid, page.path),
        )

    # ── Queries ───────────────────────────────────────────────────────────

    def page_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def get_page(self, path: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_backlinks(self, path: str) -> list[str]:
        """Return paths of pages that link to this page via wikilinks."""
        rows = self.conn.execute(
            "SELECT src FROM links WHERE dst = ? ORDER BY src", (path,)
        ).fetchall()
        return [r["src"] for r in rows]

    def fts_search(self, query: str, limit: int = 40,
                   kinds: list[str] | None = None) -> list[dict[str, Any]]:
        """BM25-ranked FTS5 search. Returns list of {name, kind, score, ...}."""
        if not query.strip():
            return []

        # FTS5 query with BM25 ranking
        # Escape special FTS5 characters
        safe = query.replace('"', '""')
        fts_query = f'"{safe}"'

        where_kinds = ""
        params: list[Any] = [fts_query, limit]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            where_kinds = f"AND kind IN ({placeholders})"
            params = [fts_query] + kinds + [limit]

        # Standalone FTS5: all columns are directly on fts_pages
        sql = f"""
            SELECT path, kind, name, title, summary, rank AS score
            FROM fts_pages
            WHERE fts_pages MATCH ?
              {where_kinds}
            ORDER BY rank
            LIMIT ?
        """
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Incremental update ────────────────────────────────────────────────

    def reindex_delta(self, vault: VaultStore) -> None:
        """Reconcile index with vault: hash-compare, add/update/remove."""
        self._ensure_schema()

        # Get all current vault pages
        vault_pages = {p.path: p for p in vault.list()}

        # Get all indexed pages
        indexed = {
            row["path"]: dict(row)
            for row in self.conn.execute("SELECT * FROM pages").fetchall()
        }

        # Remove deleted — use fts_rowid for O(1) FTS DELETE (F2)
        for path in set(indexed) - set(vault_pages):
            row = indexed[path]
            self.conn.execute("DELETE FROM pages WHERE path = ?", (path,))
            self.conn.execute("DELETE FROM tags WHERE page_id = ?", (row["id"],))
            self.conn.execute("DELETE FROM links WHERE src = ?", (path,))
            if row.get("fts_rowid") is not None:
                self.conn.execute("DELETE FROM fts_pages WHERE rowid = ?", (row["fts_rowid"],))
            else:
                self.conn.execute("DELETE FROM fts_pages WHERE path = ?", (path,))

        # Add new / update changed — use for_update=True (F1)
        for path, page in vault_pages.items():
            if path not in indexed:
                self._index_page(page, for_update=True)
            elif page.content_hash != (indexed[path].get("idx_hash") or ""):
                self._index_page(page, for_update=True)

        self.conn.commit()
        self._checkpoint()

    def list_paths(self, kind: str | None = None, status: str = "active") -> list[str]:
        """Return page paths matching kind/status from the index (avoiding full-tree parse).

        This is the fast path for KernelBridge and search — O(1) SQL query
        instead of O(n) full-vault parse per call.
        """
        if kind is not None:
            rows = self.conn.execute(
                "SELECT path FROM pages WHERE kind = ? AND status = ? ORDER BY path",
                (kind, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT path FROM pages WHERE status = ? ORDER BY path",
                (status,),
            ).fetchall()
        return [r["path"] for r in rows]


def rebuild_index(vault: VaultStore, db_path: Path) -> Index:
    """Convenience: build an index from a vault and return it."""
    idx = Index(db_path)
    idx.build(vault)
    return idx
