# RLM Operator Guide

**How to install, configure, and operate the RLM harness, kernel, and web frontend.**

---

## 1. What This System Is

RLM (Recursive Language Model) is a harness that wraps a local LLM and
transforms it into a recursive reasoning system. It can answer questions
over contexts 10×–100× larger than the model's native context window.

```
User ──► completion(query, context)
              │
              ▼
     ┌──────────────────────────────────────────────────┐
     │  RootLoop (orchestrator)                         │
     │  Root model writes code in ```repl blocks        │
     │  REPL sandbox executes code, proxies sub-calls    │
     │  Results flow through REPL variables              │
     └──────────────────────────────────────────────────┘
              │                       │
              ▼                       ▼
     ┌──────────────┐        ┌──────────────────┐
     │  Model server │        │  rlm-kernel vault │
     │  (llama.cpp)  │        │  (pages, index,   │
     │  :9010        │        │   gate, memory)   │
     └──────────────┘        └──────────────────┘
```

The **root model** never sees your data. Context lives in a sandboxed Python
REPL. The model writes code to probe, search, chunk, and delegate. Results
stay in REPL variables — only small, deliberate `print()` outputs reach the
model's conversation.

The **rlm-kernel** makes the system evolvable. Prompts, helpers, few-shots,
and memory are human-readable pages in a git-versioned wiki. The system can
describe itself to itself, grow new vocabulary, and improve its own prompts
through GEPA offline optimization.

---

## 2. Quickstart

### 2.1 Prerequisites

- Python ≥ 3.12
- [llama.cpp](https://github.com/ggerganov/llama.cpp) server (or Ollama, LM Studio)
- Git (for vault versioning)

### 2.2 Install

```bash
git clone <repo> rlm_for_local
cd rlm_for_local
uv sync
```

### 2.3 Start the Model Server

```bash
llama-server \
    --model Qwen3.5-4B-Abliterated-Q4_K_M.gguf \
    --host 127.0.0.1 --port 9010 \
    --ctx-size 16384 --flash-attn \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --parallel 2
```

#### Pointing at a model server on another machine

The three profiles all default to `https://localhost:9010/v1`. To use a server
elsewhere — another LAN box, a Tailscale peer, or a llama.cpp **router** that
starts models on demand — override `root_endpoint` per call:

```python
import rlm_local
from rlm_local.config import PROFILES

# Inspect what a profile ships with
print(PROFILES["laptop"].root_endpoint)

answer = rlm_local.completion(
    "What is the archive access code?",
    context,
    profile="tiny",
    root_endpoint="https://lunacode:9010/v1",   # same host for sub-calls
    root_model="Qwen3.5-4B-Abliterated",
)
```

Overrides are ordinary `load_config()` keyword arguments, so they also work for
`config=load_config("tiny", root_endpoint="…")` and for any other profile value
(`max_turns`, `cell_timeout`, `repl_output_char_cap`, …).

From the CLI, two environment variables configure **every** model-facing command
— `ask`, `chat` and `check` alike:

```bash
export RLM_ENDPOINT="https://lunacode:9010/v1"
export RLM_MODEL="Qwen3.5-4B-Abliterated"

uv run python -m rlm_local.cli ask "What is the access code?" --context-file doc.md
uv run python -m rlm_local.cli chat
uv run python -m rlm_local.cli check "$RLM_MODEL" --quick
```

Or per invocation with `--endpoint` / `--model` (flags beat the environment):

```bash
uv run python -m rlm_local.cli ask "…" --context-file doc.md \
    --endpoint https://lunacode:9010/v1 --model Qwen3.5-2B-Instruct
```

Four things worth knowing:

- **`--model` sets both tiers.** The root model and the sub-call model are the
  same, so sub-calls run on the model you named rather than the profile default.
  Naming one model while silently delegating to another would make a suitability
  result meaningless.
- **`sub_endpoint` follows `root_endpoint`** (empty means "same as root"), so
  `--endpoint` covers both tiers.
- **The `/v1` suffix is optional** — endpoints are normalized, so
  `https://lunacode:9010` and `https://lunacode:9010/v1` are equivalent (R9).
- **A cold router model takes time.** A llama.cpp router loads a model on first
  touch (tens of seconds for a 4B on CPU); the default 300 s request timeout
  absorbs that, and the R9 retry policy covers a momentary refusal during a swap.

To sweep every model a router offers:

```bash
uv run python scripts/assess_router_models.py --endpoint "$RLM_ENDPOINT"
```

It runs the `check` battery against each advertised model, sequentially (model
swapping is not safe to parallelise), and appends one JSON line per model so
partial results survive an interruption.

A non-loopback `https` endpoint emits a `UserWarning` because verification is off
by default — see §4, "TLS Verification Posture".

### 2.4 Initialize the Vault

```bash
uv run python -m rlm_kernel.cli init
```

This creates `~/.local/share/rlm-kernel/vault/` with contract pages, template
pages and builtin helpers. Every page lives in a directory named for its own
kind, **singular**: `contract/`, `helper/`, `fewshot/`, `definition/`,
`memory/`, and `quarantine/` for proposals awaiting review.

If you have a vault seeded before that convention was unified, it will contain
`helpers/` and `fewshots/`; the kernel manual §4.7 has a one-time move snippet.

