"""Tests for the RO6 summariser engine.

The engine is the only place a real model is asked to describe a corpus document, so the tests
that matter are about what it *sends* and what it *leaves behind*: the document goes into the
prompt, the token bound reaches the backend, the model is part of the engine's identity (or a
second model is served the first model's work), and the metrics log on disk quotes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rlm_local.summarise import (
    BACKEND_DEFAULT_TIMEOUT,
    DECODE_TOKENS_PER_SECOND,
    PROMPT_TOKENS_PER_SECOND,
    append_record,
    engine_tag_for,
    make_summarise_engine,
    summarise_timeout_seconds,
)
from rlm_local.summary_metrics import CHARS_PER_TOKEN

DOCUMENT = (
    "Zanzibaricum ferromagnetic sprocket calibration notes, revision nine. "
    "The turbine governs at 3000 revolutions and tolerance is 0.02 millimetres."
)


class FakeBackend:
    """A backend that records every call and can be made to fail."""

    def __init__(self, reply: str = "Calibration notes for a turbine retrofit.",
                 error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def chat(self, messages, *, tier="root", max_tokens=1500, temperature=0.0,
             response_schema=None):
        self.calls.append({
            "messages": messages, "tier": tier, "max_tokens": max_tokens,
            "temperature": temperature,
        })
        if self.error is not None:
            raise self.error
        return self.reply


class TestTheTimeoutCoversTheCap:
    """Two bounds that contradicted each other, and the run it cost.

    The kernel caps a summary's input; the HTTP client caps how long one call may take. Each is
    reasonable alone, and together they guaranteed that the largest documents — the ones most
    worth describing — would fail.
    """

    def _modelled(self, input_chars: int, max_tokens: int) -> float:
        return (
            (input_chars / CHARS_PER_TOKEN) / PROMPT_TOKENS_PER_SECOND
            + max_tokens / DECODE_TOKENS_PER_SECOND
        )

    def test_it_is_derived_from_the_kernels_cap_and_bound(self) -> None:
        from rlm_kernel.mine import MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS

        derived = summarise_timeout_seconds(MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS)
        assert derived > self._modelled(MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS), (
            "the safety factor must leave room above the modelled cost"
        )
        assert derived > BACKEND_DEFAULT_TIMEOUT, (
            "the client's own default is exactly what killed the first live call"
        )

    def test_it_would_have_covered_the_first_live_call(self) -> None:
        """Measured 2026-09-23: `ReadTimeout` after 901.9 s on a 30 838-character document.

        Two things are pinned here. The incident must be *explained* by these constants — three
        300 s attempts were not enough for a document that size — and the derived default must
        cover it. If someone changes the throughput figures, the explanation stops holding and
        this test says so rather than leaving a story that no longer adds up.
        """
        from rlm_kernel.mine import MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS

        modelled = self._modelled(30_838, SUMMARY_MAX_TOKENS)
        assert modelled > BACKEND_DEFAULT_TIMEOUT * 3, (
            "the incident is only explained if three 300 s attempts were too few"
        )
        assert summarise_timeout_seconds(MAX_SUMMARY_INPUT_BYTES, SUMMARY_MAX_TOKENS) > modelled

    def test_a_smaller_cap_means_a_shorter_timeout(self) -> None:
        assert summarise_timeout_seconds(4096, 400) < summarise_timeout_seconds(32768, 400)


class TestWhatItSends:
    def test_the_document_and_the_bound_reach_the_model(self) -> None:
        backend = FakeBackend()
        engine = make_summarise_engine(backend, model="the-model")
        summary, _meta = engine(DOCUMENT, 400)
        call = backend.calls[0]
        user = call["messages"][-1]["content"]
        assert DOCUMENT in user
        assert "400 tokens" in user, "the bound must be stated to the model, not only enforced"
        assert call["max_tokens"] == 400, "and enforced too"
        assert call["tier"] == "root"
        assert call["temperature"] == 0.0
        assert summary == backend.reply

    def test_the_system_message_names_the_injection_boundary(self) -> None:
        """The document is untrusted text placed inside a prompt: instructions inside it are
        content. A description task that obeys the document is a description of nothing."""
        backend = FakeBackend()
        make_summarise_engine(backend, model="m")(DOCUMENT, 400)
        system = backend.calls[0]["messages"][0]["content"].lower()
        assert backend.calls[0]["messages"][0]["role"] == "system"
        assert "never instructions" in system

    def test_the_default_bound_is_the_kernels_own(self) -> None:
        from rlm_kernel.mine import SUMMARY_MAX_TOKENS

        backend = FakeBackend()
        engine = make_summarise_engine(backend, model="m")
        engine(DOCUMENT, SUMMARY_MAX_TOKENS)
        assert backend.calls[0]["max_tokens"] == SUMMARY_MAX_TOKENS == 400

    def test_max_tokens_can_be_overridden_for_a_different_model(self) -> None:
        backend = FakeBackend()
        engine = make_summarise_engine(backend, model="m", max_tokens=120)
        engine(DOCUMENT, 120)
        assert backend.calls[0]["max_tokens"] == 120


class TestTheEngineHasAnIdentity:
    def test_the_tag_names_the_model_and_the_endpoint(self) -> None:
        """Two servers can serve the same model name with different weights, and a description
        from one is not the other's — so the host is part of the identity."""
        assert engine_tag_for("qwen", "https://lunacode:9010/v1") == "qwen@lunacode:9010"
        assert engine_tag_for("qwen", None) == "qwen"

    def test_the_engine_carries_its_tag_for_the_cache_key(self) -> None:
        engine = make_summarise_engine(FakeBackend(), model="qwen",
                                       endpoint="https://lunacode:9010/v1")
        assert engine.engine_tag == "qwen@lunacode:9010"

    def test_two_models_get_two_tags(self) -> None:
        first = make_summarise_engine(FakeBackend(), model="model-a", endpoint="https://h:9010/v1")
        second = make_summarise_engine(FakeBackend(), model="model-b", endpoint="https://h:9010/v1")
        assert first.engine_tag != second.engine_tag

    def test_an_explicit_tag_wins(self) -> None:
        engine = make_summarise_engine(FakeBackend(), model="m", tag="named-by-the-operator")
        assert engine.engine_tag == "named-by-the-operator"

    def test_the_meta_the_kernel_caches_names_the_engine(self) -> None:
        engine = make_summarise_engine(FakeBackend(), model="qwen", endpoint="https://h:9010/v1")
        _summary, meta = engine(DOCUMENT, 400)
        assert meta["engine"] == "qwen@h:9010"
        assert meta["model"] == "qwen"
        assert meta["input_chars"] == len(DOCUMENT)
        assert meta["seconds"] >= 0.0


