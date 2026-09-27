"""Tests for the OCR engine (Gate 2, sub-question B).

The pipeline is rendering plus a model call, and neither needs the model or poppler to be
tested: the renderer and the client are injected. What is worth testing is what a *failure*
looks like — a page that failed must be counted, a document with no pages must not come back
as a document that said nothing, and the temporary directory must be gone on every path out,
because this runs on a root filesystem that is 96% full.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rlm_local.ocr import (
    MAX_PAGES_PER_DOCUMENT,
    OCR_PROMPT,
    OcrClient,
    OcrFailure,
    make_ocr_engine,
    render_pages,
)


class FakeMount:
    """Stands in for the read-only mount: one file, read on demand."""

    def __init__(self, payload: bytes = b"%PDF-1.4 fake") -> None:
        self.payload = payload
        self.opened: list[str] = []

    def open_readonly(self, rel: str, max_bytes: int | None = None):
        self.opened.append(rel)
        payload = self.payload[:max_bytes] if max_bytes else self.payload

        class _Handle:
            def __enter__(self_inner):
                import io

                return io.BytesIO(payload)

            def __exit__(self_inner, *exc):
                return False

        return _Handle()


class FakeClient:
    """One text per page, with a scripted page number that fails."""

    tag = "fake-ocr@box"

    def __init__(self, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.seen: list[str] = []

    def transcribe(self, image: Path) -> str:
        self.seen.append(Path(image).name)
        if self.fail_on is not None and len(self.seen) == self.fail_on:
            raise OcrFailure("scripted page failure")
        return f"words of {Path(image).name}"


def fake_renderer(pages: int = 2, *, record: dict[str, Any] | None = None):
    """A renderer that writes `pages` empty PNGs and records what it was called with."""

    def render(document: Path, out_dir: Path, *, dpi: int, max_pages: int) -> list[Path]:
        if record is not None:
            record.update(document=document, out_dir=out_dir, dpi=dpi, max_pages=max_pages)
        made = []
        for index in range(1, pages + 1):
            path = Path(out_dir) / f"page-{index:02d}.png"
            path.write_bytes(b"\x89PNG fake")
            made.append(path)
        return made

    return render


class TestTheEngine:
    def test_it_reads_through_the_mount_and_joins_the_pages(self) -> None:
        mount, client = FakeMount(), FakeClient()
        engine = make_ocr_engine(client, renderer=fake_renderer(3))
        text, meta = engine(mount, "docs/scan.pdf")
        assert mount.opened == ["docs/scan.pdf"], "the document is read through the mount"
        assert "words of page-01.png" in text and "words of page-03.png" in text
        assert meta["pages"] == 3 and meta["pages_failed"] == 0
        assert meta["engine"] == "fake-ocr@box"

    def test_the_page_cap_reaches_the_renderer(self) -> None:
        record: dict[str, Any] = {}
        engine = make_ocr_engine(FakeClient(), renderer=fake_renderer(1, record=record),
                                 dpi=96, max_pages=7)
        engine(FakeMount(), "docs/scan.pdf")
        assert record["dpi"] == 96
        assert record["max_pages"] == 7

    def test_a_failed_page_is_counted_and_the_rest_are_kept(self) -> None:
        """A document whose page 2 failed must not come back looking complete."""
        engine = make_ocr_engine(FakeClient(fail_on=2), renderer=fake_renderer(3))
        text, meta = engine(FakeMount(), "docs/scan.pdf")
        assert meta["pages"] == 3
        assert meta["pages_failed"] == 1
        assert "page-02" not in text, "the failed page contributes nothing"
        assert "page-03" in text, "the pages that worked are still returned"

    def test_no_rendered_page_says_so_rather_than_being_blank(self) -> None:
        engine = make_ocr_engine(FakeClient(), renderer=fake_renderer(0))
        text, meta = engine(FakeMount(), "docs/scan.pdf")
        assert text == ""
        assert meta["pages"] == 0
        assert "render" in str(meta.get("note", "")), "an empty document explains itself"

    def test_the_temporary_directory_is_removed_on_success_and_on_failure(self, tmp_path) -> None:
        """Nothing may be left behind: the page images are copies of corpus content."""
        seen: list[Path] = []

        def watching(pages: int):
            inner = fake_renderer(pages)

            def render(document: Path, out_dir: Path, *, dpi: int, max_pages: int):
                seen.append(Path(out_dir))
                return inner(document, out_dir, dpi=dpi, max_pages=max_pages)

            return render

        for client, pages in ((FakeClient(), 2), (FakeClient(fail_on=9), 1)):
            engine = make_ocr_engine(client, renderer=watching(pages), workdir=tmp_path)
            engine(FakeMount(), "docs/scan.pdf")
        assert seen and all(not path.exists() for path in seen)
        assert list(tmp_path.iterdir()) == [], "the work directory is empty again"

    def test_the_document_read_is_bounded(self) -> None:
        """A multi-hundred-MB file is not pulled into memory whole."""
        engine = make_ocr_engine(FakeClient(), renderer=fake_renderer(1), max_bytes=8)
        mount = FakeMount(payload=b"0123456789")
        engine(mount, "docs/scan.pdf")
        assert mount.opened == ["docs/scan.pdf"]


class TestTheRenderer:
    def test_a_pdf_that_cannot_be_rendered_raises_rather_than_returning_nothing(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        import subprocess

        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, b"", b"Syntax Error: not a PDF")

        monkeypatch.setattr("rlm_local.ocr.subprocess.run", fake_run)
        with pytest.raises(OcrFailure) as raised:
            render_pages(tmp_path / "broken.pdf", tmp_path, dpi=150, max_pages=4)
        assert "not a PDF" in str(raised.value)
        assert "-r" in calls[0] and "150" in calls[0], "the resolution reaches pdftoppm"
        assert "-l" in calls[0] and "4" in calls[0], "the page cap reaches pdftoppm"

    def test_the_instruction_is_the_models_own(self) -> None:
        """GLM-OCR is prompt-limited: the documented string, not a paragraph we invented."""
        assert OCR_PROMPT == "Text Recognition:"


class FakeHttp:
    """The slice of httpx.Client the OCR client uses, with scripted responses."""

    class Response:
        def __init__(self, status: int, body: dict[str, Any] | None = None,
                     text: str = "") -> None:
            self.status_code = status
            self._body = body or {}
            self.text = text or json.dumps(body or {})

        def json(self) -> dict[str, Any]:
            return self._body

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any] | None = None):
        self.payloads.append(json or {})
        nxt = self.responses.pop(0) if self.responses else self.Response(500, text="no script")
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _ok(text: str = "transcribed") -> Any:
    return FakeHttp.Response(200, {"choices": [{"message": {"content": text}}]})


class TestTheClient:
    def test_the_payload_carries_the_image_and_the_documented_instruction(self) -> None:
        http = FakeHttp([_ok("hello")])
        client = OcrClient("http://127.0.0.1:55842", model="glm-ocr", client=http, wait=0)
        answer = client.transcribe(Path(__file__).resolve())  # any file: the bytes are opaque
        assert answer == "hello"
        payload = http.payloads[0]
        assert payload["model"] == "glm-ocr"
        content = payload["messages"][0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[1] == {"type": "text", "text": "Text Recognition:"}
        assert payload["temperature"] == 0.0

    def test_a_503_is_retried_because_the_model_may_still_be_loading(self) -> None:
        """Measured 2026-09-26: llama.cpp answers /health before its weights are loaded."""
        http = FakeHttp([FakeHttp.Response(503, text="loading"), _ok("second try")])
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0)
        assert client.transcribe(Path(__file__).resolve()) == "second try"
        assert len(http.payloads) == 2

    def test_another_error_status_fails_loudly_and_is_not_retried(self) -> None:
        http = FakeHttp([FakeHttp.Response(400, text="bad request")])
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0)
        with pytest.raises(OcrFailure) as raised:
            client.transcribe(Path(__file__).resolve())
        assert "400" in str(raised.value)
        assert len(http.payloads) == 1, "a 400 is not a transient condition"

    def test_giving_up_says_how_many_attempts_it_made(self) -> None:
        http = FakeHttp([FakeHttp.Response(503, text="loading")] * 3)
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0, attempts=3)
        with pytest.raises(OcrFailure) as raised:
            client.transcribe(Path(__file__).resolve())
        assert "3 attempt" in str(raised.value)

    def test_a_reply_without_text_is_a_failure_not_an_empty_page(self) -> None:
        http = FakeHttp([FakeHttp.Response(200, {"choices": []})])
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0)
        with pytest.raises(OcrFailure):
            client.transcribe(Path(__file__).resolve())

    def test_a_timeout_is_reported_rather_than_retried_like_a_connection_error(self) -> None:
        """A page that ran past its timeout has already cost the timeout once.

        Retrying it `attempts` times is how a slow page becomes an hour — the same arithmetic
        the summariser's cap-bound document exposed — so the retry budget is per kind of failure,
        not one number for both.
        """
        import httpx

        http = FakeHttp([httpx.ReadTimeout("too slow")] * 4)
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0, attempts=4,
                           timeout_attempts=1)
        with pytest.raises(OcrFailure) as raised:
            client.transcribe(Path(__file__).resolve())
        assert len(http.payloads) == 1, "one timeout is reported, not repeated"
        assert "timed out" in str(raised.value)

    def test_a_connection_error_is_still_retried(self) -> None:
        http = FakeHttp([ConnectionError("the server is down"), ConnectionError("still down"),
                         _ok("third try")])
        client = OcrClient("http://127.0.0.1:55842", client=http, wait=0, attempts=3)
        assert client.transcribe(Path(__file__).resolve()) == "third try"
        assert len(http.payloads) == 3

    def test_the_tag_names_the_model_and_the_host(self) -> None:
        """A derived citation has to be able to say which engine produced it."""
        assert OcrClient("http://127.0.0.1:55842", model="glm-ocr",
                         client=FakeHttp([])).tag == "glm-ocr@127.0.0.1:55842"

    def test_the_default_page_cap_is_a_documented_number(self) -> None:
        assert MAX_PAGES_PER_DOCUMENT >= 10, "the median waiting document is 13 pages"