### 2.5 Build the Search Index

```bash
uv run python -m rlm_kernel.cli index --rebuild
```

### 2.6 First Query

```bash
# Ask a question with a Markdown file as context
uv run python -m rlm_local.cli ask "What color is mentioned?" \
    --context-file my_document.md \
    --profile laptop
```

### 2.7 Interactive Chat

```bash
uv run python -m rlm_local.cli chat --profile laptop
```

Type queries directly. Use `/ingest <file>` to load documents, `/search <query>`
to search the vault, `/context` to see what's loaded.

### 2.8 Web Frontend (First Login)

```bash
# Set a token for web auth — and the secret that signs the session cookie
export RLM_WEB_TOKEN="your-secret-token"
export RLM_WEB_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

# Start the web server
uv run python -m rlm_web.app
```

Open `http://localhost:8778`, log in with your token. Upload Markdown files
or paste context, submit queries, watch live progress.

**`RLM_WEB_SECRET` is required whenever `RLM_WEB_TOKEN` is set.** The session
cookie is signed with it, so without one either the signing key is guessable —
which lets anyone forge an authenticated session and skip the token — or it
changes on every restart and logs you out constantly. The server refuses to
start in that configuration rather than serving an authentication check that
does not hold:

```
error: RLM_WEB_TOKEN is set but RLM_WEB_SECRET is not.
```

Running without `RLM_WEB_TOKEN` needs no secret: the console then serves
loopback-only, and login is disabled.

---

## 3. CLI Reference

### `rlm ask`

```bash
rlm ask "QUERY" [--context-file FILE.md ...] [--context-dir DIR] \
    [--stdin] [--vault PATH] [--profile tiny|laptop|workstation] \
    [--max-turns N] [--log-path FILE] [--endpoint URL] [--model ID] \
    [--corpus-root DIR] [--corpus-index FILE]
```

`--endpoint` / `--model` (or `RLM_ENDPOINT` / `RLM_MODEL`) point the run at a
model server on another host; `--model` sets both tiers. See §2.3.

Context is assembled in this order: `--context-file` (each with a `# filename`
heading, separated by `---`), `--context-dir` (all `*.md` sorted), `--stdin`,
`--vault`. Returns the answer on stdout. Exit code 0 on success, 2 on error.

`--corpus-root` / `--corpus-index` (or `RLM_CORPUS_ROOT` / `RLM_CORPUS_INDEX`)
give the run read-only access to a corpus instead of a context string: the REPL
gains `corpus_find`, `corpus_list`, `corpus_stat`, `corpus_read` and
`corpus_count`, and no `--context-*` argument is needed. See `rlm corpus` in §3.

**Examples:**
```bash
# Single file
rlm ask "Summarize the report." --context-file report.md

# Multiple files
rlm ask "Compare proposals." --context-file proposal-a.md --context-file proposal-b.md

# From stdin
cat notes.md | rlm ask "Extract dates." --stdin

# From vault
rlm ask "What is a widget?" --vault definitions/widget.md

# With trajectory logging
rlm ask "Find dates." --context-file data.md --log-path /tmp/traj.jsonl
```

### `rlm chat`

```bash
rlm chat [--profile tiny|laptop|workstation] [--vault-path PATH] \
    [--endpoint URL] [--model ID]
```

Interactive loop with slash commands:
- `/ask <query>` — completion with session context
- `/ingest <path...>` — load `.md` files into session context
- `/context` — show loaded files and total characters
- `/clear` — drop session context
- `/search <query>` — hybrid vault search (compact cards)
- `/get <path>` — print a vault page
- `/note <text>` — create a memory note
- `/check <model-id>` — run suitability battery
- `/quit` — exit

Non-slash input is treated as `/ask`. Ctrl+C to interrupt, Ctrl+D to quit.

### `rlm ingest`

```bash
rlm ingest <path...> [--kind note|definition|topic] [--tags a,b] [--vault PATH]
```

Creates one vault page per Markdown file. Slugifies the filename, extracts
title from the first `# heading`. Idempotent — re-ingesting the same content
prints `skipped (duplicate)`. Pages land in `<kind>/<name>.md` (notes go to
`memory/notes/`), matching the kernel's directory convention.

### `rlm search`

```bash
rlm search "QUERY" [--kind K] [-k N] [--vault PATH]
```

BM25 hybrid search over the vault. Compact cards showing kind, name, title,
summary.

### `rlm get`

```bash
rlm get <path> [--vault PATH]
```

Prints the full page (frontmatter + body).

### `rlm check`

```bash
rlm check <model-id> [--endpoint URL] [--quick] [--weights PROFILE] [--profile tiny]
```

Runs the 9-probe suitability battery. `--quick` runs P1+P4+P6 only (a 50-point
scale reported out of 100) and takes a few minutes per probe against a local 4B
model. Both P1 and P4 sample three times, on three different questions each, so
they dominate a run's wall time; `RLM_CHECK_P4_TRIALS=1` reduces P4 to one
sample (see §5.3).

**Weighting (`--weights`, or `RLM_CHECK_WEIGHTS`).** The battery is a weighted
average, and the weights decide what the score can mean:

