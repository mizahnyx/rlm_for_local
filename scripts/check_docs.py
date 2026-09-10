"""Structural lint for the living documentation.

Catches, cheaply and mechanically, the defect class that R25 had to repair by
hand in the 2026-09 remediation:

  * unbalanced ``` code fences (a stray trailing fence),
  * a `§x.y` reference that resolves neither to a heading in this document nor
    to a document named on the same line — an auditor should not have to guess
    which manual `§5.3` means,
  * relative markdown links whose target does not exist,
  * UTF-8 damage (U+FFFD) or a BOM, which a careless PowerShell round-trip
    introduces on Windows.

Prose inside fenced code blocks is ignored, so Python samples such as
`ns["word_count"]("hello world")` are not mistaken for markdown links.

Only the *living* documents are checked. `docs/2026*.md` are point-in-time
records: they are supposed to describe the state of the world when they were
written, so "stale" claims in them are history, not defects.

Usage:
    .venv/Scripts/python.exe scripts/check_docs.py

Exit code is 0 only when every living document passes.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

LIVING = [
    "README.md",
    "docs/operator-guide.md",
    "docs/rlm-local-manual.md",
    "docs/rlm-kernel-manual.md",
    "docs/extensibility-guide.md",
    "docs/load-test-report.md",
    "docs/conformance/README.md",
]

SECTION_RE = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)*)", re.MULTILINE)
REF_RE = re.compile(r"§\s?(\d+(?:\.\d+)*)")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")

# Words that mean "this reference points at a different document".
OTHER_DOC_HINTS = (
    "manual", "guide", "readme", "report", "spec", "runbook", "design",
    "plan", "analysis", "validation", "diagnosis", "docs/",
)


def prose_lines(text: str) -> list[tuple[int, str]]:
    """(line number, text) for lines outside fenced code blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((i, line))
    return out


def check(path: pathlib.Path) -> list[str]:
    """Return a list of problems for one document (empty when it is clean)."""
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []

    if "\ufffd" in text:
        issues.append("contains U+FFFD (encoding damage)")
    if text.startswith("\ufeff"):
        issues.append("starts with a BOM")

    fences = sum(1 for ln in text.splitlines() if ln.lstrip().startswith("```"))
    if fences % 2:
        issues.append(f"unbalanced code fences: {fences} fence lines (odd)")

    headings = set(SECTION_RE.findall(text))
    prose = prose_lines(text)

    for i, line in prose:
        for ref in REF_RE.findall(line):
            if ref in headings:
                continue
            if any(hint in line.lower() for hint in OTHER_DOC_HINTS):
                continue  # names another document — fine
            issues.append(
                f"line {i}: §{ref} does not resolve here and names no document"
            )

    for i, line in prose:
        for target in LINK_RE.findall(line):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                issues.append(f"line {i}: link target missing: {target!r}")

    return issues


def main() -> int:
    problems = 0
    for rel in LIVING:
        path = REPO / rel
        if not path.exists():
            print(f"MISSING  {rel}")
            problems += 1
            continue
        issues = check(path)
        if issues:
            problems += len(issues)
            print(f"--- {rel} ({len(issues)})")
            for issue in issues:
                print("   ", issue)
        else:
            print(f"ok  {rel}")

    print()
    print(f"{problems} problem(s) across {len(LIVING)} living documents")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
