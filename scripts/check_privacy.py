"""Fail if a corpus-derived identifier has reached the repository (AGENTS.md §1.9).

Why this exists
---------------
On 2026-09-19 a question id — a name out of the prose corpus — was committed to this
public repository in a dated record, in a test, and in a commit message. The rule
existed; nothing checked it. This checks it.

How the list can be checked without being published
--------------------------------------------------
The check is public; the *list* is not. `--tokens` names a file that lives where the
corpus lives (`~/rlm-derived/private-tokens.txt`, mode 0600), one identifier per line,
`#` comments ignored. Nothing about a token is ever printed: a failure names the file,
the line number and the token's position in the list, never the token itself — because
an error message is exactly where a leak would go next.

Usage:
    python scripts/check_privacy.py --tokens ~/rlm-derived/private-tokens.txt
    python scripts/check_privacy.py --tokens … --history    # commit messages too

Exit code is 0 only when no token appears. With no token file it exits 0 and says it
could not check — a check that cannot see the truth says so rather than reporting
"clean" (the `AGENTS.md` §1.8 corollary).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Files whose *content* is expected to be free of corpus material. Everything
#: tracked is scanned, but these are named so the report can say where it looked.
SKIP_DIRS = {".git", ".venv", "__pycache__", ".tmp_verify", ".tmp_probe", "logs"}


def load_tokens(path: Path) -> list[str]:
    """The sensitive identifiers, in order, ignoring blanks and `#` comments."""
    tokens: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            tokens.append(token)
    return tokens


def scan_tree(tokens: list[str], *, root: Path | None = None) -> list[tuple[str, int, int]]:
    """`(path, line number, token index)` for every occurrence in the working tree."""
    base = root or ROOT
    hits: list[tuple[str, int, int]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        for index, token in enumerate(tokens):
            if token.lower() not in lowered:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if token.lower() in line.lower():
                    hits.append((path.relative_to(base).as_posix(), number, index))
    return hits


def format_report(hits: list[tuple[str, int, int]],
                  history_hits: list[tuple[str, int, int]], *,
                  token_count: int, checked_history: bool) -> str:
    """The report text — and it must not contain a token.

    A privacy check whose failure message quotes the identifier would move the leak
    from the file to the log, the terminal and whatever pastes the terminal. It names
    the file, the line and the token's *position* in the list instead, which is all an
    operator needs to find it.
    """
    lines = [
        f"checked {token_count} token(s) against the working tree"
        + (" and every commit message" if checked_history else "")
    ]
    for path, number, index in hits:
        lines.append(f"LEAK  working tree: {path}:{number} "
                     f"(token #{index + 1} of the list)")
    for subject, number, index in history_hits:
        lines.append(f"LEAK  commit {subject} message line {number} "
                     f"(token #{index + 1} of the list)")
    if hits or history_hits:
        lines.append(
            f"\n{len(hits) + len(history_hits)} occurrence(s). Remove them from the "
            "tree; a token in *history* needs a rewrite and a force-push, which is the "
            "owner's call."
        )
    else:
        lines.append("no occurrence in the tree"
                     + (" or in history" if checked_history else ""))
    return "\n".join(lines)


def scan_history(tokens: list[str]) -> list[tuple[str, int, int]]:
    """`(subject, body line, token index)` for every occurrence in commit messages."""
    log = subprocess.run(
        ["git", "log", "--format=%h%x09%s%x09%b%x1e"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    hits: list[tuple[str, int, int]] = []
    if log.returncode != 0:
        return hits
    for record in log.stdout.split("\x1e"):
        if not record.strip():
            continue
        first, _, rest = record.partition("\t")
        for number, line in enumerate(rest.splitlines(), 1):
            lowered = line.lower()
            for index, token in enumerate(tokens):
                if token.lower() in lowered:
                    hits.append((first.strip(), number, index))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=Path, default=None,
                    help="File of identifiers to search for, one per line, 0600, kept "
                         "where the corpus is. Never printed in full.")
    ap.add_argument("--history", action="store_true",
                    help="Also scan commit messages (slower; catches what a tree scan "
                         "cannot)")
    args = ap.parse_args()

    if args.tokens is None:
        print("Privacy check not run: no --tokens file, so how clean this tree is "
              "cannot be established (AGENTS.md §1.8 corollary). Point --tokens at a "
              "list beside the corpus.")
        return 0
    if not args.tokens.exists():
        print(f"Privacy check not run: no token file at {args.tokens}.")
        return 0

    tokens = load_tokens(args.tokens)
    if not tokens:
        print("Privacy check not run: the token file is empty.")
        return 0

    hits = scan_tree(tokens)
    history_hits = scan_history(tokens) if args.history else []
    report = format_report(hits, history_hits, token_count=len(tokens),
                           checked_history=args.history)
    print(report)
    return 1 if (hits or history_hits) else 0


if __name__ == "__main__":
    sys.exit(main())