| Profile | P1 | P4 | P6 | Use it when |
|---|---|---|---|---|
| `default` | 10 | 20 | 20 | Ranking models. P1 ("emits a valid `repl` block") is saturated on the local router — every model tested scored full marks, down to 0.8B — so its weight sits on P4 (voluntary submission) and P6 (needle retrieval), the two probes that actually separate a usable model from an unusable one (finding recorded in `docs/20260911-0050-router-model-assessment.md`). |
| `p1-heavy` | 20 | 15 | 15 | Reproducing the scores recorded in `docs/20260911-0050-router-model-assessment.md`. Those numbers were produced on this scale; a score is meaningless without the scale it came from. |

Every result records the profile and the resolved weights, and the persisted
report carries them in its frontmatter, so old reports stay interpretable.

Reports score, verdict, timing and per-probe evidence to stdout. It does **not**
write a report file — redirect it yourself if you want to keep it:

```bash
rlm check Qwen3.5-4B-Abliterated --quick 2>&1 | tee model-check-$(date +%F).txt
```

When P4 scores 0 *and* the transcript contains submission text, the probe now
says where the line went instead of just reporting two disagreeing booleans:
text inside a string or comment in the block, outside every fence, inside a fence
tag the parser does not execute, in a block that raised, or in a block that ran
clean and never reached the line. Those findings appear as
`DIAGNOSTIC [code]` in the evidence.

`--endpoint` is how you check a model served elsewhere (another LAN box, a
Tailscale peer, a llama.cpp router); the default is
`https://localhost:9010/v1`. Checking a non-loopback `https` endpoint emits the
R18 warning described in §4, which is expected.

### `rlm vault`

```bash
rlm vault init|index|review|promote|demote [--vault PATH]
```

Pass-through to `rlm-kernel` vault management. Two flags matter for the trust
model:

- `rlm vault review` validates quarantined proposals **statically** (no code
  execution) and prints `Review mode: static validation only`. Add `--execute`
  to also run helper code in the restricted-builtin sandbox — a convenience for
  code you already trust, never a containment boundary.
- `rlm vault promote` refuses to overwrite a `deprecated`/`superseded` page at the
  target path unless you pass `--force` (an `active` occupant always blocks).

### `rlm optimize`

```bash
rlm optimize [--target T] [--suite S] [--profile P] [--max-calls N]
```

GEPA offline optimization of harness prompts. Targets: `prologue`,
`how-to-work`, `nudges`, `fewshots`, `helper-docs`.

### `rlm corpus`

```bash
rlm corpus index  --corpus-root DIR --corpus-index FILE [--progress-every N]
rlm corpus status --corpus-index FILE
rlm corpus count  --corpus-index FILE [--kind file|dir|symlink] [--under DIR]
rlm corpus find   --corpus-index FILE QUERY [-k N] [--kind K] [--under DIR]
rlm corpus read   --corpus-root DIR REL [--max-bytes N]
rlm corpus verify --corpus-root DIR (--since T | --since-file F) [--sample N]
rlm corpus classify --corpus-root DIR --corpus-index FILE [--limit N] [--hash-mode head|none]
rlm mine plan   --corpus-index FILE [--limit N]
rlm mine status --corpus-index FILE
rlm mine run    --corpus-root DIR --corpus-index FILE [--tasks T,T] [--for 2h] [--until 07:00] [--max-items N]
rlm mine pause | resume [--corpus-index FILE]
rlm mine retry  --corpus-index FILE [--task T]
```

Read-only access to a large file tree — the "corpus" the harness can answer
questions about. Both paths also come from the environment
(`RLM_CORPUS_ROOT`, `RLM_CORPUS_INDEX`).

**The index covers names, not contents.** `rlm corpus index` makes one streaming
pass over the tree and records `path`, `parent`, `name`, `kind`, `size` and
`mtime` in SQLite. It opens no files, so it is bounded by directory-entry cost
rather than by the size of what is in them, and it is what keeps search from
walking millions of entries per call. Measured on the owner's corpus
(4.97M entries, ~1.01 TB): the walk is tens of minutes, one-off; every query
after it is index arithmetic.

**Read-only, in three layers** (`AGENTS.md` §1.8). The boundary is the mount:
open the LUKS container with `cryptsetup open --readonly`, mount it `ro`, and
point `--corpus-root` at that path. The code is defence in depth —
`rlm_kernel/mounts.py` has no write verb to call, resolves every path inside the
root (refusing symlink escapes), and reads through `O_RDONLY` with a byte cap.
Derived state must live **outside** the corpus: an index path inside it is
refused at startup, not accepted with a warning. `rlm corpus index` prints
aggregate counts only; that is the form safe to quote off the machine, because a
path listing is a private index of someone's files.

**From a model cell**, `rlm ask --corpus-root … --corpus-index …` exposes five
helpers — `corpus_find`, `corpus_list`, `corpus_stat`, `corpus_read`,
`corpus_count` — answered in the parent process through the mount, never by the
sandboxed worker. The system prompt advertises them and warns that the tree must
never be walked from a cell. A corpus question needs no `--context-*`.

