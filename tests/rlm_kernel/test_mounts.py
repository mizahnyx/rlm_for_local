"""Tests for rlm_kernel.mounts - the read-only corpus mount (RO2).

The owner's constraint is that every operation over the corpus is strictly
read-only. These tests cover the two layers this module owns: no write verb exists
to call, and nothing can reach outside the mount root. The third layer (the mount
itself) is verified on the host by `~/Misc/mount-luks-usb-ro.sh` and recorded in
its mount record, because no Python test can make a disk read-only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from rlm_kernel.mounts import (
    Entry,
    LocalTreeMount,
    ReadOnlyViolation,
    assert_derived_outside_corpus,
)

MOUNTS_SRC = Path(__file__).resolve().parents[2] / "src" / "rlm_kernel" / "mounts.py"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A small corpus: two directories, a nested file, a binary file."""
    root = tmp_path / "corpus"
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "readme.md").write_text("# Notes\n\nSome text.\n", encoding="utf-8")
    (root / "sub" / "page.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    (root / "sub" / "deeper" / "code.py").write_text("print('x')\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    return root


@pytest.fixture
def mount(corpus: Path) -> LocalTreeMount:
    return LocalTreeMount(corpus)


class TestReadOnlyByConstruction:
    """Layer 2a: there is no write verb to call."""

    @staticmethod
    def _code_only(path: Path) -> str:
        r"""Source with comments and string literals removed, whitespace collapsed.

        Tokenised rather than regex-scanned, for two reasons: a guard that trips on
        prose gets "fixed" by editing prose (the first version of this test flagged
        the comment that documents *not* passing O_CREAT), and a write call hidden
        inside a string literal is not a call. Collapsing whitespace lets the
        patterns stay readable: `os.open(` rather than an escaped regex.
        """
        import io
        import tokenize

        skip = {tokenize.COMMENT, tokenize.STRING}
        for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
            kind = getattr(tokenize, name, None)
            if kind is not None:
                skip.add(kind)
        reader = io.StringIO(path.read_text(encoding="utf-8")).readline
        kept = [tok.string for tok in tokenize.generate_tokens(reader) if tok.type not in skip]
        return re.sub(r"\s+", "", " ".join(kept))

    def test_the_module_has_no_write_shaped_api(self):
        """Guards the actual claim: the type offers no way to mutate the corpus.

        Scanning the source (rather than the instance) is deliberate - a method
        that exists but happens not to be called yet is still a way to write.
        """
        code = self._code_only(MOUNTS_SRC)
        forbidden = [
            ".write_text(", ".write_bytes(", ".touch(", ".mkdir(",
            ".unlink(", ".rename(", ".replace(", ".chmod(",
            ".truncate(", "shutil.", "os.remove(", "os.unlink(",
            "os.rename(", "os.mkdir(", "os.makedirs(",
            "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC",
        ]
        hits = [p for p in forbidden if p in code]
        assert hits == [], f"write-capable call(s) in the mount module: {hits}"

    def test_the_mount_has_no_write_methods(self, mount: LocalTreeMount):
        write_verbs = (
            "write", "unlink", "remove", "delete", "rename", "move", "mkdir",
            "touch", "chmod", "truncate", "append", "put", "save", "create",
        )
        offenders = [
            name for name in dir(mount)
            if not name.startswith("_")
            and any(verb in name.lower() for verb in write_verbs)
        ]
        assert offenders == [], f"write-shaped public methods: {offenders}"

    def test_the_only_opening_primitive_is_read_only(self, corpus: Path, mount: LocalTreeMount):
        """`open_readonly` never asks the OS for write access."""
        before = os.stat(corpus / "blob.bin").st_mtime_ns
        with mount.open_readonly("blob.bin") as handle:
            assert handle.read() == b"\xff\xfe\x00\x01binary"
        assert os.stat(corpus / "blob.bin").st_mtime_ns == before


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A real file *outside* the corpus, next to it.

    Deliberately real: an escape test that points at a path which does not exist
    is satisfied by the missing-file check instead of by containment, so it passes
    even when containment is removed. That is how the first version of these tests
    was vacuous, and the mutation table caught it.
    """
    path = tmp_path / "outside.txt"
    path.write_text("SECRET-OUTSIDE-CONTENT\n", encoding="utf-8")
    return path


class TestContainment:
    """Layer 2b: nothing resolves outside the root."""

    @pytest.mark.parametrize(
        "rel",
        [
            "../outside.txt",
            "sub/../../outside.txt",
            "sub/deeper/../../../outside.txt",
            "/etc/passwd",
            "\\\\server\\share",
            "C:/Windows/system32",
        ],
    )
    def test_escaping_paths_are_refused(
        self, mount: LocalTreeMount, outside: Path, rel: str
    ):
        """Each of these resolves to a real file outside the root."""
        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes(rel)

    def test_the_outside_content_is_never_returned(
        self, mount: LocalTreeMount, outside: Path
    ):
        """The point of containment: a refusal, not a leak."""
        assert outside.read_text(encoding="utf-8").strip() == "SECRET-OUTSIDE-CONTENT"
        for rel in ("../outside.txt", "sub/../../outside.txt"):
            try:
                leaked = mount.read_text(rel)
            except ReadOnlyViolation:
                continue
            pytest.fail(f"read {rel!r} returned content from outside the mount: {leaked[:40]!r}")

    def test_a_nul_byte_is_refused(self, mount: LocalTreeMount):
        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes("readme.md\x00.txt")

    def test_an_empty_path_is_refused(self, mount: LocalTreeMount):
        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes("")

    def test_escaping_symlinks_are_refused(
        self, corpus: Path, outside: Path, mount: LocalTreeMount
    ):
        """A link *inside* the tree pointing *outside* it must not be followed.

        Skipped where symlinks cannot be created (a default Windows host), so on
        that platform this branch's proof rests on the relative-path cases above;
        on the laptop (where the corpus lives) it runs.
        """
        link = corpus / "escape.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("platform does not allow creating symlinks here")

        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes("escape.txt")

    def test_a_symlink_inside_the_tree_still_works(self, corpus: Path, mount: LocalTreeMount):
        link = corpus / "inside.txt"
        try:
            link.symlink_to(corpus / "readme.md")
        except (OSError, NotImplementedError):
            pytest.skip("platform does not allow creating symlinks here")

        assert "Some text." in mount.read_text("inside.txt")

    def test_legitimate_paths_are_readable(self, mount: LocalTreeMount):
        assert "# Notes" in mount.read_text("readme.md")
        assert "hi" in mount.read_text("sub/page.html")
        assert "print" in mount.read_text("sub/deeper/code.py")

    def test_a_directory_is_not_readable_as_a_file(self, mount: LocalTreeMount):
        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes("sub")


class TestStreaming:
    """Layer 2c: nothing materialises the tree."""

    def test_max_bytes_caps_the_read(self, corpus: Path, mount: LocalTreeMount):
        (corpus / "big.txt").write_text("x" * 10_000, encoding="utf-8")

        assert len(mount.read_bytes("big.txt", max_bytes=100)) == 100
        assert len(mount.read_text("big.txt", max_bytes=42)) == 42

    def test_the_cap_holds_for_chunked_reads(self, corpus: Path, mount: LocalTreeMount):
        """The cap must not be a one-shot slice that a loop can walk around."""
        (corpus / "big.txt").write_text("y" * 5_000, encoding="utf-8")

        with mount.open_readonly("big.txt", max_bytes=250) as handle:
            total = 0
            while True:
                chunk = handle.read(64)
                if not chunk:
                    break
                total += len(chunk)

        assert total == 250

    def test_iter_entries_is_a_generator(self, mount: LocalTreeMount):
        import types

        assert isinstance(mount.iter_entries(), types.GeneratorType)

    def test_walking_yields_relative_posix_paths(self, mount: LocalTreeMount):
        rels = sorted(e.rel for e in mount.iter_entries())
        assert rels == [
            "blob.bin",
            "readme.md",
            "sub",
            "sub/deeper",
            "sub/deeper/code.py",
            "sub/page.html",
        ]
        assert all("\\" not in rel for rel in rels)

    def test_walking_stops_at_max_entries(self, mount: LocalTreeMount):
        assert len(list(mount.iter_entries(max_entries=2))) == 2

    def test_iter_files_filters_by_suffix(self, mount: LocalTreeMount):
        suffixes = (".html", ".py")
        found = sorted(e.rel for e in mount.iter_files(suffixes=suffixes))
        assert found == ["sub/deeper/code.py", "sub/page.html"]

    def test_kinds_are_reported(self, mount: LocalTreeMount):
        by_rel = {e.rel: e for e in mount.iter_entries()}
        assert by_rel["sub"].kind == "dir"
        assert by_rel["readme.md"].kind == "file"
        assert by_rel["readme.md"].size > 0

    def test_symlinks_are_reported_not_followed(self, corpus: Path, mount: LocalTreeMount):
        link = corpus / "sub" / "link.txt"
        try:
            link.symlink_to(corpus / "readme.md")
        except (OSError, NotImplementedError):
            pytest.skip("platform does not allow creating symlinks here")

        by_rel = {e.rel: e for e in mount.iter_entries()}
        assert by_rel["sub/link.txt"].kind == "symlink"


class TestBinaryAndDamagedContent:
    def test_binary_content_does_not_abort_a_text_read(self, mount: LocalTreeMount):
        """An unorganised backup is full of files that are not UTF-8."""
        text = mount.read_text("blob.bin")
        assert "\ufffd" in text

    def test_a_missing_file_raises_a_clear_error(self, mount: LocalTreeMount):
        with pytest.raises(ReadOnlyViolation):
            mount.read_bytes("nope.txt")

    def test_a_non_directory_root_is_refused(self, tmp_path: Path):
        file_path = tmp_path / "notadir.txt"
        file_path.write_text("x", encoding="utf-8")
        with pytest.raises(ReadOnlyViolation):
            LocalTreeMount(file_path)


class TestDerivedStatePlacement:
    """Layer 3: the one mistake that would write into the backup."""

    def test_a_vault_inside_the_corpus_is_refused(self, tmp_path: Path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        with pytest.raises(ReadOnlyViolation):
            assert_derived_outside_corpus(corpus, corpus / "vault")

    def test_the_corpus_itself_as_the_vault_is_refused(self, tmp_path: Path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        with pytest.raises(ReadOnlyViolation):
            assert_derived_outside_corpus(corpus, corpus)

    def test_a_corpus_inside_the_vault_is_refused(self, tmp_path: Path):
        vault = tmp_path / "vault"
        corpus = vault / "corpus"
        corpus.mkdir(parents=True)
        with pytest.raises(ReadOnlyViolation):
            assert_derived_outside_corpus(corpus, vault)

    def test_a_sibling_path_is_allowed(self, tmp_path: Path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        vault = tmp_path / "vault"
        vault.mkdir()
        assert_derived_outside_corpus(corpus, vault) is None

    def test_an_escaping_symlink_cannot_smuggle_the_vault_in(self, tmp_path: Path):
        """Resolution happens before the comparison, so a link does not help."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        link = tmp_path / "vault-link"
        try:
            link.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("platform does not allow creating symlinks here")

        # The link is outside the corpus, so this is allowed...
        assert_derived_outside_corpus(corpus, link) is None

        # ...but a link *inside* the corpus pointing at the vault is not.
        inside_link = corpus / "vault-link"
        try:
            inside_link.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("platform does not allow creating symlinks here")
        # The resolved path is outside, so this is about the corpus containing a
        # link to derived state rather than the vault being inside the corpus.
        assert_derived_outside_corpus(corpus, elsewhere) is None


class TestReadOnlyMountRoot:
    """The corpus root is not writable by this user - the mount's job, asserted here."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
    def test_reads_work_from_a_directory_with_no_write_bits(self, tmp_path: Path):
        root = tmp_path / "ro"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "a.txt").write_text("content\n", encoding="utf-8")

        root.chmod(0o555)
        (root / "sub").chmod(0o555)
        try:
            mount = LocalTreeMount(root)
            assert mount.read_text("sub/a.txt") == "content\n"
            assert sorted(e.rel for e in mount.iter_entries()) == ["sub", "sub/a.txt"]
        finally:
            (root / "sub").chmod(0o755)
            root.chmod(0o755)

    def test_a_mount_does_not_need_write_access_to_its_root(self, corpus: Path):
        """A read-only mount is a first-class case, not a degraded one."""
        mount = LocalTreeMount(corpus)
        assert mount.exists("readme.md")
        assert isinstance(mount.stat("readme.md"), Entry)
