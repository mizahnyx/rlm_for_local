# Gate 2: what counts as in scope for text — context for deciding

**Date:** 2026-09-24 (series ordering; the machine clock runs behind it). **Status:** decision
context, nothing decided. **Every figure is recording-derived** from this repository's records
(mainly the Stage 0/1 census of 2026-09-13), not re-measured — the box is away. Sources are named
per figure.

## 1. The decision, in the roadmap's own words

*"What counts as in scope for text: archives expanded or indexed as blobs? PDFs OCR'd or
metadata-only? source code summarised or only searchable?"* — `docs/20260912-1155-roadmap.md`, in
its "Not decided yet" list, with the note that RO1's census is the input to the call. This document
is that input.

## 2. What the corpus is, by content

Stage 1 classified **4 281 585 files (100%)**, reading 41.7 GiB of heads, in ~11.5 h, and found
**13 unreadable (0.0003%)** (`docs/20260913-2110-corpus-stage1-content-classification.md`):

| kind | files | % files | bytes | % bytes |
|---|---:|---:|---:|---:|
| binary | 724 571 | 16.9% | 758.2 GiB | 75.1% |
| archive | 99 993 | 2.3% | 104.1 GiB | 10.3% |
| media | 489 066 | 11.4% | 96.7 GiB | 9.6% |
| **text** | **2 882 822** | **67.3%** | **42.1 GiB** | **4.2%** |
| document | 19 453 | 0.5% | 6.8 GiB | 0.7% |
| database | 1 919 | 0.0% | 2.2 GiB | 0.2% |
| empty | 63 748 | 1.5% | 0 B | 0.0% |
| unreadable | 13 | 0.0% | 1.5 MiB | 0.0% |

Two definitional notes, so the numbers are not double-counted: the census *entries* total
4 972 609 while the classified *files* total 4 281 585, and I am **not resolving that difference
here** — they are two records counting different things. Likewise the content-sniffed archives
(99 993) and the containers the mining queue knows (84 361, `docs/20260921-1719`) are not the same
set: one is what the bytes look like, the other what the queue claimed by extension and sniff.

## 3. Text, after dedup — the fact that should drive the decision

| | files | bytes |
|---|---:|---:|
| text files (all copies) | 2 882 822 | 42.1 GiB |
| **distinct text (head+size hash)** | **1 382 200** | **28.6 GiB** |
| dedup factor within text | 2.09× | |
| …of which in vendored/build/cache paths | **1 474 367** | 14.0 GiB |
| …everything else | 1 408 455 | 28.1 GiB |

So: **half the text corpus by file count is vendored**, and the file count overstates distinct
content by about 2×. That is why search already counts vendored matches and hides them behind a
bounded cap (`docs/20260922-2025-the-search-counts-every-vendored-match.md`), and why the owner's own
framing — *"a vendored `.jar` should probably only be described by a single line"* — is the natural
policy.

## 4. Sub-question A — archives expanded, or indexed as blobs?

**What happens today** (`docs/operator-guide.md`, `docs/20260922-1033-…`): containers are **listed**
— members recorded for search, listing complete as of 2026-09-22 — and their *document-type* members
are **extracted** (pdftotext; zip+XML for OOXML/ODF/EPUB; libarchive for `.rar`, verified live). A
container member is readable through the container's extraction cache (RO14). Everything else inside
an archive stays unexpanded.

**Costs that are measured:** archive listing and indexing ≈ **33 items/s**; text extraction ≈ **5.3
items/s**. At ~100 K content-archives, listing the lot is hours, not days.

**What is not measured:** the cost and volume of *expanding* every archive. Nothing has ever
extracted all members of all archives, so the text volume that would appear — and the generation cost
that would follow if any of it were described — is unknown. This is the honest gap behind the
question.

## 5. Sub-question B — PDFs OCR'd, or metadata-only?

**Scale:** `.pdf` is **4 318 files, 3.7 GiB** (`docs/20260913-0930-corpus-stage0-format-census.md`);
documents by content number 19 453 files / 6.8 GiB, which includes office formats as well.

**What happens today:** pdftotext, and **an empty text layer is recorded as `needs_ocr`, never as a
failure**. No OCR handler exists — `ocr_page` is a task name with no implementation.

**The shape of the question, then:** this is not a corpus-scale decision. It is a bounded question
about a few thousand files, and the two unknowns are (i) how many of them actually lack a text layer
— a count that lives in derived state on the box — and (ii) OCR quality and speed on this hardware,
which has **never been measured here** and for which no engine is wired.

## 6. Sub-question C — source code summarised, or only searchable?

**Where code sits:** code is the **largest text class** — 1 642 536 files by extension, of which
**1 623 717 sniffed as text (17.6 GiB)** — and it is also the **largest single class in the value
set**: code 44 of 143 documents (`docs/20260923-1700-…`).

**What happens today:** code is *searchable* — it is text, so it is indexed in full. It is not
described.

**The arithmetic, from the measured rates** (5.0 min for a 6.9 KB document, 29.6 min for a 20.4 KB
one, `docs/20260923-2100-…`): describing **all** code is out of reach by orders of magnitude
(1.6 M documents × minutes each). Describing the **value set's** code is **44 documents ≈ 4–22 h**,
which is affordable — and is exactly the same decision as Gate 3, not a separate one.

## 7. What each answer costs

| answer | what changes | recorded cost | what stays unknown |
|---|---|---|---|
| archives stay listed, not expanded | nothing (today's behaviour) | 0 h | what is inside them, and whether it matters |
| expand archives | text volume and index size grow by an unmeasured factor | listing ≈33 items/s; expansion **unmeasured** | whether the content justifies it |
| PDFs metadata-only | nothing (today) | 0 | how many need OCR; count lives on the box |
| PDFs OCR'd | a few thousand documents become readable | **unmeasured**; no engine wired | OCR quality/speed here |
| code searchable only | nothing (today) | 0 | whether code descriptions would help |
| value-set code described | 44 descriptions | **4–22 h** | usefulness (Gate 3's gap) |

## 8. What is already settled

Search **counts** vendored matches and hides them behind a bounded cap rather than pretending they
are absent; extraction claims **document** extensions and listing takes the rest (the claim is by
name, not by sniffed kind, because a `.docx` is a zip); a container member is served **through the
container's extraction cache** with the substitution named; and `needs_ocr` is a **state**, not a
failure. Corpus-derived text never leaves the machine, and the corpus is read only through the
harness (`AGENTS.md` §1.9).

## 9. What this document cannot tell you, and the cheapest thing that would

It cannot tell you whether anything inside the archives matters, how many PDFs need OCR, or what OCR
would cost here — none of the three has ever been measured. Two bounded measurements would close
most of it, and both need the box: **sample N archives, expand them, and report the volume and mix
of what came out** (hours, no model), and **attempt OCR on a sample of the PDFs recorded `needs_ocr`**
(needs an engine installed and a quality judgement you would have to make).