```bash
# One-off: build the index, outside the corpus
rlm corpus index --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite

# Ask the corpus a question (the model reaches it through the helpers)
rlm ask "How many files are PDFs, and what are the ten largest?" \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite

# Operator-side checks that need no model
rlm corpus count --corpus-index ~/rlm-derived/corpus.sqlite --kind file
rlm corpus find  --corpus-index ~/rlm-derived/corpus.sqlite budget -k 20

# Prove a run did not touch the corpus (take the marker before the run)
touch ~/rlm-derived/marker
rlm corpus verify --corpus-root /srv/corpus --since-file ~/rlm-derived/marker

# Stage 1: read each file's head and record what it actually is
rlm corpus classify --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite
```

**`rlm corpus classify` is the content pass.** Extensions lie — on the owner's
corpus, 1.07M files have unknown extensions and 486k have none, ~191 GiB whose
nature no name can tell. This reads the **head** of each file (default 8 KiB,
bounded, `O_RDONLY`, through the read-only mount — a 200 GiB image costs what a
4-byte file costs), and records in a table beside the path index:

- `kind` — `text`, `archive`, `media`, `database`, `document`, `binary`, `empty`,
  `unreadable`;
- `encoding` — `utf-8`, `cp1252`, or nothing;
- a content hash over the head plus the size (exact for any file under the
  window, and 1.49M files here are under 1 KiB), which is what makes the dedup
  estimate real.

It is **resumable**: rows commit in batches and a second run classifies only what
is left, so a pass killed after three hours resumes rather than restarts. Use
`--limit N` for a pilot and `--redo` to redo everything (e.g. after a sniffing
rule changes — rows carry a `sniff_version`). Output is aggregates only: counts
and bytes by kind, encodings, dedup factor, and unreadable count — never a path.

### `rlm mine` — mining the corpus in windows

```bash
# 1. Turn the map into work. Idempotent: running it again adds nothing.
rlm mine plan --corpus-index ~/rlm-derived/corpus.sqlite

# 2. Work it for as long as the machine is yours, then it stops by itself.
rlm mine run --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite \
    --tasks list_archive,extract_text --for 2h --progress-every 500

# 3. Or hand the machine over immediately: the worker finishes the item in
#    flight, commits it, and exits.
rlm mine pause          # ... and later:
rlm mine resume
rlm mine status         # queue depth by task and state, cache size, PAUSED?
rlm mine retry          # put failed items back (e.g. after the code improves)
```

**The corpus is mined, not read once.** The Stage 1 map says what each file is, so
`plan` queues exactly the work each file needs and nothing is discovered by
walking. A `run` is a *window*: a `--for` budget, a `--until` clock time, an
`--max-items` cap and the `--pause` flag are each checked **between items**, and
every item commits as it lands — stopping at any moment loses at most the item in
flight.

**Everything derived is cached by content hash** under `~/rlm-derived/cache/<task>/`,
so the corpus's 2.09× duplication in text is paid for once and re-running a batch
is free. The cache entry records the engine and version, never the source path —
two byte-identical files share one derivation.

**One worker at a time.** `run` takes a lock (`~/rlm-derived/mine.lock`) whose
liveness is a *heartbeat*: the worker touches it per item, and a lock untouched
for five minutes is stale and may be taken over. (It deliberately does not check
whether the recorded pid exists — see `AGENTS.md` §3 for why that check cost this
project a night.) A second `run` refuses to start.

Tasks implemented today: **`list_archive`** (zip/tar listings through the mount,
capped at 20,000 members, members recorded for search), **`extract_text`**
(pdftotext, or zip+XML for OOXML/ODF/EPUB; an empty text layer is recorded as
`needs_ocr`, never as a failure), and **`index_text`** (a plain text file's own
bytes go into the text index — no conversion needed, and this is the largest class
at 2,882,822 files). `vlm_describe`, `asr_transcribe`, `ocr_page`, `summarise` and
`synthesise` are queued names with no handler yet, and the worker records them as
`no_handler` rather than pretending to do them.

**Measured rates on the owner's corpus**, so a window can be planned: text
extraction 5.3 items/s; archive listing and indexing ~33 items/s (with the claim
fixed — see below). Indexing all 2.88M text files is therefore ~24 hours of
windows, and every hour of it is independently useful because the queue commits
per item.

### `rlm corpus search` and the `corpus_search` helper

```bash
rlm corpus search "Cuicani" --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite
rlm corpus search "Cuicani" --corpus-index ~/rlm-derived/corpus.sqlite --count-only
rlm corpus search x --corpus-index ~/rlm-derived/corpus.sqlite --coverage
```

Search reaches the **words** inside the corpus, not just names. Inside a model
cell the same capability is `corpus_search(query, k=8)`, alongside
`corpus_find` (paths, **and members inside listed archives**, reported as
`container!member`), `corpus_list`, `corpus_stat`, `corpus_read`,
`corpus_count` and `corpus_coverage`.

Every hit is an address — `path#L<byte_start>-<byte_end>` — that can be re-read
**verbatim**: `corpus_read(hit)` or `corpus_read(<address>)` returns exactly that
passage, which is what makes it a citation rather than a lead. Text that came from
an extraction is labelled (`derived:pdftotext`), vendored matches are counted and
can be included with `include_vendored=True`, and `corpus_coverage()` reports how
much of the corpus is indexed at all. Its output on 2026-09-14 — a live number,
not a constant:

