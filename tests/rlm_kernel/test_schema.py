"""Tests for rlm_kernel.schema — page/frontmatter models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rlm_kernel.schema import (
    Frontmatter,
    HelperDef,
    Page,
    PageKind,
    PageStatus,
    extract_helper_code,
    extract_helper_signature,
    parse_page,
)


# ── Frontmatter validation ─────────────────────────────────────────────────

class TestFrontmatter:
    def test_minimal_valid(self):
        fm = Frontmatter(
            schema=1,
            kind="definition",
            name="test-concept",
            title="Test Concept",
            summary="A test concept for validation.",
        )
        assert fm.schema == 1
        assert fm.kind == PageKind.DEFINITION
        assert fm.status == PageStatus.ACTIVE  # default
        assert fm.version == 1  # default
        assert fm.tags == []  # default

    def test_all_kinds_accepted(self):
        for kind in ["contract", "template", "definition", "helper",
                      "fewshot", "note", "topic", "cache"]:
            fm = Frontmatter(schema=1, kind=kind, name="x", title="X", summary=".")
            assert fm.kind.value == kind

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            Frontmatter(schema=1, kind="bogus", name="x", title="X", summary=".")

    def test_invalid_schema_version_rejected(self):
        with pytest.raises(ValidationError):
            Frontmatter(schema=99, kind="definition", name="x", title="X", summary=".")

    def test_all_statuses_accepted(self):
        for status in ["active", "deprecated", "superseded", "pending"]:
            fm = Frontmatter(schema=1, kind="note", name="x", title="X",
                             summary=".", status=status)
            assert fm.status.value == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            Frontmatter(schema=1, kind="note", name="x", title="X",
                        summary=".", status="deleted")

    def test_ulid_auto_generated(self):
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        assert fm.id is not None
        assert len(fm.id) == 26  # ULID length

    def test_ulid_stable_when_provided(self):
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X",
                         summary=".", id="01JZK4ABCDEFGHIJKLMNOPQRST")
        assert fm.id == "01JZK4ABCDEFGHIJKLMNOPQRST"

    def test_summary_max_length(self):
        long_summary = "x" * 201
        with pytest.raises(ValidationError):
            Frontmatter(schema=1, kind="definition", name="x", title="X",
                        summary=long_summary)

    def test_summary_at_boundary(self):
        summary = "x" * 200
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X",
                         summary=summary)
        assert len(fm.summary) == 200

    def test_hash_must_be_valid_sha256(self):
        with pytest.raises(ValidationError):
            Frontmatter(schema=1, kind="helper", name="test", title="T",
                        summary=".", hash="not-a-hash")

    def test_hash_valid_sha256_accepted(self):
        fm = Frontmatter(schema=1, kind="helper", name="test", title="T",
                         summary=".", hash="sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789")
        assert fm.hash is not None

    def test_tags_default_empty(self):
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X", summary=".")
        assert fm.tags == []

    def test_tags_preserved(self):
        fm = Frontmatter(schema=1, kind="definition", name="x", title="X",
                         summary=".", tags=["test", "example"])
        assert fm.tags == ["test", "example"]


# ── Page parsing ───────────────────────────────────────────────────────────

class TestParsePage:
    def test_parse_valid_page(self):
        content = """---
schema: 1
kind: definition
name: test-concept
title: "Test Concept"
summary: "A test concept."
tags: [test]
---
# Test Concept

This is the body.
"""
        page = parse_page(content, path="definitions/test-concept.md")
        assert page.frontmatter.kind == PageKind.DEFINITION
        assert page.frontmatter.name == "test-concept"
        assert page.body.startswith("# Test Concept")
        assert page.path == "definitions/test-concept.md"

    def test_parse_with_no_frontmatter(self):
        content = "# Just a heading\n\nBody text."
        with pytest.raises(ValueError, match="frontmatter"):
            parse_page(content, path="test.md")

    def test_parse_preserves_frontmatter_order(self):
        content = """---
schema: 1
kind: note
name: my-note
title: "My Note"
summary: "A note."
tags: [a, b]
version: 5
status: deprecated
---
Body.
"""
        page = parse_page(content, path="notes/my-note.md")
        assert page.frontmatter.version == 5
        assert page.frontmatter.status == PageStatus.DEPRECATED

    def test_page_hash_computed(self):
        content = """---
schema: 1
kind: helper
name: test-helper
title: "Test"
summary: "."
---
body text
"""
        page = parse_page(content, path="helpers/test-helper.md")
        h = page.content_hash
        assert h.startswith("sha256:")
        assert len(h) == 71  # "sha256:" + 64 hex chars

    def test_page_hash_stable(self):
        content = "---\nschema: 1\nkind: note\nname: n\ntitle: T\nsummary: .\n---\nbody"
        h1 = parse_page(content, "a.md").content_hash
        h2 = parse_page(content, "a.md").content_hash
        assert h1 == h2


# ── Helper extraction ──────────────────────────────────────────────────────

class TestHelperExtraction:
    def test_extract_signature(self):
        body = """## Signature
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```
## Implementation
```python
def grep(pattern, max_hits=50):
    return []
```
"""
        sig = extract_helper_signature(body)
        assert "def grep(pattern: str, max_hits: int = 50)" in sig

    def test_extract_signature_missing(self):
        body = "## Something\nNo signature here."
        assert extract_helper_signature(body) is None

    def test_extract_code(self):
        body = """## Implementation
```python
def grep(pattern, max_hits=50):
    import re
    return []
```
"""
        code = extract_helper_code(body)
        assert "def grep(pattern, max_hits=50):" in code
        assert "import re" in code

    def test_extract_code_missing(self):
        body = "No implementation."
        assert extract_helper_code(body) is None

    def test_extract_code_returns_first_block_only(self):
        body = """## Implementation
```python
def first():
    pass
```
```python
def second():
    pass
```
"""
        code = extract_helper_code(body)
        assert "def first()" in code
        assert "def second()" not in code


# ── HelperDef ──────────────────────────────────────────────────────────────

class TestHelperDef:
    def test_from_page(self):
        content = """---
schema: 1
kind: helper
name: grep
title: "grep — regex search"
summary: "Search context with regex."
tags: [builtin, search]
---
## Signature
```python
def grep(pattern: str, max_hits: int = 50) -> list[str]: ...
```
## Implementation
```python
def grep(pattern, max_hits=50):
    import re
    return []
```
"""
        page = parse_page(content, "helpers/grep.md")
        hd = HelperDef.from_page(page)
        assert hd.name == "grep"
        assert hd.signature is not None
        assert "def grep(pattern, max_hits=50)" in hd.code

    def test_from_page_no_implementation(self):
        content = """---
schema: 1
kind: helper
name: empty
title: "E"
summary: "."
---
# No implementation
"""
        page = parse_page(content, "helpers/empty.md")
        with pytest.raises(ValueError, match="Implementation"):
            HelperDef.from_page(page)
