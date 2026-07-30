# The 100K Load Gate: Runbook and Organic-Corpus Preparation Guide

**Date:** 2026-07-25 (09:53)
**For:** the user, to execute personally
**Context:** Addendum D-5 (`docs/20260725-0838-rlm-kernel-conformity-review-addendum.md`) and validation report #2 (`docs/20260725-0928-rlm-kernel-remediation-validation-2.md`, item F3/G3). This is the last open item before the K4-real GEPA milestone is unblocked.

---

## 1. What the gate is

Spec §12 / D-5 acceptance targets, measured at **100,000 pages** on the Windows dev box:

| Metric | Target | Current test coverage |
|---|---|---|
| Full index rebuild | < 2 h | only smoke-tested at 10K (green) |
| FTS search latency, p95 | < 300 ms | only smoke-tested at 10K (green) |
| `git status` on the vault | < 2 s | **no automated test exists** — measured manually via the script in §3 |
| Per-`completion()` helper listing (G4b residual) | informational | not covered — measured via §3 script |

Two tiers, per the amended D-5:

- **Tier 1 (the gate):** deterministic synthetic corpus, reproducible anywhere. Acceptance targets apply here.
- **Tier 2 (validation):** your organic corpus, confirming Tier-1 numbers hold on realistic text. Runs only when `RLM_KERNEL_LOAD_CORPUS` is set.

**Privacy rules for the organic corpus (unchanged):** lives outside the repo, referenced only by env var, never committed, never copied into any git history. The test skips cleanly when the env var is unset.

**Current state:** the test infrastructure exists (`tests/load/gen_corpus.py`, `tests/load/test_load.py`, both slow-marked). Tier-1 is hardcoded to a 10K CI smoke — the 100K run is a one-off you execute with the script below. No numbers at 100K exist yet anywhere.

---

## 2. Estimated cost (so you can plan)

| Step | Estimate on the dev box |
|---|---|
| Generate 100K synthetic pages | ~2–5 min, ~300 MB disk |
| Full reindex at 100K | minutes (target is generous: 2 h) |
| `git init` + first `git add`/`commit` on 100K files | several minutes, one-time |
| Tier-2 reindex over organic corpus | scales with page count; same code path |
| Total wall-clock for Tier 1 | allow ~30–60 min including git |

Run it when the machine can be left alone; close the llama-server if RAM is tight (the load test uses no LLM — it is pure file/SQLite/git work).

---

## 3. Tier 1 at 100K — step-by-step

All commands from the repo root (`~/Documents/Misc/rlm_for_local`) in Git Bash.

**Step 0 — save the runner script.** Save the following as `scripts/run_load_gate_100k.py` in the repo (or anywhere; adjust `REPO`):

