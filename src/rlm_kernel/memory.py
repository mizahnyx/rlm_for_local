"""Memory layer — A-MEM-style note, gist, forget, compact (§9, K3).

The memory layer turns raw text into persistent notes and manages
their lifecycle: decay scoring, forgetting, and compaction.
"""

from __future__ import annotations

import math
import re
import hashlib
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from rlm_kernel.schema import (
    Frontmatter,
    Page,
    PageKind,
    PageStatus,
    HelperDef,
    parse_page,
    extract_helper_code,
    extract_helper_signature,
)
from rlm_kernel.vault import VaultStore, LocalVault
from rlm_kernel.index import Index, rebuild_index
from rlm_kernel.search import search_vault, SearchBackend

# ── Constants ────────────────────────────────────────────────────────────────

_MEMORY_PREFIX = "memory/notes"
_CORE_MEMORY_PATH = f"{_MEMORY_PREFIX}/core-memory.md"
_MEMORY_KINDS = ["note", "topic", "cache"]

# R12: search fetches a wider BM25 pool than it returns, then re-orders it by
# decay — otherwise a k-limited BM25 cut would already have discarded the
# freshly-accessed notes before decay ever saw them.
_SEARCH_POOL_MULTIPLIER = 4

_SENTENCE_RE = re.compile(r"([^.!?\n]+[.!?]?)")
_WORD_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── Decay Arithmetic ─────────────────────────────────────────────────────────

def decay_score(
    access_count: int,
    last_access: datetime | None = None,
    created: datetime | None = None,
) -> float:
    """Ebbinghaus-style forgetting curve score.

    Returns a retention score in [0, 1] where 1.0 is perfect recall
    and values near 0 indicate near-total forgetting.

    Formula: R = exp(-t / S) where:
        t = seconds since last_access (or created, if never accessed)
        S = base_strength * (1 + ln(1 + access_count))

    base_strength defaults to 86400 s (1 day), so an un-revisited note
    decays to ~37% after one day.

    Args:
        access_count: number of times the memory has been accessed.
        last_access: timestamp of last access (defaults to created).
        created: creation timestamp (required if last_access is None).

    Returns:
        float in [0.0, 1.0].
    """
    now = datetime.now(timezone.utc)
    ref = last_access if last_access is not None else created
    if ref is None:
        return 1.0  # no timestamp → assume fresh
    t_seconds = max(0.0, (now - ref).total_seconds())

    base_s = 86400.0  # 1 day in seconds
    strength = base_s * (1.0 + math.log(1.0 + access_count))

    return math.exp(-t_seconds / strength)


# ── Text extraction helpers ──────────────────────────────────────────────────

