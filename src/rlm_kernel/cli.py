"""CLI for rlm-kernel — vault management and gate operations (§11).

Commands:
  rlm-kernel init [--vault PATH]       Seed a new vault
  rlm-kernel index [--rebuild]          Rebuild the search index
  rlm-kernel review [--execute]         Review quarantined proposals (static by default)
  rlm-kernel promote PATH               Promote a quarantined page
  rlm-kernel demote PATH [--by PATH]    Demote an active page
  rlm-kernel search QUERY [--kind ...] [--status ...]  Search the vault (active pages by default)
  rlm-kernel optimize --target ...      Run GEPA offline optimization
  rlm-kernel compact [--confirm]        Compact memory notes (dry-run default)
  rlm-kernel migrate-schema [--dry-run] Rename frontmatter `schema:` to `schema_version:` (R23)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _default_vault() -> Path:
    return Path.home() / ".local" / "share" / "rlm-kernel" / "vault"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rlm-kernel",
        description="Evolvable RLM kernel — vault management",
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Seed a new vault")
    p_init.add_argument("--vault", type=Path, default=_default_vault(),
                        help="Vault root path")

    # index
    p_idx = sub.add_parser("index", help="Manage search index")
    p_idx.add_argument("--rebuild", action="store_true", help="Full rebuild")
    p_idx.add_argument("--vault", type=Path, default=_default_vault())

    # review
    p_review = sub.add_parser("review", help="List quarantined proposals")
    p_review.add_argument("--vault", type=Path, default=_default_vault())
    p_review.add_argument(
        "--execute",
        action="store_true",
        help="Also run helper code in the sandbox (opt-in; not containment)",
    )

    # promote
    p_promote = sub.add_parser("promote", help="Promote a quarantined page")
    p_promote.add_argument("path", help="Page path within vault")
    p_promote.add_argument("--vault", type=Path, default=_default_vault())
    p_promote.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a deprecated/superseded page at the target path",
    )

    # demote
    p_demote = sub.add_parser("demote", help="Demote an active page")
    p_demote.add_argument("path", help="Page path within vault")
    p_demote.add_argument("--by", dest="superseded_by", default=None)
    p_demote.add_argument("--vault", type=Path, default=_default_vault())

    # search
    p_search = sub.add_parser("search", help="Search the vault")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--kind", nargs="*", default=None)
    p_search.add_argument(
        "--status", nargs="*", default=None,
        choices=["active", "deprecated", "superseded", "pending"],
        help="Page statuses to include (default: active only — retired pages "
             "stop answering queries, F14/CL1)",
    )
    p_search.add_argument("--vault", type=Path, default=_default_vault())

    # optimize
    p_opt = sub.add_parser("optimize", help="Run GEPA optimization")
    p_opt.add_argument("--target", choices=["prologue", "how-to-work", "nudges", "fewshots", "helper-docs"],
                       default="how-to-work")
    p_opt.add_argument("--vault", type=Path, default=_default_vault())

    # compact
    p_compact = sub.add_parser("compact", help="Compact memory notes")
    p_compact.add_argument("--confirm", action="store_true",
                           help="Actually perform merges (default: dry-run)")
    p_compact.add_argument("--vault", type=Path, default=_default_vault())

    # migrate-schema (R23)
    p_migrate = sub.add_parser(
        "migrate-schema",
        help="Rename the frontmatter `schema:` key to `schema_version:` (R23)",
    )
    p_migrate.add_argument("--vault", type=Path, default=_default_vault())
    p_migrate.add_argument("--dry-run", action="store_true",
                           help="Report what would change without writing")

    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(args)
    elif args.command == "index":
        return _cmd_index(args)
    elif args.command == "review":
        return _cmd_review(args)
    elif args.command == "promote":
        return _cmd_promote(args)
    elif args.command == "demote":
        return _cmd_demote(args)
    elif args.command == "search":
        return _cmd_search(args)
    elif args.command == "optimize":
        return _cmd_optimize(args)
    elif args.command == "compact":
        return _cmd_compact(args)
    elif args.command == "migrate-schema":
        return _cmd_migrate_schema(args)
    else:
        parser.print_help()
        return 0


# ── Command implementations ────────────────────────────────────────────────

def _cmd_init(args: argparse.Namespace) -> int:
    from rlm_kernel.seed import seed_vault
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    seed_vault(vault)
    print(f"Vault seeded at {args.vault}")
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    from rlm_kernel.index import rebuild_index
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    idx_path = args.vault / ".index" / "meta.sqlite"
    idx = rebuild_index(vault, idx_path)
    print(f"Index rebuilt: {idx.page_count()} pages indexed")
    idx.close()
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Review quarantined proposals.

    Static validation by default (S3/R19). `--execute` additionally runs helper
    code in the restricted-builtin sandbox — an opt-in convenience for trusted
    authors, never a containment boundary.
    """
    from rlm_kernel.gate import validate
    from rlm_kernel.vault import LocalVault

    execute = bool(getattr(args, "execute", False))
    mode = "executing sandbox" if execute else "static validation only"
    print(f"Review mode: {mode}")

    vault = LocalVault(args.vault, init_git=False)
    pending = vault.list(prefix="quarantine")
    if not pending:
        print("No pending proposals.")
        return 0
    for p in pending:
        report = validate(p, vault=vault, execute=execute)
        status = "PASS" if report.passed else "FAIL"
        print(f"  {p.path} — {p.frontmatter.title} [{status}]")
        print(f"    kind={p.frontmatter.kind.value} name={p.name}")
        print(f"    summary={p.frontmatter.summary[:100]}")
        if report.errors:
            for e in report.errors:
                print(f"    ERROR: {e}")
        if report.warnings:
            for w in report.warnings:
                print(f"    WARNING: {w}")
        print()
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    from rlm_kernel.gate import promote
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1
    new_path = promote(vault, page, force=bool(getattr(args, "force", False)))
    print(f"Promoted: {args.path} -> {new_path}")
    return 0


