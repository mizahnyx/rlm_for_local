"""The RO20 sampling tool: what it shows, and what it must never print.

The tool exists to let the owner decide whether the 98 files whose names are not valid
UTF-8 should be text-indexed. Its samples are corpus text and its table holds corpus
identifiers, so the property that matters most is not what it writes but what it
**prints**: aggregates only, and never a path, a name or a passage (`AGENTS.md` §1.9).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rlm_kernel.classify import classify_entries
from rlm_kernel.corpus import CorpusIndex, path_text
from rlm_kernel.mounts import LocalTreeMount

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sample_encoding_wall.py"

PLAIN_WORDS = "a plain sentence for the sample"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "plain.txt").write_text("ordinary\n", encoding="utf-8")
    # Two names that are not valid UTF-8, with recognisable content so the sample is
    # checkable, and one ordinary file that must never appear.
    (root / "docs" / b"na\xedve.txt".decode("ascii", "surrogateescape")).write_bytes(
        f"{PLAIN_WORDS} in the naive file\n".encode("utf-8"))
    (root / "docs" / b"caf\xe9.txt".decode("ascii", "surrogateescape")).write_bytes(
        f"{PLAIN_WORDS} in the cafe file\n".encode("utf-8"))
    return root


@pytest.fixture
def index(corpus: Path, tmp_path: Path) -> CorpusIndex:
    derived = tmp_path / "derived"
    derived.mkdir()
    idx = CorpusIndex.open_for(corpus, derived / "corpus.sqlite")
    idx.build(LocalTreeMount(corpus))
    idx.classifications().ensure()
    classify_entries(LocalTreeMount(corpus), idx.classifications())
    yield idx
    idx.close()


def _db_path(index: CorpusIndex) -> Path:
    return Path(index._conn.execute("PRAGMA database_list").fetchone()[2])  # noqa: SLF001


def _run(index_path: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    # `errors="replace"`: the tool's stdout is ASCII by construction, but the *platform*
    # console encoding is not always UTF-8, and a decode failure here would look like a
    # tool bug rather than a test-harness one.
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus-index", str(index_path),
         "--out-dir", str(out_dir), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class TestWhatItPrints:
    def test_stdout_carries_aggregates_only(
        self, corpus: Path, index: CorpusIndex, tmp_path: Path,
    ) -> None:
        out = tmp_path / "samples"
        proc = _run(_db_path(index), out, "--corpus-root", str(corpus), "--n", "4")
        assert proc.returncode == 0, proc.stderr
        printed = (proc.stdout or "") + (proc.stderr or "")
        # No content, no identifier. The assertion is on *the names and the text*, not on
        # the replacement character alone: the harness's own prose holds an em-dash, which
        # a non-UTF-8 console decodes to the same character, so checking for U+FFFD would
        # fail on the tool's own message rather than on a leak.
        assert PLAIN_WORDS not in printed, "a sample passage reached stdout"
        assert "naive.txt" not in printed, printed
        assert "caf" not in printed, printed
        assert "docs/" not in printed, printed
        assert not any(part in printed for part in ("\\ufffd", "\\udced", "\\xe9")), printed
        # Aggregates are there.
        assert "kinds:" in printed
        assert "guessed MIME" in printed
        assert "nothing above this line names a file" in printed.lower()

    def test_counts_only_reads_nothing_and_writes_no_samples(
        self, corpus: Path, index: CorpusIndex, tmp_path: Path,
    ) -> None:
        out = tmp_path / "counts"
        proc = _run(_db_path(index), out, "--counts-only")
        assert proc.returncode == 0, proc.stderr
        assert "kinds:" in proc.stdout
        assert not (out / "samples.jsonl").exists()
        assert (out / "summary.json").exists()

    def test_an_empty_population_says_so_and_invents_nothing(
        self, tmp_path: Path,
    ) -> None:
        """A corpus with no such name must not be reported as having some."""
        root = tmp_path / "clean"
        root.mkdir()
        (root / "ordinary.txt").write_text("nothing strange here\n", encoding="utf-8")
        derived = tmp_path / "d2"
        derived.mkdir()
        idx = CorpusIndex.open_for(root, derived / "corpus.sqlite")
        idx.build(LocalTreeMount(root))
        try:
            proc = _run(derived / "corpus.sqlite", tmp_path / "out")
            assert proc.returncode == 0, proc.stderr
            assert "nothing to decide" in proc.stdout.lower(), proc.stdout
        finally:
            idx.close()


class TestWhatItWrites:
    def test_the_samples_hold_the_content_and_the_rendering_claim(
        self, corpus: Path, index: CorpusIndex, tmp_path: Path,
    ) -> None:
        out = tmp_path / "written"
        db = Path(index._conn.execute("PRAGMA database_list").fetchone()[2])
        proc = _run(db, out, "--corpus-root", str(corpus), "--n", "4")
        assert proc.returncode == 0, proc.stderr

        rows = [json.loads(line) for line in
                (out / "samples.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows, "the tool must sample something"
        assert all(row["guessed_mime_is_a_guess"] for row in rows), (
            "a guessed MIME type must be labelled as a guess on every line"
        )
        assert any(PLAIN_WORDS in row["preview"] for row in rows), (
            "the preview must be the file's actual content, or the owner cannot judge it"
        )
        # The claim the read path depends on: the display is exactly `path_text` of the
        # name, which is what `entries.path` holds and what `raw_for` resolves.
        assert all(row["path_text_matches_entries_path"] for row in rows)
        for row in rows:
            assert "\ufffd" in row["display"], row["display"]
            assert path_text(row["display"]) == row["display"]

    def test_the_output_directory_is_locked_down(
        self, corpus: Path, index: CorpusIndex, tmp_path: Path,
    ) -> None:
        out = tmp_path / "locked"
        db = Path(index._conn.execute("PRAGMA database_list").fetchone()[2])
        _run(db, out, "--corpus-root", str(corpus), "--n", "4")
        import os
        import stat
        if os.name == "posix":
            assert stat.S_IMODE(out.stat().st_mode) == 0o700
            assert stat.S_IMODE((out / "samples.jsonl").stat().st_mode) == 0o600

    def test_an_output_dir_inside_the_corpus_is_refused(
        self, corpus: Path, index: CorpusIndex,
    ) -> None:
        """Layer 3: derived state inside the corpus is the mistake the mount exists to stop."""
        db = Path(index._conn.execute("PRAGMA database_list").fetchone()[2])
        proc = _run(db, corpus / "inside", "--corpus-root", str(corpus), "--n", "2")
        assert proc.returncode != 0, proc.stdout
        assert "outside" in (proc.stderr + proc.stdout).lower()