def _extract_title(text: str) -> str:
    """Extract a title from raw text: first markdown heading or first line."""
    heading_match = re.match(r"^#+\s+(.+)$", text.strip(), re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()[:256]
    first_line = text.strip().split("\n")[0].strip()
    if len(first_line) > 4:
        return first_line[:256]
    return "untitled"


def _extract_summary(text: str, max_len: int = 200) -> str:
    """Extract a summary: first sentence, truncated to max_len."""
    stripped = text.strip()
    # Skip leading markdown headings for summary extraction
    body = re.sub(r"^#+\s+.*\n", "", stripped, count=1).strip()
    if not body:
        body = stripped
    m = _SENTENCE_RE.search(body)
    if m:
        sentence = m.group(1).strip()
        if sentence:
            return sentence[:max_len]
    return body[:max_len]


def _extract_keywords(text: str, max_tags: int = 5) -> list[str]:
    """Extract capitalized words as keyword tags."""
    words: list[str] = []
    seen: set[str] = set()
    # Also extract words from headings
    for heading in re.finditer(r"^#+\s+(.+)", text, re.MULTILINE):
        for w in _WORD_RE.findall(heading.group(1)):
            lower = w.lower()
            if lower not in seen and len(lower) > 2:
                seen.add(lower)
                words.append(lower)
    for w in _WORD_RE.findall(text):
        lower = w.lower()
        if lower not in seen and len(lower) > 2:
            seen.add(lower)
            words.append(lower)
    return words[:max_tags]


def _slugify(name: str) -> str:
    """Turn a title into a filesystem-safe slug."""
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug[:64].strip("-") or "untitled"


def _extract_via_llm(llm: Any, text: str) -> tuple[str, list[str]]:
    """Extract summary and keywords using an LLM callable.

    Args:
        llm: A callable ``prompt → str``.
        text: Raw text to summarize.

    Returns:
        A tuple of ``(summary: str, keywords: list[str])``.
    """
    prompt = (
        "Extract a one-sentence summary (max 200 chars) and up to 5 "
        "lowercase keyword tags from the following text.\n"
        "Respond with exactly two lines:\n"
        "Summary: <summary>\n"
        "Keywords: <keyword1, keyword2, ...>\n\n"
        f"Text:\n{text}"
    )
    response = llm(prompt)

    # Parse the structured response
    summary = ""
    keywords: list[str] = []
    for line in response.strip().split("\n"):
        if line.lower().startswith("summary:"):
            summary = line[len("summary:"):].strip()
        elif line.lower().startswith("keywords:"):
            kw_part = line[len("keywords:"):].strip()
            keywords = [k.strip().lower() for k in kw_part.split(",") if k.strip()]

    if not summary:
        summary = _extract_summary(text)  # fallback
    if not keywords:
        keywords = _extract_keywords(text)  # fallback

    return summary, keywords


# ── Core memory ──────────────────────────────────────────────────────────────

def _get_core_memory_summary(vault: VaultStore) -> str | None:
    """Read core-memory.md and return its summary, or None."""
    page = vault.get(_CORE_MEMORY_PATH)
    if page is None:
        return None
    return page.frontmatter.summary


# ── Compaction clustering ────────────────────────────────────────────────────

def _cluster_notes(
    notes: list[Page], similarity_threshold: float
) -> list[list[Page]]:
    """Cluster notes by title similarity, newest anchor first.

    Single shared implementation for both the dry-run report and the merge
    (R12): the two paths cannot disagree about what would be merged because
    they consume the same groups. Each group is ordered newest-first, so
    ``group[0]`` is the keeper and every later member is absorbed.

    Args:
        notes: candidate note pages (core memory already excluded).
        similarity_threshold: minimum SequenceMatcher ratio for a merge.

    Returns:
        List of groups; groups of length 1 are non-merging singletons.
    """
    ordered = sorted(notes, key=lambda p: p.frontmatter.updated, reverse=True)
    groups: list[list[Page]] = []

    for note in ordered:
        for group in groups:
            ratio = SequenceMatcher(
                None,
                group[0].frontmatter.title.lower(),
                note.frontmatter.title.lower(),
            ).ratio()
            if ratio >= similarity_threshold:
                group.append(note)
                break
        else:
            groups.append([note])

    return groups


def _title_similarity(a: Page, b: Page) -> float:
    """SequenceMatcher ratio of two pages' titles (lowercased)."""
    return SequenceMatcher(
        None, a.frontmatter.title.lower(), b.frontmatter.title.lower()
    ).ratio()


# ── MemoryManager ────────────────────────────────────────────────────────────

class MemoryManager:
    """Manages the memory lifecycle: add, search, note, forget, compact.

    Can be used with explicit per-call vault/index_path or with
    constructor defaults for convenience.
    """

    def __init__(
        self,
        vault: VaultStore | None = None,
        index_path: Path | None = None,
    ) -> None:
        self._vault = vault
        self._index_path = Path(index_path) if index_path else None

    # ── add ───────────────────────────────────────────────────────────────

    def add(
        self,
        vault: VaultStore | None = None,
        text: str = "",
        tags: list[str] | None = None,
        llm: Any | None = None,
    ) -> Page:
        """Create a memory note from raw text.

        Extracts title, summary, and keywords. When *llm* is provided
        (a callable ``prompt → str``), it is used for summary and keyword
        extraction via a template prompt. Otherwise regex extraction is
        the fallback.

        Stores the page under memory/notes/<slug>.md.

        Args:
            vault: vault store (uses constructor default if omitted).
            text: raw text to memoize.
            tags: optional explicit tags (merged with extracted keywords).
            llm: optional callable for LLM-based extraction.

        Returns:
            The created Page.
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        title = _extract_title(text)

        if llm is not None and callable(llm):
            summary, keywords = _extract_via_llm(llm, text)
        else:
            summary = _extract_summary(text)
            keywords = _extract_keywords(text)

        if tags:
            for t in tags:
                if t.lower() not in keywords:
                    keywords.append(t.lower())

        slug = _slugify(title)
        path = f"{_MEMORY_PREFIX}/{slug}.md"

        now = datetime.now(timezone.utc)
        fm = Frontmatter(
            kind=PageKind.NOTE,
            name=slug,
            title=title,
            summary=summary,
            tags=keywords,
            status=PageStatus.ACTIVE,
            created=now,
            updated=now,
        )
        page = Page(frontmatter=fm, body=text.strip())
        v.put(page, path)
        return page

    # ── search ────────────────────────────────────────────────────────────

    def search(
        self,
        vault: VaultStore | None = None,
        index_path: Path | None = None,
        query: str = "",
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory notes, topics, and caches.

        Results are BM25-retrieved over a widened pool, then re-ranked by
        ``decay_score`` multiplied into the relevance term (R12): the decay
        curve only means something if it can actually reorder what search
        returns. Each returned hit records the access (``access_count += 1``,
        ``last_access = now``) through the same atomic ``vault.put`` path used
        by every other write, so the next search has real inputs.

        Args:
            vault: vault store.
            index_path: path to the index database.
            query: free-text search query.
            k: max results.

        Returns:
            List of result cards (with a ``decay`` field) ordered by
            decay-weighted relevance, with the core-memory summary injected
            in the first result's metadata when available.
        """
        v = vault if vault is not None else self._vault
        ip = Path(index_path) if index_path else self._index_path
        if v is None or ip is None:
            raise ValueError("vault and index_path are required")

        results = search_vault(
            v, ip, query, k=max(k * _SEARCH_POOL_MULTIPLIER, k), kinds=_MEMORY_KINDS,
        )

        # Re-rank the BM25 pool by decay-weighted relevance.
        now = datetime.now(timezone.utc)
        scored: list[tuple[float, float, dict[str, Any], Page]] = []
        for r in results:
            page = v.get(r.get("path", ""))
            if page is None:
                continue
            fm = page.frontmatter
            d = decay_score(
                fm.access_count, last_access=fm.last_access, created=fm.created,
            )
            rank = float(r.get("score") or 0.0)
            relevance = 1.0 / (1.0 + abs(rank))  # BM25 rank: more negative = better
            scored.append((relevance * d, d, r, page))

        scored.sort(key=lambda item: item[0], reverse=True)

        ordered: list[dict[str, Any]] = []
        for _, d, r, page in scored[:k]:
            card = dict(r)
            card["decay"] = round(d, 6)
            ordered.append(card)
            # Hit bookkeeping — same atomic write path as every other mutation.
            try:
                page.frontmatter.access_count = (page.frontmatter.access_count or 0) + 1
                page.frontmatter.last_access = now
                v.put(page, page.path)
            except Exception:
                # A read-only or vanished page must not break search.
                pass

        # Inject core-memory summary as metadata on first result
        core_summary = _get_core_memory_summary(v)
        if core_summary and ordered:
            ordered[0]["core_memory"] = core_summary

        return ordered

    # ── note (A-MEM gisting) ──────────────────────────────────────────────

    def note(
        self,
        vault: VaultStore | None = None,
        chunk: str = "",
    ) -> Page:
        """Explicit gisting: distill a chunk into a compact note.

        A-MEM-style: the caller explicitly requests gisting of raw context.
        The summary is the first sentence of the chunk; the body is the
        full chunk preserved verbatim.

        Args:
            vault: vault store.
            chunk: raw text to gist.

        Returns:
            The created gist Page.
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        title = _extract_title(chunk)
        summary = _extract_summary(chunk)
        keywords = _extract_keywords(chunk)
        slug = _slugify(title)
        path = f"{_MEMORY_PREFIX}/{slug}.md"

        now = datetime.now(timezone.utc)
        fm = Frontmatter(
            kind=PageKind.NOTE,
            name=slug,
            title=title,
            summary=summary,
            tags=keywords,
            status=PageStatus.ACTIVE,
            created=now,
            updated=now,
        )
        page = Page(frontmatter=fm, body=chunk.strip())
        v.put(page, path)
        return page

    # ── forget ────────────────────────────────────────────────────────────

    def forget(
        self,
        vault: VaultStore | None = None,
        query: str | None = None,
        older_than: timedelta | None = None,
    ) -> int:
        """Forget memories: delete or mark deprecated.

        If *query* is given, only notes matching the query text are eligible.
        If *older_than* is given, only notes whose updated timestamp is older
        than the delta are eligible. When both are given they are **combined
        with AND** — a note must match the query *and* be old enough.

        Args:
            vault: vault store.
            query: free-text search to select notes to forget.
            older_than: age threshold for deprecation.

        Returns:
            Number of notes affected.
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        candidates: list[Page] = []

        # Collect matching notes via vault listing + text match
        # (lightweight; avoids requiring an index for forget)
        q_lower = query.lower() if query else None
        cutoff = (
            datetime.now(timezone.utc) - older_than
            if older_than is not None
            else None
        )

        for page in v.list(prefix=_MEMORY_PREFIX, kind="note"):
            if page.path == _CORE_MEMORY_PATH:
                continue  # never forget core memory

            if q_lower is not None:
                haystack = (
                    page.frontmatter.title
                    + " "
                    + page.frontmatter.summary
                    + " "
                    + page.body
                ).lower()
                if q_lower not in haystack:
                    continue

            if cutoff is not None and page.frontmatter.updated > cutoff:
                continue

            candidates.append(page)

        affected = 0
        for page in candidates:
            page.frontmatter.status = PageStatus.DEPRECATED
            page.frontmatter.updated = datetime.now(timezone.utc)
            v.put(page, page.path)
            affected += 1

        return affected

    # ── write_core ────────────────────────────────────────────────────────

    def write_core(
        self,
        vault: VaultStore | None = None,
        text: str = "",
    ) -> Page:
        """Write (or overwrite) the core-memory.md pinned page.

        Core memory is always included in search metadata.

        Args:
            vault: vault store.
            text: markdown or plain text for core memory.

        Returns:
            The core-memory Page.
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        title = _extract_title(text)
        summary = _extract_summary(text)

        now = datetime.now(timezone.utc)
        existing = v.get(_CORE_MEMORY_PATH)

        fm = Frontmatter(
            kind=PageKind.NOTE,
            name="core-memory",
            title=title if title != "untitled" else "Core Memory",
            summary=summary,
            tags=["core", "pinned"],
            status=PageStatus.ACTIVE,
            version=(existing.frontmatter.version + 1) if existing else 1,
            created=existing.frontmatter.created if existing else now,
            updated=now,
        )

        page = Page(frontmatter=fm, body=text.strip())
        v.put(page, _CORE_MEMORY_PATH)
        return page

    # ── compact ───────────────────────────────────────────────────────────

    def compact(
        self,
        vault: VaultStore | None = None,
        index_path: Path | None = None,
        similarity_threshold: float = 0.85,
        dry_run: bool = True,
    ) -> int | list[dict[str, Any]]:
        """Merge near-duplicate notes by title similarity.

        Groups notes under memory/notes/, merges groups whose titles
        have a SequenceMatcher ratio ≥ *similarity_threshold*.
        The newest note absorbs content from older ones; older ones
        are marked superseded.

        Dry-run and merge share ``_cluster_notes`` (R12), so a dry run reports
        exactly the notes a merge would absorb: ``len(compact(dry_run=True))``
        equals ``compact(dry_run=False)`` by construction.

        Args:
            vault: vault store.
            index_path: path to index (rebuilt after compaction).
            similarity_threshold: minimum title similarity to merge (0-1).
                Defaults to 0.85.
            dry_run: When True (default), returns a list of merge candidate
                dicts without modifying files. When False, performs merges.

        Returns:
            When *dry_run* is True: list of candidate dicts with keys
            ``title_a`` (keeper), ``title_b`` (absorbed note), ``similarity``.
            When *dry_run* is False: number of notes merged (== the dry-run
            list length for the same vault state and threshold).
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        notes = v.list(prefix=_MEMORY_PREFIX, kind="note")
        # Exclude core memory from compaction
        notes = [p for p in notes if p.path != _CORE_MEMORY_PATH]
        if len(notes) < 2:
            return [] if dry_run else 0

        groups = _cluster_notes(notes, similarity_threshold)
        merge_groups = [g for g in groups if len(g) >= 2]

        if dry_run:
            candidates: list[dict[str, Any]] = []
            for group in merge_groups:
                keeper = group[0]
                for dup in group[1:]:
                    candidates.append({
                        "title_a": keeper.frontmatter.title,
                        "title_b": dup.frontmatter.title,
                        "similarity": round(_title_similarity(keeper, dup), 4),
                    })
            return candidates

        # Merge each cluster: newest note keeps the page, older ones are absorbed.
        merge_count = 0
        for group in merge_groups:
            keeper = group[0]  # _cluster_notes orders newest-first

            # Absorb bodies from superseded notes
            absorbed: list[str] = [keeper.body]
            for dup in group[1:]:
                absorbed.append(f"<!-- merged from {dup.path} -->\n{dup.body}")
                dup.frontmatter.status = PageStatus.SUPERSEDED
                dup.frontmatter.superseded_by = keeper.path
                dup.frontmatter.updated = datetime.now(timezone.utc)
                v.put(dup, dup.path)
                merge_count += 1

            keeper.body = "\n\n---\n\n".join(absorbed)
            keeper.frontmatter.updated = datetime.now(timezone.utc)
            v.put(keeper, keeper.path)

        # Rebuild index if path provided
        ip = Path(index_path) if index_path else self._index_path
        if ip:
            idx = Index(ip)
            try:
                idx.build(v)
            finally:
                idx.close()

        return merge_count
# ── Module-level convenience ─────────────────────────────────────────────────

# Expose decay_score at module level for direct import
__all__ = [
    "MemoryManager",
    "decay_score",
]
