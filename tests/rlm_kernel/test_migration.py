"""Tests for schema migration (W1) — Index must migrate old databases with fts_rowid column."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rlm_kernel.index import Index


class TestSchemaMigration:
    """W1: old-schema databases must be migrated when opened by new code."""

    def _create_old_schema_db(self, db_path: Path) -> None:
        """Create a database with the pre-fts_rowid schema (user_version=1)."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                hash TEXT,
                idx_hash TEXT,
                version INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                updated TEXT
            );
            CREATE TABLE IF NOT EXISTS links (
                src TEXT NOT NULL, dst TEXT NOT NULL,
                PRIMARY KEY (src, dst)
            );
            CREATE TABLE IF NOT EXISTS tags (
                page_id TEXT NOT NULL, tag TEXT NOT NULL,
                PRIMARY KEY (page_id, tag)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_pages USING fts5(
                path, kind, name, title, summary, body
            );
            PRAGMA user_version = 1;
        """)
        # Insert a row to test migration preserves data
        conn.execute(
            "INSERT INTO pages (id, path, kind, name, title, summary, idx_hash, version, status, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("01OLD", "definitions/old-page.md", "definition", "old-page",
             "Old Page", "An old page.", "sha256:abc123", 1, "active", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

    def test_migration_adds_fts_rowid_column(self, temp_vault):
        """Opening an old-schema index adds fts_rowid column, preserves data."""
        db_path = temp_vault / ".index" / "meta.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_old_schema_db(db_path)

        # Open with current Index class — migration should run
        idx = Index(db_path)

        # Verify column exists
        columns = [r["name"] for r in idx.conn.execute("PRAGMA table_info('pages')").fetchall()]
        assert "fts_rowid" in columns, "Migration did not add fts_rowid column"

        # Verify user_version is now 2
        uv = idx.conn.execute("PRAGMA user_version").fetchone()[0]
        assert uv == 2, f"Expected user_version=2, got {uv}"

        # Verify old data preserved
        row = idx.conn.execute("SELECT * FROM pages WHERE path = ?",
                               ("definitions/old-page.md",)).fetchone()
        assert row is not None, "Old data not preserved after migration"
        assert row["id"] == "01OLD"
        assert row["name"] == "old-page"
        # fts_rowid should be NULL for old rows (no FTS entry exists yet)
        assert row["fts_rowid"] is None

        idx.close()

    def test_migration_is_idempotent(self, temp_vault):
        """Running migration on already-migrated DB is a no-op."""
        db_path = temp_vault / ".index" / "meta.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_old_schema_db(db_path)

        # First open — migrates
        idx1 = Index(db_path)
        uv1 = idx1.conn.execute("PRAGMA user_version").fetchone()[0]
        data1 = dict(idx1.conn.execute(
            "SELECT * FROM pages WHERE path = ?",
            ("definitions/old-page.md",),
        ).fetchone())
        idx1.close()

        # Second open — should be no-op
        idx2 = Index(db_path)
        uv2 = idx2.conn.execute("PRAGMA user_version").fetchone()[0]
        data2 = dict(idx2.conn.execute(
            "SELECT * FROM pages WHERE path = ?",
            ("definitions/old-page.md",),
        ).fetchone())
        idx2.close()

        assert uv1 == uv2 == 2
        assert data1["id"] == data2["id"]

    def test_reindex_delta_works_after_migration(self, temp_vault):
        """After migration, reindex_delta works without error on old-schema index."""
        from rlm_kernel.schema import Frontmatter, Page
        from rlm_kernel.vault import LocalVault

        db_path = temp_vault / ".index" / "meta.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_old_schema_db(db_path)

        vault = LocalVault(temp_vault, init_git=False)
        # Add a page that matches what the old row references
        fm = Frontmatter(schema=1, kind="definition", name="old-page",
                         title="Old Page Updated", summary="Updated summary.")
        page = Page(fm, "# Updated\n\nBody.")
        vault.put(page, "definitions/old-page.md")

        # Open with migration, run reindex_delta
        idx = Index(db_path)
        idx.reindex_delta(vault)  # Must not crash
        # After reindex_delta, the page should have an fts_rowid
        row = idx.conn.execute(
            "SELECT fts_rowid FROM pages WHERE path = ?",
            ("definitions/old-page.md",),
        ).fetchone()
        assert row is not None
        assert row["fts_rowid"] is not None, "reindex_delta should populate fts_rowid"
        idx.close()
