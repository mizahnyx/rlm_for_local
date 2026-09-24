"""CLI frontend for rlm — the RLM harness command group (D1).

Subcommands:
  rlm ask        Run a single completion with Markdown context assembly
  rlm chat       Interactive chat loop with session context
  rlm ingest     Ingest Markdown documents into the vault as pages
  rlm search     Hybrid search over the vault
  rlm get        Print a vault page
  rlm tag        Tag a vault page (and reindex)
  rlm check      Model suitability battery
  rlm vault      Pass-through to rlm-kernel vault management
  rlm optimize   GEPA offline optimization
  rlm corpus     Read-only corpus access: build the path index, search, read (RO3)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _default_vault() -> Path:
    return Path.home() / ".local" / "share" / "rlm-kernel" / "vault"


def build_parser() -> argparse.ArgumentParser:
    """Build the `rlm` argument parser.

    Split out of `main()` so the parser itself is testable — notably the
    `--kind` choices, which must stay a subset of `PageKind` values and must
    describe a directory convention that exists (R13/R16).
    """
    parser = argparse.ArgumentParser(
        prog="rlm",
        description="RLM harness — recursive reasoning with local models",
    )
    sub = parser.add_subparsers(dest="command")

    # ── ask ──────────────────────────────────────────────────────────────
    p_ask = sub.add_parser("ask", help="Run a single completion")
    p_ask.add_argument("query", help="The question or task")
    p_ask.add_argument("--context-file", action="append", default=[],
                       type=Path, dest="context_files",
                       help="Markdown file(s) to use as context (repeatable)")
    p_ask.add_argument("--context-dir", type=Path, default=None,
                       help="Directory of .md files to use as context")
    p_ask.add_argument("--stdin", action="store_true",
                       help="Read context from stdin")
    p_ask.add_argument("--vault", type=str, default=None,
                       help="Vault page path to use as context")
    p_ask.add_argument("--profile", default="laptop",
                       choices=["tiny", "laptop", "workstation"])
    p_ask.add_argument("--max-turns", type=int, default=None)
    p_ask.add_argument(
        "--cell-timeout", type=float,
        default=os.environ.get("RLM_CELL_TIMEOUT"),
        help="Seconds one REPL cell may run (default: the profile's value — 60 s on "
             "tiny/laptop, 120 s on workstation; env RLM_CELL_TIMEOUT). Raise it when "
             "a legitimate corpus call exceeds the budget on a loaded host: a cell "
             "stopped by the harness is not a model failure, and the trajectory "
             "records it as a `cell_timeout` event naming the budget and the helper.",
    )
    p_ask.add_argument(
        "--cell-timeout-hard", type=float,
        default=os.environ.get("RLM_CELL_TIMEOUT_HARD"),
        help="Seconds one REPL cell may run in total, after the soft limit has "
             "signalled that the cell is doing work (default: the profile's value — "
             "3600 s; env RLM_CELL_TIMEOUT_HARD). Raised from 1200 s on 2026-09-23 "
             "because a search on this corpus measured p95 ≥ 60 s and one cell was "
             "stopped at the old limit while running one. Set it equal to "
             "--cell-timeout to switch the second limit off: a cell then stops at the "
             "soft limit whether or not it was working.",
    )
    p_ask.add_argument("--log-path", type=Path, default=None,
                       help="Write trajectory JSONL to this path")
    _add_model_server_flags(p_ask)
    _add_corpus_flags(p_ask, require_root=False, require_index=False)

    # ── chat ─────────────────────────────────────────────────────────────
    p_chat = sub.add_parser("chat", help="Interactive chat loop")
    p_chat.add_argument("--profile", default="laptop",
                        choices=["tiny", "laptop", "workstation"])
    p_chat.add_argument("--vault-path", type=Path, default=None,
                        help="Path to vault root")
    _add_model_server_flags(p_chat)

    # ── ingest ───────────────────────────────────────────────────────────
    p_ingest = sub.add_parser("ingest", help="Ingest Markdown into vault")
    p_ingest.add_argument("paths", nargs="+", type=Path,
                          help="Markdown files to ingest")
    p_ingest.add_argument("--kind", default="note",
                          choices=["note", "definition", "topic"])
    p_ingest.add_argument("--tags", default="",
                          help="Comma-separated tags")
    p_ingest.add_argument("--vault", type=Path, default=_default_vault())

    # ── search ───────────────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="Search the vault")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--kind", nargs="*", default=None)
    p_search.add_argument("--tag", nargs="*", default=None)
    p_search.add_argument(
        "--status", nargs="*", default=None,
        choices=["active", "deprecated", "superseded", "pending"],
        help="Page statuses to include (default: active only — retired pages "
             "stop answering queries, F14/CL1)",
    )
    p_search.add_argument("-k", type=int, default=5)
    p_search.add_argument("--vault", type=Path, default=_default_vault())

    # ── get ──────────────────────────────────────────────────────────────
    p_get = sub.add_parser("get", help="Print a vault page")
    p_get.add_argument("path", help="Page path within vault")
    p_get.add_argument("--vault", type=Path, default=_default_vault())

    # tag subcommand
    p_tag = sub.add_parser("tag", help="Add or remove tags from a vault page")
    p_tag.add_argument("path", help="Page path within vault")
    p_tag.add_argument("--add", default="", help="Comma-separated tags to add")
    p_tag.add_argument("--remove", default="", help="Comma-separated tags to remove")
    p_tag.add_argument("--vault", type=Path, default=_default_vault())
    # ── check ────────────────────────────────────────────────────────────
    p_check = sub.add_parser("check", help="Model suitability battery")
    p_check.add_argument("model_id", help="Model ID on the server")
    p_check.add_argument(
        "--endpoint",
        default=os.environ.get("RLM_ENDPOINT", "https://localhost:9010/v1"),
        help="Base URL of the model server (env RLM_ENDPOINT)",
    )
    p_check.add_argument("--quick", action="store_true")
    p_check.add_argument("--profile", default="tiny")
    p_check.add_argument(
        "--weights",
        default=os.environ.get("RLM_CHECK_WEIGHTS", "default"),
        help=("named battery weighting (default: default; use p1-heavy to "
              "reproduce the scores recorded on 2026-09-11) [env RLM_CHECK_WEIGHTS]"),
    )

    # ── vault ────────────────────────────────────────────────────────────
    p_vault = sub.add_parser("vault", help="Vault management (pass-through to rlm-kernel)")
    p_vault.add_argument("vault_args", nargs=argparse.REMAINDER,
                         help="Arguments forwarded to rlm-kernel")

    # ── optimize ─────────────────────────────────────────────────────────
    p_opt = sub.add_parser("optimize", help="GEPA offline optimization")
    p_opt.add_argument("--target", default="how-to-work",
                       choices=["prologue", "how-to-work", "nudges",
                                "fewshots", "helper-docs"])
    p_opt.add_argument("--suite", default="needle_search")
    p_opt.add_argument("--profile", default="tiny")
    p_opt.add_argument("--max-calls", type=int, default=150)
    p_opt.add_argument("--vault", type=Path, default=_default_vault())

    # ── corpus ───────────────────────────────────────────────────────────
    p_corpus = sub.add_parser(
        "corpus",
        help="Read-only corpus access: index, search, read (RO3/RO4)",
        description=(
            "Work with a read-only corpus. `index` walks the corpus once and "
            "records its PATHS (never file contents) in a SQLite file outside "
            "the corpus; every other subcommand reads through the read-only "
            "mount. The corpus is never written to."
        ),
    )
    corpus_sub = p_corpus.add_subparsers(dest="corpus_command")

    p_cidx = corpus_sub.add_parser("index", help="Build the corpus path index")
    _add_corpus_flags(p_cidx, require_root=True, require_index=True)
    p_cidx.add_argument("--progress-every", type=int, default=250_000,
                        help="Print a running entry count every N entries")

    p_cstatus = corpus_sub.add_parser(
        "status", help="Aggregate counts for the corpus (no paths printed)"
    )
    _add_corpus_flags(p_cstatus, require_root=False, require_index=True)

    p_cfind = corpus_sub.add_parser("find", help="Search the path index")
    _add_corpus_flags(p_cfind, require_root=False, require_index=True)
    p_cfind.add_argument("query")
    p_cfind.add_argument("-k", "--limit", type=int, default=20)
    p_cfind.add_argument("--kind", default=None,
                         help="file | dir | symlink | other")
    p_cfind.add_argument("--under", default="", help="Restrict to a subtree")

    p_cread = corpus_sub.add_parser("read", help="Read one corpus file (bounded)")
    _add_corpus_flags(p_cread, require_root=True, require_index=False)
    p_cread.add_argument("rel", help="Path relative to the corpus root")
    p_cread.add_argument("--max-bytes", type=int, default=20_000)

    p_ccount = corpus_sub.add_parser("count", help="Count entries in the index")
    _add_corpus_flags(p_ccount, require_root=False, require_index=True)
    p_ccount.add_argument("--kind", default=None)
    p_ccount.add_argument("--under", default="")

    p_cverify = corpus_sub.add_parser(        "verify",
        help="Read-only proof: what changed in the corpus since a marker",
        description=(
            "Walk the corpus through the mount provider and report, in "
            "aggregates only, what has an mtime after a marker. This is the "
            "proof obligation of AGENTS.md 1.8 done by the harness rather than "
            "by hand-typed shell — and it distinguishes 'written during this "
            "run' from 'already dated in the future', which a bare "
            "`find -newer` cannot."
        ),
    )
    _add_corpus_flags(p_cverify, require_root=True, require_index=False)
    p_cverify.add_argument("--since", default=None,
                           help="ISO-8601 timestamp or epoch seconds to compare against")
    p_cverify.add_argument("--since-file", type=Path, default=None,
                           help="Use this file's mtime as the marker")
    p_cverify.add_argument("--sample", type=int, default=1000,
                           help="Stop after this many newer entries (0 = scan everything)")
    p_cverify.add_argument("--run-started", default=None,
                           help="When this run started, to judge whether the newer "
                                "mtimes could be ours (default: the marker)")

    p_cdigest = corpus_sub.add_parser(
        "digest",
        help="Snapshot the whole corpus as a 32-byte digest (for a before/after proof)",
        description=(
            "Reduce every entry's path, kind, size and mtime to one digest. Two "
            "digests that match are a complete proof that nothing changed — "
            "unlike a marker scan, which cannot clear a corpus containing "
            "future-dated files. Take one from the index (cheap, no filesystem "
            "access) and one from a fresh walk (a full pass), and compare."
        ),
    )
    _add_corpus_flags(p_cdigest, require_root=False, require_index=False)
    p_cdigest.add_argument("--from-index", action="store_true",
                           help="Digest the index instead of walking the corpus")
    p_cdigest.add_argument("--out", type=Path, default=None,
                           help="Write the JSON snapshot here (to compare later)")
    p_cdigest.add_argument("--compare", type=Path, default=None,
                           help="Compare this snapshot file with the one just taken")

    p_cclassify = corpus_sub.add_parser(
        "classify",
        help="Stage 1: read each file's head and record what it actually is",
        description=(
            "Read the head of every file (bounded, O_RDONLY, through the "
            "read-only mount) and record kind, encoding and a content hash beside "
            "the path index. This is what tells text from databases, media and "
            "vendored blobs when the extension does not — the 1.56M files Stage 0 "
            "could not classify. Resumable: it commits in batches and skips what "
            "is already done. Prints aggregates only."
        ),
    )
    _add_corpus_flags(p_cclassify, require_root=True, require_index=True)
    p_cclassify.add_argument("--sniff-bytes", type=int, default=8192,
                             help="How many bytes to read for the sniff")
    p_cclassify.add_argument("--hash-bytes", type=int, default=65536,
                             help="How many bytes to hash (together with the size)")
    p_cclassify.add_argument("--hash-mode", choices=["head", "none"], default="head",
                             help="'head' hashes the head window plus the size; "
                                  "'none' skips hashing entirely")
    p_cclassify.add_argument("--batch-size", type=int, default=2000)
    p_cclassify.add_argument("--limit", type=int, default=None,
                             help="Classify at most N files (smoke tests, pilots)")
    p_cclassify.add_argument("--redo", action="store_true",
                             help="Re-classify files that already have a row")
    p_cclassify.add_argument("--progress-every", type=int, default=100_000)

    p_csearch = corpus_sub.add_parser(
        "search",
        help="Search the text index: words inside the corpus, with citations",
        description=(
            "Search what mining has indexed — the text of files and of everything "
            "extracted from documents. Every hit is an address "
            "(source#L<byte_start>-<byte_end>) that can be re-read, derived text is "
            "labelled with the engine that produced it, vendored matches are "
            "counted rather than hidden, and the result says how much of the "
            "corpus is indexed at all."
        ),
    )
    _add_corpus_flags(p_csearch, require_root=True, require_index=True)
    p_csearch.add_argument("query")
    p_csearch.add_argument("-k", "--limit", type=int, default=8)
    p_csearch.add_argument("--include-vendored", action="store_true",
                           help="Include matches from vendored/build/cache paths")
    p_csearch.add_argument("--derived-only", action="store_true",
                           help="Only matches from extracted text (PDF, office, OCR)")
    p_csearch.add_argument("--count-only", action="store_true",
                           help="Print match counts and coverage, never text")
    p_csearch.add_argument("--coverage", action="store_true",
                           help="Print indexing coverage and exit")
    p_csearch.add_argument("--cache-root", type=Path, default=None)

    p_csample = corpus_sub.add_parser(
        "sample",
        help="Print random passages with their addresses, to devise questions from",
        description=(
            "Draw real passages out of the index, each with the address that re-reads "
            "it, so questions can be written against the corpus instead of against a "
            "fixture. Nothing is searched and nothing is counted: the draw probes a "
            "random chunk id, so it costs a handful of indexed lookups however large "
            "the index is. Reproducible with --seed, and vendored paths and "
            "container-member chunks are left out unless asked for. The output is "
            "corpus text: it may be read where the corpus is and must not travel "
            "(AGENTS.md §1.9)."
        ),
    )
    _add_corpus_flags(p_csample, require_root=True, require_index=True)
    p_csample.add_argument("-n", "--n", type=int, default=5,
                           help="How many passages to draw (default 5)")
    p_csample.add_argument("--chars", type=int, default=2000,
                           help="Clip each passage to this many characters "
                                "(default 2000; 0 prints the whole chunk)")
    p_csample.add_argument("--seed", type=int, default=None,
                           help="Seed the draw, so the same passages can be produced "
                                "again; the seed is printed either way")
    p_csample.add_argument("--include-vendored", action="store_true",
                           help="Allow passages from vendored/build/cache paths")
    p_csample.add_argument("--include-derived", action="store_true",
                           help="Allow passages extracted from containers (PDF, "
                                "office, archives) — the addresses that still re-read "
                                "slowly (RO11)")
    p_csample.add_argument("--cache-root", type=Path, default=None)
    p_csample.add_argument("--any", dest="any_text", action="store_true",
                           help="Draw any indexed text, prose or not. The default "
                                "prioritises human prose: a question devised from minified "
                                "JavaScript or a subtitle file is not a question about the "
                                "corpus a person would ask (owner, 2026-09-22).")
    p_csample.add_argument("--floor", type=float, default=None,
                           help="Override the prose floor (0..1). Lower it when the draw "
                                "keeps rejecting; the floor in force is printed either way.")

    p_creindex = corpus_sub.add_parser(
        "reindex-encodings",
        help="Re-index the text files whose encoding is not UTF-8 (RO19)",
        description=(
            "Indexing used to decode every source as UTF-8 with replacement "
            "characters, so words with accented vowels were unsearchable in the "
            "46,735 files the sniffer recorded as cp1252 or latin-1. New indexing "
            "uses the recorded encoding; this repairs the sources that were indexed "
            "before it did. Addresses do not move: byte offsets still name the raw "
            "file, and only the tokens change. Idempotent — a source whose chunks "
            "already carry an encoding is skipped — so it can be run in slices with "
            "--limit. Reads through the read-only mount; writes only the derived "
            "index."
        ),
    )
    _add_corpus_flags(p_creindex, require_root=True, require_index=True)
    p_creindex.add_argument("--limit", type=int, default=0,
                            help="Stop after this many sources (0 = all of them)")
    p_creindex.add_argument("--progress-every", type=int, default=500)

    p_ccounters = corpus_sub.add_parser(
        "counters",
        help="Show or refresh the published coverage snapshot a search quotes",
        description=(
            "How much of the corpus is searchable, as one line. Without "
            "--refresh this only *reads* the last published snapshot and says how "
            "old it is; a search quotes exactly this and never counts the chunk "
            "table itself, because counting 25M chunks takes ~16 minutes and a "
            "REPL cell has 120 seconds. --refresh is the deliberate, slow way to "
            "compute a new one: run it when the index is idle, after a mining "
            "window, or when a search reports coverage as unknown."
        ),
    )
    _add_corpus_flags(p_ccounters, require_root=False, require_index=True)
    p_ccounters.add_argument("--refresh", action="store_true",
                             help="Recompute the snapshot now (reads the whole index)")

    p_cfresh = corpus_sub.add_parser(
        "freshness",
        help="Is each derived cache current against the corpus?",
        description=(
            "The cache freshness ledger (RO15): for every derived artefact — the "
            "coverage snapshot, the archive listings, the extraction cache — what it is "
            "derived from, whether it is *current* (its inputs have not moved since it "
            "was built, which is not the same as being recent), the operation that would "
            "make it current, and how much work that is. A cache with no recorded "
            "fingerprint reports UNKNOWN, never current: nothing can vouch for it. "
            "Read-only, and deliberately cheap — it never counts the chunk table."
        ),
    )
    _add_corpus_flags(p_cfresh, require_root=False, require_index=True)

    # ── trace ────────────────────────────────────────────────────────────
    p_trace = sub.add_parser(
        "trace",
        help="Read a run's trajectory back as Markdown, for a human",
        description=(
            "Turn a trajectory JSONL into readable Markdown: one page per run "
            "(question, turn-by-turn transcript, citation audit, and the passage "
            "behind every cited or served address) plus an index over a directory "
            "of runs. **The pages contain corpus text**, so they are written where "
            "the corpus is — 0600 in a 0700 directory, refused inside the corpus "
            "root — and this command prints only counts and the output path. "
            "`trace summary` prints those counts and writes nothing, which is the "
            "form that may travel (AGENTS.md 1.9)."
        ),
    )
    trace_sub = p_trace.add_subparsers(dest="trace_command")

    p_trender = trace_sub.add_parser(
        "render", help="Write an index and one Markdown page per trajectory")
    p_trender.add_argument("path", nargs="?", default="logs/trajectories",
                           help="A trajectory JSONL, or a directory of them "
                                "(default: logs/trajectories)")
    p_trender.add_argument("--out-dir", type=Path, required=True,
                           help="Where the pages go — outside the corpus root")
    p_trender.add_argument("--summary", action="store_true",
                           help="Also print one counts line per run (no paths, no text)")
    _add_corpus_flags(p_trender, require_root=False, require_index=False)
    p_trender.add_argument("--no-passages", action="store_true",
                           help="Addresses and bands only: do not embed passage text")
    p_trender.add_argument("--max-passage", type=int, default=None,
                           help="Characters of each passage to embed")

    p_tsummary = trace_sub.add_parser(
        "summary", help="Print one counts line per trajectory and write nothing")
    p_tsummary.add_argument("path", nargs="?", default="logs/trajectories")

    # ── mine ─────────────────────────────────────────────────────────────
    p_mine = sub.add_parser(
        "mine",
        help="Mine the corpus: queue, run in windows, pause, resume",
        description=(
            "Work the corpus batch by batch, in whatever windows the machine is "
            "free. `plan` turns the Stage 1 map into a queue; `run` works it for a "
            "budget, a deadline or an item count and stops cleanly; `pause` and "
            "`resume` are a file flag the worker checks between items. Everything "
            "derived is cached by content hash, so a re-run is free, and one "
            "worker at a time holds a heartbeat lock. Output is aggregates only."
        ),
    )
    mine_sub = p_mine.add_subparsers(dest="mine_command")

    p_mplan = mine_sub.add_parser("plan", help="Enqueue work from the Stage 1 map")
    _add_corpus_flags(p_mplan, require_root=False, require_index=True)
    p_mplan.add_argument("--limit", type=int, default=None,
                         help="Queue at most N files per task (pilots)")

    p_mstatus = mine_sub.add_parser("status", help="Queue depth and cache size")
    _add_corpus_flags(p_mstatus, require_root=False, require_index=True)

    p_mrun = mine_sub.add_parser("run", help="Work the queue for one window")
    _add_corpus_flags(p_mrun, require_root=True, require_index=True)
    p_mrun.add_argument("--tasks", default="list_archive,extract_text",
                        help="Comma-separated task names, or 'all'")
    p_mrun.add_argument("--for", dest="budget", default=None,
                        help="Window length: 90s, 30m, 2h (or plain seconds)")
    p_mrun.add_argument("--until", default=None,
                        help="Stop at this clock time, e.g. 07:00")
    p_mrun.add_argument("--max-items", type=int, default=None)
    p_mrun.add_argument("--cache-root", type=Path, default=None,
                        help="Where derived artefacts live "
                             "(default: <index dir>/cache)")
    p_mrun.add_argument("--lock", type=Path, default=None,
                        help="Worker lock file (default: <index dir>/mine.lock)")
    p_mrun.add_argument("--pause-file", type=Path, default=None,
                        help="Pause flag (default: <index dir>/mine.pause)")
    p_mrun.add_argument("--progress-every", type=int, default=200)
    p_mrun.add_argument("--no-coverage-scan", dest="coverage_scan", action="store_false",
                        help="Skip the whole-index coverage scan this window would "
                             "otherwise publish (~16 min warm, ~51 min cold). Use it for "
                             "every window of a chain and pay once at the end: the scan "
                             "runs past the window's budget, because the budget bounds "
                             "items, not the closing work.")

    p_mpause = mine_sub.add_parser("pause", help="Ask the worker to stop between items")
    _add_corpus_flags(p_mpause, require_root=False, require_index=False)
    p_mpause.add_argument("--pause-file", type=Path, default=None)

    p_mresume = mine_sub.add_parser("resume", help="Clear the pause flag")
    _add_corpus_flags(p_mresume, require_root=False, require_index=False)
    p_mresume.add_argument("--pause-file", type=Path, default=None)

    p_mretry = mine_sub.add_parser("retry", help="Put failed items back in the queue")
    _add_corpus_flags(p_mretry, require_root=False, require_index=True)
    p_mretry.add_argument("--task", default=None)
    p_mretry.add_argument(
        "--skipped-note", default=None,
        help="Also re-open rows this task *skipped* with this note, e.g. "
             "no_listing_engine — for when the harness has learned to do the thing the "
             "skip recorded it could not (required with --task)",
    )

    # ── RO6: describe the documents that are worth describing ─────────────
    #
    # Not a `mine` subcommand, deliberately. `mine run` works a task across the whole queue;
    # this spends *generation* on one hand-picked set — the head of the enrichment plan — and
    # reports what it cost. Generation is the expensive half: ~23 minutes per document at the
    # kernel's bounds, so the corpus cannot be summarised uniformly and something has to choose.
    p_sum = sub.add_parser(
        "summarise",
        help="Describe the documents the value set selects, by value (RO6)",
    )
    _add_corpus_flags(p_sum, require_root=True, require_index=True)
    _add_model_server_flags(p_sum)
    p_sum.add_argument("--profile", default="laptop")
    p_sum.add_argument(
        "--plan", type=Path, default=None,
        help="Enrichment plan TSV to read (default: enrich-plan.tsv beside the index)",
    )
    p_sum.add_argument(
        "--limit", type=int, default=3,
        help="How many documents to describe, from the head of the ranking (default 3: each "
             "one costs minutes, so the default is small on purpose)",
    )
    p_sum.add_argument(
        "--cited-only", action="store_true",
        help="Only documents an answer has actually cited (the cheapest defensible set)",
    )
    p_sum.add_argument(
        "--max-tokens", type=int, default=None,
        help="Output bound for one description (default: the kernel's SUMMARY_MAX_TOKENS)",
    )
    p_sum.add_argument("--cache-root", type=Path, default=None)
    p_sum.add_argument(
        "--pause-file", type=Path, default=None,
        help="Stop between items when this file exists (default: mine.pause beside the index — "
             "the file `rlm mine pause` writes, which this command honours because it takes the "
             "same mining lock)",
    )
    p_sum.add_argument(
        "--timeout", type=float, default=None,
        help="Seconds one description may take (default: derived from the kernel's input cap and "
             "output bound — a 32 KiB document is ~21 minutes of prompt on this host, so the "
             "HTTP client's own 300 s default kills exactly the largest documents)",
    )
    p_sum.add_argument(
        "--log", type=Path, default=None,
        help="Metrics log, one JSON line per call (default: summaries.jsonl beside the index)",
    )
    p_sum.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be described and stop: enqueues nothing, calls no model",
    )

    return parser


def _add_corpus_flags(
    parser: argparse.ArgumentParser,
    *,
    require_root: bool,
    require_index: bool,
) -> None:
    """Corpus location flags, with the environment as the shared default.

    Both values are required where they are needed and never guessed: a default
    index path could silently land *inside* the corpus, which is the one mistake
    the mount exists to prevent (AGENTS.md §1.8, layer 3).
    """
    parser.add_argument(
        "--corpus-root", default=os.environ.get("RLM_CORPUS_ROOT"),
        help="Read-only corpus root, e.g. /srv/corpus (env RLM_CORPUS_ROOT)"
             + ("" if require_root else " (only needed to read file contents)"),
    )
    parser.add_argument(
        "--corpus-index", default=os.environ.get("RLM_CORPUS_INDEX"),
        help="Path index file, OUTSIDE the corpus "
             "(env RLM_CORPUS_INDEX)" + ("" if require_index else " (optional)"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return _cmd_ask(args)
    elif args.command == "chat":
        return _cmd_chat(args)
    elif args.command == "ingest":
        return _cmd_ingest(args)
    elif args.command == "search":
        return _cmd_search(args)
    elif args.command == "get":
        return _cmd_get(args)
    elif args.command == "tag":
        return _cmd_tag(args)
    elif args.command == "check":
        return _cmd_check(args)
    elif args.command == "vault":
        return _cmd_vault(args)
    elif args.command == "optimize":
        return _cmd_optimize(args)
    elif args.command == "corpus":
        return _cmd_corpus(args)
    elif args.command == "trace":
        return _cmd_trace(args)
    elif args.command == "mine":
        return _cmd_mine(args)
    elif args.command == "summarise":
        return _cmd_summarise(args)
    else:
        parser.print_help()
        return 0

# ── ask ───────────────────────────────────────────────────────────────────

def _add_model_server_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared model-server overrides to a subcommand.

    The shipped profiles point at `https://localhost:9010/v1`. A model server on
    another host — a LAN box, a Tailscale peer, or a llama.cpp **router** that
    loads models on demand — needs these overrides, and the environment variables
    let one export configure every command (`ask`, `chat`, `check`) at once.
    """
    parser.add_argument(
        "--endpoint", default=os.environ.get("RLM_ENDPOINT"),
        help="Base URL of the model server, e.g. https://lunacode:9010/v1 "
             "(default: the profile's root_endpoint; env RLM_ENDPOINT)",
    )
    parser.add_argument(
        "--model", default=os.environ.get("RLM_MODEL"),
        help="Model ID for BOTH tiers (default: the profile's model; env "
             "RLM_MODEL). The sub tier follows the root tier, so sub-calls use "
             "the model you name rather than the profile default.",
    )


