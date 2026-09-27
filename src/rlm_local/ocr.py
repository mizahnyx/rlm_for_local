"""OCR over the corpus, through the harness's read-only mount (Gate 2, sub-question B).

The owner's call (2026-09-26): the PDFs with no text layer may be OCR'd, with a *recent ML*
OCR model rather than a classical engine, and the cost in time and system load is accepted. This
module is the harness side of it: it reads a document through the mount, renders its pages to a
temporary directory **outside** the corpus, hands each page to an OCR model served by the box's
own llama.cpp, and returns one text for the document.

Four properties are deliberate, and each is a test:

* **Nothing is written into the corpus, and nothing is left behind outside it.** The document is
  read with `mount.open_readonly`; the page images go to a temporary directory that is removed
  when the call ends — including when the call fails, because a mining window that dies should
  not leave tens of PNGs on a root filesystem that is 96% full.
* **The instruction is the model's own.** GLM-OCR is *prompt-limited*: the documented
  document-parsing instruction is the string in `templates.OCR_PROMPT`, and a longer custom
  instruction is not what this checkpoint was trained on. It lives in `templates.py` because it
  is a model-facing string.
* **A page that fails is counted, never skipped silently.** A document whose third of ten pages
  failed to transcribe must not come back looking complete: the engine returns what it got and
  says how many pages failed, and a document with *no* text is an empty result rather than a lie.
* **The work is bounded per document.** `max_pages` caps how many pages one document may spend,
  because a 400-page scan would otherwise hold a window for a day; the count that was skipped is
  in the metadata rather than silently dropped.

The client is injected, as the summariser's engine is, so this module imports no HTTP library at
import time and no model is needed to test the pipeline.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from rlm_local.templates import OCR_PROMPT

#: Rendered resolution. Measured 2026-09-26 on lunacode: the page's *pixels* drive the cost,
#: because GLM-OCR's image encoder is what is slow on CPU — not the decoder. 150 DPI is the
#: vendor's documented working resolution and the resolution the cost curve was measured at.
RENDER_DPI = 150

#: How many pages one document may spend in a single call. The waiting population's median is
#: 13 pages and the mean 14.4 (measured on a sample of 20), so this covers the typical document
#: and bounds the pathological one.
MAX_PAGES_PER_DOCUMENT = 24

#: A document larger than this is not read into memory at all. Bounded because the renderer and
#: the OCR client both take a file, and the corpus contains multi-hundred-MB files.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

#: How long one page may take before the call is abandoned. The measured cost is ~10 minutes per
#: page at 150 DPI on this CPU, so this is generous by design: it exists to stop a hang, not to
#: enforce a budget.
PAGE_TIMEOUT_SECONDS = 1800.0


class OcrFailure(RuntimeError):
    """The page could not be transcribed: a transport failure, a refusal, or a torn reply."""


class OcrClientLike(Protocol):
    """What the engine needs: one page image in, one text out."""

    def transcribe(self, image: Path) -> str: ...


def render_pages(
    document: Path, out_dir: Path, *, dpi: int = RENDER_DPI,
    max_pages: int = MAX_PAGES_PER_DOCUMENT,
) -> list[Path]:
    """Render the first `max_pages` pages of `document` to PNGs in `out_dir`.

    `pdftoppm` (poppler) does the work, the same engine the extraction path already depends on,
    and it is what writes the pages: a page that cannot be rendered is missing from the list
    rather than represented by an empty file.
    """
    stem = out_dir / "page"
    command = [
        "pdftoppm", "-png", "-r", str(int(dpi)),
        "-f", "1", "-l", str(int(max_pages)), str(document), str(stem),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrFailure(f"pdftoppm could not run: {e}") from e
    if proc.returncode != 0 and not list(out_dir.glob("page-*.png")):
        raise OcrFailure(
            f"pdftoppm failed with {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    return sorted(out_dir.glob("page-*.png"))


class OcrClient:
    """An OCR model behind an OpenAI-compatible endpoint's chat route.

    GLM-OCR on llama.cpp is served exactly like any other chat model, with one image part and
    one instruction — so this is the *chat* shape rather than the typed-decision shape, and it
    deliberately does not reuse `HTTPModelBackend` (which sends text-only messages and carries
    none of the retry policy a one-page call needs).

    **503 is not a failure here.** Measured 2026-09-26: a llama.cpp server answers `/health`
    before its weights are loaded, and returns 503 to a request that arrives in between. The
    first probe read that as a failure and reported an error for a model that was merely still
    loading, so this client waits and retries — bounded, and only for 503.
    """

    def __init__(
        self, endpoint: str = "http://127.0.0.1:8080", *,
        model: str = "glm-ocr", prompt: str = OCR_PROMPT,
        timeout: float = PAGE_TIMEOUT_SECONDS, attempts: int = 5, wait: float = 10.0,
        client: Any = None,
    ) -> None:
        import httpx

        self._url = endpoint.rstrip("/") + "/v1/chat/completions"
        self._model = model
        self._prompt = prompt
        self._attempts = max(1, int(attempts))
        self._wait = wait
        # Injectable so the retry policy and the payload can be tested without a server, and so
        # a caller can share one connection pool across a window.
        self._client = client if client is not None else httpx.Client(
            timeout=httpx.Timeout(timeout))

    @property
    def tag(self) -> str:
        """The engine identity a cache entry and a derived citation carry."""
        return f"{self._model}@{self._url.split('/')[2]}"

    def transcribe(self, image: Path) -> str:
        """One page image as text, or `OcrFailure` — never an empty string for a failure."""
        data = base64.b64encode(Path(image).read_bytes()).decode("ascii")
        payload = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," + data}},
                    {"type": "text", "text": self._prompt},
                ],
            }],
            "max_tokens": 4096,
            "temperature": 0.0,
        }
        last: str = "no attempt made"
        for attempt in range(self._attempts):
            try:
                response = self._client.post(self._url, json=payload)
            except Exception as e:  # transport: a dead server, a torn socket
                last = f"{type(e).__name__}: {e}"
                time.sleep(self._wait)
                continue
            if response.status_code == 503:
                last = "503 (the model is still loading)"
                time.sleep(self._wait)
                continue
            if response.status_code != 200:
                raise OcrFailure(f"OCR endpoint answered {response.status_code}: "
                                 f"{response.text[:200]}")
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as e:
                raise OcrFailure(f"OCR reply was not a chat completion ({e})") from e
            if not isinstance(content, str):
                raise OcrFailure("OCR reply carried no text")
            return content
        raise OcrFailure(f"OCR gave up after {self._attempts} attempt(s): {last}")

    def close(self) -> None:
        self._client.close()


def make_ocr_engine(
    client: OcrClientLike,
    *,
    renderer: Callable[..., list[Path]] | None = None,
    dpi: int = RENDER_DPI,
    max_pages: int = MAX_PAGES_PER_DOCUMENT,
    workdir: Path | None = None,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> Callable[[Any, str], tuple[str, dict[str, Any]]]:
    """Build the kernel's OCR engine: `(mount, rel) -> (text, metadata)`.

    The shape mirrors the extraction engines the queue already injects, so `task_ocr_page` is a
    thin caller. The temporary directory is created per document and removed on every path out
    of the function.
    """
    render = renderer or render_pages

    def engine(mount: Any, rel: str) -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="rlm-ocr-", dir=workdir) as tmp:
            work = Path(tmp)
            document = work / "document.pdf"
            with mount.open_readonly(rel) as handle:
                data = handle.read(max_bytes)
            document.write_bytes(data)
            images = render(document, work, dpi=dpi, max_pages=max_pages)
            if not images:
                return "", {"engine": getattr(client, "tag", ""), "dpi": dpi,
                            "pages": 0, "pages_failed": 0,
                            "note": "no page could be rendered"}
            parts: list[str] = []
            failed = 0
            for image in images:
                try:
                    parts.append(client.transcribe(image))
                except Exception:
                    failed += 1
            text = "\n\n".join(part.strip() for part in parts if part.strip())
            meta: dict[str, Any] = {
                "engine": getattr(client, "tag", ""),
                "dpi": int(dpi),
                "pages": len(images),
                "pages_failed": failed,
            }
            return text, meta

    engine.engine_tag = getattr(client, "tag", "")  # type: ignore[attr-defined]
    return engine
