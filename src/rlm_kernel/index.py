"""SQLite index with FTS5 — derived, rebuildable from vault pages (§4).

The index is always rebuildable from markdown (the source of truth).
Tables:
  pages(id, path, kind, name, title, summary, hash, version, status, updated)
  links(src, dst) — wikilinks between pages
  tags(page_id, tag)
  fts_pages — FTS5 external-content table over (title, summary, body)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rlm_kernel.schema import Page, PageKind
from rlm_kernel.vault import VaultStore


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    hash TEXT,
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
        return self._conn


    def __enter__(self) -> Index:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema ────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()

    # ── Build / rebuild ───────────────────────────────────────────────────

    def build(self, vault: VaultStore) -> None:
        """Full rebuild from vault pages. Idempotent."""
        self._ensure_schema()

        # Clear existing data
        self.conn.execute("DELETE FROM fts_pages")
        self.conn.execute("DELETE FROM tags")
        self.conn.execute("DELETE FROM links")
        self.conn.execute("DELETE FROM pages")

        pages = vault.list()
        for page in pages:
            self._index_page(page)
        self.conn.commit()

    def _index_page(self, page: Page) -> None:
        """Insert or replace a single page in the index."""
        fm = page.frontmatter
        self.conn.execute(
            """INSERT OR REPLACE INTO pages (id, path, kind, name, title, summary, hash, version, status, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fm.id, page.path, fm.kind.value, fm.name, fm.title,
                fm.summary, fm.hash, fm.version, fm.status.value,
                fm.updated.isoformat() if fm.updated else None,
            ),
        )
        # Tags
        self.conn.execute("DELETE FROM tags WHERE page_id = ?", (fm.id,))
        for tag in fm.tags:
            self.conn.execute(
                "INSERT OR IGNORE INTO tags (page_id, tag) VALUES (?, ?)",
                (fm.id, tag),
            )
        # FTS — standalone table, direct insert
        self.conn.execute(
            """INSERT INTO fts_pages (path, kind, name, title, summary, body)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (page.path, fm.kind.value, fm.name, fm.title,
             fm.summary, page.body),
        )

    # ── Queries ───────────────────────────────────────────────────────────

    def page_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def get_page(self, path: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pages WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

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

        # Remove deleted
        for path in set(indexed) - set(vault_pages):
            row = indexed[path]
            self.conn.execute("DELETE FROM pages WHERE path = ?", (path,))
            self.conn.execute("DELETE FROM tags WHERE page_id = ?", (row["id"],))
            self.conn.execute("DELETE FROM fts_pages WHERE path = ?", (path,))

        # Add new / update changed
        for path, page in vault_pages.items():
            if path not in indexed:
                self._index_page(page)
            elif page.content_hash != (indexed[path].get("hash") or ""):
                # Hash changed — reindex
                self._index_page(page)

        self.conn.commit()


def rebuild_index(vault: VaultStore, db_path: Path) -> Index:
    """Convenience: build an index from a vault and return it."""
    idx = Index(db_path)
    idx.build(vault)
    return idx
