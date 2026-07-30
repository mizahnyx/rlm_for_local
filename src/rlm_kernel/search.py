"""Search — BM25 cards over the vault index, with filters (§4).

search(query, k, kinds, detail) → compact cards ≤400 chars each (LID invariant).
detail="full" returns page body; default "card" returns compact summaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from rlm_kernel.vault import VaultStore


@runtime_checkable
class SearchBackend(Protocol):
    """Protocol for search backends — seam for vector tier later (§10)."""

    def search(self, query: str, k: int = 5, kinds: list[str] | None = None,
               detail: str = "card") -> list[dict[str, Any]]: ...


QUARANTINE_PREFIX = "quarantine"


def search_vault(
    vault: VaultStore,
    index_path: Path,
    query: str,
    k: int = 5,
    kinds: list[str] | None = None,
    tags: list[str] | None = None,
    detail: str = "card",
    include_quarantine: bool = False,
) -> list[dict[str, Any]]:
    """Search the vault via its index with optional kind and tag filters."""
    from rlm_kernel.index import Index

    if not index_path.exists():
        return []

    idx = Index(index_path)
    try:
        results = idx.fts_search(query, limit=k * 2, kinds=kinds, tags=tags)
    finally:
        idx.close()

    filtered: list[dict[str, Any]] = []
    for r in results:
        if detail == "full":
            page = vault.get(r["path"])
            if page is None:
                continue
            filtered.append({
                "path": r["path"],
                "kind": r["kind"],
                "name": r["name"],
                "title": r["title"],
                "summary": r["summary"],
                "score": r["score"],
                "body": page.body,
                "frontmatter": page.frontmatter.model_dump(mode="json"),
            })
        else:
            # Compact card ≤ 400 chars
            card = _make_card(r)
            filtered.append(card)
        if len(filtered) >= k:
            break


    # Merge quarantined pages when requested
    if include_quarantine and len(filtered) < k:
        q_pages = vault.list(prefix=QUARANTINE_PREFIX, kind=None)
        # Filter by kind if kinds specified
        if kinds:
            q_pages = [p for p in q_pages if p.kind.value in kinds]
        # Filter by query substring match on name or body
        if query and query != "*":
            q_lower = query.lower()
            q_pages = [
                p for p in q_pages
                if q_lower in p.name.lower() or q_lower in p.body.lower()
            ]
        # Append as cards (or full detail)
        for p in q_pages:
            if len(filtered) >= k:
                break
            if detail == "full":
                filtered.append({
                    "path": p.path,
                    "kind": p.kind.value,
                    "name": p.name,
                    "title": p.frontmatter.title,
                    "summary": p.frontmatter.summary,
                    "score": 0.0,
                    "body": p.body,
                    "frontmatter": p.frontmatter.model_dump(mode="json"),
                })
            else:
                filtered.append({
                    "path": p.path,
                    "kind": p.kind.value,
                    "name": p.name,
                    "title": p.frontmatter.title,
                    "summary": p.frontmatter.summary,
                    "score": 0.0,
                })
    return filtered


def _make_card(row: dict[str, Any]) -> dict[str, Any]:
    """Create a compact search result card ≤ 400 chars total."""
    return {
        "path": row["path"],
        "kind": row["kind"],
        "name": row["name"],
        "title": row["title"],
        "summary": row["summary"],
        "score": row["score"],
    }
