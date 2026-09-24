"""Tests for the RO6 summary metrics.

The tests that matter here are not the arithmetic ones — those are easy and dull. They are the
two properties the module exists to hold: a record carries **no document text** (so the log can
be read and its aggregates pasted without anyone's file travelling), and a metric that cannot see
the truth says so rather than reporting a confident zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from rlm_local.summary_metrics import (
    BOILERPLATE,
    aggregate,
    cap_hit,
    content_words,
    estimated_tokens,
    groundedness,
    is_boilerplate,
    measure,
    projections,
    read_log,
    render,
    start_record,
)

SOURCE = (
    "Ferromagnetic sprocket calibration notes for the Markhausen turbine retrofit. "
    "The turbine governs at 3000 revolutions and the sprocket tolerance is 0.02 millimetres."
)


class TestItCarriesNoText:
    def test_a_record_contains_no_document_word_and_no_reply(self) -> None:
        """§1.9, mechanised: the log is arithmetic, so nothing in it is anyone's prose.

        The metrics log is written where the corpus is and read from a session that travels; if a
        record carried a snippet "for context" the whole design would leak one document at a
        time, with the leak looking like a feature.
        """
        record = measure(
            model="test-model",
            seconds=12.5,
            source=SOURCE,
            summary="Replytoken describes a turbine retrofit.",
            max_tokens=400,
        )
        serialised = json.dumps(record)
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in serialised.lower(), f"{word!r} reached the record"
        assert "Replytoken" not in serialised, "the reply itself must not be in the record"

    def test_the_record_still_says_what_happened(self) -> None:
        record = measure(
            model="test-model", seconds=12.5, source=SOURCE,
            summary="Replytoken describes a turbine retrofit.", max_tokens=400,
        )
        assert record["input_chars"] == len(SOURCE)
        assert record["output_chars"] == len("Replytoken describes a turbine retrofit.")
        assert record["seconds"] == 12.5
        assert record["compression"] > 0


class TestACallInFlightIsVisible:
    """A call that has not finished is a fact, not an absence — and not a zero."""

    def test_a_start_row_is_not_counted_as_a_call(self) -> None:
        start = start_record(model="m", started_at="2026-09-23T19:00:00+00:00", source=SOURCE,
                             max_tokens=400)
        end = measure(model="m", seconds=10.0, source=SOURCE, summary="turbine notes",
                      max_tokens=400, started_at="2026-09-23T19:00:00+00:00",
                      finished_at="2026-09-23T19:00:10+00:00")
        agg = aggregate([start, end])
        assert agg["records"] == 2
        assert agg["models"]["m"]["attempted"] == 1, "the start row is not a second call"
        assert agg["models"]["m"]["usable"] == 1
        assert agg["models"]["m"]["seconds_median"] == 10.0, (
            "a start row counted as a call would drag the median toward zero"
        )
        assert agg["models"]["m"]["in_flight"] == 0

    def test_a_start_with_no_completion_is_reported_as_in_flight(self) -> None:
        start = start_record(model="m", started_at="2026-09-23T19:00:00+00:00", source=SOURCE,
                             max_tokens=400)
        agg = aggregate([start])
        assert agg["in_flight"] == 1
        assert agg["models"]["m"]["in_flight"] == 1
        assert agg["models"]["m"]["attempted"] == 0
        assert "started with no completion" in render(agg)

    def test_a_start_row_quotes_nothing(self) -> None:
        record = start_record(model="m", started_at="2026-09-23T19:00:00+00:00", source=SOURCE,
                              max_tokens=400)
        serialised = json.dumps(record)
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in serialised.lower()


class TestTheServersOwnAccount:
    """What separates 'slow' from 'did a lot of work': the server's own split of a call."""

    def test_timings_are_carried_and_the_residual_is_the_difference(self) -> None:
        record = measure(
            model="m", seconds=100.0, source=SOURCE, summary="turbine calibration notes",
            max_tokens=400,
            started_at="2026-09-23T15:00:00+00:00", finished_at="2026-09-23T15:01:40+00:00",
            usage={"prompt_tokens": 5000, "completion_tokens": 40,
                   "prompt_tokens_details": {"cached_tokens": 4996}},
            timings={"prompt_ms": 3000.0, "prompt_n": 4, "predicted_ms": 20000.0,
                     "predicted_n": 40, "cache_n": 4996},
        )
        assert record["server"]["cached_tokens"] == 4996
        assert record["server"]["prompt_n"] == 4
        assert record["client_residual_seconds"] == 77.0, (
            "100 s observed, 23 s the server accounts for: the rest is the finding"
        )
        assert record["started_at"] == "2026-09-23T15:00:00+00:00"
        assert record["finished_at"] == "2026-09-23T15:01:40+00:00"

    def test_the_server_block_carries_no_document_text_either(self) -> None:
        record = measure(
            model="m", seconds=10.0, source=SOURCE, summary="turbine calibration notes",
            max_tokens=400,
            usage={"prompt_tokens": 1, "completion_tokens": 2},
            timings={"prompt_ms": 1.0, "predicted_ms": 2.0},
        )
        serialised = json.dumps(record)
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in serialised.lower()

    def test_no_timings_says_unknown_rather_than_zero(self) -> None:
        """A backend that reports nothing must not look like a measured 0 ms of work."""
        record = measure(model="m", seconds=5.0, source=SOURCE, summary="notes", max_tokens=400)
        assert record["server"] is None
        assert record["client_residual_seconds"] is None

    def test_the_aggregate_counts_the_calls_the_cache_answered(self) -> None:
        cached = measure(
            model="m", seconds=30.0, source=SOURCE, summary="turbine calibration notes",
            max_tokens=400, timings={"prompt_ms": 1000.0, "prompt_n": 4, "cache_n": 4996,
                                     "predicted_ms": 20000.0},
        )
        fresh = measure(
            model="m", seconds=600.0, source=SOURCE, summary="turbine calibration notes",
            max_tokens=400, timings={"prompt_ms": 500000.0, "prompt_n": 5000, "cache_n": 0,
                                     "predicted_ms": 20000.0},
        )
        entry = aggregate([cached, fresh])["models"]["m"]
        assert entry["server_calls"] == 2
        assert entry["cached_prompt_calls"] == 1
        assert entry["prompt_ms_median"] == 250500.0


