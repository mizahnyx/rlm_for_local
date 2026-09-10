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


# ── R12: decay-weighted search ordering and hit bookkeeping ──────────────────

def _memory_note(
    vault: LocalVault,
    name: str,
    title: str,
    body: str,
    *,
    access_count: int = 0,
    last_access: datetime | None = None,
    age_days: float = 0.0,
) -> str:
    """Write a memory note with explicit access bookkeeping. Returns its path."""
    older = datetime.now(timezone.utc) - timedelta(days=age_days)
    fm = Frontmatter(
        kind=PageKind.NOTE,
        name=name,
        title=title,
        summary="A memory note for decay ordering.",
        tags=["memory"],
        status=PageStatus.ACTIVE,
        created=older,
        updated=older,
        access_count=access_count,
        last_access=last_access,
    )
    path = f"memory/notes/{name}.md"
    vault.put(Page(frontmatter=fm, body=body), path)
    return path


class TestDecayOrderedSearch:
    """R12: decay_score must actually influence memory search ordering."""

    def test_recently_accessed_note_outranks_stale_note(self, temp_vault):
        """Two notes with identical relevance: the fresh one must rank first.

        BM25 alone cannot separate them (same body, same token counts), so a
        first result of the stale note means decay is not being applied.
        """
        from rlm_kernel.index import rebuild_index

        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        now = datetime.now(timezone.utc)

        body = "widgets are useful; widgets appear here twice."
        stale_path = _memory_note(
            vault, "stale-note", "Alpha Note", body,
            access_count=0, last_access=None, age_days=90.0,
        )
        fresh_path = _memory_note(
            vault, "fresh-note", "Beta Note", body,
            access_count=7, last_access=now - timedelta(minutes=1), age_days=90.0,
        )

        idx_path = temp_vault / ".index" / "meta.sqlite"
        idx = rebuild_index(vault, idx_path)
        idx.close()

        results = mm.search(vault, idx_path, "widgets", k=5)

        paths = [r["path"] for r in results]
        assert set(paths) == {stale_path, fresh_path}, paths
        assert paths[0] == fresh_path, (
            f"stale note outranked the freshly-accessed note: {paths}"
        )
        assert results[0]["decay"] > results[1]["decay"]

    def test_search_records_access_through_vault_put(self, temp_vault):
        """A hit persists access_count/last_access on disk (atomic vault path)."""
        from rlm_kernel.index import rebuild_index

        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        path = _memory_note(vault, "counted", "Counted Note", "widgets here")

        idx_path = temp_vault / ".index" / "meta.sqlite"
        rebuild_index(vault, idx_path).close()

        assert vault.get(path).frontmatter.access_count == 0

        mm.search(vault, idx_path, "widgets", k=5)
        after_first = vault.get(path).frontmatter
        assert after_first.access_count == 1
        assert after_first.last_access is not None, "last_access was not persisted"

        mm.search(vault, idx_path, "widgets", k=5)
        assert vault.get(path).frontmatter.access_count == 2

    def test_search_does_not_bump_notes_it_did_not_return(self, temp_vault):
        """Only returned hits are touched — no bookkeeping on the whole corpus."""
        from rlm_kernel.index import rebuild_index

        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        mm.add(text="## Widget Notes\n\nwidgets widgets widgets")
        untouched = _memory_note(vault, "other", "Other Note", "unrelated content")

        idx_path = temp_vault / ".index" / "meta.sqlite"
        rebuild_index(vault, idx_path).close()

        results = mm.search(vault, idx_path, "widgets", k=1)
        assert len(results) == 1
        assert vault.get(untouched).frontmatter.access_count == 0