def _model_server_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate `--endpoint`/`--model` into `load_config` overrides.

    Both tiers are set from `--model`: leaving `sub_model` at the profile
    default would run the root model on the server you named while every
    sub-call went somewhere else. `sub_endpoint` is deliberately left alone —
    empty means "same as root", so it follows `--endpoint` automatically.
    """
    overrides: dict[str, Any] = {}
    endpoint = getattr(args, "endpoint", None)
    model = getattr(args, "model", None)
    if endpoint:
        overrides["root_endpoint"] = endpoint
    if model:
        overrides["root_model"] = model
        overrides["sub_model"] = model
    return overrides


def _assemble_context(args: argparse.Namespace) -> str | None:
    """Assemble context from files, dir, stdin, and/or vault."""
    parts: list[str] = []

    # Context files
    for fpath in args.context_files:
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8", errors="replace")
            parts.append(f"# {fpath.name}\n\n{content}")
        else:
            print(f"Warning: file not found: {fpath}", file=sys.stderr)

    # Context dir
    if args.context_dir:
        d = args.context_dir
        if d.is_dir():
            for md in sorted(d.glob("*.md")):
                content = md.read_text(encoding="utf-8", errors="replace")
                parts.append(f"# {md.name}\n\n{content}")

    # Stdin
    if args.stdin:
        stdin_text = sys.stdin.read()
        if stdin_text.strip():
            parts.append(f"# stdin\n\n{stdin_text}")

    # Vault page
    if args.vault:
        try:
            from rlm_kernel.vault import LocalVault
            vault = LocalVault(_default_vault(), init_git=False)
            page = vault.get(args.vault)
            if page:
                parts.append(f"# {args.vault}\n\n{page.body}")
            else:
                print(f"Warning: vault page not found: {args.vault}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: vault error: {e}", file=sys.stderr)

    if not parts:
        return None
    return "\n\n---\n\n".join(parts)


def ask_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """The per-run overrides `rlm ask` applies on top of the profile.

    Split out so the flags that change *how a run is allowed to spend its budget*
    are testable without a model server: `--max-turns`, `--cell-timeout` and
    `--cell-timeout-hard` are the knobs an operator reaches for under load, and a
    knob that silently fails to reach the run is worse than no knob (2026-09-17).
    """
    overrides: dict[str, Any] = _model_server_overrides(args)
    if getattr(args, "max_turns", None) is not None:
        overrides["max_turns"] = args.max_turns
    if getattr(args, "cell_timeout", None) is not None:
        overrides["cell_timeout"] = float(args.cell_timeout)
    if getattr(args, "cell_timeout_hard", None) is not None:
        overrides["cell_timeout_hard"] = float(args.cell_timeout_hard)
    return overrides


def _cmd_ask(args: argparse.Namespace) -> int:
    import rlm_local

    context = _assemble_context(args)
    if context is None:
        if not getattr(args, "corpus_root", None):
            print("Error: no context provided. Use --context-file, --context-dir, "
                  "--stdin, --vault, or --corpus-root.", file=sys.stderr)
            return 2
        # A corpus run needs no context string; the helpers are the interface.
        # The stub says that out loud so the model does not go looking for the
        # corpus inside `context`.
        from rlm_local.templates import CORPUS_CONTEXT_STUB

        context = CORPUS_CONTEXT_STUB

    overrides: dict[str, Any] = ask_overrides(args)

    corpus_bridge = _corpus_bridge_for(args)
    try:
        answer = rlm_local.completion(
            args.query, context,
            profile=args.profile,
            log_path=str(args.log_path) if args.log_path else None,
            corpus_bridge=corpus_bridge,
            warning_sink=lambda message: print(message, file=sys.stderr),
            **overrides,
        )
        print(answer)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    finally:
        if corpus_bridge is not None:
            corpus_bridge.close()


# ── chat ──────────────────────────────────────────────────────────────────

def _cmd_chat(args: argparse.Namespace) -> int:
    try:
        from rlm_local.chat import run_chat
        from rlm_local.config import load_config
        vault_str = str(args.vault_path) if args.vault_path else None
        run_chat(
            profile=args.profile,
            vault_path=vault_str,
            config=load_config(args.profile, **_model_server_overrides(args)),
        )
        return 0
    except ImportError as e:
        # Chat mode exists; this fires only when a kernel dependency is missing.
        print(f"Chat mode unavailable: {e}", file=sys.stderr)
        print("Install the kernel dependencies (uv sync) and retry, "
              "or use 'rlm ask' for single completions.", file=sys.stderr)
        return 1


# ── ingest ────────────────────────────────────────────────────────────────

def _cmd_ingest(args: argparse.Namespace) -> int:
    from rlm_kernel.index import Index, rebuild_index
    from rlm_kernel.schema import Frontmatter, Page, PageKind
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=True)
    idx_path = args.vault / ".index" / "meta.sqlite"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    kind = PageKind(args.kind)

    ingested = 0
    skipped = 0

    for fpath in args.paths:
        if not fpath.exists():
            print(f"  SKIP: not found: {fpath}")
            continue

        content = fpath.read_text(encoding="utf-8", errors="replace")

        # Extract title from first heading, or use filename
        title = fpath.stem
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # Slugify name
        import re
        name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64] or fpath.stem

        # Check for duplicate by content hash
        # We compute a quick hash to check
        import hashlib
        content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()

        # Check existing pages
        existing = vault.list(kind=args.kind)
        duplicate = False
        for ex in existing:
            if ex.frontmatter.hash and ex.frontmatter.hash == content_hash:
                duplicate = True
                break
            # Also check body content
            if ex.body.strip() == content.strip():
                duplicate = True
                break

        if duplicate:
            print(f"  SKIP: duplicate: {fpath}")
            skipped += 1
            continue

        fm = Frontmatter(
            schema=1,
            kind=kind,
            name=name,
            title=title,
            summary=title,
            tags=tags,
            hash=content_hash,
        )
        page = Page(fm, content)
        page_path = f"{kind.value}/{name}.md" if kind.value != "note" else f"memory/notes/{name}.md"
        vault.put(page, page_path)
        print(f"  OK: {fpath} → {page_path}")
        ingested += 1

    # Reindex
    if ingested > 0:
        idx = Index(idx_path)
        idx.reindex_delta(vault)
        idx.close()
        vault.git_commit(f"rlm ingest: {ingested} page(s)")

    print(f"\nIngested: {ingested}, Skipped: {skipped}")
    return 0


# ── search ────────────────────────────────────────────────────────────────

def _cmd_search(args: argparse.Namespace) -> int:
    from rlm_kernel.search import search_vault
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    idx_path = args.vault / ".index" / "meta.sqlite"

    if not idx_path.exists():
        print("Index not found. Run: rlm vault index --rebuild")
        return 1

    results = search_vault(
        vault, idx_path, args.query, k=args.k, kinds=args.kind, tags=args.tag,
        statuses=getattr(args, "status", None),
    )
    if not results:
        print("(no results)")
        if getattr(args, "status", None) is None:
            print("Search returns active pages by default; "
                  "--status deprecated superseded includes retired pages.")
        return 0

    for r in results:
        status = r.get("status", "active")
        marker = "" if status == "active" else f" [{status}]"
        print(f"[{r['kind']}]{marker} {r['name']}: {r['title']}")
        print(f"    {r['summary'][:120]}")
        print()
    return 0


# ── get ───────────────────────────────────────────────────────────────────

def _cmd_get(args: argparse.Namespace) -> int:
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault, init_git=False)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1

    print(page.to_markdown())
    return 0



# ── tag ────────────────────────────────────────────────────────────────────

def _cmd_tag(args: argparse.Namespace) -> int:
    """Add or remove tags from a vault page."""
    from rlm_kernel.vault import LocalVault
    from rlm_kernel.index import Index

    vault = LocalVault(args.vault, init_git=False)
    page = vault.get(args.path)
    if page is None:
        print(f"Page not found: {args.path}", file=sys.stderr)
        return 1

    add_tags = [t.strip() for t in args.add.split(",") if t.strip()]
    remove_tags = [t.strip() for t in args.remove.split(",") if t.strip()]

    if not add_tags and not remove_tags:
        # Just show current tags
        current = page.frontmatter.tags
        print(f"Tags for {args.path}: {', '.join(current) if current else '(none)'}")
        return 0

    current = set(page.frontmatter.tags)
    for t in add_tags:
        current.add(t)
    for t in remove_tags:
        current.discard(t)

    page.frontmatter.tags = sorted(current)
    vault.put(page, args.path)

    idx_path = args.vault / ".index" / "meta.sqlite"
    if idx_path.exists():
        idx = Index(idx_path)
        idx.reindex_delta(vault)
        idx.close()

    print(f"Tags updated: {', '.join(page.frontmatter.tags) if page.frontmatter.tags else '(none)'}")
    return 0


# ── check ─────────────────────────────────────────────────────────────────

def _cmd_check(args: argparse.Namespace) -> int:
    try:
        from rlm_local.model_check import check_model
        from rlm_local.model_backend import HTTPModelBackend

        backend = HTTPModelBackend(
            root_endpoint=args.endpoint,
            root_model=args.model_id,
            verify=False,
        )
        try:
            result = check_model(
                backend, args.model_id,
                quick=args.quick, profile=args.profile,
                weight_profile=args.weights,
            )
        finally:
            backend.close()

        print(f"Model: {args.model_id}")
        print(f"Score: {result['score']}/100")
        print(f"Verdict: {result['verdict']}")
        print(f"Weights: {result['weight_profile']} "
              f"({', '.join(f'{p} {w}' for p, w in result['weights'].items())})")
        print(f"Probes: {result['probes_passed']}/{result['probes_total']} passed, "
              f"{result['probes_failed']} failed")
        print(f"Time: {result['elapsed_seconds']:.0f}s")
        print()
        print("Failed probes:")
        for pid, pr in result.get("per_probe", {}).items():
            if not pr["passed"]:
                print(f"  {pid}: {pr['score']}/{pr['max_score']} pts")
        print()
        if result.get("evidence_lines"):
            print("Evidence:")
            evidence = result["evidence_lines"]
            for line in evidence[:20]:
                print(f"  {line}")
            # Never truncate silently: P4 alone contributes up to seven lines
            # (three trials plus a summary), so a full battery can now push
            # later probes past the cap.
            if len(evidence) > 20:
                print(f"  ... {len(evidence) - 20} more line(s) not shown")
        return 0
    except ImportError as e:
        # The battery exists; this fires only when a dependency is missing.
        print(f"Model check unavailable: {e}", file=sys.stderr)
        print("Install the dependencies (uv sync) and retry.", file=sys.stderr)
        return 1
    except ValueError as e:
        # An unknown --weights value: a usage error, not a missing dependency.
        print(f"{e}", file=sys.stderr)
        return 2
# ── vault ─────────────────────────────────────────────────────────────────

def _cmd_vault(args: argparse.Namespace) -> int:
    from rlm_kernel.cli import main as kernel_main
    return kernel_main(args.vault_args)


# ── optimize ──────────────────────────────────────────────────────────────

def _cmd_optimize(args: argparse.Namespace) -> int:
    from rlm_kernel.optimize import run_optimization
    from rlm_kernel.vault import LocalVault

    vault = LocalVault(args.vault)
    result = run_optimization(
        vault,
        target=args.target,
        suite_name=args.suite,
        profile=args.profile,
        max_metric_calls=args.max_calls,
    )
    print(f"Status: {result['status']}")
    print(f"Baseline: {result['baseline_score']:.1%}")
    print(f"Best: {result['best_score']:.1%}")
    if result.get("held_out_score") is not None:
        print(f"Held-out: {result['held_out_score']:.1%}")
    return 0


# ── corpus ────────────────────────────────────────────────────────────────

def _corpus_bridge_for(args: argparse.Namespace):
    """Open the corpus bridge the CLI was configured with, or return None.

    Used by `ask`/`chat` so a completion can be pointed at a corpus; the corpus
    subcommands build their own pieces so that `index` works even when there is
    no index yet.
    """
    from rlm_kernel.corpus import CorpusBridge
    from rlm_kernel.mounts import ReadOnlyViolation

    root = getattr(args, "corpus_root", None)
    if not root:
        return None
    index = getattr(args, "corpus_index", None)
    # Derived text lives beside the index unless told otherwise, and the bridge
    # needs it to re-read a chunk that came from an extracted document rather
    # than from a file.
    cache_root = _mine_paths(args)[0] if index else None
    try:
        bridge = CorpusBridge.open_for(root, index, cache_root=cache_root)
    except ReadOnlyViolation as e:
        print(f"Error: {e}", file=sys.stderr)
        return None
    # The run's question, so a hit's match-quality label measures the passage
    # against what was asked rather than against the AND search that found it
    # (RO4, 2026-09-16). `ask` and `chat` both carry it as `query`.
    bridge.question = getattr(args, "query", None)
    return bridge


# ── trace ─────────────────────────────────────────────────────────────────

def _cmd_trace(args: argparse.Namespace) -> int:
    """`rlm trace render|summary` — read a trajectory back as a human can (RO10).

    The rendered pages contain corpus text, so this command is deliberately terse:
    it prints the output directory and counts, never a page, a question or an
    address. `summary` writes nothing at all and is the form that may leave the
    machine (AGENTS.md §1.9).
    """
    from rlm_local.traceview import (
        DEFAULT_PASSAGE_CHARS,
        collect_passages,
        collect_trajectories,
        read_trajectory,
        render_summary,
        render_traces,
    )

    subcommand = getattr(args, "trace_command", None)
    if subcommand not in ("render", "summary"):
        print("Usage: rlm trace render|summary <path> [--out-dir DIR]")
        return 2

    paths = collect_trajectories(args.path)
    if not paths or any(not Path(path).exists() for path in paths):
        print(f"Error: no trajectories found at {args.path}", file=sys.stderr)
        return 2

    if subcommand == "summary":
        for path in paths:
            print(render_summary(read_trajectory(path)))
        return 0

    passages: dict[str, dict[str, str]] = {}
    if not args.no_passages:
        bridge = _corpus_bridge_for(args)
        if bridge is not None:
            try:
                for path in paths:
                    run = read_trajectory(path)
                    passages[str(path)] = collect_passages(run, bridge)
            finally:
                bridge.close()
        else:
            print("Note: no corpus configured, so pages carry addresses without "
                  "their passages (`--corpus-root`/`--corpus-index` add them).",
                  file=sys.stderr)

    try:
        written = render_traces(
            paths, args.out_dir,
            corpus_root=getattr(args, "corpus_root", None),
            passages=passages,
            max_passage=args.max_passage or DEFAULT_PASSAGE_CHARS,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    pages = [p for p in written if p.name != "index.md"]
    print(f"Wrote {len(pages)} page(s) and an index to {args.out_dir}")
    print(f"  open {args.out_dir / 'index.md'}")
    if args.summary:
        for path in paths:
            print(render_summary(read_trajectory(path)))
    return 0


def _cmd_corpus(args: argparse.Namespace) -> int:
    """`rlm corpus ...` — the operator's side of RO3/RO4.

    Every subcommand except `index` refuses to invent a corpus: they report what
    is configured and fail loudly when it is not.
    """
    from rlm_kernel.corpus import CorpusBridge, CorpusIndex, count_report, index_report
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    sub = getattr(args, "corpus_command", None)
    if not sub:
        print("Usage: rlm corpus {index,status,find,count,read} ...")
        return 2

    root, index_path = args.corpus_root, args.corpus_index

    if sub == "index":
        if not root or not index_path:
            print("Error: --corpus-root and --corpus-index are both required "
                  "(env RLM_CORPUS_ROOT / RLM_CORPUS_INDEX).", file=sys.stderr)
            return 2
        try:
            # The mount is built first so a bad root fails before anything is
            # created, and before a walk starts.
            mount = LocalTreeMount(root)
            idx = CorpusIndex.open_for(root, index_path)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        every = max(1, int(getattr(args, "progress_every", 250_000) or 250_000))

        def progress(n: int) -> None:
            print(f"  {n} entries indexed", flush=True)

        print(f"Indexing the corpus at {Path(root)} (paths only, no file reads)")
        try:
            written = idx.build(mount, progress=progress, progress_every=every)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        finally:
            report = index_report(idx)
            idx.close()
        print(f"Indexed {written} entries into {index_path}")
        print(report)
        return 0

    # From here on, index-backed answers. They read the index only, so they need
    # no corpus root and no mount: `rlm corpus count` works wherever the index is.
    if sub in ("status", "find", "count"):
        if not index_path:
            print("Error: --corpus-index is required for this subcommand "
                  "(env RLM_CORPUS_INDEX).", file=sys.stderr)
            return 2
        if not Path(index_path).exists():
            print(f"Error: no index at {index_path}. Build one with "
                  "`rlm corpus index`.", file=sys.stderr)
            return 2
        idx = CorpusIndex(index_path)
        try:
            if sub == "status":
                print(index_report(idx))
                return 0
            if sub == "count":
                print(count_report(idx, kind=args.kind, under=args.under))
                return 0
            hits = idx.find(args.query, limit=args.limit, kind=args.kind,
                            under=args.under)
            for entry in hits:
                print(f"{entry.rel}  [{entry.kind}, {entry.size} bytes]")
            if not hits:
                print("(no matches)")
            return 0
        finally:
            idx.close()

    if sub == "read":
        if not root:
            print("Error: --corpus-root is required to read file contents.",
                  file=sys.stderr)
            return 2
        try:
            bridge = CorpusBridge.open_for(root, index_path)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        try:
            print(bridge.handle_read(args.rel, max_bytes=args.max_bytes))
            return 0
        finally:
            bridge.close()

    if sub == "verify":
        return _cmd_corpus_verify(args)

    if sub == "digest":
        return _cmd_corpus_digest(args)

    if sub == "classify":
        return _cmd_corpus_classify(args)

    if sub == "search":
        return _cmd_corpus_search(args)

    if sub == "sample":
        return _cmd_corpus_sample(args)

    if sub == "reindex-encodings":
        return _cmd_corpus_reindex_encodings(args)

    if sub == "counters":
        return _cmd_corpus_counters(args)

    if sub == "freshness":
        return _cmd_corpus_freshness(args)

    print(f"Error: unknown corpus subcommand {sub!r}", file=sys.stderr)
    return 2


def _cmd_corpus_freshness(args: argparse.Namespace) -> int:
    """`rlm corpus freshness` — the ledger: is each derived cache current? (RO15).

    Read-only, and cheap by construction: every marker it compares is an indexed count or
    a value already stored in the coverage snapshot, so the diagnosis never becomes the
    expensive operation (which is what CL6 was, and what a `COUNT(*) FROM text_chunks`
    here would be again).
    """
    from rlm_kernel.corpus import CorpusIndex
    from rlm_kernel.freshness import render, report

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).", file=sys.stderr)
        return 2
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return 2

    index = CorpusIndex(args.corpus_index)
    try:
        print(render(report(index._conn)))  # noqa: SLF001 - the index owns the connection
    finally:
        index.close()
    return 0


def _cmd_corpus_counters(args: argparse.Namespace) -> int:
    """`rlm corpus counters` — show, or recompute, the published coverage snapshot.

    The read is instant and the refresh is deliberately slow (~16 minutes on the
    live index: 25M chunks). Splitting them is the whole point — a search inside a
    120 s REPL cell can afford the first and never the second.
    """
    from rlm_kernel.corpus import CORPUS_COVERAGE_UNKNOWN, CorpusIndex
    from rlm_kernel.textindex import coverage_note

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).",
              file=sys.stderr)
        return 2
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return 2

    index = CorpusIndex(args.corpus_index)
    try:
        text_index = index.text()
        if args.refresh:
            print("Recomputing coverage (reads the whole index; can take minutes)…",
                  flush=True)
            # Through the mining helper, which publishes the snapshot *and* fingerprints
            # the caches derived from it (RO15) — so one command both refreshes and records
            # that it did, which is what makes the freshness ledger able to say `current`.
            from rlm_kernel.mine import publish_coverage_snapshot

            publish_coverage_snapshot(index._conn)  # noqa: SLF001 - the index's connection
        snapshot = text_index.published_coverage()
        # Same command, same deliberate scan: it publishes the member count too, so that
        # `mine status` and this report can quote it instead of counting 30M rows each
        # time (measured 2026-09-22: that count held a finished window open for an hour).
        from rlm_kernel.mine import published_member_count

        members = published_member_count(index._conn)  # noqa: SLF001
    finally:
        index.close()

    if members is not None:
        print(f"archive_members: {members:,}")

    if snapshot is None:
        print(CORPUS_COVERAGE_UNKNOWN)
        return 0
    for key, value in sorted(snapshot.items()):
        if key == "published_at":
            continue
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value:,}" if isinstance(value, int) else f"{key}: {value}")
    note = coverage_note(snapshot)
    print(note if note else "[coverage: complete]")
    return 0


def _cmd_corpus_search(args: argparse.Namespace) -> int:
    """`rlm corpus search` — words inside the corpus, each with an address."""
    from rlm_kernel.corpus import CORPUS_COVERAGE_UNKNOWN, CorpusIndex
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation
    from rlm_kernel.textindex import coverage_note, format_hits

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).",
              file=sys.stderr)
        return 2
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return 2

    index = CorpusIndex(args.corpus_index)
    try:
        text_index = index.text()
        # `ensure` only creates the tables if this database lacks them (it is a
        # no-op otherwise); it is not what made a search slow — the coverage scan
        # was. The *published* snapshot below is the part that must never scan:
        # on the live index that scan is ~16 minutes and a cell has 120 seconds.
        # `rlm corpus counters --refresh` is the deliberate, slow way to produce one.
        text_index.ensure()
        coverage = text_index.published_coverage()
        if coverage is None and not args.count_only and not args.coverage:
            coverage = {}

        if args.coverage:
            if coverage is None:
                print(CORPUS_COVERAGE_UNKNOWN)
                return 0
            for key, value in sorted(coverage.items()):
                if isinstance(value, float):
                    print(f"{key}: {value:.4f}")
                else:
                    print(f"{key}: {value:,}" if isinstance(value, int) else f"{key}: {value}")
            note = coverage_note(coverage)
            if note:
                print(note)
            return 0

        result = text_index.search(
            args.query, k=args.limit,
            include_vendored=args.include_vendored,
            derived_only=args.derived_only,
        )
        if args.count_only:
            # Counts and coverage only: this is the form that is safe to paste
            # anywhere, because it cannot carry a path or a fragment of content.
            print(f"matches: {len(result.hits)}")
            print(f"hidden_by_vendored_filter: {result.hidden_vendored}")
            if coverage is None:
                print(CORPUS_COVERAGE_UNKNOWN)
                return 0
            print(f"sources_indexed: {coverage['sources_indexed']:,}")
            print(f"text_coverage: {coverage['text_coverage']:.4f}")
            print(f"chunks: {coverage['chunks']:,}")
            return 0

        cache_root, _, _ = _mine_paths(args)
        try:
            mount = LocalTreeMount(args.corpus_root)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        texts: list[str] = []
        for hit in result.hits:
            try:
                texts.append(text_index.read(hit, mount=mount, cache_root=cache_root))
            except ReadOnlyViolation as e:
                texts.append(f"(cannot re-read: {e})")
        result.coverage_note = coverage_note(coverage) if coverage else CORPUS_COVERAGE_UNKNOWN
        print(format_hits(result, texts))
        return 0
    finally:
        index.close()


def _cmd_corpus_sample(args: argparse.Namespace) -> int:
    """`rlm corpus sample` — random passages with addresses, to ask questions about.

    The only corpus command whose entire output is corpus text, which is why the
    header says so out loud: the passages and their addresses are readable where the
    corpus is and nowhere else (`AGENTS.md` §1.9). It reads through the same
    read-only mount as everything else, and it never searches or counts — a draw is
    a bounded number of indexed lookups, so `--n 20` costs the same on a 29M-chunk
    index as on a small one.
    """
    import random

    from rlm_kernel.corpus import CorpusIndex
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).",
              file=sys.stderr)
        return 2
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return 2
    if not args.corpus_root:
        print("Error: --corpus-root is required to read the passages.",
              file=sys.stderr)
        return 2

    index = CorpusIndex(args.corpus_index)
    try:
        text_index = index.text()
        text_index.ensure()
        seed = args.seed if args.seed is not None else random.randrange(2 ** 31)
        picker = random.Random(seed)
        cache_root, _, _ = _mine_paths(args)
        try:
            mount = LocalTreeMount(args.corpus_root)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        stats: dict = {}
        texts: list[str] = []
        if getattr(args, "any_text", False):
            hits = text_index.random_chunks(
                args.n, rng=picker,
                include_vendored=args.include_vendored,
                include_derived=args.include_derived,
            )
        else:
            hits, texts, stats = text_index.random_prose(
                args.n, rng=picker, mount=mount, cache_root=cache_root,
                include_vendored=args.include_vendored,
                chars=max(4_000, int(args.chars or 0)),
                **(dict(floor=args.floor) if args.floor is not None else {}),
            )
        print(f"# {len(hits)} of {args.n} passage(s) drawn from the corpus index, "
              f"seed={seed}"
              + ("" if getattr(args, "any_text", False) else ", prose-preferred"))
        if stats:
            print(f"# prose filter: drew {stats['drawn']}, kept {stats['kept']}, "
                  f"rejected {stats['rejected_content']} on content, "
                  f"unreadable {stats['unreadable']}, floor {stats['floor']}, "
                  f"mean score {stats['mean_score']}")
            if stats["kept"] < args.n:
                print(f"# only {stats['kept']} of {args.n} drew as prose within the draw "
                      "budget — widen it or lower --floor rather than assuming the corpus "
                      "holds no prose")
        print("# This is corpus text, read through the read-only mount: it may be "
              "read where")
        print("# the corpus is and must not be copied anywhere else (AGENTS.md §1.9).")
        print()
        for position, hit in enumerate(hits, 1):
            print(f"=== {position}/{len(hits)}  {hit.address}")
            if texts:
                text = texts[position - 1]
            else:
                try:
                    text = text_index.read(hit, mount=mount, cache_root=cache_root)
                except (ReadOnlyViolation, OSError) as e:
                    print(f"(cannot re-read this passage: {e})")
                    print()
                    continue
            print(_clip_passage(text.strip(), args.chars))
            print()
        if not hits:
            print("(the index drew nothing — is anything indexed, and are the "
                  "vendored/derived filters hiding it?)")
        return 0
    finally:
        index.close()


def _clip_passage(text: str, chars: int) -> str:
    """Clip a passage for printing, saying so rather than ending mid-sentence.

    Console output, so not a template (`AGENTS.md` §1.3): the marker is for the
    operator reading it, not for the model.
    """
    cap = int(chars or 0)
    if cap <= 0 or len(text) <= cap:
        return text
    return f"{text[:cap].rstrip()}\n[... clipped at {cap} characters ...]"


def _cmd_corpus_reindex_encodings(args: argparse.Namespace) -> int:
    """`rlm corpus reindex-encodings` — repair the files the old decode damaged (RO19).

    A targeted pass rather than a rebuild: the text index holds 29M chunks over
    2.88M sources and only 46,735 of those sources were indexed with the wrong
    decode. The pass is idempotent, so a run cut short is resumed by running it
    again, and `--limit` slices it for a window.
    """
    from rlm_kernel.corpus import CorpusIndex
    from rlm_kernel.mine import MAX_INDEX_BYTES
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).",
              file=sys.stderr)
        return 2
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return 2
    if not args.corpus_root:
        print("Error: --corpus-root is required to read the files.", file=sys.stderr)
        return 2

    index = CorpusIndex(args.corpus_index)
    try:
        text_index = index.text()
        text_index.ensure()
        try:
            mount = LocalTreeMount(args.corpus_root)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        print("Reading the classification table to find the affected sources "
              "(one scan; ~1 min on the live index)…", flush=True)
        todo = text_index.sources_needing_encoding_repair()
        every = max(1, int(args.progress_every or 500))
        limit = max(0, int(args.limit or 0))
        done = current = failed = empty = 0
        causes: dict[str, int] = {}
        for position, (raw, encoding, source_hash) in enumerate(todo, 1):
            if limit and done >= limit:
                break
            if text_index.source_encoding(raw) is not None:
                # Already carries an encoding: this pass has been here, or the source
                # was indexed after the fix. Nothing to repair, and no read performed.
                current += 1
                continue
            rel = raw.decode("utf-8", "surrogateescape")
            try:
                with mount.open_readonly(rel, max_bytes=MAX_INDEX_BYTES) as handle:
                    data = handle.read()
                written = text_index.add_text(
                    raw=raw, display=rel, source_hash=source_hash, text=data,
                    encoding=encoding, replace=True,
                )
            except Exception as e:
                # One unreadable or unstorable source must not end a 46,735-file pass.
                # The first live run of this command died six seconds in, on a path
                # whose name is not UTF-8: a surrogate cannot be stored as SQLite TEXT
                # (roadmap RO20). The exception *type* is counted; the file never is.
                failed += 1
                causes[type(e).__name__] = causes.get(type(e).__name__, 0) + 1
                continue
            if written:
                done += 1
            else:
                empty += 1
            if position % every == 0:
                print(f"  {position}/{len(todo)} considered: re-indexed={done} "
                      f"already-current={current} empty={empty} failed={failed}",
                      flush=True)
        print(f"considered={len(todo)} re-indexed={done} already-current={current} "
              f"empty={empty} failed={failed}")
        if causes:
            print("failures by cause: " + ", ".join(
                f"{name}={count}" for name, count in sorted(causes.items())))
        print("Re-indexed sources now carry their encoding, so an accented word in "
              "them is searchable. Verify with `rlm corpus search <word>`.")
        return 0
    finally:
        index.close()


def _cmd_corpus_classify(args: argparse.Namespace) -> int:
    """`rlm corpus classify` — Stage 1, the content sniffing pass."""
    from rlm_kernel.classify import classify_entries, format_report
    from rlm_kernel.corpus import CorpusIndex
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    if not args.corpus_root or not args.corpus_index:
        print("Error: --corpus-root and --corpus-index are both required "
              "(env RLM_CORPUS_ROOT / RLM_CORPUS_INDEX).", file=sys.stderr)
        return 2

    try:
        mount = LocalTreeMount(args.corpus_root)
        idx = CorpusIndex.open_for(args.corpus_root, args.corpus_index)
    except ReadOnlyViolation as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    table = idx.classifications()
    table.ensure()

    every = max(1, int(getattr(args, "progress_every", 100_000) or 100_000))
    reported = 0

    def progress(stats) -> None:
        nonlocal reported
        if stats.files_seen - reported < every and stats.files_seen:
            return
        reported = stats.files_seen
        print(f"  {stats.files_seen:,} files classified", flush=True)

    print(f"Classifying the corpus at {Path(args.corpus_root)} "
          f"(heads only, no writes)")
    try:
        stats = classify_entries(
            mount, table,
            sniff_bytes=args.sniff_bytes,
            hash_bytes=args.hash_bytes,
            hash_mode=args.hash_mode,
            batch_size=args.batch_size,
            limit=args.limit,
            redo=args.redo,
            progress=progress,
        )
    except ReadOnlyViolation as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    finally:
        report = table.report()
        summary = idx.summary()
        idx.close()

    print(format_report(report, total_files=summary["by_kind"].get("file", 0),
                        total_bytes=summary["file_bytes"], stats=stats))
    return 0


def _cmd_corpus_digest(args: argparse.Namespace) -> int:
    """`rlm corpus digest` — a snapshot of the whole tree, in 32 bytes."""
    from rlm_kernel.corpus import (
        CorpusIndex,
        compare_digests,
        digest_of_index,
        digest_of_mount,
    )
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    if args.from_index:
        if not args.corpus_index:
            print("Error: --from-index needs --corpus-index.", file=sys.stderr)
            return 2
        if not Path(args.corpus_index).exists():
            print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
            return 2
        idx = CorpusIndex(args.corpus_index)
        try:
            snapshot = digest_of_index(idx)
        finally:
            idx.close()
    else:
        if not args.corpus_root:
            print("Error: --corpus-root is required to digest a walk.",
                  file=sys.stderr)
            return 2
        try:
            mount = LocalTreeMount(args.corpus_root)
        except ReadOnlyViolation as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        snapshot = digest_of_mount(mount)

    print(json.dumps(snapshot, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(json.dumps(snapshot, indent=2, sort_keys=True),
                                  encoding="utf-8")
        print(f"snapshot written to {args.out}")
    if args.compare:
        try:
            before = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Error: cannot read {args.compare}: {e}", file=sys.stderr)
            return 2
        verdict = compare_digests(before, snapshot)
        print(verdict)
        return 0 if verdict.startswith("PROOF") else 1
    return 0


def _cmd_corpus_verify(args: argparse.Namespace) -> int:
    """`rlm corpus verify` — the read-only proof, run by the harness."""
    from rlm_kernel.corpus import scan_newer, verify_report
    from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

    since = _parse_time(getattr(args, "since", None))
    if since is None and getattr(args, "since_file", None):
        marker = Path(args.since_file)
        try:
            since = marker.stat().st_mtime
        except OSError as e:
            print(f"Error: cannot use {marker} as a marker: {e}", file=sys.stderr)
            return 2
    if since is None:
        print("Error: give a marker with --since (ISO-8601 or epoch) or "
              "--since-file.", file=sys.stderr)
        return 2
    run_started = _parse_time(getattr(args, "run_started", None))
    sample = getattr(args, "sample", 1000) or None
    try:
        mount = LocalTreeMount(args.corpus_root)
    except ReadOnlyViolation as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    scan = scan_newer(mount, since, sample=sample, run_started=run_started)
    print(verify_report(scan))
    return 0 if (scan.newer == 0 and scan.complete) else 1


def _parse_time(raw: str | None) -> float | None:
    """ISO-8601 or epoch seconds, or None."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