```python
"""One-off 100K load-gate runner (Tier 1). Prints a report table to stdout."""
import os, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] if "__file__" in dir() else Path.cwd()
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))                      # for tests.load.gen_corpus
from tests.load.gen_corpus import generate_corpus  # noqa: E402
from rlm_kernel.index import Index, rebuild_index  # noqa: E402
from rlm_kernel.vault import LocalVault            # noqa: E402

CORPUS = Path(os.environ.get("LOAD_CORPUS_OUT",
                             Path.home() / "load-corpus-synth-100k"))
N = 100_000
QUERIES = ["error", "function", "system", "data", "process",
           "implementation", "configuration", "test", "result", "value"]

def pct(values, p):
    s = sorted(values); k = (len(s) - 1) * p / 100
    f = int(k); c = k - f
    return s[f] + (s[f + 1] - s[f]) * c if f + 1 < len(s) else s[f]

print(f"=== Tier-1 load gate: {N} pages at {CORPUS} ===")
if not (CORPUS / ".corpus_manifest.json").exists():
    print("[1/5] generating…"); t = time.perf_counter()
    generate_corpus(CORPUS, N, seed=42)
    print(f"  generated in {time.perf_counter()-t:.0f}s")
else:
    print("[1/5] corpus already present, skipping generation")

idx_path = CORPUS / ".index" / "meta.sqlite"
vault = LocalVault(CORPUS, init_git=False)
print("[2/5] full reindex…"); t = time.perf_counter()
idx = rebuild_index(vault, idx_path)
rebuild_s = time.perf_counter() - t
print(f"  {idx.page_count()} pages in {rebuild_s:.0f}s  (target < 7200s)")

print("[3/5] search latency…")
lat = []
for q in QUERIES:
    t = time.perf_counter(); idx.fts_search(q, limit=5)
    lat.append((time.perf_counter() - t) * 1000)
p95 = pct(lat, 95)
print(f"  p95 = {p95:.1f}ms  (target < 300ms)")

print("[4/5] helper listing (G4b check)…")
t = time.perf_counter(); paths = idx.list_paths(kind="helper", status="active")
idx_ms = (time.perf_counter() - t) * 1000
t = time.perf_counter(); pages = vault.list(kind="helper")
walk_s = time.perf_counter() - t
print(f"  index list_paths: {idx_ms:.1f}ms ({len(paths)} paths)")
print(f"  vault.list walk:  {walk_s:.1f}s ({len(pages)} parsed)  <- per-completion cost")
idx.close()

print("[5/5] git…")
if not (CORPUS / ".git").exists():
    subprocess.run(["git", "init"], cwd=CORPUS, capture_output=True)
    (CORPUS / ".gitignore").write_text(".index/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=CORPUS, capture_output=True)
    subprocess.run(["git", "-c", "user.email=load@test", "-c", "user.name=load",
                    "commit", "-m", "corpus", "--quiet"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "config", "core.fsmonitor", "true"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "config", "core.untrackedCache", "true"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "status", "--porcelain"], cwd=CORPUS, capture_output=True)  # warm
t = time.perf_counter()
subprocess.run(["git", "status", "--porcelain"], cwd=CORPUS, capture_output=True)
git_ms = (time.perf_counter() - t) * 1000
print(f"  git status: {git_ms:.0f}ms  (target < 2000ms)")

print("\n=== RESULTS ===")
print(f"pages={N} rebuild={rebuild_s:.0f}s search_p95={p95:.1f}ms "
      f"git_status={git_ms:.0f}ms helper_idx={idx_ms:.1f}ms helper_walk={walk_s:.1f}s")
print(f"gate: rebuild {'PASS' if rebuild_s < 7200 else 'FAIL'} | "
      f"search {'PASS' if p95 < 300 else 'FAIL'} | git {'PASS' if git_ms < 2000 else 'FAIL'}")
```

**Step 1 — run it** (Git Bash, from the repo root):

```bash
cd ~/Documents/Misc/rlm_for_local
.venv/Scripts/python.exe scripts/run_load_gate_100k.py 2>&1 | tee load-gate-tier1.txt
```

**Step 2 — keep the output.** The `RESULTS` block is what goes into the report (§6). The corpus lands in `~/load-corpus-synth-100k` (outside the repo; override with `LOAD_CORPUS_OUT`). Delete it freely afterwards, or keep it for reruns (generation is skipped if present).

---

## 4. Tier 2 — preparing your organic corpus

### 4.1 The format contract (critical)

Tier 2 opens your corpus with `LocalVault(corpus_path)` and indexes every `*.md` file it can parse. The parser (`schema.py:parse_page`) requires each file to **start** with valid YAML frontmatter:

```markdown
---
schema: 1
kind: note            # one of: contract, template, definition, helper, fewshot, note, topic, cache
name: my-page-slug    # 1–128 chars
title: "My page title"
summary: "One sentence, ≤ 200 chars."
status: active
---
Body text, markdown, any length (200–8,000 chars recommended).
```

**Files that fail to parse are silently skipped** (`vault.list()` swallows the error) — so a corpus of plain `.txt` or frontmatter-less `.md` would index as ~0 pages with no error. Use the converter in §4.2; it guarantees the contract.

Rules: UTF-8 encoding; one page per file; split large documents into ≤ ~8K-char pages at paragraph boundaries (matches real wiki statistics); bodies must not begin with a literal `---` line; strip or rename `.git/`, `.index/` directories if copying from an existing vault.

### 4.2 The converter script

Save as `scripts/prepare_organic_corpus.py`, then run it against any folder of raw texts (`.txt`, `.md`, `.markdown`, case-insensitive, recursive):

