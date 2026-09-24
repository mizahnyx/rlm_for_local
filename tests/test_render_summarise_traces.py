"""Tests for the summarise trace renderer.

The property that matters is the one that makes these traces useful *and* safe: they carry the
numbers and none of the prose, so a call can be analysed — cold or cache-served, prompt or decode,
accounted for or not — without a document travelling. Plus one structural property: the JSONL is
hierarchical, because a flat list of numbers is what made the first measurement unreadable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rlm_local.summary_metrics import content_words, measure
from scripts.render_summarise_traces import call_trace, main, render_index, render_page

SOURCE = (
    "Ferromagnetic sprocket calibration notes for the Markhausen turbine retrofit. "
    "The turbine governs at 3000 revolutions and tolerance is 0.02 millimetres."
)


def _cold() -> dict:
    return measure(
        model="qwen@host:9010", seconds=600.0, source=SOURCE,
        summary="Turbine calibration notes, Markhausen retrofit.",
        max_tokens=400, started_at="2026-09-23T19:00:00+00:00",
        finished_at="2026-09-23T19:10:00+00:00",
        usage={"prompt_tokens": 5000, "completion_tokens": 40,
               "prompt_tokens_details": {"cached_tokens": 0}},
        timings={"prompt_ms": 500000.0, "prompt_n": 5000, "predicted_ms": 20000.0,
                 "predicted_n": 40, "cache_n": 0},
    )


def _warm() -> dict:
    return measure(
        model="qwen@host:9010", seconds=30.0, source=SOURCE,
        summary="Turbine calibration notes, Markhausen retrofit.",
        max_tokens=400, started_at="2026-09-23T19:11:00+00:00",
        finished_at="2026-09-23T19:11:30+00:00",
        usage={"prompt_tokens": 5000, "completion_tokens": 40,
               "prompt_tokens_details": {"cached_tokens": 4996}},
        timings={"prompt_ms": 100.0, "prompt_n": 4, "predicted_ms": 20000.0,
                 "predicted_n": 40, "cache_n": 4996},
    )


class TestItIsHierarchical:
    def test_a_call_nests_the_client_the_server_and_the_difference(self) -> None:
        trace = call_trace(_cold(), 1)
        assert trace["client"]["seconds"] == 600.0
        assert trace["server"]["prompt_n"] == 5000
        assert trace["client"]["residual_seconds"] == pytest.approx(80.0), (
            "600 s observed, 520 s the server accounts for"
        )
        assert trace["document"]["input_chars"] == len(SOURCE)
        assert trace["outcome"]["error"] is None

    def test_no_server_numbers_stays_null_rather_than_zero(self) -> None:
        trace = call_trace(measure(model="m", seconds=1.0, source=SOURCE, summary="notes",
                                   max_tokens=400), 1)
        assert trace["server"] is None


class TestItQuotesNothing:
    def test_a_page_carries_no_document_word(self) -> None:
        text = render_page(call_trace(_cold(), 1), model="qwen@host:9010")
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in text.lower()
        assert "600.0" in text

    def test_the_index_carries_no_document_word(self) -> None:
        text = render_index(
            [call_trace(_cold(), 1), call_trace(_warm(), 2)],
            scale="lunacode, one model, cold/warm pairs", summary="2 record(s)",
        )
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in text.lower()


class TestTheIndexReadsTheSpread:
    def test_cold_and_cached_calls_are_counted_apart(self) -> None:
        """The number the whole instrument exists for: how many calls the cache answered."""
        text = render_index(
            [call_trace(_cold(), 1), call_trace(_warm(), 2)],
            scale="s", summary="2 record(s)",
        )
        assert "**1 served from the prompt cache** and 1 paid for a fresh prompt" in text

    def test_a_reused_prefix_does_not_read_as_a_cache_hit(self) -> None:
        """The cold probe's shape: 87 reused tokens against 9 431 fresh, 29.6 minutes."""
        from rlm_local.summary_metrics import measure as measure_call

        cold = measure_call(
            model="qwen@host:9010", seconds=1773.3, source=SOURCE,
            summary="Turbine calibration notes.", max_tokens=400,
            timings={"prompt_ms": 1751603.0, "prompt_n": 9431, "cache_n": 87,
                     "predicted_ms": 21213.0, "predicted_n": 45},
        )
        text = render_index([call_trace(cold, 1)], scale="s", summary="1 record(s)")
        assert "**0 served from the prompt cache** and 1 paid for a fresh prompt" in text

    def test_it_writes_pages_and_a_jsonl_beside_the_corpus(self, tmp_path: Path) -> None:
        log = tmp_path / "summaries.jsonl"
        log.write_text(
            "\n".join(json.dumps(record) for record in (_cold(), _warm())) + "\n",
            encoding="utf-8",
        )
        out = tmp_path / "traces"
        assert main(["--log", str(log), "--out-dir", str(out), "--model", "qwen@host:9010"]) == 0
        assert (out / "index.md").exists()
        assert (out / "call-001.md").exists() and (out / "call-002.md").exists()
        traces = [json.loads(line) for line in (out / "traces.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert [trace["call"] for trace in traces] == [1, 2]
        assert traces[1]["server"]["cache_n"] == 4996

    def test_a_start_row_does_not_become_a_page(self, tmp_path: Path) -> None:
        """A call in flight is reported by the aggregate, not rendered as a call of its own."""
        from rlm_local.summary_metrics import start_record

        log = tmp_path / "summaries.jsonl"
        start = start_record(model="qwen@host:9010", started_at="2026-09-23T19:00:00+00:00",
                             source=SOURCE, max_tokens=400)
        log.write_text(json.dumps(start) + "\n" + json.dumps(_cold()) + "\n", encoding="utf-8")
        out = tmp_path / "traces"
        assert main(["--log", str(log), "--out-dir", str(out)]) == 0
        assert (out / "call-001.md").exists()
        assert not (out / "call-002.md").exists(), "a start row is not a call"
        traces = (out / "traces.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(traces) == 1

    def test_an_out_dir_inside_the_corpus_is_refused(self, tmp_path: Path) -> None:
        log = tmp_path / "summaries.jsonl"
        log.write_text(json.dumps(_cold()) + "\n", encoding="utf-8")
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        assert main(["--log", str(log), "--out-dir", str(corpus / "traces"),
                     "--corpus-root", str(corpus)]) == 2
        assert not (corpus / "traces").exists()