```
[coverage: 36,745 sources indexed (1.3% of the 2,882,822 text files;
 5,354 documents extracted, 517 awaiting OCR)]
```

because "no matches" over a partial index is a different fact from "no matches"
over all of it. A search returning nothing yields a single element carrying both
the reason and the coverage — the case where a false negative would otherwise
look like proof of absence.

Inside a cell, `corpus_search` returns a **list of hits**: `len(hits)`, `hits[0]`
and iteration all behave as a caller expects, and each element is
`<address>  [labels]` followed by a snippet. That shape is a contract learned the
hard way — the first version returned one formatted string, and the first live run
saw the model write `len(hits)` and `hits[0]` against it, get a character count and
the letter `A`, then report "malformed data" and give up.

**A corpus run that never calls a helper is nudged, not accepted.** The parent
process serves every `corpus_*` request, so it *knows* whether the model looked:
`rlm ask --corpus-index …` refuses the first submission from a run with zero
helper calls, appends `NUDGE_CORPUS_UNSEARCHED`, and restarts the turn (at most
`max_consecutive_nudges` times, then forced finalization). A `context` in a
corpus run is a placeholder, so "not mentioned in the corpus" delivered after one
`print(len(context))` is a claim about a corpus nobody opened — which is what the
third live run did, and what this guard is for. One helper call of any kind
clears it, including a search that finds nothing or raises: the guard asks "did
it look", not "did it win". When it fires, the trajectory JSONL carries
`{"event": "guardrail", "guardrail": "corpus_unsearched"}`, so an operator can
tell a nudged run from an unlooked-at one without reading the transcript.

**Citations are required, and now enforced once — with an escape hatch.** The
corpus section of the system prompt asks for a final line of the form
`Citations: <path>#L<start>-<end>; …`, and an answer that cites no address and
names no coverage is refused: `NUDGE_CORPUS_UNCITED` restarts the turn, at most
`max_consecutive_nudges` times, and then the run falls through to forced
finalization. The escape hatch is deliberate — *"the corpus does not contain this,
here is the coverage"* is a truthful answer and is accepted — because at partial
coverage "nothing citable" is the common case, and a refusal a truthful run cannot
satisfy is a trap rather than a guard.

The order of events is worth keeping: the requirement was added and **measured**
first, and the refusal only after the measurement failed. Three consecutive live
runs of the 4B laptop model searched, read up to five passages, printed addresses,
and cited nothing — 0 for 3 (`docs/20260915-0655-corpus-citation-compliance-measured.md`).

Every accepted answer is recorded as one `corpus_citation` guardrail event, and
every refusal as `corpus_uncited`:

```bash
# How did this run's answers do on provenance? (counts only, no answer text)
grep -c '"guardrail": "corpus_citation"' "$LOG"                    # answers recorded
grep -c 'answers_with_address=True' "$LOG"                          # of which cited one
grep -c '"guardrail": "corpus_uncited"' "$LOG"                      # refusals
```

A run with many `corpus_uncited` events and few cited answers is a run the model
fought; a run with none of either cites everything first time.

`--count-only` prints counts and coverage and no path or fragment — the form that
is safe to paste anywhere.

**`rlm corpus verify` is the read-only proof.** Take a marker before the run, and
this walks the corpus afterwards through the same mount provider and reports, as
aggregates only, everything whose mtime is after it. Exit 0 means a complete scan
and nothing newer; anything else exits 1 with a JSON report. It classifies each
newer entry by where its mtime falls — inside the run window (**breach**), after
the scan (pre-existing future dates: a skewed clock or a tool that wrote future
dates), or before the run (the marker was taken early) — because a bare
`find -newer` cannot tell those apart, and on this corpus it reported a breach
that turned out not to be one. `--sample N` stops after N newer entries, which
bounds the cost of a bad answer; a clean corpus costs a full walk, which is the
price of saying "nothing changed" honestly. The report never contains a path (see
`AGENTS.md` §1.9).

---

## 4. Web UI Reference

### Pages

| Route | Description |
|---|---|
| `GET /` | Console — query form with Markdown upload/paste |
| `POST /jobs` | Submit a query (pasted context **and** uploaded `.md` files) → redirects to job page |
| `GET /jobs/{id}` | Live job view with SSE progress stream |
| `GET /vault?q=` | Read-only vault search |
| `GET /vault/page/{path}` | Read-only page view |
| `POST /vault/ingest` | Upload `.md` files as permanent vault pages |
| `GET /chat`, `POST /chat/send` | Chat console (SSE response stream) |
| `GET /docs/{name}` | Operator guide and manuals |

### Job Lifecycle

1. User submits query + context on `/`.
2. A job is created with a unique ID, state = `pending`.
3. A background thread runs `rlm_local.completion()` with trajectory logging.
4. The job page opens an SSE connection to `/jobs/{id}/events`.
5. Events stream as they occur (turns, REPL output, final answer).
6. Final answer is rendered when the job completes.

Jobs survive page refresh (state is in memory; not resumable across server
restarts in v1).

### HTTPS Setup

**Option A — Tailscale cert (preferred):**
```bash
tailscale cert <machine>.<tailnet>.ts.net
# Certificates land in the current directory
uv run python -m rlm_web.app \
    --host 0.0.0.0 --port 8778 \
    --ssl-keyfile <machine>.ts.net.key \
    --ssl-certfile <machine>.ts.net.crt
```
This gives you a real Let's Encrypt certificate — no phone warnings.

