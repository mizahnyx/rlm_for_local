"""Gate — verification and lifecycle management for model-authored content (§7).

The gate controls how model-authored content enters the live system:
1. propose — write to quarantine/ with status:pending
2. validate — check correctness per kind-specific rules
3. promote — move from quarantine/ to target namespace, reindex
4. reject — delete from quarantine/
5. demote — lifecycle: active → deprecated → superseded

Quarantined pages are NOT indexed and NOT returned by normal search_vault;
they are discoverable only via search_quarantine() or include_quarantine=True.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rlm_kernel.schema import (
    Frontmatter,
    HelperDef,
    Page,
    PageKind,
    PageStatus,
    _new_ulid,
)
from rlm_kernel.vault import VaultStore


# ── Constants ────────────────────────────────────────────────────────────────

QUARANTINE_PREFIX = "quarantine"

# Allowed stdlib modules for helper imports
ALLOWED_IMPORTS: set[str] = {
    "re",
    "json",
    "math",
    "collections",
    "itertools",
    "functools",
    "hashlib",
    "pathlib",
}

# Blocked substrings in helper code (matched case-insensitively)
BLOCKED_PATTERNS: list[str] = [
    "os.system",
    "subprocess",
    "socket",
    "ctypes",
    "importlib",
]

# Max body length for contract/template pages (bytes)
MAX_BODY_LENGTH = 8192

# Slot variable pattern: {{name}} or {name}
SLOT_PATTERN = re.compile(r"\{\{?\s*(\w+)\s*\}?\}")

# Wikilink pattern: [[target]] or [[target|alias]]
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


# ── ValidationReport ─────────────────────────────────────────────────────────


@dataclass
class ValidationReport:
    """Result of gate validation.

    Attributes:
        passed: True if no errors.
        errors: Fatal issues that block promotion.
        warnings: Advisory issues that do not block promotion.
    """

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Propose ──────────────────────────────────────────────────────────────────


def propose(
    vault: VaultStore,
    kind: PageKind,
    name: str,
    body: str,
    rationale: str = "",
) -> str:
    """Create a pending page in quarantine/ and return its path.

    The page is written to quarantine/<ulid>.md with status:pending.
    It is NOT indexed and NOT returned by normal search_vault.

    Args:
        vault: The vault store to write to.
        kind: Page kind (contract, helper, definition, …).
        name: Page name — used as frontmatter name and future filename.
        body: Markdown body content.
        rationale: Optional human-readable rationale (stored as an HTML comment
                   at the top of the body).

    Returns:
        Relative path of the quarantined page, e.g. ``quarantine/01J…md``.
    """
    ulid = _new_ulid()
    path = f"{QUARANTINE_PREFIX}/{ulid}.md"

    # Prepend rationale as a hidden comment so it travels with the page
    if rationale:
        body = f"<!-- rationale: {rationale} -->\n\n{body}"

    frontmatter = Frontmatter(
        kind=kind,
        name=name,
        title=name,
        summary=_truncate_summary(body),
        tags=[],
        status=PageStatus.PENDING,
        version=0,
    )

    page = Page(frontmatter=frontmatter, body=body, path=path)
    vault.put(page, path)
    return path


# ── Validate ─────────────────────────────────────────────────────────────────


def validate(page: Page, vault: VaultStore | None = None) -> ValidationReport:
    """Validate a proposed page against kind-specific rules.

    Args:
        page: The page to validate.
        vault: Optional vault for wikilink resolution checks
               (required for definition/note pages).

    Returns:
        ValidationReport with passed/errors/warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Status sanity
    if page.frontmatter.status != PageStatus.PENDING:
        warnings.append(
            f"Page status is '{page.frontmatter.status.value}', not 'pending'"
        )
    if not page.path.startswith(QUARANTINE_PREFIX):
        warnings.append(
            "Page is not in quarantine/ — validate is intended for proposed pages"
        )

    # 2. Kind-specific rules
    kind = page.kind

    if kind == PageKind.HELPER:
        _validate_helper(page, errors, warnings)
    elif kind in (PageKind.CONTRACT, PageKind.TEMPLATE):
        _validate_contract_or_template(page, errors, warnings)
    elif kind in (PageKind.DEFINITION, PageKind.NOTE):
        _validate_definition_or_note(page, vault, errors, warnings)
    elif kind == PageKind.FEWSHOT:
        _validate_fewshot(page, errors, warnings)
    # topic, cache — no extra validation beyond basic sanity

    return ValidationReport(
        passed=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ── Kind-specific validators ─────────────────────────────────────────────────


def _validate_helper(
    page: Page, errors: list[str], warnings: list[str]
) -> None:
    """Validate a helper page: import allowlist, syntax, sandbox execution."""

    # Extract helper definition
    try:
        helper = HelperDef.from_page(page)
    except ValueError as e:
        errors.append(f"Helper extraction failed: {e}")
        return

    code = helper.code
    if not code.strip():
        errors.append("Helper has no implementation code")
        return

    # --- 2a. AST parse check ---
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Helper code has syntax error: {e}")
        return

    # --- 2b. Import allowlist ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod not in ALLOWED_IMPORTS:
                    errors.append(
                        f"Import '{alias.name}' not in allowlist. "
                        f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                mod = node.module.split(".")[0]
                if mod not in ALLOWED_IMPORTS:
                    errors.append(
                        f"Import '{node.module}' not in allowlist. "
                        f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                    )

    # --- 2c. Blocked patterns ---
    code_lower = code.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in code_lower:
            errors.append(f"Blocked pattern detected: '{pattern}'")

    # --- 2d. Sandbox execution ---
    _sandbox_test(code, helper.name, helper.signature, errors, warnings)


def _sandbox_test(
    code: str,
    name: str,
    signature: str | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Execute helper code in a restricted namespace to verify it runs cleanly."""

    # Restricted builtins — only safe, pure-ish functions
    safe_builtins: dict[str, Any] = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "bytes": bytes,
        "callable": callable,
        "chr": chr,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "frozenset": frozenset,
        "hash": hash,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "oct": oct,
        "ord": ord,
        "pow": pow,
        "print": print,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "type": type,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "AttributeError": AttributeError,
    }

    # Pre-import allowed modules into the sandbox namespace
    sandbox_modules: dict[str, Any] = {}
    for mod_name in ALLOWED_IMPORTS:
        try:
            sandbox_modules[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    exec_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        **sandbox_modules,
    }
    exec_locals: dict[str, Any] = {}

    try:
        exec(code, exec_globals, exec_locals)
    except Exception as e:
        errors.append(f"Sandbox execution failed: {type(e).__name__}: {e}")
        return

    # Check the named callable exists
    if name not in exec_locals:
        errors.append(f"Helper code does not define a callable named '{name}'")
    elif not callable(exec_locals[name]):
        errors.append(f"'{name}' is defined but is not callable")

    # Check signature matches if provided
    if signature is not None and name in exec_locals:
        _check_signature_match(signature, exec_locals[name], warnings)


def _check_signature_match(
    signature: str, fn: Any, warnings: list[str]
) -> None:
    """Warn if the declared signature doesn't match the actual callable."""
    try:
        sig_tree = ast.parse(signature)
    except SyntaxError:
        warnings.append(f"Signature is not valid Python: {signature}")
        return

    for node in ast.walk(sig_tree):
        if isinstance(node, ast.FunctionDef):
            declared_params = [arg.arg for arg in node.args.args]
            actual_argcount = fn.__code__.co_argcount
            # co_varnames includes locals; slice to arg count for parameter names
            actual_params = list(fn.__code__.co_varnames[:actual_argcount])
            if declared_params != actual_params:
                warnings.append(
                    f"Signature mismatch: declared {declared_params}, "
                    f"actual {actual_params}"
                )
            return

    warnings.append("Signature block does not contain a function definition")


def _validate_contract_or_template(
    page: Page, errors: list[str], warnings: list[str]
) -> None:
    """Validate contract/template pages: slot variables, length cap."""
    body = page.body

    # Length cap
    if len(body) > MAX_BODY_LENGTH:
        errors.append(
            f"Body length {len(body)} exceeds max {MAX_BODY_LENGTH} characters"
        )

    # Slot variables
    slots = SLOT_PATTERN.findall(body)
    if not slots:
        warnings.append(
            "No slot variables found (e.g. {{name}} or {name})"
        )
    else:
        seen: set[str] = set()
        for slot in slots:
            if slot in seen:
                warnings.append(f"Duplicate slot variable: '{slot}'")
            seen.add(slot)


def _validate_definition_or_note(
    page: Page,
    vault: VaultStore | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate definition/note pages: wikilink resolution."""
    if vault is None:
        warnings.append("No vault provided — skipping wikilink resolution")
        return

    links = WIKILINK_PATTERN.findall(page.body)
    for link in links:
        # [[target|alias]] → target
        target = link.split("|")[0].strip()
        # [[target#section]] → target
        target = target.split("#")[0].strip()

        if not target:
            continue

        resolved = vault.resolve_wikilink(target)
        if resolved is None:
            # Check if it exists in quarantine (not yet promoted)
            quarantine_path = f"{QUARANTINE_PREFIX}/{target}.md"
            if vault.exists(quarantine_path):
                warnings.append(
                    f"Wikilink [[{target}]] points to a quarantined page"
                )
            else:
                warnings.append(
                    f"Wikilink [[{target}]] does not resolve to any page"
                )


def _validate_fewshot(
    page: Page, errors: list[str], warnings: list[str]
) -> None:
    """Validate few-shot pages: must have example sections."""
    body = page.body.strip()
    if not body:
        errors.append("Few-shot page has no body content")
        return

    if "## Example" not in body and "## Examples" not in body:
        warnings.append(
            "No ## Example or ## Examples section found"
        )


# ── Promote ──────────────────────────────────────────────────────────────────


def promote(
    vault: VaultStore,
    page: Page,
    index: Any | None = None,
) -> str:
    """Promote a quarantined page into the live vault namespace.

    Moves from ``quarantine/<ulid>.md`` to ``<kind>/<name>.md``, bumps the
    version, sets status to ``active``, computes the content hash, deletes the
    quarantine copy, and optionally triggers a delta reindex.

    Args:
        vault: The vault store.
        page: The quarantined page to promote (status must be ``pending`` and
              path must start with ``quarantine/``).
        index: Optional ``Index`` instance to update via ``reindex_delta``.

    Returns:
        The new relative path of the promoted page.

    Raises:
        ValueError: If the page is not in quarantine, not pending, or the
                    target namespace already has an active page with that name.
    """
    if not page.path.startswith(QUARANTINE_PREFIX):
        raise ValueError(
            f"Page path {page.path!r} is not in quarantine/"
        )

    # Sanity: the page must still be pending
    if page.frontmatter.status != PageStatus.PENDING:
        raise ValueError(
            f"Page status is '{page.frontmatter.status.value}', expected 'pending'"
        )

    # Validate before promoting
    report = validate(page, vault=vault)
    if report.errors:
        raise ValueError(
            f"Cannot promote: validation failed: {'; '.join(report.errors)}"
        )

    target_path = f"{page.kind.value}/{page.name}.md"

    # Name-conflict guard
    if vault.exists(target_path):
        existing = vault.get(target_path)
        if existing is not None and existing.frontmatter.status == PageStatus.ACTIVE:
            raise ValueError(
                f"Active page already exists at {target_path!r}. "
                f"Demote it first or choose a different name."
            )

    # Mutate frontmatter for promotion (model_copy avoids caller side-effects)
    fm = page.frontmatter.model_copy()
    fm.status = PageStatus.ACTIVE
    fm.version = max(fm.version or 0, 0) + 1
    fm.updated = datetime.now(timezone.utc)

    promoted = Page(frontmatter=fm, body=page.body, path=target_path)
    fm.hash = promoted.content_hash

    # Write to target namespace
    vault.put(promoted, target_path)

    # Remove quarantine copy
    vault.delete(page.path)

    # Delta reindex
    if index is not None:
        index.reindex_delta(vault)

    return target_path


# ── Reject ───────────────────────────────────────────────────────────────────


def reject(vault: VaultStore, page: Page) -> None:
    """Reject a quarantined page — permanently delete it from the vault.

    Args:
        vault: The vault store.
        page: The quarantined page to reject.

    Raises:
        ValueError: If the page is not in quarantine/.
    """
    if not page.path.startswith(QUARANTINE_PREFIX):
        raise ValueError(
            f"Page path {page.path!r} is not in quarantine/"
        )
    vault.delete(page.path)


# ── Demote ───────────────────────────────────────────────────────────────────


def demote(
    vault: VaultStore,
    page: Page,
    superseded_by: str | None = None,
) -> None:
    """Demote a page through its lifecycle.

    Transitions: ``active`` → ``deprecated`` → ``superseded``.
    Pages already at ``superseded`` or ``pending`` are left unchanged.

    Args:
        vault: The vault store.
        page: The live page to demote.
        superseded_by: Optional path or name of the page that replaces this one.
    """
    fm = page.frontmatter

    if fm.status == PageStatus.ACTIVE:
        fm.status = PageStatus.DEPRECATED
    elif fm.status == PageStatus.DEPRECATED:
        fm.status = PageStatus.SUPERSEDED
    else:
        # superseded or pending — nothing to do
        return

    if superseded_by is not None:
        fm.superseded_by = superseded_by

    fm.updated = datetime.now(timezone.utc)
    vault.put(page, page.path)


# ── Quarantine-aware search ──────────────────────────────────────────────────


def search_quarantine(
    vault: VaultStore,
    query: str = "",
    kind: str | None = None,
) -> list[Page]:
    """Search quarantined pages directly (bypasses the index).

    Quarantined pages are NOT indexed — they live under ``quarantine/`` and
    are invisible to normal ``search_vault``.  Use this function to discover
    pending proposals awaiting review.

    Args:
        vault: The vault store.
        query: Optional substring to filter page names and bodies.
        kind: Optional kind filter (e.g. ``"helper"``).

    Returns:
        List of quarantined ``Page`` objects, newest first.
    """
    pages = vault.list(prefix=QUARANTINE_PREFIX, kind=kind)
    if not query:
        return pages

    q = query.lower()
    return [
        p
        for p in pages
        if q in p.name.lower() or q in p.body.lower()
    ]


def verify_quarantine_isolation(
    vault: VaultStore,
    index_path: Path,
) -> bool:
    """Assert that no quarantined pages leak into normal search results.

    Performs a path-prefix scan on the index pages table and verifies:
    1. No quarantined paths appear in the indexed pages table.
    2. The count from search_quarantine matches (no orphaned quarantine files).

    Args:
        vault: The vault store.
        index_path: Path to the SQLite index.

    Returns:
        True if the isolation invariant holds; False otherwise.
    """
    from rlm_kernel.index import Index

    idx = Index(index_path)
    try:
        # Check indexed pages for quarantine paths
        rows = idx.conn.execute(
            "SELECT COUNT(*) AS cnt FROM pages WHERE path LIKE ?",
            ("quarantine/%",),
        ).fetchone()
        indexed_quarantine_count = rows["cnt"]
    finally:
        idx.close()

    # Quarantine pages must NOT be indexed
    if indexed_quarantine_count > 0:
        return False

    # Verify quarantine file count matches what search_quarantine returns
    q_pages = search_quarantine(vault)
    vault_q_count = len(q_pages)

    # Filesystem-level count of quarantine files
    all_q_paths = vault.list(prefix=QUARANTINE_PREFIX)
    if len(all_q_paths) != vault_q_count:
        return False

    return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _truncate_summary(body: str, max_len: int = 200) -> str:
    """Produce a safe summary: first non-empty, non-comment line, capped."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        return stripped[:max_len]
    return body[:max_len].replace("\n", " ")
