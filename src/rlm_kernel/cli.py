"""CLI for rlm-kernel — vault management and gate operations (§11).

Commands:
  rlm-kernel init [--vault PATH]       Seed a new vault
  rlm-kernel index [--rebuild]          Rebuild the search index
  rlm-kernel review                     List quarantined proposals
  rlm-kernel promote PATH               Promote a quarantined page
  rlm-kernel demote PATH [--by PATH]    Demote an active page
  rlm-kernel search QUERY [--kind ...]  Search the vault
  rlm-kernel optimize --target ...      Run GEPA offline optimization
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

    # promote
    p_promote = sub.add_parser("promote", help="Promote a quarantined page")
    p_promote.add_argument("path", help="Page path within vault")
    p_promote.add_argument("--vault", type=Path, default=_default_vault())

    # demote
    p_demote = sub.add_parser("demote", help="Demote an active page")
    p_demote.add_argument("path", help="Page path within vault")
    p_demote.add_argument("--by", dest="superseded_by", default=None)
    p_demote.add_argument("--vault", type=Path, default=_default_vault())

    # search
    p_search = sub.add_parser("search", help="Search the vault")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--kind", nargs="*", default=None)
    p_search.add_argument("--vault", type=Path, default=_default_vault())

    # optimize
    p_opt = sub.add_parser("optimize", help="Run GEPA optimization")
    p_opt.add_argument("--target", choices=["prologue", "how-to-work", "nudges", "fewshots", "helper-docs"],
                       default="how-to-work")
    p_opt.add_argument("--vault", type=Path, default=_default_vault())

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
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    pending = vault.list(prefix="quarantine")
    if not pending:
        print("No pending proposals.")
        return 0
    for p in pending:
        print(f"  {p.path} — {p.frontmatter.title}")
        print(f"    kind={p.frontmatter.kind.value} name={p.name}")
        print(f"    summary={p.frontmatter.summary[:100]}")
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
    new_path = promote(vault, page)
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
    results = search_vault(vault, idx_path, args.query, k=10, kinds=args.kind)
    if not results:
        print("(no results)")
        return 0
    for r in results:
        print(f"[{r['kind']}] {r['name']}: {r['title']}")
        print(f"    {r['summary'][:120]}")
        print()
    return 0


def _cmd_optimize(args: argparse.Namespace) -> int:
    from rlm_kernel.optimize import run_optimization
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    run_optimization(vault, target=args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