**Option B — Self-signed cert (fallback):**

TLS material is **never committed** — `cert.pem` / `key.pem` were removed from
version control and `*.pem` / `*.crt` / `*.key` are gitignored. Generate a fresh
pair **per host**, in the directory you run the server from:

```bash
# One-liner, per host (825 days is the max the CA/B forum allows for a leaf cert)
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
    -days 825 -subj "/CN=localhost"

# Or, with SAN entries for the Tailscale hostname and 100.x.y.z address:
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
    -days 825 -nodes \
    -subj "/CN=<tailscale-hostname>" \
    -addext "subjectAltName=DNS:<tailscale-hostname>,IP:<100.x.y.z>"

# Start server
uv run python -m rlm_web.app \
    --host 0.0.0.0 --port 8778 \
    --ssl-keyfile key.pem --ssl-certfile cert.pem
```

If you are upgrading an existing checkout, the old committed files are already
on disk; the `git rm --cached` that untracked them leaves them in place, so
nothing breaks. Just do not re-add them.
Install the cert on your phone (Android: Settings → Security → Install from
storage; iOS: Settings → General → About → Certificate Trust Settings).

### Phone Setup on Tailscale

1. Install Tailscale on your phone.
2. Connect to your tailnet.
3. Open `https://<machine>.<tailnet>.ts.net:8778` in your browser.
4. Accept the self-signed cert warning (first time only) or use the
   Tailscale cert path above.
5. Log in with your `RLM_WEB_TOKEN`.

### TLS Verification Posture (R18)

The harness talks to a local self-signed inference server, so
`HTTPModelBackend` defaults to `verify=False`. That default is fine on
loopback — and only there:

- **Loopback endpoints** (`localhost`, `127.0.0.0/8`, `::1`) are exempt, and no
  warning is emitted.
- **A non-loopback `https` endpoint with verification off emits a
  `UserWarning`** naming the endpoint, because anything on the path can
  intercept the traffic. Plain `http` has no certificate to verify, so it is not
  warned about.
- To silence it responsibly, put the server on `localhost` (an SSH tunnel or a
  local port-forward counts) or pass `verify=True`.

The same posture applies to the GEPA optimizer: it used to set
`litellm.ssl_verify = False` **process-globally**, which would have disabled
verification for every litellm call in the process, including a later remote
one. It now passes `ssl_verify` per call on the reflection model only.

**Web console (S5/R21).** The console's trust model is worth knowing before
exposing it:

- Authentication is a route dependency, so a new route cannot forget it —
  `tests/test_web.py::TestAuthCoverageByConstruction` walks the route table.
- With `RLM_WEB_TOKEN` set, an authenticated session is required for **every**
  route, including both SSE endpoints (`/jobs/{id}/events`,
  `/chat/events/{stream_id}`), which previously streamed answers unauthenticated.
- Without a token the console is **loopback-only**; `POST /login` returns 400
  because there is nothing to compare against. Starlette's `TestClient` connects
  from a synthetic non-loopback host, so the test suite opts in explicitly with
  `RLM_WEB_ALLOW_TESTCLIENT=1`.
- Session cookies are marked `Secure` whenever the server is started with
  `--ssl-keyfile`/`--ssl-certfile`.
- `/check` was removed: the page posted to a route that did not exist. Use
  `rlm check <model>` from the CLI.

---

## 5. Model Management

### 5.1 Initialize a Vault for a New Model

1. **Start the model server** with your new model.
2. **Create a vault:**
   ```bash
   uv run python -m rlm_kernel.cli init
   uv run python -m rlm_kernel.cli index --rebuild
   ```
3. **Run the suitability check:**
   ```bash
   uv run python -m rlm_local.cli check <model-id>
   ```
4. **If SUITABLE**, update your profile:
   ```python
   # In your script or config
   config = load_config("laptop", root_model="<model-id>")
   ```
5. **Baseline sanity eval:**
   ```bash
   uv run python -m rlm_local.cli ask "What color?" \
       --context-file test.md --max-turns 4
   ```

### 5.2 Repurpose an Existing Vault for a New Model

Vault content is model-agnostic — nothing to migrate. Required steps:

1. **Run suitability check on the new model:**
   ```bash
   uv run python -m rlm_local.cli check <new-model-id>
   ```
2. **Update your config** to point at the new model.
3. **Evaluate evolved prompts against the new model:**
   ```python
   from rlm_kernel.optimize import evaluate_candidate
   from rlm_kernel.vault import LocalVault
   vault = LocalVault(Path.home() / ".local/share/rlm-kernel/vault")
   result = evaluate_candidate(
       vault.get("contract/how-to-work.md").body,
       "needle_search", Path("tests/evals"),
       profile="tiny", max_turns=6,
   )
   print(f"Score: {result.score:.1%}")
   ```
4. **If degraded** (per the AppWorld negative-transfer lesson), restore the
   seed and re-optimize:
   ```bash
   git -C ~/.local/share/rlm-kernel/vault checkout -- contract/
   uv run python -m rlm_local.cli optimize --target how-to-work
   ```

### 5.3 Grade a Model's Suitability

