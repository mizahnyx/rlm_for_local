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
from typing import Any, Iterable

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
        executed: True if the helper sandbox actually ran (R19). False means
            the verdict came from static checks only.
    """

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    executed: bool = False


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


def validate(
    page: Page,
    vault: VaultStore | None = None,
    *,
    execute: bool = False,
) -> ValidationReport:
    """Validate a proposed page against kind-specific rules.

    Trust model (S3/R19): this is a **quality gate, not containment**. By
    default it is purely static — AST parse, import allowlist, blocked-pattern
    scan, and a static signature check. The restricted-builtin sandbox `exec`
    is an opt-in convenience for trusted authors (`execute=True`) and must
    never be relied on to contain hostile code: restricted-builtin `exec` is
    escapable on CPython.

    Args:
        page: The page to validate.
        vault: Optional vault for wikilink resolution checks
               (required for definition/note pages).
        execute: Run helper code in the sandbox namespace. Off by default;
            `rlm-kernel review` enables it only with `--execute`.

    Returns:
        ValidationReport with passed/errors/warnings and an `executed` flag
        telling the caller which mode produced the verdict.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not execute:
        warnings.append(
            "Static validation only — helper code was parsed, not executed. "
            "Pass execute=True (CLI: --execute) to run the sandbox check; "
            "note that the sandbox is a quality check, not a containment "
            "boundary."
        )

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
        _validate_helper(page, errors, warnings, execute=execute)
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
        executed=execute and kind == PageKind.HELPER,
    )


# ── Kind-specific validators ─────────────────────────────────────────────────


def _validate_helper(
    page: Page,
    errors: list[str],
    warnings: list[str],
    *,
    execute: bool = False,
) -> None:
    """Validate a helper page: syntax, import allowlist, blocked patterns.

    The sandbox execution stage runs only when `execute=True` (R19).
    """

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
    # A substring blocklist is a tripwire for the obvious cases, NOT a security
    # control: `().__class__.__bases__[0].__subclasses__()` walks straight past
    # it. See the module docstring for the trust model.
    code_lower = code.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower() in code_lower:
            errors.append(f"Blocked pattern detected: '{pattern}'")

    # --- 2d. Sandbox execution (opt-in, R19) ---
    if execute:
        _sandbox_test(code, helper.name, helper.signature, errors, warnings)
    else:
        # Static equivalents of the sandbox's two structural checks: the code
        # must define a callable with the page's name, and its `def` line must
        # agree with the declared `## Signature`.
        _static_define_check(tree, helper.name, errors)
        _static_signature_check(code, helper.name, helper.signature, warnings)


