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
# The message the worker and this bridge must agree on word for word (CL3): the
# worker asks for a search and prints whatever the harness returns, so the
# "nothing found" text is one constant, not two copies.
from rlm_local.templates import WORKER_SEARCH_NO_RESULTS


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

        Uses index-backed listing when the index exists (O(1) SQL query),
        falling back to full vault walk.
        """
        defs: list[dict[str, str]] = []
        try:
            # Fast path: index-backed listing (G4)
            if self.index_path.exists():
                from rlm_kernel.index import Index
                idx = Index(self.index_path)
                try:
                    paths = idx.list_paths(kind="helper", status="active")
                    for path in paths:
                        page = self.vault.get(path)
                        if page is None:
                            continue
                        hd = HelperDef.from_page(page)
                        defs.append(hd.to_dict())
                    return defs
                finally:
                    idx.close()

            # Slow path: full vault walk
            helpers = self.vault.list(kind="helper")
            for page in helpers:
                if page.frontmatter.status.value != "active":
                    continue
                try:
                    hd = HelperDef.from_page(page)
                    defs.append(hd.to_dict())
                except Exception:
                    continue
        except Exception:
            pass
        return defs

    def get_helper_summaries(self, limit: int = 30) -> list[str]:
        """One-line summaries of active helpers, for prompt assembly (DG9).

        This was a dead method — a test called it, nothing else did — while
        `prompts.load_system_prompt_from_vault` built the same section itself,
        including an index-backed path. The assembly now calls this instead, so
        the "which helpers does the model see" rule lives in one place:

        * active pages only — a demoted helper stops being advertised;
        * the SQL index when it exists, a vault walk when it does not;
        * capped, because the prompt's helper section is progressive disclosure
          and a vault with hundreds of helpers must not crowd out the contract.
        """
        lines: list[str] = []
        try:
            if self.index_path.exists():
                from rlm_kernel.index import Index

                idx = Index(self.index_path)
                try:
                    paths = idx.list_paths(kind="helper", status="active")
                finally:
                    idx.close()
                pages = [self.vault.get(path) for path in paths]
                pages = [p for p in pages if p is not None]
            else:
                pages = [
                    p for p in self.vault.list(kind="helper")
                    if p.frontmatter.status.value == "active"
                ]
        except Exception:
            return []

        for page in pages[:limit]:
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
            return WORKER_SEARCH_NO_RESULTS

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