Run the full battery:
```bash
uv run python -m rlm_local.cli check <model-id>
```

**Verdict bands:**
- **≥75: SUITABLE** — can serve as root tier. Correct protocol, recovers from errors, answers needles.
- **50–74: MARGINAL** — usable as sub-tier or with assistance. May need more nudges or lower expectations.
- **<50: NOT SUITABLE** — cannot reliably operate the harness protocol. Try a different model.

**P4 is sampled three times, on three different questions.** It asks whether the
model submits *on its own*, and that turned out to be the least stable thing the
battery measures: `Qwen3.5-2B-Instruct` scored 46.7 (NOT SUITABLE) and 86.7
(SUITABLE) minutes apart on the same prompt against the same server, differing
only in P4 (`docs/20260911-1359-p4-live-confirmation.md`). The cause is now
measured rather than guessed: **it tracks the router's cache state.** On a freshly
restarted router that model did not submit in 2 of 2 cold runs; on the immediately
following run of the identical prompt it submitted in 2 of 2 warm runs
(`docs/20260912-1226-p4-cache-state-confirmed.md`).

That is also why each P4 trial uses a *different* question: every trial is then a
first run at a fresh prompt, which is the situation a user bringing a new task is
in. **The verdict is a cold-prompt verdict** — the honest one to act on — and a
repeat of the same task on a warm router may well behave better. The probe scores
the mean of three trials and passes only on a majority, which is why a `--quick`
run on a slow host costs roughly two extra model conversations:
`RLM_CHECK_P4_TRIALS=1` (or `2`) buys that time back at the price of exactly the
stability, and the run says so in its own evidence when you do. On a cold router
that same model scored **0 of 3** trials — the same 46.7 and NOT SUITABLE, but as
"it does not submit, three times, for three different reasons" rather than "the
sample we took" (`docs/20260912-0219-p4-multi-trial-live-confirmation.md`).

**Re-check cadence:** on model upgrade, on prompt change, or when you notice
degradation. Record the run yourself — `rlm check` prints its report, it does not
write a file (see §3). Record the weight profile with the score, or the number
cannot be compared with an older run (see `--weights` in §3).

---

## 6. Security Notes

- **Token handling:** `RLM_WEB_TOKEN` via environment variable. Never commit it.
  Change it periodically. The login form sets an HttpOnly session cookie, and
  the cookie is marked `Secure` whenever the server is started with
  `--ssl-keyfile`/`--ssl-certfile`. Token comparison is constant-time.
- **Session secret:** set `RLM_WEB_SECRET` to a random value whenever
  `RLM_WEB_TOKEN` is set — it signs the session cookie. There is no built-in
  default: without it the key would be guessable (so an attacker could forge an
  authenticated session and bypass the token entirely) or would change on every
  restart. The server refuses to start in that configuration. Generate one with
  `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
- **Fail-closed by default:** without `RLM_WEB_TOKEN` the console is reachable
  from **loopback only**, and `POST /login` returns 400 because there is nothing
  to compare against. Authentication is a route dependency, so every route —
  including both SSE streams — enforces it; adding a route without it fails a test.
  `RLM_WEB_ALLOW_TESTCLIENT=1` exists solely so the test suite's synthetic
  non-loopback client can reach the app, and it never overrides a configured token.
- **Cross-origin (CSRF) protection — on by default.** A session cookie rides
  along on any request the browser makes, so authentication alone does not
  authorise a `POST`. Every state-changing route (`POST /jobs`, `/vault/ingest`,
  `/chat/send`, `/chat/clear`, `/chat/context`, `/login`, and `GET /logout`)
  carries an origin check as a route dependency; adding another one without it
  fails a test. Modes, from `RLM_WEB_ORIGIN_CHECK`:

  | Value | Behaviour |
  |---|---|
  | `same-origin` (default) | A request that claims an `Origin` (or `Referer`) must claim this server's. A request that claims neither — `curl`, a script, a typed URL — is allowed, which is why this mode does not break automation. |
  | `strict` | An origin must be claimed *and* match. Use it behind a proxy, or when only browsers should reach the console. |
  | `off` | No check. For a console that is genuinely loopback-only; the server refuses to start on any other value, so a typo cannot disable it silently. |

  `Origin: null` (a sandboxed iframe, a `file://` page) always fails closed, and
  the request's own origin may also be named explicitly in
  `RLM_WEB_ALLOWED_ORIGINS` (comma-separated, e.g.
  `http://127.0.0.1:8778,https://lunacode.tail-scale.ts.net`). Setting that
  allowlist replaces the same-origin comparison and does **not** implicitly
  include this server — that is what also resists DNS rebinding, where the
  attacker's page resolves to your console and its `Origin` and `Host` agree.
  An unusable entry (no scheme, or a bare hostname) makes the server exit 2 at
  startup rather than silently never matching. `localhost`, `127.0.0.1` and
  `[::1]` are treated as one origin, so opening the console on both names does
  not lock you out.
- **Tailscale scope:** the web frontend binds `0.0.0.0` but only the Tailscale
  interface is reachable from outside your LAN. Verify with `tailscale status`.
- **Self-signed certs:** browsers and phones will warn. Either use the
  Tailscale cert path for a real certificate, or accept the warning on your
  own devices only. TLS material is never committed: `*.pem`, `*.crt` and `*.key`
  are gitignored — generate a pair per host (§4).