```python
"""Convert a directory of raw text files into schema-1 vault pages (Tier-2 corpus)."""
import hashlib, re, sys
from pathlib import Path

SRC = Path(sys.argv[1])          # e.g. ~/Documents/some-export
DST = Path(sys.argv[2])          # e.g. ~/organic-corpus   (must be OUTSIDE the repo)
MAX_BODY = 6000

def slug(s):
    s = re.sub(r"[^a-z0-9\s-]", "", s.lower())
    return (re.sub(r"\s+", "-", s).strip("-") or "page")[:64]

def first_sentence(text, n=200):
    m = re.match(r"\s*([^.!?\n]{10,}[.!?]?)", text)
    return (m.group(1) if m else text[:n])[:n].replace('"', "'").replace("\n", " ")

def split_paragraphs(text, max_len):
    paras, cur = re.split(r"\n\s*\n", text), ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_len:
            yield cur; cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        yield cur

seen, count, skipped = set(), 0, 0
for f in sorted(SRC.rglob("*")):
    if f.suffix.lower() not in (".txt", ".md", ".markdown") or not f.is_file():
        continue
    text = f.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 50:
        skipped += 1; continue
    for i, body in enumerate(split_paragraphs(text, MAX_BODY)):
        h = hashlib.sha256(body.encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        title = first_sentence(body, 80)
        name = f"{slug(f.stem)}-{i:03d}-{h[:6]}"
        page = (f'---\nschema: 1\nkind: note\nname: {name}\ntitle: "{title}"\n'
                f'summary: "{first_sentence(body)}"\ntags: [organic]\n'
                f'version: 1\nstatus: active\n---\n{body}\n')
        out = DST / "memory" / "notes" / f"{name}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
        count += 1
print(f"Wrote {count} pages to {DST} (skipped {skipped} tiny files, deduped by content hash)")
```

Run:

```bash
.venv/Scripts/python.exe scripts/prepare_organic_corpus.py \
    "/c/Users/Mizahnyx/Documents/your-source-folder" "/c/Users/Mizahnyx/organic-corpus"
```

- **Size:** if the result is well under 100K pages, that is fine — Tier 2 is a validation pass, not the gate. Report the actual count. (The addendum's optional "amplification" is **not implemented** in `gen_corpus.py`; don't fake volume — realistic text at real count beats synthetic padding.)
- **Confidentiality check before running:** the corpus is plain text — verify the source folder contains nothing you wouldn't want on this machine's disk unencrypted. The vault is *not* encrypted at rest.
- **Never commit it:** keep the destination outside `rlm_for_local/` and outside any git repo.

### 4.3 Running Tier 2

Git Bash (one session):

```bash
cd ~/Documents/Misc/rlm_for_local
export RLM_KERNEL_LOAD_CORPUS="/c/Users/Mizahnyx/organic-corpus"
.venv/Scripts/python.exe -m pytest tests/load/test_load.py -v -k "tier2" 2>&1 | tee load-gate-tier2.txt
```

PowerShell equivalent: `$env:RLM_KERNEL_LOAD_CORPUS = "C:\Users\Mizahnyx\organic-corpus"`.

Expected output: `Build: N pages in Xs` and `Search latency p95: Yms (target: < 300ms)`. Without the env var the tests skip — that's correct behavior, not a failure.

---

## 5. What to do with the results

1. Paste the Tier-1 `RESULTS` block and the Tier-2 pytest output into a message to me (or to the implementation agent).
2. The agent then commits `rlm_for_local/docs/load-test-report.md` filling this table:

| Metric | Target | Tier-1 measured | Tier-2 measured | Verdict |
|---|---|---|---|---|
| Pages | 100,000 | | | |
| Full reindex | < 2 h | | | |
| Search p95 | < 300 ms | | | |
| `git status` | < 2 s | | n/a | |
| Helper listing (index) | informational | | | |
| Helper listing (walk) | informational | | | |

3. **Interpretation:**
   - All three gate metrics PASS at 100K → the load gate is declared passed; K4-real is unblocked.
   - If Tier-1 and Tier-2 diverge >2× on the same metric, that's a finding (per the D-5 amendment), not a failure — it gets noted in the report.
   - `helper_walk` seconds at 100K tells us how urgent the G4b residual fix is (the per-`completion()` cost of `load_system_prompt_from_vault`'s full-tree walk).

## 6. Troubleshooting

- **Generation is slow / AV scanner:** Windows Defender real-time scanning of 100K file writes is the usual suspect; the corpus dir can be added to exclusions temporarily.
- **`git status` > 2 s on first run:** the script runs a warm-up status first and enables `core.fsmonitor` + `core.untrackedCache` (the spec's own recipe). The *second* measurement is the reported one.
- **sqlite-vec absent:** irrelevant here — the gate is lexical-only (vectors are the deferred K3 option).
- **Tier 2 indexes ~0 pages:** the corpus files lack valid frontmatter — rerun the converter; check one page with `head -8`.
- **Memory:** all scripts stream; nothing holds the corpus in RAM. 16 GB is comfortable.

---

## 7. After the gate

Once the report is committed with PASS numbers, the conformance loop from the July 24–25 review cycle is fully closed: R1–R6 green, F1–F4 closed, load gate passed. The next milestone is **K4-real** (Addendum §3): the GEPA offline optimizer — student 4B / reflection 8B, eval suites in `tests/evals/`, candidates routed through the gate with held-out promotion. The load-gate report is its entry ticket.
