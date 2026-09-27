# OCR costs 187 seconds a page, and the branch is wired

**Date:** 2026-09-26/27. **Status:** Gate 2 sub-question B answered and implemented. The owner's call
was that the scans get OCR'd with a **recent ML OCR model**, not a classical engine, and that the
cost in time and system load is accepted — item 1 of `docs/20260927-0300-what-is-waiting-on-the-owner.md`.
This is the measurement of that cost, on this hardware, with the engine he named.

## What ran

`fastino`-independent and locally served: **GLM-OCR** ([model card](https://huggingface.co/zai-org/GLM-OCR),
MIT, 0.9B — a CogViT encoder plus a GLM-0.5B decoder) as `ggml-org/GLM-OCR-GGUF`
(`GLM-OCR-Q8_0.gguf` 950 MB + `mmproj-GLM-OCR-Q8_0.gguf` 484 MB), served by **lunacode's own
llama.cpp** (build 11009, `mtmd`) and driven through its OpenAI-compatible chat route with the
checkpoint's documented `Text Recognition:` instruction. Pages are rendered with `pdftoppm`, which
the extraction path already depends on; the document is read **through the harness mount**, the
copy lives in `/tmp` and is removed on every path out.

It loads in **4 s** and holds **1.7–2.5 GiB** of RSS. Everything below was measured with the
resident 4B text model in memory but idle, which is the state the box is normally in between
windows; the OCR window takes all four cores while it runs.

## What a page costs

One page of a real scanned document from the waiting set, two documents, three configurations:

| render | image tokens | prompt | decode | **wall** |
|---|---:|---:|---:|---:|
| 150 DPI, off the shelf | 2 725 | 501 s | 99 s | **600 s** |
| 150 DPI, off the shelf (second document) | 2 725 | 500 s | 124 s | **624 s** |
| 96 DPI, off the shelf | 1 113 | 137 s | 79 s | **215 s** |
| 96 DPI, off the shelf (second document) | 1 113 | 137 s | 94 s | **231 s** |
| 150 DPI, `--image-max-tokens 1024` | 1 019 | 118 s | 68 s | **187 s** |
| 96 DPI, `--image-max-tokens 1024` | 1 019 | 118 s | 69 s | **187 s** |
| capped, second document at 96 / 150 DPI | 1 019 | 119 / 120 s | 75 / 79 s | **194 / 199 s** |

Three things fall out of that table (eleven page transcriptions across two documents, plus the
two pages the end-to-end window did):

1. **The image encoder is the cost, not the decoder.** At 150 DPI, 501 of 600 seconds is prompt
   processing; the decoder is 100. A smaller quant would not help — fewer *pixels* would.
2. **The image tokens scale with the square of the resolution** (1 113 × (150/96)² = 2 720 ≈ the
   2 725 measured), and a capped prompt processes at 8.6 tok/s against 5.4 uncapped, so the
   saving is better than linear.
3. **Capping the image tokens at 1 024 makes a 150-DPI page cost what a 96-DPI page costs** — 187 s
   against 600 s — while the model still receives the **150-DPI rendering**, downsampled by the
   encoder rather than by the renderer. On the test page the transcription came back byte-identical
   capped and uncapped (3 825 characters). That is the configuration `scripts/run_ocr_window.sh`
   now uses by default.

## What the waiting population is

**517 documents** carry a `needs_ocr` row (of the 4 318 PDFs the census recorded — 12%). A sample
of 20, read through the mount and probed with `pdfinfo`: **median 13 pages, mean 14.4, min 3,
max 28**, median 501 KB.

So the population is roughly **6 700–7 400 pages**, and at the measured best configuration:

| configuration | per page | the 517 documents |
|---|---:|---:|
| 150 DPI, capped at 1 024 image tokens | 187 s | **~350–385 h (15–16 days)** |
| 96 DPI, capped | 187 s | the same |
| 150 DPI, off the shelf | 600 s | ~1 100–1 230 h (46–51 days) |

**Seven of the twenty sampled documents could not be probed by `pdfinfo` at all** — the reason is
not established (encrypted, damaged, or not a PDF under a `.pdf` name), and those documents are
in the 517, so part of the population may not be OCR-able by rendering at all.

## What the transcription is worth

Accuracy was measured on **a page whose text we wrote ourselves** — the only way to score it
without putting corpus text in a transcript: a hand-built PDF (no converter, no library) with ten
known lines, rendered at 96 DPI and transcribed.

- **Word accuracy 0.840** (50 known words; 42 words matched in order).
- Numbers: 7 of 8 recovered — `3000`, `0.02`, `0.03`, `0.05`, `13`, `7`, `3` — and the date
  **1992 missed**.
- **Two whole lines were dropped**: the title and the revision line.

That is a *clean, machine-generated* page, which is the easiest case this pipeline will see; the
corpus holds scans. And it is the **model alone**: the vendor's own SDK adds PP-DocLayoutV3 layout
analysis, which is the two-stage pipeline their benchmark numbers come from. So 0.84 here is not a
contradiction of the vendor's SOTA claim, it is a different configuration — and it is the
configuration this harness runs, because the SDK is a separate dependency the project has not
adopted.

## What was built

The branch, so the campaign can start whenever it is scheduled:

- `task_ocr_page` in the kernel: an **injected** engine (`ctx.engines["ocr"]`), a skip with a
  reason when there is none (`no_ocr_engine`), the transcription stored under the **cache origin**
  and indexed as **derived** text with `cache_task=ocr_page` and the engine tag, the engine tag
  **in the cache key** (a different model or resolution re-transcribes rather than reusing
  another configuration's words), a cache hit that still repairs the index, and failed pages
  counted in the entry's metadata rather than dropped.
- `run_queue` turns the extraction pass's `needs_ocr` outcome into a queued `ocr_page` row — the
  signal the extraction path has been promising since it was written.
- `OCR_PAGE` has a handler and is **not** in `IMPLEMENTED_TASKS`: no ordinary window can start a
  model by accident.
- `src/rlm_local/ocr.py` (renderer, client, engine) with the retry policy the measurements taught:
  a 503 while the model loads is retried, a **timeout is not** — retrying a slow page is how a
  ten-minute page becomes an hour.
- `mine run --ocr-endpoint/--ocr-model/--ocr-dpi/--ocr-max-pages/--ocr-timeout`, and
  `scripts/run_ocr_window.sh`, which owns the server's lifetime, refuses to start when another
  model is resident (RSS-based: the router is always running, so counting processes was the wrong
  test), and caps the image tokens by default.

## Verified end to end on the real corpus

`scripts/run_ocr_window.sh --for 20m --dpi 96 --max-pages 2 --max-items 1` against the live index:

```
glm-ocr serving on 127.0.0.1:55845 (rss 1.71 GiB) image_max_tokens=1024
mining for 20m, at most 1 items
ocr engine glm-ocr@127.0.0.1:55845 (dpi 96, max pages 2)
  1 items (done 1, skipped 0, failed 0, cache hits 0)
```

and the document it transcribed now has **five chunks in the text index**, every one of them
`origin='cache'`, `derived=1`, `cache_task='ocr_page'`, `engine='glm-ocr@127.0.0.1:55845'` — the
transcription is findable, and it is labelled as a transcription. One `ocr_page` cache entry.

Two operational notes from that run, both worth knowing before scheduling a campaign:

- **One queue item is one *document*.** `--max-items 1` does not bound a window's length: a
  13-page document is thirteen pages of inference, and the budget is checked *between* items, so a
  24-page scan is a 75-minute window that overruns its `--for`. `--max-pages` is the lever that
  bounds it, and the window script passes it through.
- **`text_chunks` has exactly one index, on `source`.** A verification query by `source_hash`,
  `cache_task` or `origin` scans 29M rows — measured, ~100 s each — which is the same trap RO14
  and RO21 recorded for `display`, walked into here while *verifying* this branch. Ask by `source`
  (0.13 s) or through the cache.

## What this does not establish

- **Quality at scale.** One synthetic page, scored by word match. Corpus scans may be worse, and
  no corpus page's transcription was ever read by anyone — it is corpus-derived text and stays on
  the machine.
- **Whether 1 024 image tokens loses content on dense pages.** One page came back identical capped
  and uncapped; a dense table or a small-print page is untested.
- **Whether the 7 unprobeable documents can be OCR'd at all.**
- **Whether OCR'd text helps retrieval.** That is the same usefulness gap the descriptions have
  (`docs/20260927-0000-what-the-descriptions-actually-reach.md`): the words become findable, and
  whether an answer is better for it is a separate experiment.
- **Anything about running the OCR window while the text model is generating.** Every measurement
  had the 4B resident but idle; concurrent generation is exactly the contention the owner's
  one-model rule exists to avoid.

## What it changes in Gate 2's answer

Sub-question B — *PDFs OCR'd, or metadata-only?* — is now **OCR, and it is a campaign, not a
step**. The measured shape is: **~15 days of continuous machine time** for the 517 waiting
documents at the best configuration, run as windows with the box to itself; 46–51 days if the
image tokens are left uncapped at 150 DPI. Two policy levers are now in the tooling rather than in
a document: `--image-max-tokens` (the 3× ) and `--ocr-max-pages` (the sample's longest document is
28 pages, and a cap is what keeps one document from holding a window).
