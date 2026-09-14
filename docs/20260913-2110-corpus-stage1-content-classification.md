# Stage 1: what the corpus is made of, by content (RO1/RO3)

**Created 2026-09-13 21:10.** The pass that read the head of every file and
recorded what it found (`rlm corpus classify`, commit `6c583bf`), its results, and
what those results settle. Index-only aggregates; nothing here names a file, and
the raw output stays on `lunacode` (`~/rlm-derived/classify-full.log`,
`~/rlm-derived/stage1-crosstab.txt`, both 0600).

---

## 1. The pass

| | |
|---|---|
| Files classified | 4,281,585 of 4,281,585 (100%) |
| Bytes read off the corpus | **41.7 GiB** (heads only — bounded by `max(sniff, hash)`) |
| Unreadable | **13** (0.0003%) |
| Wall time | ~11.5 h at ~100 files/s, `nice` 10 / `ionice` best-effort 7 |
| Dedup (head+size hash) | 4,281,572 files → 2,237,952 distinct, **1.91×**; 177.1 GiB of file bytes are copies |

Reading 41.7 GiB to describe 1,010 GiB is the whole point of the bounded head: the
pass cost 4% of the corpus's size and knows what every file is.

## 2. What the corpus is, by content

| kind | files | % files | bytes | % bytes |
|---|---:|---:|---:|---:|
| binary | 724,571 | 16.9% | 758.2 GiB | 75.1% |
| archive | 99,993 | 2.3% | 104.1 GiB | 10.3% |
| media | 489,066 | 11.4% | 96.7 GiB | 9.6% |
| **text** | **2,882,822** | **67.3%** | **42.1 GiB** | **4.2%** |
| document | 19,453 | 0.5% | 6.8 GiB | 0.7% |
| database | 1,919 | 0.0% | 2.2 GiB | 0.2% |
| empty | 63,748 | 1.5% | 0 B | 0.0% |
| unreadable | 13 | 0.0% | 1.5 MiB | 0.0% |

Text: UTF-8 2,834,521 files, no encoding 1,400,329, **cp1252 46,107**, latin-1 628.
The single-byte encodings matter (1.6% of text) and are readable — which is what a
Spanish-language corpus from the CD era should look like.

## 3. Text, after dedup

| | Files | Bytes |
|---|---:|---:|
| Text files (all copies) | 2,882,822 | 42.1 GiB |
| **Distinct text (by head+size hash)** | **1,382,200** | **28.6 GiB** |
| Dedup factor within text | 2.09× | |
| …of which in vendored/build/cache paths | 1,474,367 files | 14.0 GiB |
| …everything else | 1,408,455 files | 28.1 GiB |

**This settles the content-index scope.** A FTS5 index costs ~0.15–0.6× of text
bytes: **~4–17 GiB for all of it**, inside the 99 GB available. Nothing needs to be
subselected for disk reasons, so the vendored-content counter (I4) can be real
rather than names-only.

The caveat worth stating: the "first-party" figure is a *path heuristic*
(`node_modules/`, `site-packages/`, `/build/`, `third_party/`, …). Third-party
source trees that do not sit in such a directory are counted as first-party. The
largest text extensions are `.js` (667,972 files, 7.3 GiB), `.h` (336,805, 2.7
GiB), `.c` (129,083, 2.2 GiB), `.py` (154,532, 1.8 GiB), `.java` (110,238, 0.9
GiB) — code and configuration, not prose. Human documents are `.html` (182,692,
2.8 GiB), `.txt` (41,546, 1.2 GiB), `.log` (2,824, 1.4 GiB), plus 19,453
documents (6.8 GiB, including 4,318 PDFs). **The prose core is a few GiB.**

## 4. Cross-tabulation against the extension classes (Stage 0)

| Extension class | Files | Bytes | What the sniff found |
|---|---:|---:|---|
| video | 109,289 | 268.8 GiB | **text 101,822** (356 MiB), binary 6,867 (235.6 GiB), media 488 (32.8 GiB) |
| config_data | 157,286 | 144.9 GiB | text 153,925 (5.7 GiB), **binary 2,593 (137.8 GiB)** |
| other (unknown ext) | 1,074,728 | 138.7 GiB | **text 566,886 (9.6 GiB)**, binary 404,562 (115.3 GiB), empty 42,783 |
| disc_image | 249 | 137.3 GiB | binary 100 (137.3 GiB) |
| archive | 80,978 | 98.4 GiB | archive 78,251 (92.8 GiB) |
| executable | 280,975 | 62.4 GiB | binary 277,055 (60.8 GiB) |
| ambiguous | 13,028 | 54.6 GiB | binary 9,875 (54.2 GiB) |
| image | 450,646 | 51.4 GiB | media 441,168 (48.6 GiB) |
| audio | 16,891 | 18.3 GiB | media 8,673 (13.1 GiB), text 6,441 (15.3 MiB) |
| code | 1,642,536 | 17.8 GiB | text 1,623,717 (17.6 GiB) |
| markup_web | 302,456 | 5.3 GiB | text 301,455 (5.2 GiB) |
| plain_text | 121,251 | 3.3 GiB | text 117,034 (3.0 GiB) |

Three findings, one of which is a correction to Stage 0:

1. **`.mdb` is not a database.** 793 files, 137.7 GiB: **792 sniffed as binary**
   (no Jet/ACE magic), one as a database. Stage 0 called this class
   `config_data`/databases and it is the second-largest byte class — so the
   "extract personal data from old Access databases" worry is unsupported. They
   are opaque blobs of unknown format, and no extraction path is justified for
   0.02% of files.
2. **`.ts` is TypeScript, not MPEG transport stream.** 101,822 files with a
   video extension sniffed as *text* (356 MiB) — the extension classifier had
   them as video, and content corrected it. This is the clearest illustration of
   why Stage 1 exists.
3. **The "unknown extension" blind spot is closed**: of the 1,074,728 files with
   unknown extensions, **566,886 are text** (9.6 GiB), 404,562 binary, 42,783
   empty. Nothing needs to be inferred from names any more.

**A gap this exposes:** 6,867 files with video extensions are `binary`, not
`media` — 235.6 GiB, more than twice the 96.7 GiB the sniff called media. The
magic table does not know every container (RealMedia, some MPEG-PS variants, WTV,
game video). So the true video volume is the extension class's 268.8 GiB, and
**any ASR price must come from probing durations, not from the sniff's media
class.**

## 5. What this settles, and what it does not

**Settled.** The content index covers *all* text (28.6 GiB distinct, ~4–17 GiB of
FTS5); no subsetting; vendored content is indexed and rank-suppressed with a real
counter; `.mdb` needs no extraction; disc images stay out; the 1.56M unknown files
are classified.

**Not settled.** ASR's price (needs durations), whether images get OCR'd (441,168
media images, 48.6 GiB — Tesseract is days and unattended, vision OCR is
query-driven), and the retrieval-quality question that decides embeddings.

**Not verified here.** That the pass left the corpus untouched: the digest
comparison is running (index digest before, fresh walk after).
