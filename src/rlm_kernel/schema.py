"""Page and frontmatter models — schema version 1 (§3.2).

The frontmatter is the structured envelope of every wiki page.
Pages are the universal medium: everything (contracts, helpers, definitions,
few-shots, memory) is a page.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from rlm_kernel._ulid import new as _ulid_new
from pydantic import AliasChoices, BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────

class PageKind(str, Enum):
    CONTRACT = "contract"
    TEMPLATE = "template"
    DEFINITION = "definition"
    HELPER = "helper"
    FEWSHOT = "fewshot"
    NOTE = "note"
    TOPIC = "topic"
    CACHE = "cache"


class PageStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    PENDING = "pending"


# ── ULID helper ────────────────────────────────────────────────────────────

def _new_ulid() -> str:
    return _ulid_new()
# ── Frontmatter ────────────────────────────────────────────────────────────

SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")

VALID_SCHEMA_VERSIONS = {1}

# S4/R20: `name` is model-controlled and is used to build page paths
# (`promote` defaults to `f"{kind}/{name}.md"`), so it must not be able to
# carry a separator, a traversal segment, or leading whitespace/dot. The
# charset is deliberately narrow; legitimate names like `my.helper-v2` pass.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Frontmatter(BaseModel):
    """Validated frontmatter for a wiki page (§3.2)."""

    # R23: this field was named `schema`, which shadowed pydantic's deprecated
    # `BaseModel.schema` and produced a UserWarning on every import. Renamed to
    # `schema_version`, with the old key still accepted on *read* so pages written
    # before the rename load unchanged (dual-parse), and
    # `migrate_schema_key()`/`LocalVault.migrate_schema_keys()` to rewrite them.
    schema_version: int = Field(
        default=1,
        ge=1,
        le=max(VALID_SCHEMA_VERSIONS),
        validation_alias=AliasChoices("schema_version", "schema"),
    )
    id: str = Field(default_factory=_new_ulid, min_length=26, max_length=26)
    kind: PageKind
    name: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=0)
    hash: str | None = Field(default=None)
    status: PageStatus = Field(default=PageStatus.ACTIVE)
    superseded_by: str | None = Field(default=None)
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # R12: memory-decay bookkeeping. Optional and defaulted, so pages written
    # before these fields existed parse unchanged (backward compatible), and
    # they are deliberately excluded from `content_hash` — a search hit must
    # not look like a content change to the index's delta pass.
    access_count: int = Field(default=0, ge=0)
    last_access: datetime | None = Field(default=None)

    model_config = {"use_enum_values": False, "populate_by_name": True}

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, v: int) -> int:
        if v not in VALID_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported schema version: {v}. Supported: {VALID_SCHEMA_VERSIONS}")
        return v

    @field_validator("hash")
    @classmethod
    def _check_hash(cls, v: str | None) -> str | None:
        if v is not None and not SHA256_PATTERN.match(v):
            raise ValueError(f"hash must match pattern sha256:<64-hex-chars>, got: {v}")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        """Reject names that could escape the vault when turned into a path (S4)."""
        if not NAME_PATTERN.match(v):
            raise ValueError(
                "name must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ "
                f"(no separators, traversal, leading dot or whitespace), got: {v!r}"
            )
        return v

    def to_yaml(self) -> str:
        """Serialize frontmatter to YAML block (without body)."""
        import yaml

        d = self.model_dump(mode="json", exclude_none=True)
        # Ensure clean ordering
        ordered = {}
        for key in [
            "schema_version", "id", "kind", "name", "title", "summary",
            "tags", "version", "hash", "status", "superseded_by",
            "created", "updated", "access_count", "last_access",
        ]:
            if key in d and d[key] is not None:
                val = d[key]
                # Convert datetime to ISO string
                if isinstance(val, str) and key in ("created", "updated"):
                    pass  # already a string
                ordered[key] = val
        return yaml.dump(ordered, default_flow_style=False, allow_unicode=True,
                         sort_keys=False).strip()


# ── Page ───────────────────────────────────────────────────────────────────

class Page:
    """A complete wiki page: frontmatter + markdown body."""

    __slots__ = ("frontmatter", "body", "_path")

    def __init__(self, frontmatter: Frontmatter, body: str, path: str = "") -> None:
        self.frontmatter = frontmatter
        self.body = body
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = value

    @property
    def kind(self) -> PageKind:
        return self.frontmatter.kind

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def content_hash(self) -> str:
        """SHA-256 hash of stable page content (body + identity fields, excluding timestamps)."""
        fm = self.frontmatter
        stable = f"{fm.kind.value}:{fm.name}:{fm.title}:{fm.summary}:{':'.join(sorted(fm.tags))}\n{self.body}"
        return "sha256:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        """Serialize to full markdown file content."""
        yaml_block = self.frontmatter.to_yaml()
        return f"---\n{yaml_block}\n---\n{self.body}"

    def __repr__(self) -> str:
        return f"Page(kind={self.kind.value}, name={self.name!r}, path={self.path!r})"


# ── Parsing ────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SCHEMA_KEY_RE = re.compile(r"^schema:([ \t]*)(.*)$", re.MULTILINE)
_SCHEMA_VERSION_KEY_RE = re.compile(r"^schema_version:", re.MULTILINE)


def migrate_schema_key(content: str) -> tuple[str, bool]:
    """Rename the frontmatter `schema:` key to `schema_version:` (R23).

    Returns ``(content, changed)``.

    Textual and confined to the frontmatter block, on purpose: a migration should
    change the one key it is about. Re-serializing the page through
    `Frontmatter.to_yaml()` would also reorder fields, restamp nothing but drop
    any key the current model does not know, and rewrite formatting — a much
    larger change than the one being made.

    If both keys are present the new one wins on read (it is first in the
    validation alias), so the stale `schema:` line is removed rather than renamed,
    leaving a single source of truth in the file.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return content, False

    block = m.group(1)
    match = _SCHEMA_KEY_RE.search(block)
    if not match:
        return content, False

    if _SCHEMA_VERSION_KEY_RE.search(block):
        # Drop the stale line (leading newline included) — the new key is present.
        start = match.start()
        if start > 0 and block[start - 1] == "\n":
            start -= 1
        new_block = block[:start] + block[match.end():]
    else:
        new_block = (
            block[:match.start()]
            + "schema_version:" + match.group(1) + match.group(2)
            + block[match.end():]
        )

    return content[:m.start(1)] + new_block + content[m.end(1):], True


