"""Mechanical cost and quality metrics for RO6 summaries (2026-09-23).

A summary is corpus-derived prose, so it may not leave the machine that holds the corpus
(`AGENTS.md` §1.9). That rules out the obvious way to judge one — a person, or another model,
reading it — and leaves arithmetic: how long each description took, how much it compressed,
whether its words are the document's own words, whether it is boilerplate, whether it stopped at
the output bound.

Two properties make this honest rather than decorative:

* **No metric here is called quality.** `groundedness` measures whether the summary's words
  occur in the document; that catches a fluent description built from words the document does not
  contain, and it also punishes a legitimate paraphrase. It is a floor-detector, not a score.
* **A record carries no text.** `measure` returns counts and ratios only, so the log can be read
  — and its aggregates pasted — without a single word of anyone's document travelling. That is
  enforced by a test, not by care: `tests/test_summary_metrics.py` fails if a record contains a
  document's word.

Token figures derived from characters are **estimates** (`WORDS_PER_TOKEN`, ~4 chars/token) and
are labelled as such wherever they are reported; the server's own tokeniser is not consulted.
"""

from __future__ import annotations

import collections
import json
import statistics
from pathlib import Path
from typing import Any, Iterable

#: Inferred, not measured: characters per token for the local models. Every token count computed
#: in this module inherits it, which is why they are named `estimated_*`.
CHARS_PER_TOKEN = 4.0

#: A word shorter than this is not a subject-matter word ("the", "of", "is" and most connectives).
MIN_CONTENT_WORD = 4

#: Words carrying no topic, so a summary that shares only these with its document shares nothing.
STOPWORDS = frozenset(
    """
    about above after again against also among because been before being below between both
    does doing down during each either from further have having here into itself just list more
    most only other over same should some such than that their them then there these they this
    those through under until very were what when where which while with within without would
    your
    """.split()
)

#: Phrases that mean the model did not describe anything. Flagged, never judged: "does not
#: contain" can be a true statement about an empty container member, and the count exists so a
#: reader can go and look.
BOILERPLATE = (
    "as an ai",
    "i cannot",
    "i can't",
    "i'm sorry",
    "i am sorry",
    "unable to",
    "the provided text",
    "the given text",
    "no content",
    "does not contain any",
    "cannot summarize",
    "cannot summarise",
    "no text was provided",
    "empty document",
)


def content_words(text: str) -> set[str]:
    """The subject-matter words of `text`: long enough, not a stopword, not pure punctuation."""
    words = {word.strip(".,;:!?()[]{}<>\"'`*#").lower() for word in text.split()}
    return {
        word
        for word in words
        if len(word) >= MIN_CONTENT_WORD
        and word not in STOPWORDS
        and any(character.isalnum() for character in word)
    }


def estimated_tokens(text: str) -> int:
    """Characters ÷ `CHARS_PER_TOKEN` — an estimate, and named like one."""
    return int(len(text) / CHARS_PER_TOKEN)


def groundedness(summary: str, source: str) -> float:
    """Fraction of the summary's content words that also occur in the document.

    A floor-detector, not a score. 1.0 is not the target: a model that paraphrases scores below
    it honestly, and a summary may legitimately reach for a category word the document never
    uses. What it catches is a description assembled from words the document does not contain at
    all — the shape a fluent hallucination has — and a summary in the wrong language.

    A summary with no content words returns 0.0, which is why emptiness is counted separately
    instead of being read off this number.
    """
    words = content_words(summary)
    if not words:
        return 0.0
    haystack = source.lower()
    return sum(1 for word in words if word in haystack) / len(words)


def is_boilerplate(summary: str) -> bool:
    """True when the reply reads as a refusal or as commentary rather than a description."""
    lowered = summary.lower()
    return any(phrase in lowered for phrase in BOILERPLATE)