class TestGroundednessIsAFloorDetector:
    def test_the_documents_own_words_score_high(self) -> None:
        assert groundedness("Ferromagnetic sprocket calibration notes", SOURCE) == 1.0

    def test_invented_words_score_low(self) -> None:
        """A fluent description of a *different* document is what this is for."""
        assert groundedness("Photosynthesis chlorophyll bibliography", SOURCE) == 0.0

    def test_a_paraphrase_is_not_called_a_failure(self) -> None:
        """Below 1.0 is normal and must not read as a verdict: this is why it is not a score."""
        score = groundedness("A turbine manual covering retrofit tolerances", SOURCE)
        assert 0.0 < score < 1.0

    def test_an_empty_summary_is_zero_and_counted_separately(self) -> None:
        assert groundedness("", SOURCE) == 0.0
        record = measure(model="m", seconds=1.0, source=SOURCE, summary="", max_tokens=400)
        assert record["empty"] is True
        assert aggregate([record])["models"]["m"]["empty"] == 1
        assert aggregate([record])["models"]["m"]["usable"] == 0

    def test_a_summary_with_no_content_words_is_zero_not_an_error(self) -> None:
        """The §1.8 corollary at the smallest scale: nothing to measure says 0.0, and the
        emptiness count is where a reader learns *why*."""
        assert groundedness("it is of the", SOURCE) == 0.0


class TestBoilerplateIsFlaggedNotJudged:
    def test_a_refusal_is_flagged(self) -> None:
        assert is_boilerplate("I cannot summarise this document.")

    def test_a_description_is_not(self) -> None:
        assert not is_boilerplate("Calibration notes for a turbine retrofit.")

    def test_every_pattern_is_lower_case_so_matching_is_not_surprising(self) -> None:
        assert all(phrase == phrase.lower() for phrase in BOILERPLATE)