class TestItRecordsWhatTheServerSaid:
    """The cost spread is only explicable per call: how many prompt tokens were *fresh*."""

    def test_a_backend_that_reports_timings_is_asked_for_them(self, tmp_path: Path) -> None:
        from rlm_local.model_backend import ChatResult

        class DetailedBackend:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def chat_detailed(self, messages, **kwargs):
                self.calls.append(kwargs)
                return ChatResult(
                    "Calibration notes for a turbine retrofit.",
                    {"prompt_tokens": 5000, "completion_tokens": 33,
                     "prompt_tokens_details": {"cached_tokens": 4996}},
                    {"prompt_ms": 100.0, "prompt_n": 4, "predicted_ms": 25000.0,
                     "predicted_n": 33, "cache_n": 4996},
                )

            def chat(self, *args, **kwargs):
                raise AssertionError("chat() must not be used when the metadata is available")

        log = tmp_path / "summaries.jsonl"
        backend = DetailedBackend()
        engine = make_summarise_engine(backend, model="qwen", log_path=log)
        _summary, meta = engine(DOCUMENT, 400)
        assert meta["server"]["prompt_n"] == 4
        assert meta["server"]["cache_n"] == 4996
        assert meta["client_residual_seconds"] is not None
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["server"]["predicted_ms"] == 25000.0
        assert record["started_at"] and record["finished_at"], "a trace must place the call in time"

    def test_a_backend_without_timings_records_unknown(self, tmp_path: Path) -> None:
        log = tmp_path / "summaries.jsonl"
        engine = make_summarise_engine(FakeBackend(), model="qwen", log_path=log)
        _summary, meta = engine(DOCUMENT, 400)
        assert meta["server"] is None, "a backend that reports nothing must not look measured"
        assert json.loads(log.read_text(encoding="utf-8").strip())["server"] is None


class TestTheLogQuotesNothing:
    def test_a_call_writes_one_record_and_no_document_text(self, tmp_path: Path) -> None:
        """§1.9 again, at the engine: the metrics log is the artefact that travels."""
        log = tmp_path / "summaries.jsonl"
        engine = make_summarise_engine(FakeBackend(reply="Replytoken, a turbine retrofit."),
                                       model="qwen", log_path=log)
        engine(DOCUMENT, 400)
        text = log.read_text(encoding="utf-8")
        assert len(text.strip().splitlines()) == 1
        record = json.loads(text.strip())
        assert record["model"] == "qwen"
        assert record["input_chars"] == len(DOCUMENT)
        for word in ("Zanzibaricum", "ferromagnetic", "calibration", "turbine"):
            assert word.lower() not in text.lower()
        assert "Replytoken" not in text, "the reply is not in the log either"

    def test_a_failure_is_a_row_in_the_log_not_an_absence(self, tmp_path: Path) -> None:
        """A model that fails every call and a model that is never called look identical in an
        item count; the log is where they differ."""
        log = tmp_path / "summaries.jsonl"
        engine = make_summarise_engine(FakeBackend(error=RuntimeError("router down")),
                                       model="qwen", log_path=log)
        with pytest.raises(RuntimeError):
            engine(DOCUMENT, 400)
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["error"] == "RuntimeError"
        assert record["output_chars"] == 0

    def test_no_log_path_means_no_file_is_written(self, tmp_path: Path) -> None:
        engine = make_summarise_engine(FakeBackend(), model="qwen", log_path=None)
        engine(DOCUMENT, 400)
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
    def test_the_log_is_created_0600(self, tmp_path: Path) -> None:
        log = tmp_path / "derived" / "summaries.jsonl"
        append_record(log, {"model": "m"})
        assert (log.stat().st_mode & 0o777) == 0o600

    def test_appending_keeps_the_records_it_already_had(self, tmp_path: Path) -> None:
        log = tmp_path / "summaries.jsonl"
        append_record(log, {"model": "first"})
        append_record(log, {"model": "second"})
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [record["model"] for record in records] == ["first", "second"]
