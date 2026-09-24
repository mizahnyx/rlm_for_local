"""Render the summarise metrics log as per-call trace pages and a hierarchical JSONL.

Unlike `rlm trace render`, these pages contain **no corpus text**: `summary_metrics.measure`
stores counts and ratios only, so a trace here can be read anywhere, pasted, or opened in a JSONL
viewer without anyone's document travelling (`AGENTS.md` §1.9). What a call *is* has to be
inferred from its position in the log, its timestamp, its input size and the server's numbers —
which is why the server block was added.

What the pages are for: the per-document cost ranges from 29 s to 803 s, and the only way to
explain that is per call — how many prompt tokens were fresh, how many the prompt cache answered,
what the decode cost, and how much of the client's seconds the server does not account for.

Usage, on the machine that holds the corpus:

    uv run python scripts/render_summarise_traces.py \
        --log ~/rlm-derived/summaries-probe-cited.jsonl \
        --out-dir ~/rlm-derived/traces-summarise
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rlm_local.summary_metrics import aggregate, read_log, render  # noqa: E402


def call_trace(record: dict[str, Any], position: int) -> dict[str, Any]:
    """One call as a nested object: when, what the client saw, what the server said, outcome.

    Deliberately hierarchical rather than flat, so a viewer can fold it: a call is three things —
    the client's view, the server's view, and the difference between them, which is where an
    unexplained cost lives.
    """
    server = record.get("server") or {}
    return {
        "call": position,
        "when": {
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        },
        "client": {
            "seconds": record.get("seconds"),
            "residual_seconds": record.get("client_residual_seconds"),
        },
        "document": {
            "input_chars": record.get("input_chars"),
            "output_chars": record.get("output_chars"),
            "output_words": record.get("output_words"),
            "compression": record.get("compression"),
        },
        "server": server or None,
        "outcome": {
            "error": record.get("error"),
            "empty": record.get("empty"),
            "boilerplate": record.get("boilerplate"),
            "cap_hit": record.get("cap_hit"),
        },
        "quality": {"groundedness": record.get("groundedness")},
    }


def _served_from_cache(server: dict[str, Any] | None) -> bool:
    if not server:
        return False
    return bool((server.get("cache_n") or 0) > 0 or (server.get("cached_tokens") or 0) > 0)


def render_page(trace: dict[str, Any], *, model: str = "") -> str:
    """One call as Markdown."""
    server = trace["server"] or {}
    cache = _served_from_cache(trace["server"])
    lines = [
        f"# Call {trace['call']}",
        "",
        f"- model: `{model or 'unknown'}`",
        f"- started: {trace['when']['started_at']}  finished: {trace['when']['finished_at']}",
        f"- client seconds: **{trace['client']['seconds']}**",
        "",
        "## Where the time went",
        "",
        "| part | value |",
        "|---|---|",
        f"| server: prompt tokens evaluated | {server.get('prompt_n')} |",
        f"| server: prompt time | {server.get('prompt_ms')} ms "
        f"({server.get('prompt_tokens_per_second')} tok/s) |",
        f"| server: decode tokens | {server.get('predicted_n')} |",
        f"| server: decode time | {server.get('predicted_ms')} ms "
        f"({server.get('predicted_tokens_per_second')} tok/s) |",
        f"| server: prompt cache reuse (`cache_n`) | {server.get('cache_n')} |",
        f"| server: cached prompt tokens | {server.get('cached_tokens')} |",
        f"| **client seconds the server does not account for** | "
        f"**{trace['client']['residual_seconds']}** |",
        "",
        f"- served from the prompt cache: **{cache}**",
        "",
        "## The document and the reply",
        "",
        f"- input {trace['document']['input_chars']} chars, output "
        f"{trace['document']['output_chars']} chars "
        f"({trace['document']['output_words']} words), compression "
        f"{trace['document']['compression']}",
        f"- groundedness {trace['quality']['groundedness']} "
        "(fraction of the reply's content words that occur in the document; a floor-detector, "
        "not a score — a paraphrase scores below 1.0 honestly)",
        f"- outcome: error={trace['outcome']['error']}, empty={trace['outcome']['empty']}, "
        f"boilerplate={trace['outcome']['boilerplate']}, cap_hit={trace['outcome']['cap_hit']}",
        "",
        "## As JSONL",
        "",
        "```json",
        json.dumps(trace, sort_keys=True),
        "```",
        "",
        "*No document text appears anywhere on this page: the metrics record counts and ratios, "
        "never content.*",
        "",
    ]
    return "\n".join(lines)


def render_index(traces: list[dict[str, Any]], *, scale: str, summary: str) -> str:
    """The run as a table, with the aggregate block from the same records."""
    lines = ["# Summarise traces", "", f"scale: {scale}", "", "```", summary, "```", ""]
    lines += [
        "| call | started | seconds | unexplained | in chars | prompt tok (ms) | cache_n | "
        "decode tok (ms) | groundedness | error |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for trace in traces:
        server = trace["server"] or {}
        lines.append(
            f"| {trace['call']} | {trace['when']['started_at']} | "
            f"{trace['client']['seconds']} | {trace['client']['residual_seconds']} | "
            f"{trace['document']['input_chars']} | "
            f"{server.get('prompt_n')} ({server.get('prompt_ms')}) | {server.get('cache_n')} | "
            f"{server.get('predicted_n')} ({server.get('predicted_ms')}) | "
            f"{trace['quality']['groundedness']} | {trace['outcome']['error']} |"
        )
    cached = sum(1 for trace in traces if _served_from_cache(trace["server"]))
    lines += [
        "",
        f"{len(traces)} call(s), of which **{cached} served from the prompt cache** and "
        f"{len(traces) - cached} paid for a fresh prompt.",
        "",
        "*No document text appears in these traces or in the JSONL beside them.*",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, default=None,
                        help="refuse an --out-dir inside this tree")
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    if args.corpus_root is not None:
        out = args.out_dir.resolve()
        root = args.corpus_root.resolve()
        if out == root or root in out.parents:
            print(f"Error: --out-dir {args.out_dir} is inside the corpus root "
                  f"{args.corpus_root}.", file=sys.stderr)
            return 2

    records = read_log(args.log)
    traces = [call_trace(record, index + 1) for index, record in enumerate(records)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(args.out_dir, 0o700)
    except OSError:  # pragma: no cover - a filesystem that refuses the mode
        pass

    agg = aggregate(records)
    scale = f"{args.model or 'model unknown'}, from {args.log}"
    summary = render(agg, scale=scale)
    written = 0
    for trace in traces:
        path = args.out_dir / f"call-{trace['call']:03d}.md"
        path.write_text(render_page(trace, model=args.model), encoding="utf-8")
        written += 1
    (args.out_dir / "index.md").write_text(
        render_index(traces, scale=scale, summary=summary), encoding="utf-8")
    (args.out_dir / "traces.jsonl").write_text(
        "".join(json.dumps(trace, sort_keys=True) + "\n" for trace in traces), encoding="utf-8")
    for path in args.out_dir.iterdir():
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover
            pass

    print(f"wrote {written + 2} file(s) to {args.out_dir}")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
