"""Sample the files whose names are not valid UTF-8, so the owner can judge them (RO20).

Why this exists
---------------
98 paths in the corpus have a name that is not valid UTF-8. Their text is **not in the
search index** (`text_chunks.source` holds none of them), and the reason is measured: the
display column is TEXT, SQLite refuses a lone surrogate, and the mining tasks passed the
raw name into it — `mine_queue` carries `index_text 11 failed UnicodeEncodeError` and
`list_archive 3 failed UnicodeEncodeError` for exactly these.

Whether they *should* be indexed is the owner's call, because it is a naming-contract
question: the rendering that keeps the read path working lets a name with a genuine U+FFFD
and a name with an invalid byte **share one display** (measured), so two different files
can present the same address. `docs/20260921-1322-ro20-real-conditions.md` has the
measurement; this tool produces the input for the decision — what is actually in those
files, and what kinds of thing they are.

Privacy (AGENTS.md §1.9)
-----------------------
These files are corpus content and their names are corpus-derived identifiers. So:

* it prints **only aggregates** — counts, byte totals, MIME and kind distributions;
* the samples and the per-file table are written where the corpus is, into a directory
  refused if it is inside the corpus root, mode 0700, files 0600;
* a question id, a path or a passage never reaches stdout, a commit message or a document.

Usage (on the machine holding the corpus):

    python scripts/sample_encoding_wall.py \\
        --corpus-index ~/rlm-derived/corpus.sqlite \\
        --corpus-root /srv/corpus \\
        --out-dir ~/rlm-derived/encoding-wall --n 8

    # The category counts need no corpus and no read at all:
    python scripts/sample_encoding_wall.py --corpus-index ~/rlm-derived/corpus.sqlite \\
        --out-dir ~/rlm-derived/encoding-wall --counts-only
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_kernel.classify import DEFAULT_SNIFF_BYTES, sniff  # noqa: E402
from rlm_kernel.corpus import CorpusIndex, path_bytes, path_text  # noqa: E402
from rlm_kernel.mounts import (  # noqa: E402
    LocalTreeMount,
    ReadOnlyViolation,
    assert_derived_outside_corpus,
)

REPLACEMENT = "\ufffd"
#: How much of a file to show a reader. Enough to recognise the kind of document,
#: small enough that the sample file stays a sample.
PREVIEW_BYTES = 400
MAX_SNIFF_BYTES = DEFAULT_SNIFF_BYTES


def _lock_down(path: Path, mode: int) -> None:
    """Best effort on POSIX, harmless elsewhere — the samples are corpus text."""
    try:
        path.chmod(mode)
    except OSError:  # pragma: no cover - a platform without POSIX modes
        pass


def find_paths(index: CorpusIndex, *, limit: int | None = None) -> list[tuple[bytes, str, str, int]]:
    """`(raw, display, kind, size)` for every stored path holding U+FFFD.

    The stored `path` is already surrogate-free (`path_text`), which is why the query can
    run at all; `raw` is the exact bytes and is what the mount must be given.
    """
    sql = ("SELECT raw, path, kind, size FROM entries WHERE path LIKE ?"
           " ORDER BY kind, size DESC")
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = index._conn.execute(sql, (f"%{REPLACEMENT}%",)).fetchall()  # noqa: SLF001
    return [(bytes(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in rows]


def mime_of(display: str) -> str:
    """A guessed MIME type from the name, or `unknown`.

    A guess, and labelled as one on every line it appears: it is `mimetypes.guess_type`
    over the name, and these names are exactly the ones a MIME library has the least
    chance with (a damaged byte, no reliable extension in some cases).
    """
    guessed, _encoding = mimetypes.guess_type(display)
    return guessed or "unknown"


def preview(mount: LocalTreeMount, raw: bytes) -> tuple[str, str, int]:
    """`(sniffed kind, preview text, bytes read)` for one file, read-only and bounded.

    The preview is sanitised: surrogates become `\\uXXXX` so the sample file is storable,
    and the text is returned as-is otherwise, because judging what to index means seeing
    the actual content.
    """
    rel = raw.decode("utf-8", "surrogateescape")
    try:
        with mount.open_readonly(rel, max_bytes=PREVIEW_BYTES) as handle:
            data = handle.read(PREVIEW_BYTES)
    except (OSError, ReadOnlyViolation) as e:
        return f"unreadable:{type(e).__name__}", "", 0
    try:
        result = sniff(data)
        kind = f"{result.kind}"
        if result.encoding:
            kind += f"/{result.encoding}"
    except Exception as e:  # noqa: BLE001 - a sample tool must survive a weird file
        kind = f"sniff-failed:{type(e).__name__}"
    text = data.decode("utf-8", "replace")
    text = "".join(ch if ord(ch) < 0xD800 or ord(ch) > 0xDFFF else f"\\u{ord(ch):04x}"
                   for ch in text)
    return kind, text[:PREVIEW_BYTES], len(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-index", required=True,
                    help="Path index, e.g. ~/rlm-derived/corpus.sqlite")
    ap.add_argument("--corpus-root", default=None,
                    help="Read-only corpus root; needed for samples, not for counts")
    ap.add_argument("--out-dir", required=True,
                    help="Where the samples go. Refused inside the corpus root.")
    ap.add_argument("--n", type=int, default=8,
                    help="How many files to sample (per kind is not attempted; a spread "
                         "by size is taken)")
    ap.add_argument("--counts-only", action="store_true",
                    help="Report the categories and write nothing but the summary")
    args = ap.parse_args()

    index = CorpusIndex(Path(args.corpus_index).expanduser())
    rows = find_paths(index, limit=None if args.counts_only else max(args.n * 4, 40))
    if not rows:
        print("No stored path holds U+FFFD: this corpus has none, so there is nothing "
              "to decide. (The wall is historical.)")
        return 0

    kinds = Counter(kind for _raw, _d, kind, _s in rows)
    bytes_total = sum(size for _raw, _d, _k, size in rows)
    suffixes = Counter(
        (Path(display).suffix.lower() or "(none)") for _raw, display, _k, _s in rows)
    mimes = Counter(mime_of(display) for _raw, display, _k, _s in rows)

    print(f"stored paths holding U+FFFD: {len(rows)}")
    print(f"kinds: {dict(kinds)}")
    print(f"declared bytes: {bytes_total:,}")
    print(f"name suffixes (top 12): {dict(suffixes.most_common(12))}")
    print(f"guessed MIME types (top 12, a guess from the name): "
          f"{dict(mimes.most_common(12))}")

    out_dir = Path(args.out_dir).expanduser()
    if args.corpus_root is not None:
        assert_derived_outside_corpus(Path(args.corpus_root).expanduser(), out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _lock_down(out_dir, 0o700)

    summary = {
        "population": len(rows),
        "kinds": dict(kinds),
        "declared_bytes": bytes_total,
        "suffixes": dict(suffixes),
        "guessed_mime_types": dict(mimes),
        "note": ("MIME types are guessed from the name with mimetypes.guess_type; every "
                 "line of samples.jsonl carries guessed_mime=true to say so."),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _lock_down(out_dir / "summary.json", 0o600)
    print(f"\nwrote {out_dir / 'summary.json'}")

    if args.counts_only:
        index.close()
        return 0

    if not args.corpus_root:
        print("No --corpus-root, so no file was read and no sample was written. "
              "Pass it to see what is inside them.", file=sys.stderr)
        index.close()
        return 0

    mount = LocalTreeMount(args.corpus_root)
    # A spread by size: the largest few are where an indexing decision costs most, and
    # the smallest few are where it costs least. Both are worth seeing.
    ordered = sorted(rows, key=lambda r: r[3])
    chosen = ordered[: max(1, args.n // 2)] + ordered[-max(1, args.n // 2):]
    seen: set[bytes] = set()
    lines = []
    read_total = 0
    for raw, display, kind, size in chosen:
        if raw in seen:
            continue
        seen.add(raw)
        sniffed, text, n = preview(mount, raw)
        read_total += n
        lines.append(json.dumps({
            "display": display,
            "path_text_matches_entries_path": display == path_text(
                raw.decode("utf-8", "surrogateescape")),
            "kind": kind,
            "size_declared": size,
            "bytes_read": n,
            "sniffed_kind": sniffed,
            "guessed_mime": mime_of(display),
            "guessed_mime_is_a_guess": True,
            "preview": text,
        }, ensure_ascii=False))

    sample_path = out_dir / "samples.jsonl"
    sample_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _lock_down(sample_path, 0o600)

    print(f"sampled {len(lines)} file(s), {read_total} bytes read in total")
    print(f"wrote {sample_path}")
    print("These files hold corpus text. Read them where the corpus is; do not copy "
          "them anywhere else (AGENTS.md §1.9).")
    print("Nothing above this line names a file or quotes one.")
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
