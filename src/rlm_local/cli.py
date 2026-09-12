"""CLI frontend for rlm — the RLM harness command group (D1).

Subcommands:
  rlm ask        Run a single completion with Markdown context assembly
  rlm chat       Interactive chat loop with session context
  rlm ingest     Ingest Markdown documents into the vault as pages
  rlm search     Hybrid search over the vault
  rlm get        Print a vault page
  rlm tag        Tag a vault page (and reindex)
  rlm check      Model suitability battery
  rlm vault      Pass-through to rlm-kernel vault management
  rlm optimize   GEPA offline optimization
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


def _default_vault() -> Path:
    return Path.home() / ".local" / "share" / "rlm-kernel" / "vault"


def build_parser() -> argparse.ArgumentParser:
    """Build the `rlm` argument parser.

    Split out of `main()` so the parser itself is testable — notably the
    `--kind` choices, which must stay a subset of `PageKind` values and must
    describe a directory convention that exists (R13/R16).
    """
    parser = argparse.ArgumentParser(
        prog="rlm",
        description="RLM harness — recursive reasoning with local models",
    )
    sub = parser.add_subparsers(dest="command")

    # ── ask ──────────────────────────────────────────────────────────────
    p_ask = sub.add_parser("ask", help="Run a single completion")
    p_ask.add_argument("query", help="The question or task")
    p_ask.add_argument("--context-file", action="append", default=[],
                       type=Path, dest="context_files",
                       help="Markdown file(s) to use as context (repeatable)")
    p_ask.add_argument("--context-dir", type=Path, default=None,
                       help="Directory of .md files to use as context")
    p_ask.add_argument("--stdin", action="store_true",
                       help="Read context from stdin")
    p_ask.add_argument("--vault", type=str, default=None,
                       help="Vault page path to use as context")
    p_ask.add_argument("--profile", default="laptop",
                       choices=["tiny", "laptop", "workstation"])
    p_ask.add_argument("--max-turns", type=int, default=None)
    p_ask.add_argument("--log-path", type=Path, default=None,
                       help="Write trajectory JSONL to this path")
    _add_model_server_flags(p_ask)

    # ── chat ─────────────────────────────────────────────────────────────
    p_chat = sub.add_parser("chat", help="Interactive chat loop")
    p_chat.add_argument("--profile", default="laptop",
                        choices=["tiny", "laptop", "workstation"])
    p_chat.add_argument("--vault-path", type=Path, default=None,
                        help="Path to vault root")
    _add_model_server_flags(p_chat)

    # ── ingest ───────────────────────────────────────────────────────────
    p_ingest = sub.add_parser("ingest", help="Ingest Markdown into vault")
    p_ingest.add_argument("paths", nargs="+", type=Path,
                          help="Markdown files to ingest")
    p_ingest.add_argument("--kind", default="note",
                          choices=["note", "definition", "topic"])
    p_ingest.add_argument("--tags", default="",
                          help="Comma-separated tags")
    p_ingest.add_argument("--vault", type=Path, default=_default_vault())

    # ── search ───────────────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="Search the vault")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--kind", nargs="*", default=None)
    p_search.add_argument("--tag", nargs="*", default=None)
    p_search.add_argument(
        "--status", nargs="*", default=None,
        choices=["active", "deprecated", "superseded", "pending"],
        help="Page statuses to include (default: active only — retired pages "
             "stop answering queries, F14/CL1)",
    )
    p_search.add_argument("-k", type=int, default=5)
    p_search.add_argument("--vault", type=Path, default=_default_vault())

    # ── get ──────────────────────────────────────────────────────────────
    p_get = sub.add_parser("get", help="Print a vault page")
    p_get.add_argument("path", help="Page path within vault")
    p_get.add_argument("--vault", type=Path, default=_default_vault())

    # tag subcommand
    p_tag = sub.add_parser("tag", help="Add or remove tags from a vault page")
    p_tag.add_argument("path", help="Page path within vault")
    p_tag.add_argument("--add", default="", help="Comma-separated tags to add")
    p_tag.add_argument("--remove", default="", help="Comma-separated tags to remove")
    p_tag.add_argument("--vault", type=Path, default=_default_vault())
    # ── check ────────────────────────────────────────────────────────────
    p_check = sub.add_parser("check", help="Model suitability battery")
    p_check.add_argument("model_id", help="Model ID on the server")
    p_check.add_argument(
        "--endpoint",
        default=os.environ.get("RLM_ENDPOINT", "https://localhost:9010/v1"),
        help="Base URL of the model server (env RLM_ENDPOINT)",
    )
    p_check.add_argument("--quick", action="store_true")
    p_check.add_argument("--profile", default="tiny")
    p_check.add_argument(
        "--weights",
        default=os.environ.get("RLM_CHECK_WEIGHTS", "default"),
        help=("named battery weighting (default: default; use p1-heavy to "
              "reproduce the scores recorded on 2026-09-11) [env RLM_CHECK_WEIGHTS]"),
    )

    # ── vault ────────────────────────────────────────────────────────────
    p_vault = sub.add_parser("vault", help="Vault management (pass-through to rlm-kernel)")
    p_vault.add_argument("vault_args", nargs=argparse.REMAINDER,
                         help="Arguments forwarded to rlm-kernel")

    # ── optimize ─────────────────────────────────────────────────────────
    p_opt = sub.add_parser("optimize", help="GEPA offline optimization")
    p_opt.add_argument("--target", default="how-to-work",
                       choices=["prologue", "how-to-work", "nudges",
                                "fewshots", "helper-docs"])
    p_opt.add_argument("--suite", default="needle_search")
    p_opt.add_argument("--profile", default="tiny")
    p_opt.add_argument("--max-calls", type=int, default=150)
    p_opt.add_argument("--vault", type=Path, default=_default_vault())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return _cmd_ask(args)
    elif args.command == "chat":
        return _cmd_chat(args)
    elif args.command == "ingest":
        return _cmd_ingest(args)
    elif args.command == "search":
        return _cmd_search(args)
    elif args.command == "get":
        return _cmd_get(args)
    elif args.command == "tag":
        return _cmd_tag(args)
    elif args.command == "check":
        return _cmd_check(args)
    elif args.command == "vault":
        return _cmd_vault(args)
    elif args.command == "optimize":
        return _cmd_optimize(args)
    else:
        parser.print_help()
        return 0

# ── ask ───────────────────────────────────────────────────────────────────

def _add_model_server_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared model-server overrides to a subcommand.

    The shipped profiles point at `https://localhost:9010/v1`. A model server on
    another host — a LAN box, a Tailscale peer, or a llama.cpp **router** that
    loads models on demand — needs these overrides, and the environment variables
    let one export configure every command (`ask`, `chat`, `check`) at once.
    """
    parser.add_argument(
        "--endpoint", default=os.environ.get("RLM_ENDPOINT"),
        help="Base URL of the model server, e.g. https://lunacode:9010/v1 "
             "(default: the profile's root_endpoint; env RLM_ENDPOINT)",
    )
    parser.add_argument(
        "--model", default=os.environ.get("RLM_MODEL"),
        help="Model ID for BOTH tiers (default: the profile's model; env "
             "RLM_MODEL). The sub tier follows the root tier, so sub-calls use "
             "the model you name rather than the profile default.",
    )