def _static_define_check(tree: ast.AST, name: str, errors: list[str]) -> None:
    """Error if the parsed helper code defines nothing callable named `name`."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return
    errors.append(f"Helper code does not define a callable named '{name}'")


def _static_signature_check(
    code: str,
    name: str,
    signature: str | None,
    warnings: list[str],
) -> None:
    """Warn when a helper's declared `## Signature` disagrees with its code.

    The static counterpart of :func:`_check_signature_match`, used when the
    sandbox is not run (R19).
    """
    if signature is None:
        return

    try:
        sig_tree = ast.parse(signature)
    except SyntaxError:
        warnings.append(f"Signature is not valid Python: {signature}")
        return

    declared_params: list[str] | None = None
    for node in ast.walk(sig_tree):
        if isinstance(node, ast.FunctionDef):
            declared_params = [arg.arg for arg in node.args.args]
            break
    if declared_params is None:
        warnings.append("Signature block does not contain a function definition")
        return

    try:
        impl_tree = ast.parse(code)
    except SyntaxError:
        return  # already reported as an error

    actual_params: list[str] | None = None
    for node in ast.walk(impl_tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            actual_params = [arg.arg for arg in node.args.args]
            break
    if actual_params is None:
        return  # "does not define a callable named ..." is reported elsewhere

    if declared_params != actual_params:
        warnings.append(
            f"Signature mismatch: declared {declared_params}, "
            f"actual {actual_params}"
        )


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


def _formatted_contract_paths() -> set[str]:
    """Contract paths whose bodies are `.format(**prompt_vars)`-ed at runtime (CL4).

    Imported lazily and defensively: this is the only kernel→rlm_local dependency
    in validation, and a standalone kernel install without `rlm_local` must still
    be able to validate pages (the check is simply skipped there).
    """
    try:
        from rlm_local.prompts import FORMATTED_CONTRACT_PATHS

        return set(FORMATTED_CONTRACT_PATHS)
    except Exception:
        return set()


def _check_slot_names(page: Page, slots: Iterable[str], errors: list[str]) -> None:
    """Reject slots the runtime cannot fill, for pages that get rendered (CL4).

    An evolved contract page that invents `{new_slot}` used to promote happily and
    then raise `KeyError` during prompt assembly — caught by the caller's broad
    `except`, so the promoted page was *silently ignored* and the packaged prompt
    used instead. Failing validation is the honest outcome: the author is told
    rather than the vault quietly keeping a page that can never take effect.

    Scoped deliberately: only pages that would land on a formatted contract path
    are checked. The `contract/templates/*` pages use `templates.py` slots
    (`{turn}`, `{used}`, `{budget}` …) that `prompt_vars` never fills, and are
    appended verbatim — checking them would be a false positive, and a gate that
    cries wolf gets switched off.
    """
    candidates = {page.path, f"{page.frontmatter.kind.value}/{page.name}.md"}
    if not (candidates & _formatted_contract_paths()):
        return

    try:
        from rlm_local.config import load_config

        known = set(load_config().prompt_vars())
    except Exception:
        return

    unknown = sorted(set(slots) - known)
    if unknown:
        errors.append(
            f"Unknown slot variable(s) {unknown}: this page is rendered with "
            f"prompt_vars, which provides {sorted(known)}. A slot the runtime "
            "does not fill raises KeyError at prompt assembly, and the page is "
            "then silently ignored (CL4)."
        )


def _validate_contract_or_template(
    page: Page, errors: list[str], warnings: list[str]
) -> None:
    """Validate contract/template pages: slot variables, length cap."""
    body = page.body

    # Length cap — measured in UTF-8 bytes, matching this constant's contract
    # (R14). Counting characters let a page of 3000 CJK characters — 9000 bytes
    # on disk — pass an "8192-byte" cap.
    body_bytes = len(body.encode("utf-8"))
    if body_bytes > MAX_BODY_LENGTH:
        errors.append(
            f"Body length {body_bytes} bytes exceeds max {MAX_BODY_LENGTH} bytes"
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
        _check_slot_names(page, slots, errors)


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
    target_path: str | None = None,
    *,
    force: bool = False,
) -> str:
    """Promote a quarantined page into the live vault namespace.

    Moves from ``quarantine/<ulid>.md`` to ``<kind>/<name>.md`` (or
    ``target_path`` if provided), bumps the version **relative to any page
    already occupying that path**, sets status to ``active``, computes the
    content hash, deletes the quarantine copy, and optionally triggers a delta
    reindex.

    Args:
        vault: The vault store.
        page: The quarantined page to promote.
        index: Optional ``Index`` instance for ``reindex_delta``.
        target_path: Optional override for the target path. When provided,
            the page is written to this exact path instead of the default
            ``<kind>/<name>.md``. Used by the optimizer to replace
            incumbent pages without changing their path (D-K4-1a).
        force: Overwrite a **non-active** occupant (deprecated/superseded) at
            the default target path. Has no effect when `target_path` is given:
            an explicit target is the caller taking responsibility (R16).

    Returns:
        The path the page was written to.

    Raises:
        ValueError: If validation fails, the page is not promotable, or the
            target path is occupied and `force` was not given.
    """
    if not page.path.startswith(QUARANTINE_PREFIX):
        raise ValueError(
            f"Page path {page.path!r} is not in quarantine/"
        )

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

    # Compute target path — override when replacing an incumbent (D-K4-1a)
    final_path = target_path if target_path is not None else f"{page.kind.value}/{page.name}.md"

    # Name-conflict guard (skipped when target_path is explicit — caller takes
    # responsibility). R16: this used to protect ACTIVE occupants only, so a
    # deprecated or superseded page at the target path was silently
    # overwritten, destroying the page that said "this was replaced by X".
    if target_path is None and vault.exists(final_path):
        existing = vault.get(final_path)
        if existing is not None:
            status = existing.frontmatter.status
            if status == PageStatus.ACTIVE:
                raise ValueError(
                    f"Active page already exists at {final_path!r}. "
                    f"Demote it first or choose a different name."
                )
            if not force:
                raise ValueError(
                    f"A {status.value} page already occupies {final_path!r}. "
                    f"Pass force=True (CLI: --force) to overwrite it."
                )
    # Mutate frontmatter for promotion (model_copy avoids caller side-effects)
    fm = page.frontmatter.model_copy()
    fm.status = PageStatus.ACTIVE
    # R11-2: version lineage follows the page being replaced. Proposed pages
    # always start at version 0, so a naive `version + 1` reset the counter to
    # 1 on every optimized promotion into an existing target path — after N
    # optimizations the live page still claimed version 1. Derive the new
    # version from the occupant instead.
    occupant = vault.get(final_path)
    prior_version = occupant.frontmatter.version if occupant is not None else 0
    fm.version = max(prior_version, fm.version or 0) + 1
    fm.updated = datetime.now(timezone.utc)

    promoted = Page(frontmatter=fm, body=page.body, path=final_path)
    fm.hash = promoted.content_hash

    # Write to target namespace
    vault.put(promoted, final_path)

    # Remove quarantine copy
    vault.delete(page.path)

    # Delta reindex
    if index is not None:
        index.reindex_delta(vault)

    return final_path


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
    if query:
        q = query.lower()
        pages = [
            p
            for p in pages
            if q in p.name.lower() or q in p.body.lower()
        ]

    # R16: the docstring always promised newest-first, but `vault.list` returns
    # lexicographic order and ULID filenames sort oldest-first. Sort explicitly
    # on the ULID prefix so the promise is true (and reviewers see the latest
    # proposals at the top).
    pages.sort(key=lambda p: Path(p.path).stem, reverse=True)
    return pages


def verify_quarantine_isolation(
    vault: VaultStore,
    index_path: Path,
) -> bool:
    """Assert that no quarantined pages leak into normal search results.

    Compares the index against a fresh walk of the vault (R14):

    1. No quarantined path may appear in the indexed ``pages`` table.
    2. Every indexed path must exist as a file in the vault walk — this is the
       falsifiable half. The previous second check compared
       ``search_quarantine(vault)`` with ``vault.list(prefix="quarantine")``,
       i.e. the same call twice, so it could never detect anything; an index
       row left behind for a page that no longer exists is exactly the leak
       this function claims to catch.
    3. Quarantine files found by the vault walk must not be in the index.

    Args:
        vault: The vault store.
        index_path: Path to the SQLite index.

    Returns:
        True if the isolation invariant holds; False otherwise.
    """
    from rlm_kernel.index import Index

    idx = Index(index_path)
    try:
        index_paths = {
            row["path"]
            for row in idx.conn.execute("SELECT path FROM pages").fetchall()
        }
    finally:
        idx.close()

    # 1. Quarantine pages must NOT be indexed
    indexed_quarantine = {
        path for path in index_paths
        if path == QUARANTINE_PREFIX or path.startswith(QUARANTINE_PREFIX + "/")
    }
    if indexed_quarantine:
        return False

    # 2. Index rows must correspond to real vault pages (no orphan rows).
    #    `vault.list()` never returns quarantine/ pages, so this is a check
    #    against the live namespace only.
    vault_paths = {page.path for page in vault.list()}
    if not index_paths <= vault_paths:
        return False

    # 3. Quarantine files exist on disk but must not be indexed.
    quarantine_paths = {page.path for page in vault.list(prefix=QUARANTINE_PREFIX)}
    if quarantine_paths & index_paths:
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
