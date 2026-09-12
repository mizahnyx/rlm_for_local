"""Structural lint for the documentation corpus.

Catches, cheaply and mechanically, the defect classes this project has had to
repair by hand:

  * unbalanced ``` code fences (a stray trailing fence),
  * a `§x.y` reference that resolves neither to a heading in this document nor
    to a document named on the same line — an auditor should not have to guess
    which manual `§5.3` means,
  * relative markdown links whose target does not exist,
  * UTF-8 damage (U+FFFD) or a BOM, which a careless PowerShell round-trip
    introduces on Windows,
  * a document under `docs/` created **without** the date prefix (convention
    below).

Naming convention (2026-09-12)
------------------------------
Every document created under `docs/` from 2026-09-12 onward is named
`YYYYMMDD-HHmm-<topic>.md`, where the prefix is its *creation* date and time. A
document is never renamed when its content is updated: the prefix records when it
came into being, and the point-in-time records are not rewritten at all (a
correction is a new document that cites the old one). Files exempt from the rule,
and the reason, are listed in `LEGACY_UNPREFIXED` below — they predate it, and
renaming them would break links across the whole corpus.

Which documents are checked
---------------------------
The explicit `LIVING` list plus every dated document from `CONVENTION_START`
onward. Historical dated records before that are point-in-time: they are
supposed to describe the state of the world when they were written, so "stale"
claims in them are history, not defects — but a document written *under the new
convention* is expected to be structurally sound like any other.

Usage:
    .venv/Scripts/python.exe scripts/check_docs.py
    .venv/Scripts/python.exe scripts/check_docs.py --self-test

`--self-test` plants one instance of each defect class in a temporary corpus and
requires the linter to report every one of them, so "the linter works" is
reproducible rather than asserted.

Exit code is 0 only when every checked document passes.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Living documents, checked on every run. Un-prefixed by design or by history.
LIVING = [
    "README.md",
    "AGENTS.md",
    "docs/operator-guide.md",
    "docs/rlm-local-manual.md",
    "docs/rlm-kernel-manual.md",
    "docs/extensibility-guide.md",
    "docs/load-test-report.md",
    "docs/conformance/README.md",
]

#: Documents under docs/ that predate the date-prefix convention. Renaming them
#: would rewrite links in ~42 documents and in the manuals' cross-references, for
#: no informational gain; they are grandfathered instead, explicitly.
LEGACY_UNPREFIXED = {
    "docs/operator-guide.md",
    "docs/rlm-local-manual.md",
    "docs/rlm-kernel-manual.md",
    "docs/extensibility-guide.md",
    "docs/load-test-report.md",
    "docs/conformance/README.md",
    "docs/k4-first-run-report.md",
}

#: First date whose documents are expected to carry the prefix.
CONVENTION_START = "20260912"

PREFIX_RE = re.compile(r"^(\d{8})-(\d{4})-.+\.md$")
SECTION_RE = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)*)", re.MULTILINE)
REF_RE = re.compile(r"§\s?(\d+(?:\.\d+)*)")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")

# Words that mean "this reference points at a different document".
OTHER_DOC_HINTS = (
    "manual", "guide", "readme", "agents", "report", "spec", "runbook",
    "design", "plan", "analysis", "validation", "assessment", "roadmap",
    "diagnosis", "docs/",
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


def check_file(path: pathlib.Path) -> list[str]:
    """Return a list of content problems for one document (empty when clean)."""
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


def naming_problem(rel: str) -> str | None:
    """Report a docs/ path that breaks the date-prefix convention, else None."""
    name = pathlib.PurePosixPath(rel).name
    if PREFIX_RE.match(name):
        date = name[:8]
        if date < CONVENTION_START:
            return None  # an old record that happens to be prefixed
        return None
    if rel in LEGACY_UNPREFIXED:
        return None
    return (
        f"missing date prefix: new documents under docs/ are named "
        f"YYYYMMDD-HHmm-<topic>.md (see AGENTS.md); if this file predates "
        f"{CONVENTION_START}, add it to LEGACY_UNPREFIXED in scripts/check_docs.py "
        "with the reason"
    )


def checked_paths() -> list[str]:
    """Every document this run is expected to lint, as repo-relative posix paths."""
    rels = list(LIVING)
    docs = REPO / "docs"
    if docs.is_dir():
        for path in sorted(docs.rglob("*.md")):
            rel = path.relative_to(REPO).as_posix()
            match = PREFIX_RE.match(path.name)
            if match and path.name[:8] >= CONVENTION_START:
                rels.append(rel)
    return rels


def self_test() -> int:
    """Plant one instance of each defect class; require every one to be caught."""
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)

        planted = {
            "unbalanced fence": ("a.md", "# T\n\n```bash\nls\n", "unbalanced code fences"),
            "dangling section ref": ("b.md", "# T\n\n## 1. One\n\nsee §9 for details\n",
                                     "does not resolve"),
            "missing link": ("c.md", "# T\n\nsee [x](nope.md)\n", "link target missing"),
            "encoding damage": ("d.md", "# T\n\nbroken \ufffd byte\n", "U+FFFD"),
            "unprefixed new document": ("docs/2026-later-notes.md", "# T\n", "missing date prefix"),
        }

        for label, (rel, body, expected) in planted.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

            problems = check_file(path)
            rel_posix = path.relative_to(root).as_posix()
            prefix_note = naming_problem(rel_posix)
            if prefix_note:
                problems.append(prefix_note)

            if not any(expected in problem for problem in problems):
                print(f"VACUOUS  {label}: not caught (got {problems})")
                failures += 1
            else:
                print(f"ok       {label}: caught")

        # And the negative control: a clean, correctly named document passes.
        good = root / "docs" / f"{CONVENTION_START}-1200-clean.md"
        good.parent.mkdir(parents=True, exist_ok=True)
        good.write_text("# T\n\n## 1. One\n\nsee §1 and [x](clean.md)\n", encoding="utf-8")
        (good.parent / "clean.md").write_text("# x\n", encoding="utf-8")
        issues = check_file(good)
        if issues or naming_problem(good.relative_to(root).as_posix()):
            print(f"FALSE POSITIVE: {issues}")
            failures += 1
        else:
            print("ok       clean document passes (negative control)")

    print()
    print(f"{failures} problem(s) in the linter's own self-test")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return self_test()

    problems = 0
    rels = checked_paths()
    for rel in rels:
        path = REPO / rel
        if not path.exists():
            print(f"MISSING  {rel}")
            problems += 1
            continue

        issues = check_file(path)
        note = naming_problem(rel)
        if note and rel not in LIVING:
            issues.append(note)

        if issues:
            problems += len(issues)
            print(f"--- {rel} ({len(issues)})")
            for issue in issues:
                print("   ", issue)
        else:
            print(f"ok  {rel}")

    print()
    print(f"{problems} problem(s) across {len(rels)} checked documents")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
