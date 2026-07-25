"""REPL bridge — definitions payload assembly and proxy handlers (§5.1, K1).

This module bridges the rlm_kernel vault with the rlm_local REPL sandbox:
1. Assembles helper definitions from vault helper pages for REPL injection.
2. Provides search/propose proxy handlers for the REPL socket protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rlm_kernel.schema import HelperDef, PageKind


@dataclass
class KernelBridge:
    """Bridges rlm_kernel vault+index with the rlm_local REPL.

    Created once per completion when a vault is configured.
    """

    vault: Any  # VaultStore
    index_path: Path

    # ── Helper definitions assembly (§5.1) ────────────────────────────────

    def get_helper_definitions(self) -> list[dict[str, str]]:
        """Collect active helper definitions from the vault for REPL injection.

        Returns list of {"name": str, "code": str} dicts suitable for the
        `helpers` payload in the REPL init command.
        """
        try:
            helpers = self.vault.list(kind="helper")
        except Exception:
            return []

        defs: list[dict[str, str]] = []
        for page in helpers:
            if page.frontmatter.status.value != "active":
                continue
            try:
                hd = HelperDef.from_page(page)
                defs.append(hd.to_dict())
            except Exception:
                continue
        return defs

    def get_helper_summaries(self) -> list[str]:
        """Get one-line summaries of active helpers for prompt assembly."""
        try:
            helpers = self.vault.list(kind="helper")
        except Exception:
            return []

        lines: list[str] = []
        for page in helpers:
            if page.frontmatter.status.value != "active":
                continue
            lines.append(f"  {page.name}: {page.frontmatter.summary}")
        return lines

    # ── Proxy handlers (§5.1 — new socket verbs: search, propose) ────────

    def handle_search(self, query: str, k: int = 5,
                      kinds: list[str] | None = None,
                      detail: str = "card") -> str:
        """Handle a 'search' socket command from the REPL worker.

        Returns a formatted string suitable for REPL stdout.
        """
        from rlm_kernel.search import search_vault

        try:
            results = search_vault(self.vault, self.index_path, query,
                                   k=k, kinds=kinds, detail=detail)
        except Exception as e:
            return f"Error: search failed: {e}"

        if not results:
            return "(no results)"

        lines: list[str] = []
        for i, r in enumerate(results):
            if detail == "full":
                lines.append(f"[{i+1}] {r['kind']}/{r['name']}: {r['title']}")
                lines.append(f"    {r.get('body', '')[:200]}")
            else:
                lines.append(
                    f"[{i+1}] {r['kind']}/{r['name']}: {r['title']} "
                    f"— {r['summary'][:120]}"
                )
        return "\n".join(lines)

    def handle_propose(self, kind: str, name: str, body: str,
                       rationale: str = "") -> str:
        """Handle a 'propose' socket command from the REPL worker.

        Writes the proposed page to quarantine/ and returns the path.
        """
        import ulid

        from rlm_kernel.schema import Frontmatter, Page, PageKind, PageStatus

        try:
            page_kind = PageKind(kind)
        except ValueError:
            return f"Error: invalid kind '{kind}'. Valid: {[k.value for k in PageKind]}"

        fm = Frontmatter(
            schema=1,
            kind=page_kind,
            name=name,
            title=name,
            summary=rationale[:200] if rationale else f"Proposed {kind}: {name}",
            status=PageStatus.PENDING,
            tags=["proposed"],
        )
        page = Page(fm, body)

        path = f"quarantine/{fm.id}.md"
        try:
            self.vault.put(page, path)
            return f"Proposed: {path}"
        except Exception as e:
            return f"Error: propose failed: {e}"

    # ── Core memory ───────────────────────────────────────────────────────

    def get_core_memory_summary(self) -> str | None:
        """Get the core-memory page summary for metadata injection."""
        try:
            page = self.vault.get("memory/notes/core-memory.md")
            if page is not None:
                return page.frontmatter.summary
            return None
        except Exception:
            return None