# ── mine ──────────────────────────────────────────────────────────────────

def _parse_duration(raw: str | None) -> float | None:
    """`90`, `90s`, `30m`, `2h` -> seconds."""
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    units = {"s": 1, "m": 60, "h": 3600}
    if text[-1] in units:
        number, factor = text[:-1], units[text[-1]]
    else:
        number, factor = text, 1
    try:
        return max(0.0, float(number) * factor)
    except ValueError:
        return None


def _parse_deadline(raw: str | None) -> float | None:
    """`07:00` (or `7:00`) -> the next time the clock reads that."""
    if raw is None:
        return None
    from datetime import datetime, timedelta

    text = raw.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        now = datetime.now()
        when = now.replace(hour=parsed.hour, minute=parsed.minute,
                           second=parsed.second, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when.timestamp()
    return None


def _mine_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Cache root, lock and pause file, defaulting beside the index."""
    base = Path(args.corpus_index).parent if args.corpus_index else Path.cwd()
    cache_root = getattr(args, "cache_root", None) or base / "cache"
    lock = getattr(args, "lock", None) or base / "mine.lock"
    pause = getattr(args, "pause_file", None) or base / "mine.pause"
    return Path(cache_root), Path(lock), Path(pause)


def _open_mine(args: argparse.Namespace):
    """Open the index and its mining tables, or explain what is missing."""
    from rlm_kernel.corpus import CorpusIndex

    if not args.corpus_index:
        print("Error: --corpus-index is required (env RLM_CORPUS_INDEX).",
              file=sys.stderr)
        return None
    if not Path(args.corpus_index).exists():
        print(f"Error: no index at {args.corpus_index}.", file=sys.stderr)
        return None
    index = CorpusIndex(args.corpus_index)
    store = index.mining()
    store.ensure()
    return index, store


def _cmd_summarise(args: argparse.Namespace) -> int:
    """Describe the head of the enrichment plan, and report what it cost (RO6, by value).

    The plan ranks what retrieval has reached and what an answer has cited; this spends
    inference on the head of that ranking and never on the corpus, because the arithmetic says
    uniform summarisation is impossible here (~23 minutes per document at the kernel's bounds,
    so ~4.4 years for 100 000 of them — `docs/20260923-1800-…`).

    Two deliberate properties. The model defaults to **what every other command uses**
    (`--model` / `--endpoint`, `RLM_MODEL` / `RLM_ENDPOINT`) and can be another one, because the
    decision gate needs to know whether a second model's description is worth its own cost. And
    the report is **aggregates only** — counts, seconds, ratios — since a description is the
    document's own prose and may not travel (`AGENTS.md` §1.9); what the run cost may travel,
    what it said may not.
    """
    from rlm_kernel.enrich import read_plan_with_drops, render_plan, select_documents
    from rlm_kernel.mine import (
        PRIORITY_SUMMARISE, SUMMARISE, acquire_lock, release_lock, run_queue,
    )
    from rlm_local.summary_metrics import aggregate, read_log, render

    opened = _open_mine(args)
    if opened is None:
        return 2
    index, store = opened
    try:
        base = Path(args.corpus_index).parent
        plan_path = Path(args.plan) if args.plan else base / "enrich-plan.tsv"
        if not plan_path.exists():
            print(f"Error: no enrichment plan at {plan_path}. Build one with "
                  "scripts/enrich_plan.py.", file=sys.stderr)
            return 2

        candidates, dropped = read_plan_with_drops(plan_path)
        chosen = select_documents(candidates, limit=args.limit, cited_only=args.cited_only)
        print(render_plan(candidates))
        if dropped:
            print(f"  {dropped} plan row(s) dropped as malformed")
        print(f"selected {len(chosen)} of {len(candidates)} document(s)"
              + (", cited only" if args.cited_only else ""))
        if not chosen:
            print("nothing selected: nothing to summarise")
            return 0
        if args.dry_run:
            print("dry run: nothing enqueued, no model called")
            return 0

        # The same three paths `mine run` uses, from the same helper: a summarise run takes the
        # *mining* lock, so it has to honour the same pause flag. Otherwise `rlm mine pause`
        # stops the queue worker and silently leaves this one running — a stop signal that works
        # for one command and not the other is worse than none, because it is trusted.
        cache_root, lock_path, pause_path = _mine_paths(args)
        log_path = Path(args.log) if args.log else base / "summaries.jsonl"

        holder = acquire_lock(lock_path)
        if holder is None:
            print(f"Error: another mining worker holds {lock_path}. Stop it, or remove the "
                  "file if it is stale.", file=sys.stderr)
            return 2
        try:
            from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation
            from rlm_local.config import load_config
            from rlm_local.model_backend import HTTPModelBackend
            from rlm_local.summarise import make_summarise_engine, summarise_timeout_seconds

            try:
                mount = LocalTreeMount(args.corpus_root)
            except ReadOnlyViolation as e:
                print(f"Error: {e}", file=sys.stderr)
                return 2

            config = load_config(getattr(args, "profile", "laptop"),
                                **_model_server_overrides(args))
            # The timeout is derived, not defaulted: the kernel's input cap is what decides how
            # long one call may take (~21 min of prompt for 32 KiB on this host), and a client
            # timeout below that kills the largest documents — measured 2026-09-23.
            from rlm_kernel.mine import MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS

            timeout = args.timeout or summarise_timeout_seconds(
                MAX_SUMMARY_INPUT_BYTES, args.max_tokens or SUMMARY_MAX_TOKENS)
            backend = HTTPModelBackend(
                root_endpoint=config.root_endpoint, root_model=config.root_model, verify=False,
                timeout=timeout,
            )
            try:
                engine = make_summarise_engine(
                    backend, model=config.root_model, endpoint=config.root_endpoint,
                    log_path=log_path, max_tokens=args.max_tokens,
                )
                print(f"model {engine.engine_tag}")
                print(f"metrics log {log_path}")
                store.enqueue([
                    (candidate.source.encode("utf-8", "surrogateescape"),
                     SUMMARISE, PRIORITY_SUMMARISE)
                    for candidate in chosen
                ])
                text_index = index.text()
                text_index.ensure()
                run = run_queue(
                    store=store, conn=index._conn, mount=mount, cache_root=cache_root,
                    tasks=[SUMMARISE], engines={"summarise": engine}, text_index=text_index,
                    max_items=len(chosen), lock_file=lock_path, pause_file=pause_path,
                    coverage_scan=False,
                )
                print(f"stopped: {run.stop_reason} after {run.seconds:,.1f}s")
                print(f"  done {run.stats.done:,}, skipped {run.stats.skipped:,}, "
                      f"failed {run.stats.failed:,}, cache hits {run.stats.cache_hits:,}")
                # The scale travels with the numbers (AGENTS.md §1.7): a median seconds-per-
                # document from a different model, cache state or host means nothing.
                print(render(
                    aggregate(read_log(log_path)),
                    scale=f"{engine.engine_tag}, timeout {timeout:g}s, cache {cache_root}, "
                          "descriptions indexed as derived text",
                    sets={"value set": len(candidates),
                          "cited set": sum(1 for candidate in candidates if candidate.cited)},
                ))
            finally:
                backend.close()
        finally:
            release_lock(lock_path)
        return 0
    finally:
        index.close()


def _cmd_mine(args: argparse.Namespace) -> int:
    from rlm_kernel.mine import (
        ALL_TASKS,
        IMPLEMENTED_TASKS,
        DerivationCache,
        acquire_lock,
        format_status,
        plan_queue,
        release_lock,
        run_queue,
    )

    sub = getattr(args, "mine_command", None)
    if not sub:
        print("Usage: rlm mine {plan,status,run,pause,resume,retry} ...")
        return 2

    cache_root, lock_path, pause_path = _mine_paths(args)

    if sub == "pause":
        pause_path.parent.mkdir(parents=True, exist_ok=True)
        pause_path.write_text("paused\n", encoding="utf-8")
        print(f"paused: the worker stops after the item in flight ({pause_path})")
        return 0

    if sub == "resume":
        try:
            pause_path.unlink()
            print(f"resumed (removed {pause_path})")
        except FileNotFoundError:
            print("resumed (no pause flag was set)")
        except OSError as e:
            print(f"Error: cannot remove {pause_path}: {e}", file=sys.stderr)
            return 2
        return 0

    opened = _open_mine(args)
    if opened is None:
        return 2
    index, store = opened
    try:
        if sub == "plan":
            plan = plan_queue(store, limit=args.limit)
            print(f"queued {plan['enqueued']:,} new items from the map "
                  f"(considered: {plan['considered']})")
            print(format_status(store.status()))
            return 0

        if sub == "status":
            caches = {
                task: DerivationCache(cache_root, task).stats()
                for task in IMPLEMENTED_TASKS
            }
            total = {
                "entries": sum(c["entries"] for c in caches.values()),
                "text_bytes": sum(c["text_bytes"] for c in caches.values()),
            }
            print(format_status(store.status(), cache=total))
            for task, stats in sorted(caches.items()):
                print(f"  cache/{task}: {stats['entries']:,} entries, "
                      f"{stats['text_bytes'] / 1024 / 1024:.1f} MiB")
            if pause_path.exists():
                print("  PAUSED (remove with `rlm mine resume`)")
            return 0

        if sub == "retry":
            n = store.reset_failed(args.task)
            print(f"re-queued {n:,} failed item(s)"
                  + (f" for {args.task}" if args.task else ""))
            note = getattr(args, "skipped_note", None)
            if note:
                if not args.task:
                    print("Error: --skipped-note needs --task, so the re-opened skips are "
                          "scoped to one task.", file=sys.stderr)
                    return 2
                m = store.reset_skipped(args.task, note)
                print(f"re-opened {m:,} skipped item(s) for {args.task} "
                      f"(note={note}) — the harness can do this now")
            return 0

        if sub == "run":
            if not args.corpus_root:
                print("Error: --corpus-root is required to run a batch.",
                      file=sys.stderr)
                return 2
            tasks = (list(IMPLEMENTED_TASKS) if args.tasks.strip() == "all"
                     else [t.strip() for t in args.tasks.split(",") if t.strip()])
            unknown = [t for t in tasks if t not in ALL_TASKS]
            if unknown:
                print(f"Error: unknown task(s) {unknown}. Known: {list(ALL_TASKS)}",
                      file=sys.stderr)
                return 2
            budget = _parse_duration(args.budget)
            if args.budget is not None and budget is None:
                print(f"Error: cannot read --for {args.budget!r} "
                      "(use 90s, 30m, 2h or seconds).", file=sys.stderr)
                return 2
            deadline = _parse_deadline(args.until)
            if args.until is not None and deadline is None:
                print(f"Error: cannot read --until {args.until!r} "
                      "(use HH:MM).", file=sys.stderr)
                return 2

            holder = acquire_lock(lock_path)
            if holder is None:
                print(f"Error: another mining worker holds {lock_path}. "
                      "Stop it, or remove the file if it is stale.", file=sys.stderr)
                return 2

            from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation

            try:
                mount = LocalTreeMount(args.corpus_root)
            except ReadOnlyViolation as e:
                release_lock(lock_path)
                print(f"Error: {e}", file=sys.stderr)
                return 2

            every = max(1, int(args.progress_every or 200))

            text_index = index.text()
            text_index.ensure()

            def progress(stats) -> None:
                print(f"  {stats.processed:,} items "
                      f"(done {stats.done:,}, skipped {stats.skipped:,}, "
                      f"failed {stats.failed:,}, cache hits {stats.cache_hits:,})",
                      flush=True)

            print(f"mining for {args.budget or 'unbounded'}"
                  + (f" until {args.until}" if args.until else "")
                  + (f", at most {args.max_items:,} items" if args.max_items else ""))
            try:
                run = run_queue(
                    store=store, conn=index._conn, mount=mount,
                    cache_root=cache_root, tasks=tasks,
                    text_index=text_index,
                    budget_seconds=budget, deadline=deadline,
                    max_items=args.max_items, pause_file=pause_path,
                    lock_file=lock_path, progress=progress,
                    progress_every=every,
                    coverage_scan=args.coverage_scan,
                )
                # The report is inside the lock: it used to run after `release_lock`,
                # so a window whose work was done and published still looked free while
                # it counted — and a second worker could start on the same index.
                print(f"stopped: {run.stop_reason} after {run.seconds:,.1f}s")
                print(f"  done {run.stats.done:,}, skipped {run.stats.skipped:,}, "
                      f"failed {run.stats.failed:,}, cache hits {run.stats.cache_hits:,}")
                print(f"  by task: {run.stats.by_task}")
                print(format_status(store.status()))
            finally:
                release_lock(lock_path)
            return 0
    finally:
        index.close()
    print(f"Error: unknown mine subcommand {sub!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
