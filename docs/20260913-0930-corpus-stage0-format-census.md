# Stage 0 format census: the corpus is mostly media and machine state

**Created 2026-09-13 09:30.** The first measurement of the content-scope decision
(roadmap RO1/RO3, the `I8` item in the scope conversation). It reads **only the
path index** — no filesystem access, no file contents — and reports counts and
bytes by format class. Aggregates only, per `AGENTS.md` §1.9; the raw output stays
on `lunacode` at `~/rlm-derived/stage0-histogram.txt` (0600).

Reproduce with `~/rlm-derived/corpus_class_census.py --corpus-index
~/rlm-derived/corpus.sqlite` (index-only; safe to run anywhere the index is).

---

## 1. Byte totals by class

4,281,585 files, **1,010.3 GiB**.

| Class | Files | % files | Bytes | % bytes |
|---|---:|---:|---:|---:|
| video | 109,289 | 2.6% | 268.8 GiB | 26.6% |
| config_data | 157,286 | 3.7% | 144.9 GiB | 14.3% |
| other (unknown extensions) | 1,074,728 | 25.1% | 138.7 GiB | 13.7% |
| disc_image | 249 | 0.0% | 137.3 GiB | 13.6% |
| archive | 80,978 | 1.9% | 98.4 GiB | 9.7% |
| executable | 280,975 | 6.6% | 62.4 GiB | 6.2% |
| ambiguous (`.bin`/`.dat`/`.img`…) | 13,028 | 0.3% | 54.6 GiB | 5.4% |
| image | 450,646 | 10.5% | 51.4 GiB | 5.1% |
| audio | 16,891 | 0.4% | 18.3 GiB | 1.8% |
| code | 1,642,536 | 38.4% | 17.8 GiB | 1.8% |
| document | 21,751 | 0.5% | 5.8 GiB | 0.6% |
| markup_web | 302,456 | 7.1% | 5.3 GiB | 0.5% |
| plain_text | 121,251 | 2.8% | 3.3 GiB | 0.3% |
| font | 7,809 | 0.2% | 3.1 GiB | 0.3% |
| mail_store | 1,712 | 0.0% | 9.8 MiB | 0.0% |

**Correction to the first reading of this table.** My script's own `T_plain`
(171.3 GiB) counted `config_data` as text, and `config_data` is **99% `.mdb`
databases**: 793 files, 137.7 GiB, ~174 MB each. Those are not text and cannot be
FTS-indexed without per-database extraction. The honest number for *text as it
already is on disk* is:

| | Bytes | Files |
|---|---:|---:|
| plain_text + markup_web + code + (config_data − `.mdb`) | **~33.6 GiB** | ~2.2M |
| …of which vendored/dependency code (`.js` 670k files, `.jar` 58.5k, `.pyc` 105k…) | unknown, likely 15–25 GiB | ~1.4M |
| **first-party text (estimate)** | **~10–18 GiB** | — |

## 2. Size distribution

| Bucket | Files | Bytes | % bytes |
|---|---:|---:|---:|
| ≥ 100 MiB | 992 | 638.5 GiB | 63.2% |
| 10–100 MiB | 6,177 | 161.5 GiB | 16.0% |
| 1–10 MiB | 44,817 | 128.9 GiB | 12.8% |
| 1 KiB – 1 MiB | 2,743,567 | 80.9 GiB | 8.0% |
| < 1 KiB | 1,486,032 | 538.5 MiB | 0.1% |

992 files hold 63% of the bytes; 4.23M files hold the remaining 37%. Any
per-file pass is dominated by *count* (4.28M opens), not by volume.

## 3. The largest extensions by bytes

| | Files | Bytes |
|---|---:|---:|
| `.mp4` | 6,055 | 201.4 GiB |
| `.mdb` | 793 | 137.7 GiB |
| `.qcow2` | 40 | 79.3 GiB |
| *(no extension)* | 486,287 | 52.5 GiB |
| `.img` | 60 | 51.1 GiB |
| `.jpg` | 99,605 | 38.5 GiB |
| `.zip` | 2,576 | 35.6 GiB |
| `.vmdk` | 11 | 32.3 GiB |
| `.3gp` | 399 | 29.2 GiB |
| `.jar` | 58,565 | 26.7 GiB |
| `.avi` | 200 | 22.4 GiB |
| `.iso` | 30 | 18.8 GiB |
| `.exe` | 6,727 | 15.9 GiB |
| `.pack` (git) | 615 | 15.4 GiB |
| `.mp3` | 2,799 | 13.0 GiB |
| `.pdf` | 4,318 | 3.7 GiB |

By count, `.js` (670,095), extensionless (486,287), `.h` (347,639), `.png`
(249,587), `.html` (182,753), `.py` (158,686), `.c` (129,156) lead.

---

## 4. What this changes

1. **The disk question mostly dissolves.** The content index would cover ~34 GiB
   of text, and FTS5 costs ~0.15–0.6× of text bytes: **~5–20 GiB**, inside the
   99 GB available. The `I8` decision is therefore *not* disk-bound. A full text
   index over everything text-like is affordable (Option A).
2. **The corpus is not a document collection.** Human documents — plain text,
   markup, office documents, PDFs — total **~14.4 GiB**; media and machine state
   total ~950 GiB. The needles live in a *small* text core plus inside media.
3. **The blind spots are large and cheap to close.** 1,074,728 "other" files
   (138.7 GiB) and 486,287 extensionless files (52.5 GiB) are ~1.56M files /
   ~191 GiB whose nature is unknown: extension alone cannot classify them. A
   sniffing pass (first 4–8 KB per file) closes it, and it is the same pass that
   yields content hashes for dedup.
4. **Two format families dominate the "container" bytes and neither is prose:**
   `.jar` (26.7 GiB, dependency code) and `.pack` (15.4 GiB, git history). Their
   content is code history, not knowledge.
5. **Disc images are settled by the owner's earlier call** — they are assumed to
   be installation media, not knowledge, so their 137.3 GiB leaves the scope.
6. **Transcripts are negligible to store and expensive to compute.** Speech is
   roughly 60 KB per hour of audio as text; the cost is the ASR pass, not disk.

## 5. Decisions this measurement forces

| # | Decision | Input it needs |
|---|---|---|
| D1 | Does the "days or months of indexing are acceptable" budget cover **generative** work (vision OCR, image captioning), or only non-generative passes (hash, sniff, extract, index, ASR)? | the arithmetic below |
| D2 | `.mdb` databases: 793 files, **137.7 GiB**, the second-largest byte class. In scope (per-database extraction) or out? They plausibly hold personal needles. | owner |
| D3 | `.pack` git packfiles (615 files, 15.4 GiB): extract repository history, or treat as machine state? | owner |
| D4 | The 1.56M unknown files (191 GiB): sniff them, or leave them name-searchable only? | Stage 1 |
| D5 | Media: ASR every audio+video soundtrack, or query-driven? | duration probe (Stage 1) |
| D6 | 450,646 images: OCR (Tesseract first), vision-OCR on demand, or name-only? | sample of image types |