class TestForgetCombinesFilters:
    """R12: forget(query, older_than) must AND the two filters."""

    def test_forget_with_both_filters_requires_both(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        old_matching = _memory_note(
            vault, "old-match", "Old Match", "obsolete widget details", age_days=30.0
        )
        old_other = _memory_note(
            vault, "old-other", "Old Other", "unrelated archived text", age_days=30.0
        )
        new_matching = _memory_note(
            vault, "new-match", "New Match", "fresh widget details", age_days=0.0
        )

        affected = mm.forget(query="widget", older_than=timedelta(days=7))

        assert affected == 1
        assert vault.get(old_matching).frontmatter.status == PageStatus.DEPRECATED
        assert vault.get(old_other).frontmatter.status == PageStatus.ACTIVE
        assert vault.get(new_matching).frontmatter.status == PageStatus.ACTIVE

    def test_forget_query_only_still_works(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        old = _memory_note(vault, "a-old", "A Old", "widget text", age_days=30.0)
        new = _memory_note(vault, "b-new", "B New", "widget text", age_days=0.0)

        assert mm.forget(query="widget") == 2
        assert vault.get(old).frontmatter.status == PageStatus.DEPRECATED
        assert vault.get(new).frontmatter.status == PageStatus.DEPRECATED

    def test_forget_age_only_still_works(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        old = _memory_note(vault, "a-old", "A Old", "text", age_days=30.0)
        new = _memory_note(vault, "b-new", "B New", "text", age_days=0.0)

        assert mm.forget(older_than=timedelta(days=7)) == 1
        assert vault.get(old).frontmatter.status == PageStatus.DEPRECATED
        assert vault.get(new).frontmatter.status == PageStatus.ACTIVE

    def test_forget_never_touches_core_memory(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        mm.write_core(vault, "Durable identity.")
        _memory_note(vault, "a-old", "A Old", "widget text", age_days=30.0)

        affected = mm.forget(query="widget", older_than=timedelta(days=7))

        assert affected == 1
        core = vault.get("memory/notes/core-memory.md")
        assert core.frontmatter.status == PageStatus.ACTIVE


class TestCompactDryRunMatchesMerge:
    """R12: dry-run and merge must share one clustering function."""

    def test_dry_run_count_equals_merge_count(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)

        # Two clusters of near-duplicate titles, plus one unrelated note.
        # Distinct titles (not just distinct bodies) — mm.add() slugs from the
        # title, so identical titles would overwrite one another.
        mm.add(text="## Python Tips\n\nUse list comprehensions.")
        mm.add(text="## Python Tricks\n\nUse generator expressions.")
        mm.add(text="## Python Tip Sheet\n\nUse the walrus operator.")
        mm.add(text="## Cooking Pasta\n\nBoil water first.")
        mm.add(text="## Cooking Pasta Guide\n\nSalt the water.")
        mm.add(text="## Kubernetes Networking\n\nServices and ingress.")

        candidates = mm.compact(similarity_threshold=0.65, dry_run=True)
        merge_count = mm.compact(similarity_threshold=0.65, dry_run=False)

        assert len(candidates) == merge_count, (
            f"dry-run reported {len(candidates)} merges but {merge_count} were performed"
        )
        assert merge_count == 3, (
            f"expected 2 absorbs in the 3-note cluster + 1 in the pair, got {merge_count}"
        )

    def test_dry_run_reports_keeper_and_absorbed_title(self, temp_vault):
        vault = LocalVault(temp_vault, init_git=False)
        mm = MemoryManager(vault=vault)
        mm.add(text="## Python Tips\n\nUse list comprehensions.")
        mm.add(text="## Python Tricks\n\nUse generator expressions.")

        candidates = mm.compact(similarity_threshold=0.8, dry_run=True)
        assert len(candidates) == 1
        c = candidates[0]
        assert set(c) == {"title_a", "title_b", "similarity"}
        assert c["similarity"] >= 0.8
        # title_a is the surviving keeper; title_b is the absorbed duplicate.
        assert {c["title_a"], c["title_b"]} == {"Python Tips", "Python Tricks"}

        merge_count = mm.compact(similarity_threshold=0.8, dry_run=False)
        assert merge_count == len(candidates)
        loser = None
        for title in ("Python Tips", "Python Tricks"):
            if title != c["title_a"]:
                loser = title
        statuses = {
            p.frontmatter.title: p.frontmatter.status
            for p in vault.list(prefix="memory/notes")
        }
        assert statuses[loser] == PageStatus.SUPERSEDED