class TestTheOutputBound:
    def test_a_reply_at_the_bound_is_flagged(self) -> None:
        assert cap_hit("word " * 400, 400)

    def test_a_short_reply_is_not(self) -> None:
        assert not cap_hit("a short description", 400)

    def test_estimated_tokens_is_an_estimate_and_says_so_by_name(self) -> None:
        assert estimated_tokens("x" * 400) == 100


class TestTheLogIsReadBack:
    def test_a_torn_line_is_counted_not_dropped(self, tmp_path: Path) -> None:
        """A killed run leaves half a line; dropping it would understate what happened."""
        path = tmp_path / "log.jsonl"
        path.write_text(
            json.dumps(measure(model="m", seconds=1.0, source=SOURCE,
                               summary="odd calibration notes", max_tokens=400)) + "\n"
            + '{"model": "m", "seco',
            encoding="utf-8",
        )
        records = read_log(path)
        assert len(records) == 2
        assert records[1]["torn"] is True
        # A torn line is half a record: it cannot say which model was running, so it is counted
        # under `unknown` rather than attributed to the model of the line before it. Guessing
        # would put a failure on a model that may not have caused it.
        assert aggregate(records)["models"]["unknown"]["torn"] == 1
        assert aggregate(records)["models"]["m"]["torn"] == 0

    def test_a_missing_log_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert read_log(tmp_path / "nope.jsonl") == []


class TestAggregatesAndProjections:
    def _records(self) -> list[dict]:
        return [
            measure(model="m", seconds=60.0, source=SOURCE,
                    summary="turbine calibration notes", max_tokens=400),
            measure(model="m", seconds=120.0, source=SOURCE,
                    summary="turbine calibration notes", max_tokens=400),
            measure(model="other", seconds=10.0, source=SOURCE,
                    summary="turbine calibration notes", max_tokens=400),
            measure(model="m", seconds=5.0, source=SOURCE, summary="", max_tokens=400,
                    error="RuntimeError"),
        ]

    def test_models_are_kept_apart(self) -> None:
        agg = aggregate(self._records())
        assert agg["records"] == 4
        assert agg["models"]["m"]["attempted"] == 3
        assert agg["models"]["m"]["usable"] == 2
        assert agg["models"]["m"]["failed"] == 1
        assert agg["models"]["other"]["usable"] == 1

    def test_the_median_ignores_the_failure(self) -> None:
        """A median that includes a refused call describes no model's behaviour."""
        entry = aggregate(self._records())["models"]["m"]
        assert entry["seconds_median"] == 90.0
        assert entry["seconds_total"] == 180.0

    def test_the_spread_is_reported_because_the_first_call_is_not_like_the_rest(self) -> None:
        """A router loads a model on demand, so the first description of a run can include the
        load. A median of three can hide exactly that, and a cost the operator plans with must
        not hide it."""
        entry = aggregate(self._records())["models"]["m"]
        assert entry["seconds_min"] == 60.0
        assert entry["seconds_max"] == 120.0

    def test_projections_are_arithmetic_on_the_measurement(self) -> None:
        entry = aggregate(self._records())["models"]["m"]
        projection = projections(entry, documents=240)
        assert projection["hours"] == 6.0, "240 documents at 90 s each is six hours"

    def test_no_measurement_projects_nothing_rather_than_zero(self) -> None:
        """A window that has not run must not report that it costs nothing."""
        assert projections({}, documents=240)["hours"] is None

    def test_render_states_the_scale_and_carries_no_document_words(self) -> None:
        """AGENTS.md §1.7: a number means nothing without the scale it came from."""
        text = render(
            aggregate(self._records()),
            scale="lunacode, Qwen 4B, one model resident",
            sets={"cited set": 13},
        )
        assert "lunacode" in text
        assert "cited set (13 documents)" in text
        for word in content_words(SOURCE):
            if len(word) >= 6:
                assert word.lower() not in text.lower()

    def test_an_empty_run_says_nothing_was_measured(self) -> None:
        assert "nothing measured yet" in render(aggregate([]))