def _model_server_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate `--endpoint`/`--model` into `load_config` overrides.

    Both tiers are set from `--model`: leaving `sub_model` at the profile
    default would run the root model on the server you named while every
    sub-call went somewhere else. `sub_endpoint` is deliberately left alone —
    empty means "same as root", so it follows `--endpoint` automatically.
    """
    overrides: dict[str, Any] = {}
    endpoint = getattr(args, "endpoint", None)
    model = getattr(args, "model", None)
    if endpoint:
        overrides["root_endpoint"] = endpoint
    if model:
        overrides["root_model"] = model
        overrides["sub_model"] = model
    return overrides


def _assemble_context(args: argparse.Namespace) -> str | None:
    """Assemble context from files, dir, stdin, and/or vault."""
    parts: list[str] = []

    # Context files
    for fpath in args.context_files:
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8", errors="replace")
            parts.append(f"# {fpath.name}\n\n{content}")
        else:
            print(f"Warning: file not found: {fpath}", file=sys.stderr)

    # Context dir
    if args.context_dir:
        d = args.context_dir
        if d.is_dir():
            for md in sorted(d.glob("*.md")):
                content = md.read_text(encoding="utf-8", errors="replace")
                parts.append(f"# {md.name}\n\n{content}")

    # Stdin
    if args.stdin:
        stdin_text = sys.stdin.read()
        if stdin_text.strip():
            parts.append(f"# stdin\n\n{stdin_text}")

    # Vault page
    if args.vault:
        try:
            from rlm_kernel.vault import LocalVault
            vault = LocalVault(_default_vault(), init_git=False)
            page = vault.get(args.vault)
            if page:
                parts.append(f"# {args.vault}\n\n{page.body}")
            else:
                print(f"Warning: vault page not found: {args.vault}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: vault error: {e}", file=sys.stderr)

    if not parts:
        return None
    return "\n\n---\n\n".join(parts)


def _cmd_ask(args: argparse.Namespace) -> int:
    import rlm_local

    context = _assemble_context(args)
    if context is None:
        print("Error: no context provided. Use --context-file, --context-dir, --stdin, or --vault.",
              file=sys.stderr)
        return 2

    overrides: dict[str, Any] = _model_server_overrides(args)
    if args.max_turns is not None:
        overrides["max_turns"] = args.max_turns

    try:
        answer = rlm_local.completion(
            args.query, context,
            profile=args.profile,
            log_path=str(args.log_path) if args.log_path else None,
            **overrides,
        )
        print(answer)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


# ── chat ──────────────────────────────────────────────────────────────────

def _cmd_chat(args: argparse.Namespace) -> int:
    try:
        from rlm_local.chat import run_chat
        from rlm_local.config import load_config
        vault_str = str(args.vault_path) if args.vault_path else None
        run_chat(
            profile=args.profile,
            vault_path=vault_str,
            config=load_config(args.profile, **_model_server_overrides(args)),
        )
        return 0
    except ImportError as e:
        # Chat mode exists; this fires only when a kernel dependency is missing.
        print(f"Chat mode unavailable: {e}", file=sys.stderr)
        print("Install the kernel dependencies (uv sync) and retry, "
              "or use 'rlm ask' for single completions.", file=sys.stderr)
        return 1


# ── ingest ────────────────────────────────────────────────────────────────

def _cmd_ingest(args: argparse.Namespace) -> int:
    from rlm_kernel.index import Index, rebuild_index
    from rlm_kernel.schema import Frontmatter, Page, PageKind
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=True)
    idx_path = args.vault / ".index" / "meta.sqlite"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    kind = PageKind(args.kind)

    ingested = 0
    skipped = 0

    for fpath in args.paths:
        if not fpath.exists():
            print(f"  SKIP: not found: {fpath}")
            continue

        content = fpath.read_text(encoding="utf-8", errors="replace")

        # Extract title from first heading, or use filename
        title = fpath.stem
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # Slugify name
        import re
        name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64] or fpath.stem

        # Check for duplicate by content hash
        # We compute a quick hash to check
        import hashlib
        content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()

        # Check existing pages
        existing = vault.list(kind=args.kind)
        duplicate = False
        for ex in existing:
            if ex.frontmatter.hash and ex.frontmatter.hash == content_hash:
                duplicate = True
                break
            # Also check body content
            if ex.body.strip() == content.strip():
                duplicate = True
                break

        if duplicate:
            print(f"  SKIP: duplicate: {fpath}")
            skipped += 1
            continue

        fm = Frontmatter(
            schema=1,
            kind=kind,
            name=name,
            title=title,
            summary=title,
            tags=tags,
            hash=content_hash,
        )
        page = Page(fm, content)
        page_path = f"{kind.value}/{name}.md" if kind.value != "note" else f"memory/notes/{name}.md"
        vault.put(page, page_path)
        print(f"  OK: {fpath} → {page_path}")
        ingested += 1

    # Reindex
    if ingested > 0:
        idx = Index(idx_path)
        idx.reindex_delta(vault)
        idx.close()
        vault.git_commit(f"rlm ingest: {ingested} page(s)")

    print(f"\nIngested: {ingested}, Skipped: {skipped}")
    return 0


# ── search ────────────────────────────────────────────────────────────────

def _cmd_search(args: argparse.Namespace) -> int:
    from rlm_kernel.search import search_vault
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    idx_path = args.vault / ".index" / "meta.sqlite"

    if not idx_path.exists():
        print("Index not found. Run: rlm vault index --rebuild")
        return 1

    results = search_vault(
        vault, idx_path, args.query, k=args.k, kinds=args.kind, tags=args.tag,
        statuses=getattr(args, "status", None),
    )
    if not results:
        print("(no results)")
        if getattr(args, "status", None) is None:
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


# ── get ───────────────────────────────────────────────────────────────────

def _cmd_get(args: argparse.Namespace) -> int:
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1

    print(page.to_markdown())
    return 0



# ── tag ────────────────────────────────────────────────────────────────────

def _cmd_tag(args: argparse.Namespace) -> int:
    """Add or remove tags from a vault page."""
    from rlm_kernel.vault import LocalVault
    from rlm_kernel.index import Index

    vault = LocalVault(args.vault, init_git=False)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1

    add_tags = [t.strip() for t in args.add.split(",") if t.strip()]
    remove_tags = [t.strip() for t in args.remove.split(",") if t.strip()]

    if not add_tags and not remove_tags:
        # Just show current tags
        current = page.frontmatter.tags
        print(f"Tags for {args.path}: {', '.join(current) if current else '(none)'}")
        return 0

    current = set(page.frontmatter.tags)
    for t in add_tags:
        current.add(t)
    for t in remove_tags:
        current.discard(t)

    page.frontmatter.tags = sorted(current)
    vault.put(page, args.path)

    idx_path = args.vault / ".index" / "meta.sqlite"
    if idx_path.exists():
        idx = Index(idx_path)
        idx.reindex_delta(vault)
        idx.close()

    print(f"Tags updated: {', '.join(page.frontmatter.tags) if page.frontmatter.tags else '(none)'}")
    return 0


# ── check ─────────────────────────────────────────────────────────────────

def _cmd_check(args: argparse.Namespace) -> int:
    try:
        from rlm_local.model_check import check_model
        from rlm_local.model_backend import HTTPModelBackend

        backend = HTTPModelBackend(
            root_endpoint=args.endpoint,
            root_model=args.model_id,
            verify=False,
        )
        try:
            result = check_model(
                backend, args.model_id,
                quick=args.quick, profile=args.profile,
                weight_profile=args.weights,
            )
        finally:
            backend.close()

        print(f"Model: {args.model_id}")
        print(f"Score: {result['score']}/100")
        print(f"Verdict: {result['verdict']}")
        print(f"Weights: {result['weight_profile']} "
              f"({', '.join(f'{p} {w}' for p, w in result['weights'].items())})")
        print(f"Probes: {result['probes_passed']}/{result['probes_total']} passed, "
              f"{result['probes_failed']} failed")
        print(f"Time: {result['elapsed_seconds']:.0f}s")
        print()
        print("Failed probes:")
        for pid, pr in result.get("per_probe", {}).items():
            if not pr["passed"]:
                print(f"  {pid}: {pr['score']}/{pr['max_score']} pts")
        print()
        if result.get("evidence_lines"):
            print("Evidence:")
            evidence = result["evidence_lines"]
            for line in evidence[:20]:
                print(f"  {line}")
            # Never truncate silently: P4 alone contributes up to seven lines
            # (three trials plus a summary), so a full battery can now push
            # later probes past the cap.
            if len(evidence) > 20:
                print(f"  ... {len(evidence) - 20} more line(s) not shown")
        return 0
    except ImportError as e:
        # The battery exists; this fires only when a dependency is missing.
        print(f"Model check unavailable: {e}", file=sys.stderr)
        print("Install the dependencies (uv sync) and retry.", file=sys.stderr)
        return 1
    except ValueError as e:
        # An unknown --weights value: a usage error, not a missing dependency.
        print(f"{e}", file=sys.stderr)
        return 2
# ── vault ─────────────────────────────────────────────────────────────────

def _cmd_vault(args: argparse.Namespace) -> int:
    from rlm_kernel.cli import main as kernel_main
    return kernel_main(args.vault_args)


# ── optimize ──────────────────────────────────────────────────────────────

def _cmd_optimize(args: argparse.Namespace) -> int:
    from rlm_kernel.optimize import run_optimization
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    result = run_optimization(
        vault,
        target=args.target,
        suite_name=args.suite,
        profile=args.profile,
        max_metric_calls=args.max_calls,
    )
    print(f"Status: {result['status']}")
    print(f"Baseline: {result['baseline_score']:.1%}")
    print(f"Best: {result['best_score']:.1%}")
    if result.get("held_out_score") is not None:
        print(f"Held-out: {result['held_out_score']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