- **No debug in production:** the web server runs without `--reload` in
  production. Set `RLM_WEB_TOKEN`.
- **Upload caps:** 20 files / 8 MB total per request. Files larger than 1 MB
  per document are rejected.
- **Server-escaped rendering:** all user content in the web UI is HTML-escaped
  via Jinja2 autoescaping, and the vault upload result is built with
  `textContent`/`createElement` rather than `innerHTML`, so a crafted filename
  cannot inject markup or script.
- **Vault paths are contained:** `LocalVault` refuses absolute paths, `..`
  segments and anything resolving outside the vault root, and page `name` is
  charset-validated — a proposal cannot be promoted outside the vault.
- **Trajectory logs contain full prompts and responses.** They are written to
  `logs/trajectories/` under the working directory (gitignored) and are not
  deleted automatically; `TrajectoryLogger.prune(keep=N)` bounds the history.

---

## 7. Troubleshooting

### cp1252 / Unicode Errors

**Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character`

**Fix:** All file writes in this project use explicit `encoding="utf-8"`. If
you encounter this in your own scripts, add `encoding="utf-8"` to `open()` or
`write_text()` calls.

### Smart Quotes in Model Output

**Symptom:** Model emits `"content"` (curly quotes) instead of `"content"`.

**Fix:** The parser's rescue path handles this. If you see `NameError` on
variables using curly quotes, the model may need a different chat template.
Run `rlm check <model>` to assess.

### Model Swap / Timeout

**Symptom:** `httpx.ReadTimeout` or long pauses between turns.

**Fix:** The model server may be swapping models (llama-swap). Wait for the
swap to complete (2–6 seconds on NVMe). If persistent, reduce `max_turns`.

### Slow Runs

**Symptom:** Completions take 10+ minutes.

**Fix:** This is expected on CPU-only hardware. A 10-turn loop with 24 sub-calls
takes 4–8 minutes on a 4B model. Use `--profile tiny` for lower budgets, or
upgrade to a GPU.

### Power-Loss Recovery

**Symptom:** PC powered off during an optimization run.

**Fix:** GEPA checkpoints are saved in `logs/k4-gepa-checkpoints/`. Restart
the model server, re-run the same `rlm optimize` command — GEPA will detect
the existing state and resume from the last checkpoint.

### Web Frontend Won't Start

**Symptom:** `ModuleNotFoundError: No module named 'fastapi'`

**Fix:** `uv sync` to install dependencies. Ensure `fastapi`, `uvicorn`,
`jinja2`, and `python-multipart` are in `pyproject.toml` dependencies.

---

## 8. FAQ

**Q: Where does the vault live?**
A: `~/.local/share/rlm-kernel/vault/` by default. Override with `--vault PATH`
on any command.

**Q: How do I back up the vault?**
A: The vault is a git repository. `git -C ~/.local/share/rlm-kernel/vault push`
to a remote, or use `restic`/`rsync` for the entire directory including
`.index/`.

**Q: Why did my model fail the suitability check?**
A: Run `rlm check <model>` and read the per-probe evidence. Common failures:
model emits JSON prose instead of ```repl blocks (P5), can't recover from
errors (P3), or can't find needles in context (P6). The report file at
`docs/model-checks/` has the full evidence.

**Q: Can I use model X?**
A: Run `rlm check <model-id>` to find out. Models below ~1.5B parameters
rarely pass. Models above ~8B usually pass but may be slow on CPU. The
sweet spot is 4B–8B instruct-tuned models (Qwen3, Llama-3, Phi-4).

**Q: What if the optimizer makes things worse?**
A: The vault is git-versioned. `git -C ~/.local/share/rlm-kernel/vault log`
shows the promotion commit. `git revert <commit>` rolls back instantly.
The optimizer only promotes candidates that beat baseline on the held-out
eval split.

**Q: How do I add my own helpers?**
A: Write a helper page in `helper/<name>.md` (singular — every kind directory is
named for its kind) with `## Signature` and `## Implementation` sections.
Rebuild the index: `rlm vault index --rebuild`. The helper will be available in
the next completion. Authoring directly is the trusted path; model-authored
helpers go through the gate instead (`propose` → `rlm-kernel review` →
`rlm-kernel promote`), and `review` validates them statically unless you pass
`--execute`.

**Q: Is my data sent anywhere?**
A: Only to the model endpoint you configure. By default that is a llama-server
on `localhost`, so nothing leaves the machine — the vault is on your filesystem
and there is no telemetry, no cloud and no external API call. If you point
`root_endpoint` at another host (a LAN box or a Tailscale peer), your context
goes to *that* host and nowhere else. Note that `verify=False` is the default for
local self-signed servers; a non-loopback `https` endpoint with verification off
raises a warning naming the endpoint, because that traffic can be intercepted.

**Q: Can I use this without the kernel/vault?**
A: Yes. `rlm_local.completion()` works standalone. The kernel and web
frontend are optional layers.

**Q: How do I upgrade?**
A: `git pull && uv sync`. The vault is never clobbered by upgrades — seed
pages are created on first run only; subsequent upgrades add new pages but
never overwrite existing ones.