def cap_hit(summary: str, max_tokens: int, *, threshold: float = 0.9) -> bool:
    """True when the reply came close to the output bound, i.e. may have been cut off."""
    return estimated_tokens(summary) >= threshold * max_tokens


def _server_block(
    usage: dict[str, Any] | None, timings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The server's own account of a call, or `None` when it gave none.

    llama-server reports `usage` (with `prompt_tokens_details.cached_tokens`) and a `timings`
    object splitting prompt processing from decode. Fields are copied **by name and never
    invented**: a missing field stays `None` rather than becoming a zero, because "0 ms of prompt
    processing" is a confident wrong answer (`AGENTS.md` §1.8 corollary).

    This is what makes a cost spread explicable at all. Without it, a call that took 29 s and one
    that took 803 s look like the same event at different speeds; with it, one is 4 fresh prompt
    tokens and the other is several thousand (measured 2026-09-23).
    """
    if not usage and not timings:
        return None
    usage = usage or {}
    timings = timings or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_tokens": details.get("cached_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_ms": timings.get("prompt_ms"),
        "prompt_n": timings.get("prompt_n"),
        "prompt_tokens_per_second": timings.get("prompt_per_second"),
        "predicted_ms": timings.get("predicted_ms"),
        "predicted_n": timings.get("predicted_n"),
        "predicted_tokens_per_second": timings.get("predicted_per_second"),
        "cache_n": timings.get("cache_n"),
    }


def _residual(seconds: float, timings: dict[str, Any] | None) -> float | None:
    """Client-observed seconds minus what the server says it spent, or `None` without timings.

    A large residual is the interesting case: time the model did not account for, which on this
    host has meant a request waiting on the router rather than being computed. It is reported as a
    number and **not explained here** — the number is the finding, the mechanism is not.
    """
    if not timings:
        return None
    spent = (
        float(timings.get("prompt_ms") or 0.0) + float(timings.get("predicted_ms") or 0.0)
    ) / 1000.0
    return round(float(seconds) - spent, 3)


def measure(
    *,
    model: str,
    seconds: float,
    source: str,
    summary: str,
    max_tokens: int,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    usage: dict[str, Any] | None = None,
    timings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One metrics record for one model call — **counts and ratios, never text**.

    This is the only place a run's numbers are assembled, and it is deliberately blind to
    content: the log it feeds stays on the machine that holds the corpus and its aggregates are
    safe to quote, because nothing in it is a word of anyone's document. The server's own
    timings ride along in a nested `server` object; the timestamps are what let a trace place a
    call in time beside the run it belongs to.
    """
    return {
        "kind": "end",
        "model": str(model),
        "seconds": round(float(seconds), 3),
        "started_at": started_at,
        "finished_at": finished_at,
        "input_chars": len(source),
        "output_chars": len(summary),
        "output_words": len(summary.split()),
        "estimated_output_tokens": estimated_tokens(summary),
        "max_tokens": int(max_tokens),
        "compression": round(len(summary) / len(source), 4) if source else 0.0,
        "groundedness": round(groundedness(summary, source), 4),
        "boilerplate": is_boilerplate(summary),
        "cap_hit": cap_hit(summary, max_tokens),
        "empty": not summary.strip(),
        "error": error,
        "server": _server_block(usage, timings),
        "client_residual_seconds": _residual(seconds, timings),
    }


def start_record(
    *, model: str, started_at: str, source: str, max_tokens: int,
) -> dict[str, Any]:
    """A row written **before** a call, so a call in flight is visible while it runs.

    The first live run made this necessary rather than tidy: the log only received a line when a
    call ended, so a 30-minute cold prompt looked exactly like a call that had never started, and
    the only way to tell was to watch the server's CPU. The cold probe then sat in that state for
    17 minutes with nothing in the log to say so.

    It carries the same identifiers as its completion — model, `started_at`, input size, bound —
    so a reader pairs them by `(model, started_at)`, and a start with no completion is a call that
    is still running, or one that was killed. It quotes nothing, like everything else here.
    """
    return {
        "kind": "start",
        "model": str(model),
        "started_at": started_at,
        "input_chars": len(source),
        "max_tokens": int(max_tokens),
    }


def read_log(path: Path) -> list[dict[str, Any]]:
    """Every record in a metrics log; a torn line becomes a `torn` record, not a lost one.

    A killed run leaves a partial last line. Dropping it silently would make the log understate
    what happened — which is the failure mode this project keeps meeting — so a line that does
    not parse is counted like any other outcome.
    """
    records: list[dict[str, Any]] = []
    path = Path(path)
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"torn": True, "model": "unknown"})
    return records


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def aggregate(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per-model cost and signal counts, plus the projections the decision gate needs.

    `usable` excludes failures, torn lines and empty replies, because a median that includes a
    refused call describes nothing. Failures are counted, not hidden: a model that answers two
    documents in three has a cost per *answer*, not per attempt.
    """
    materialised = list(records)
    # A `start` row is not a call: it says one *began*. Counting it as a call would put a 0.0 s
    # duration into the medians and a phantom empty reply into the failure counts — and a start
    # with no completion is the interesting row, because it is a call still running or killed.
    starts = [record for record in materialised if record.get("kind") == "start"]
    completed = [record for record in materialised if record.get("kind") != "start"]
    finished = {(str(r.get("model", "unknown")), r.get("started_at")) for r in completed}
    in_flight: collections.Counter[str] = collections.Counter(
        str(record.get("model", "unknown"))
        for record in starts
        if (str(record.get("model", "unknown")), record.get("started_at")) not in finished
    )
    by_model: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for record in materialised:
        by_model[str(record.get("model", "unknown"))].append(record)

    models: dict[str, Any] = {}
    for model, all_records in sorted(by_model.items()):
        group = [record for record in all_records if record.get("kind") != "start"]
        usable = [
            record
            for record in group
            if not record.get("error") and not record.get("torn") and not record.get("empty")
        ]
        seconds = [float(record.get("seconds", 0.0)) for record in usable]
        inputs = [float(record.get("input_chars", 0)) for record in usable]
        entry: dict[str, Any] = {
            "attempted": len(group),
            "in_flight": int(in_flight.get(model, 0)),
            "usable": len(usable),
            "failed": sum(1 for record in group if record.get("error")),
            "torn": sum(1 for record in group if record.get("torn")),
            "empty": sum(1 for record in group if record.get("empty")),
            "boilerplate": sum(1 for record in group if record.get("boilerplate")),
            "cap_hits": sum(1 for record in group if record.get("cap_hit")),
            "seconds_median": _median(seconds),
            "seconds_min": round(min(seconds), 1) if seconds else None,
            "seconds_max": round(max(seconds), 1) if seconds else None,
            "seconds_total": round(sum(seconds), 1),
            "server_calls": sum(1 for record in usable if record.get("server")),
            "cached_prompt_calls": sum(
                1
                for record in usable
                if record.get("server")
                and (
                    (record["server"].get("cache_n") or 0) > 0
                    or (record["server"].get("cached_tokens") or 0) > 0
                )
            ),
            "prompt_ms_median": _median(
                [
                    float(record["server"].get("prompt_ms") or 0.0)
                    for record in usable
                    if record.get("server")
                ]
            ),
            "predicted_ms_median": _median(
                [
                    float(record["server"].get("predicted_ms") or 0.0)
                    for record in usable
                    if record.get("server")
                ]
            ),
            "residual_seconds_median": _median(
                [
                    float(record["client_residual_seconds"])
                    for record in usable
                    if record.get("client_residual_seconds") is not None
                ]
            ),
            "input_chars_median": _median(inputs),
            "output_chars_median": _median(
                [float(record.get("output_chars", 0)) for record in usable]
            ),
            "compression_median": (
                round(
                    statistics.median(
                        [float(record.get("compression", 0.0)) for record in usable]
                    ),
                    4,
                )
                if usable
                else None
            ),
            "groundedness_median": (
                round(
                    statistics.median(
                        [float(record.get("groundedness", 0.0)) for record in usable]
                    ),
                    3,
                )
                if usable
                else None
            ),
            "groundedness_min": (
                round(min(float(record.get("groundedness", 0.0)) for record in usable), 3)
                if usable
                else None
            ),
            "groundedness_below_half": sum(
                1 for record in usable if float(record.get("groundedness", 0.0)) < 0.5
            ),
        }
        if seconds and entry["input_chars_median"]:
            kib = max(entry["input_chars_median"] / 1024.0, 1e-9)
            entry["seconds_per_kib"] = round(float(entry["seconds_median"]) / kib, 1)
        else:
            entry["seconds_per_kib"] = None
        models[model] = entry

    return {"models": models, "records": len(materialised), "in_flight": sum(in_flight.values())}


def projections(entry: dict[str, Any], *, documents: int) -> dict[str, Any]:
    """What `documents` would cost at this model's measured median, in hours.

    Median rather than mean: one document that took forty minutes because the box was swapping
    should not move the estimate for the next hundred. The projection is arithmetic on a
    measurement — it assumes the next documents resemble these, which the caller must weigh.
    """
    seconds = entry.get("seconds_median")
    if not seconds:
        return {"documents": documents, "hours": None}
    return {
        "documents": documents,
        "seconds_each": seconds,
        "hours": round(documents * float(seconds) / 3600.0, 2),
    }


def render(agg: dict[str, Any], *, scale: str = "", sets: dict[str, int] | None = None) -> str:
    """Aggregates as text — safe to paste, which is the point of `measure` returning no text."""
    lines = [f"{agg.get('records', 0)} record(s)"]
    if scale:
        lines.append(f"  scale: {scale}")
    if not agg.get("models"):
        lines.append("  nothing measured yet")
        return "\n".join(lines)
    for model, entry in agg["models"].items():
        lines.append(f"  model {model}:")
        lines.append(
            f"    attempted {entry['attempted']}, usable {entry['usable']}, "
            f"failed {entry['failed']}, empty {entry['empty']}, torn {entry['torn']}"
        )
        if entry.get("in_flight"):
            lines.append(
                f"    **{entry['in_flight']} call(s) started with no completion** — running now, "
                "or killed before they ended"
            )
        if entry["seconds_median"] is not None:
            lines.append(
                f"    median {entry['seconds_median']}s per document "
                f"(min {entry['seconds_min']}s, max {entry['seconds_max']}s; "
                f"{entry['seconds_per_kib']}s per KiB in, "
                f"{entry['input_chars_median']} chars in / "
                f"{entry['output_chars_median']} chars out)"
            )
        if entry.get("server_calls"):
            lines.append(
                f"    server: {entry['server_calls']} call(s) with timings, "
                f"{entry['cached_prompt_calls']} served from the prompt cache "
                f"(prompt ms median {entry['prompt_ms_median']}, "
                f"decode ms median {entry['predicted_ms_median']})"
            )
            if entry.get("residual_seconds_median") is not None:
                lines.append(
                    f"    client seconds the server did not account for: median "
                    f"{entry['residual_seconds_median']}s"
                )
        lines.append(
            f"    groundedness median {entry['groundedness_median']}, "
            f"min {entry['groundedness_min']}, "
            f"below 0.5: {entry['groundedness_below_half']}/{entry['usable']}"
        )
        lines.append(
            f"    boilerplate {entry['boilerplate']}, cap hits {entry['cap_hits']}, "
            f"compression median {entry['compression_median']}"
        )
        for name, size in (sets or {}).items():
            projection = projections(entry, documents=size)
            if projection["hours"] is not None:
                lines.append(
                    f"    {name} ({size} documents): {projection['hours']} hours "
                    f"at {projection['seconds_each']}s each"
                )
    return "\n".join(lines)