def parse_page(content: str, path: str = "") -> Page:
    """Parse a markdown page with YAML frontmatter into a Page object."""
    import yaml

    m = _FRONTMATTER_RE.match(content)
    if not m:
        raise ValueError(f"No valid frontmatter found in page at {path!r}")

    fm_raw = m.group(1)
    body = content[m.end():]

    fm_dict = yaml.safe_load(fm_raw)
    if not isinstance(fm_dict, dict):
        raise ValueError(f"Frontmatter is not a mapping in {path!r}")

    frontmatter = Frontmatter.model_validate(fm_dict)
    return Page(frontmatter=frontmatter, body=body, path=path)


_SIGNATURE_RE = re.compile(
    r"##\s+Signature\s*\n+```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)
_IMPLEMENTATION_RE = re.compile(
    r"##\s+Implementation\s*\n+```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)


def extract_helper_signature(body: str) -> str | None:
    """Extract the function signature from a helper page body."""
    m = _SIGNATURE_RE.search(body)
    if m:
        return m.group(1).strip()
    return None


def extract_helper_code(body: str) -> str | None:
    """Extract the implementation code from a helper page body (first fenced Python block under ## Implementation)."""
    m = _IMPLEMENTATION_RE.search(body)
    if m:
        return m.group(1).strip()
    return None


# ── HelperDef ──────────────────────────────────────────────────────────────

class HelperDef:
    """A helper function definition extracted from a helper page.

    This is the payload sent to the REPL worker for injection.
    """

    __slots__ = ("name", "signature", "code", "summary")

    def __init__(self, name: str, code: str, signature: str | None = None,
                 summary: str = "") -> None:
        self.name = name
        self.signature = signature
        self.code = code
        self.summary = summary

    @classmethod
    def from_page(cls, page: Page) -> HelperDef:
        """Extract a HelperDef from a parsed helper Page."""
        if page.kind != PageKind.HELPER:
            raise ValueError(f"Page {page.path!r} is not a helper (kind={page.kind.value})")

        code = extract_helper_code(page.body)
        if code is None:
            raise ValueError(f"Helper page {page.path!r} has no ## Implementation block")

        signature = extract_helper_signature(page.body)
        return cls(
            name=page.name,
            code=code,
            signature=signature,
            summary=page.frontmatter.summary,
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "code": self.code}