def _cmd_demote(args: argparse.Namespace) -> int:
    from rlm_kernel.gate import demote
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1
    demote(vault, page, superseded_by=args.superseded_by)
    print(f"Demoted: {args.path}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from rlm_kernel.search import search_vault
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    idx_path = args.vault / ".index" / "meta.sqlite"
    statuses = getattr(args, "status", None)
    results = search_vault(vault, idx_path, args.query, k=10, kinds=args.kind,
                           statuses=statuses)
    if not results:
        print("(no results)")
        if statuses is None:
            print("Search returns active pages by default; "
                  "--status deprecated superseded includes retired pages.")
        return 0
    for r in results:
        status = r.get("status", "active")
        marker = "" if status == "active" else f" [{status}]"
        print(f"[{r['kind']}]{marker} {r['name']}: {r['title']}")
        print(f"    {r['summary'][:120]}")
        print()
    return 0


def _cmd_optimize(args: argparse.Namespace) -> int:
    from rlm_kernel.optimize import run_optimization
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    run_optimization(vault, target=args.target)
    return 0



def _cmd_compact(args: argparse.Namespace) -> int:
    from rlm_kernel.memory import MemoryManager
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    idx_path = args.vault / ".index" / "meta.sqlite"
    mm = MemoryManager(vault=vault, index_path=idx_path)

    dry_run = not args.confirm
    result = mm.compact(vault=vault, index_path=idx_path, dry_run=dry_run)

    if dry_run:
        candidates: list = result if isinstance(result, list) else []
        if not candidates:
            print("No merge candidates found.")
            return 0
        print(f"Dry run: {len(candidates)} merge candidate(s):")
        for c in candidates:
            print(f"  {c['title_a']} ~ {c['title_b']} "
                  f"(similarity={c['similarity']:.2f})")
        print("\nRun with --confirm to perform merges.")
    else:
        merged: int = result if isinstance(result, int) else 0
        print(f"Compacted: {merged} note(s) merged.")

    return 0


def _cmd_migrate_schema(args: argparse.Namespace) -> int:
    """R23 — rewrite `schema:` to `schema_version:` in existing pages.

    Dry-run by default, like `compact`: this edits every page in the vault, and a
    migration an operator cannot preview is a migration they cannot consent to.
    """
    from rlm_kernel.schema import migrate_schema_key
    from rlm_kernel.vault import LocalVault

    vault_path = Path(args.vault)
    if not vault_path.exists():
        print(f"Vault not found: {vault_path}", file=sys.stderr)
        return 1

    if args.dry_run:
        pending: list[str] = []
        for md_file in sorted(vault_path.rglob("*.md")):
            if ".index" in md_file.parts or ".git" in md_file.parts:
                continue
            text = md_file.read_text(encoding="utf-8")
            _, did_change = migrate_schema_key(text)
            if did_change:
                pending.append(str(md_file.relative_to(vault_path)).replace("\\", "/"))
        if not pending:
            print("Nothing to migrate: every page already uses `schema_version:`.")
            return 0
        print(f"Dry run: {len(pending)} page(s) still use `schema:`:")
        for rel in pending:
            print(f"  {rel}")
        print("\nRun without --dry-run to rewrite them (the vault is git-versioned).")
        return 0

    vault = LocalVault(vault_path)
    changed = vault.migrate_schema_keys()
    if not changed:
        print("Nothing to migrate: every page already uses `schema_version:`.")
        return 0
    print(f"Migrated {len(changed)} page(s) to `schema_version:`:")
    for rel in changed:
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
