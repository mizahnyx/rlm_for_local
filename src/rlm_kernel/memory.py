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

def _ensure_core_memory_dir(vault: VaultStore) -> None:
    """Ensure the memory/notes directory exists (side-effect via put)."""
    # Vault put creates parents; we just need a sentinel write if the dir is empty.
    # We piggyback on the first core-memory write or similar.


def _get_core_memory_summary(vault: VaultStore) -> str | None:
    """Read core-memory.md and return its summary, or None."""
    page = vault.get(_CORE_MEMORY_PATH)
    if page is None:
        return None
    return page.frontmatter.summary


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

        Args:
            vault: vault store.
            index_path: path to the index database.
            query: free-text search query.
            k: max results.

        Returns:
            List of result cards with core-memory summary injected
            in the first result's metadata when available.
        """
        v = vault if vault is not None else self._vault
        ip = Path(index_path) if index_path else self._index_path
        if v is None or ip is None:
            raise ValueError("vault and index_path are required")

        results = search_vault(v, ip, query, k=k, kinds=_MEMORY_KINDS)

        # Inject core-memory summary as metadata on first result
        core_summary = _get_core_memory_summary(v)
        if core_summary and results:
            results[0]["core_memory"] = core_summary

        return results

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

        If *query* is given, search memory notes and deprecate matches.
        If *older_than* is given, deprecate notes whose updated timestamp
        is older than the delta.
        Both filters can be combined.

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

        if query:
            # Collect matching notes via vault listing + text match
            # (lightweight; avoids requiring an index for forget)
            all_notes = v.list(prefix=_MEMORY_PREFIX, kind="note")
            q_lower = query.lower()
            for page in all_notes:
                if page.path == _CORE_MEMORY_PATH:
                    continue  # never forget core memory
                haystack = (
                    page.frontmatter.title
                    + " "
                    + page.frontmatter.summary
                    + " "
                    + page.body
                ).lower()
                if q_lower in haystack:
                    candidates.append(page)
        elif older_than is not None:
            all_notes = v.list(prefix=_MEMORY_PREFIX, kind="note")
            cutoff = datetime.now(timezone.utc) - older_than
            for page in all_notes:
                if page.path == _CORE_MEMORY_PATH:
                    continue
                if page.frontmatter.updated <= cutoff:
                    candidates.append(page)
        else:
            # No criteria → deprecate all memory notes except core
            all_notes = v.list(prefix=_MEMORY_PREFIX, kind="note")
            for page in all_notes:
                if page.path == _CORE_MEMORY_PATH:
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

        Args:
            vault: vault store.
            index_path: path to index (rebuilt after compaction).
            similarity_threshold: minimum title similarity to merge (0-1).
                Defaults to 0.85.
            dry_run: When True (default), returns a list of merge candidate
                dicts without modifying files. When False, performs merges.

        Returns:
            When *dry_run* is True: list of candidate dicts with keys
            ``title_a``, ``title_b``, ``similarity``.
            When *dry_run* is False: number of notes merged.
        """
        v = vault if vault is not None else self._vault
        if v is None:
            raise ValueError("No vault provided")

        notes = v.list(prefix=_MEMORY_PREFIX, kind="note")
        # Exclude core memory from compaction
        notes = [p for p in notes if p.path != _CORE_MEMORY_PATH]
        if len(notes) < 2:
            return [] if dry_run else 0

        # Build candidate pairs
        candidates: list[dict[str, Any]] = []
        for i, a in enumerate(notes):
            for b in notes[i + 1:]:
                ratio = SequenceMatcher(
                    None,
                    a.frontmatter.title.lower(),
                    b.frontmatter.title.lower(),
                ).ratio()
                if ratio >= similarity_threshold:
                    candidates.append({
                        "title_a": a.frontmatter.title,
                        "title_b": b.frontmatter.title,
                        "similarity": round(ratio, 4),
                    })

        if dry_run:
            return candidates

        # Group by title similarity
        merged: set[str] = set()
        groups: list[list[Page]] = []

        remaining = list(notes)
        while remaining:
            anchor = remaining.pop(0)
            if anchor.path in merged:
                continue
            group = [anchor]
            for other in list(remaining):
                if other.path in merged:
                    remaining.remove(other)
                    continue
                ratio = SequenceMatcher(
                    None,
                    anchor.frontmatter.title.lower(),
                    other.frontmatter.title.lower(),
                ).ratio()
                if ratio >= similarity_threshold:
                    group.append(other)
                    remaining.remove(other)
            groups.append(group)

        # Merge each group
        merge_count = 0
        for group in groups:
            if len(group) < 2:
                continue

            # Sort by updated descending (newest first)
            group.sort(key=lambda p: p.frontmatter.updated, reverse=True)
            keeper = group[0]

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
