"""Tests for rlm_kernel.memory — MemoryManager add, compact, decay."""

from __future__ import annotations

from pathlib import Path

import pytest

from rlm_kernel.memory import MemoryManager, decay_score
from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus
from rlm_kernel.vault import LocalVault


from datetime import datetime, timedelta, timezone


# ── decay_score ──────────────────────────────────────────────────────────────

class TestDecayScore:
    def test_initial_score_is_one(self):
        """Fresh memory with recent access returns score near 1."""
        now = datetime.now(timezone.utc)
        score = decay_score(0, last_access=now)
        assert 0.99 <= score <= 1.0

    def test_score_decays_over_time(self):
        """Memory accessed 1 hour ago has reduced score."""
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        score = decay_score(0, last_access=one_hour_ago)
        assert 0.0 < score < 1.0
# ── add with regex fallback (no LLM) ────────────────────────────────────────

class TestAddRegexPath:
    def test_add_creates_note_with_extracted_metadata(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        text = "## Introduction to Python\n\nPython is a high-level programming language."
        page = mm.add(text=text)

        assert page.frontmatter.kind == PageKind.NOTE
        assert page.frontmatter.title == "Introduction to Python"
        assert "high-level" in page.frontmatter.summary.lower()
        assert page.frontmatter.status == PageStatus.ACTIVE
        assert page.path.startswith("memory/notes/")

        # Verify persisted
        stored = vault.get(page.path)
        assert stored is not None
        assert stored.body == text

    def test_add_with_explicit_tags(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        text = "## Caching Strategy\n\nUse Redis for hot data."
        page = mm.add(text=text, tags=["redis", "performance"])

        assert "redis" in page.frontmatter.tags
        assert "performance" in page.frontmatter.tags
        # Extracted keywords also present
        assert any("caching" in t for t in page.frontmatter.tags) or any(
            "strategy" in t for t in page.frontmatter.tags
        )

    def test_add_no_vault_raises(self, temp_vault):
        mm = MemoryManager()  # no vault set
        with pytest.raises(ValueError, match="No vault provided"):
            mm.add(text="test")


# ── add with LLM stub ───────────────────────────────────────────────────────

class TestAddLLMPath:
    def test_add_with_llm_stub(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        def stub_llm(prompt: str) -> str:
            # Return structured response
            return (
                "Summary: Python is a versatile programming language.\n"
                "Keywords: python, programming, language"
            )

        text = "## Python\n\nPython is widely used in data science and web development."
        page = mm.add(text=text, llm=stub_llm)

        assert page.frontmatter.summary == "Python is a versatile programming language."
        assert "python" in page.frontmatter.tags
        assert "programming" in page.frontmatter.tags
        assert "language" in page.frontmatter.tags

    def test_add_with_llm_fallback_on_bad_response(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        def bad_llm(prompt: str) -> str:
            return "This response has no summary or keywords line."

        text = "## Go Programming\n\nGo is a statically typed language."
        page = mm.add(text=text, llm=bad_llm)

        # Falls back to regex extraction
        assert page.frontmatter.title == "Go Programming"
        assert len(page.frontmatter.summary) > 0
        assert len(page.frontmatter.tags) > 0


# ── compact dry-run ──────────────────────────────────────────────────────────

class TestCompactDryRun:
    def test_dry_run_returns_candidates_no_writes(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        # Add two similar notes
        mm.add(text="## Python Tips\n\nUse list comprehensions.")
        mm.add(text="## Python Tricks\n\nUse generator expressions.")

        candidates = mm.compact(similarity_threshold=0.6, dry_run=True)
        assert isinstance(candidates, list)
        # Both titles are similar ("Python Tips" vs "Python Tricks")
        assert len(candidates) >= 1
        c = candidates[0]
        assert "title_a" in c and "title_b" in c and "similarity" in c
        assert c["similarity"] >= 0.6

        # Verify no files were modified (statuses unchanged)
        notes = vault.list(prefix="memory/notes")
        for n in notes:
            if n.path.endswith("core-memory.md"):
                continue
            assert n.frontmatter.status == PageStatus.ACTIVE

    def test_dry_run_no_candidates_below_threshold(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        mm.add(text="## Python\n\nPython content.")
        mm.add(text="## JavaScript\n\nJavaScript content.")

        candidates = mm.compact(similarity_threshold=0.95, dry_run=True)
        assert candidates == []

    def test_compact_with_confirm_performs_merge(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        p1 = mm.add(text="## Python Tips\n\nUse list comprehensions.")
        p2 = mm.add(text="## Python Tricks\n\nAlso use walrus operator.")

        merge_count = mm.compact(similarity_threshold=0.6, dry_run=False)
        assert isinstance(merge_count, int)
        assert merge_count >= 1

        # Reload pages — one should be superseded
        note1 = vault.get(p1.path)
        note2 = vault.get(p2.path)
        statuses = {note1.frontmatter.status, note2.frontmatter.status}
        assert PageStatus.SUPERSEDED in statuses

    def test_default_threshold_is_085(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        mm.add(text="## Alpha\n\nContent A.")
        mm.add(text="## Beta\n\nContent B.")

        # Default threshold 0.85 — "Alpha" and "Beta" should NOT match
        candidates = mm.compact(dry_run=True)
        assert candidates == []
