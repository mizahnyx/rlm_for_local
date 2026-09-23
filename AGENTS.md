# AGENTS.md — working conventions for this repository

Instructions for anyone (agent or human) changing `rlm_for_local`. The project's
own discipline is unusual enough that most of this file is rules that already
exist, written down in one place so they stop being re-derived per session.

**Read first:** `docs/20260912-1155-roadmap.md` (the plan and the open ledger),
`README.md` (what the system is), `docs/operator-guide.md` (how to run it).

---

## 1. Non-negotiable rules

1. **Test first.** A fix or feature lands with the test that fails without it.
2. **Prove every guard non-vacuous.** Remove the guard, watch the *named* test go
   red, restore it. This is mechanised: add an entry to
   `scripts/check_guard_nonvacuity.py` and run it. This project has shipped
   vacuous tests three times, each caught late; the mutation table is the antidote
   and a mutation whose target no longer exists is reported as a problem rather
   than skipped.
3. **Harness-emitted strings live in `templates.py`.** Prompts, nudges and
   harness errors are not inlined at the call site; `tests/test_templates.py`
   enforces it. (Console/CLI output is not governed by this.)
4. **Documents are dated and never rewritten.** New documents under `docs/` are
   named `YYYYMMDD-HHmm-<topic>.md` (creation time). Point-in-time records are
   history: a correction is a *new* document that cites the old one. Living docs
   are listed in `docs/20260912-1155-roadmap.md` §1.
5. **Do not state what you have not verified.** If a number, a behaviour or a
   mechanism is inferred rather than observed, label it inferred. Several of this
   project's best findings came from that habit, and its worst incidents came
   from skipping it. And when something *unexpected* happens, the response is to
   produce the traces and let the owner reach the verdict (owner, 2026-09-17):
   the instinct to explain an anomaly from the code is the instinct that produced
   three wrong stories about this model, whereas the owner can read a page of
   events and see what happened. So: instrument it, render the trajectory
   (`rlm trace render`), point at the file, and stop short of concluding.
   Reproducers live in `scripts/` beside the mutation table — `scripts/probe_cell_budget.py`
   measures a *mechanism* (a cell that sleeps), `scripts/run_question_probe.py` measures
   the *task* (real questions asked of the corpus, one trajectory each, aggregates on
   stdout and the answers left beside the corpus) — and the traces go where the corpus
   is, never into this conversation.
6. **No scope creep without an owner call.** Scoring semantics, vault-format
   changes, sandbox boundaries and load-test scope are owner decisions. Ask, or
   record the item in the roadmap's ledger as `owner call`.
7. **A score means nothing without its scale.** Any reported model verdict states
   the weight profile, the sampling, and the host state it came from.
8. **The source corpus is read-only — enforced, not intended.** Every operation
   over the big file corpus (mounting, crawling, extracting, indexing, answering,
   wiki generation) must be strictly non-mutating. Because model-authored REPL
   cells run as the harness user with no `open` jail (DG2/DG10), *our code cannot
   be the boundary*: the boundary is the mount, and our code is defence in depth.
   The three layers, in order of authority:

   | Layer | Mechanism | What it actually guarantees |
   |---|---|---|
   | 1. OS / mount | Read-only bind mount (`mount -o remount,ro,bind`), a drive mounted `ro`, a read-only NFS/SMB export, `borg`/`restic mount`, or a dedicated user with no write access to the tree | The only real guarantee. Holds even against a model cell calling `os.remove`. |
   | 2. Mount layer in code | A provider protocol with **no write verbs at all** (`list`/`stat`/`read` only), reads via `os.open(..., O_RDONLY)`, path containment + symlink-escape refusal | Removes the accident: no ingest code path *can* write, and none can be asked to. |
   | 3. Derived state elsewhere | Index, CAS, vault, checkpoints and logs live under a separate root, asserted at startup to be **outside** the corpus root (fail hard if it is inside) | Stops the one mistake that would silently write into the backup. |

   And it must be *proved*, not asserted: a pre/post manifest check (paths, sizes,
   mtimes, and hashes of a sample), a `find <corpus> -newer <marker>` scan that
   must come back empty after a run, and a recorded `mount` line showing `ro`.
   Note for that scan: reading updates **atime**, not mtime — compare mtime
   (`find -newer` does) or mount `noatime`, otherwise every check reports a
   false positive. Streaming is part of the constraint: the corpus is never
   copied into the vault, and no code path may materialize a whole directory into
   memory (`--context-dir` does exactly that today and is unusable at corpus
   scale).

   **Corollary — a check that cannot see the truth must say "unknown".** The
   first version of `mount-luks-usb-ro.sh` verified the block layer with
   `$(blockdev --getro /dev/mapper/... 2>/dev/null || echo 0)`, which turns a
   permission error (the device node is `root:disk 0660`) into the value `0` and
   reports a read-only disk as writable. It now reads `/sys/class/block/<dm>/ro`
   and returns `?` when it cannot tell. Any probe that swallows its own failure
   and substitutes a default is worse than no probe: it produces confident wrong
   answers, which is how a working mount got reported as broken.

   **Second corollary — a probe must not be able to *change* what it measures.**
   `os.kill(pid, 0)` is the standard liveness check on POSIX and **terminates the
   process** on Windows. A mining lock used it to ask "is the worker alive?", and
   the answer cost a test run, the DSH harness hosting it, and the config write
   that was in flight. Liveness is decided by a heartbeat now (§3). The question a
   probe asks must not be answered by the probe itself.

9. **Corpus-derived data does not leave the machine that holds the corpus.**
   The census, any path listing, any per-file table, and any extract is
   sensitive: it is a consolidated index of someone's private files, which is
   *more* revealing than the tree itself. Therefore:

   - **Analysis runs where the data is.** Scripts that read the corpus or its
     census run on `lunacode`; they write their output to a local file under
     `~/rlm-corpus-inventory/` (mode 0600, directory 0700) and print *only
     aggregates* — counts, byte totals, distributions.
   - **Aggregates may travel; identifiers may not.** Counts and sizes are fine in
     a session, a commit message or a document. Directory names, file names, path
     samples, symlink targets, per-directory sizes and "top N largest files"
     listings are not — not in this conversation (it is sent to a model
     provider), not in the repository (it is public), not in a commit message.
   - **A record that needs names stays on the laptop.** The repository gets the
     aggregate summary and a pointer to the local record; the full record lives
     beside the data. **A question id is one of those names** — an id like
     `<person>-<place>` is derived from the prose the harness read, so it belongs in
     `~/rlm-derived/`, never in a document, a test, or a commit message. This is
     checked, not intended: `python scripts/check_privacy.py --tokens
     ~/rlm-derived/private-tokens.txt` (add `--history` to scan commit messages),
     where the *list* stays beside the corpus and the failure message names a file,
     a line and a position rather than the identifier. Measured 2026-09-19: a
     question id reached this public repository in a record, a test and a commit
     message because the rule had nothing checking it —
     `docs/20260919-2330-a-question-id-reached-the-public-repository.md`.
   - **Artifacts are locked down after use**: `chmod 600` the census TSVs, and
     decide deliberately whether to keep the walk (it contains every path in the
     backup) or delete it once the aggregates are computed. Keeping it saves a
     34-minute re-walk; it is also the single most sensitive derived file the
     project produces.
   - **The corpus is reached only through the harness** (owner call,
     2026-09-12). Ad-hoc shell work over the tree stops: reads go through
     `rlm_kernel/mounts.py`, either as `rlm corpus …` or as a `corpus_*` helper
     inside a model cell, and the path index is built by the harness
     (`rlm corpus index`) rather than by a hand-run walk. Two consequences worth
     stating: the harness therefore runs **beside** the corpus (`~/Misc/rlm_for_local`
     on `lunacode`, derived state in `~/rlm-derived`, model router on
     `127.0.0.1:9010`), and mount *state* checks (`findmnt`, sysfs `ro`) are the
     only permitted exception, because they read the mount, not the corpus.

## 2. Commands

```bash
# Fast suite — select on MARKERS, never on names
uv run pytest -m "not slow and not load" -q        # ~1180 tests, ~7 min

# Everything, including tests needing a live model server and the load corpus
uv run pytest -q

# Guard non-vacuity (the whole table, or one item)
uv run python scripts/check_guard_nonvacuity.py
uv run python scripts/check_guard_nonvacuity.py --only R26

# Documentation lint, and the linter's own falsifiability check
uv run python scripts/check_docs.py
uv run python scripts/check_docs.py --self-test

# Model suitability: sweep every model a router offers (sequential by design)
uv run python scripts/assess_router_models.py --screen
uv run python scripts/assess_router_models.py --only <model> --before-each \
    "ssh lunacode systemctl --user restart llama-router.service"

# Re-score a recorded sweep under a different weight profile — no model runs
uv run python scripts/rescore_sweep.py logs/router-model-battery.jsonl \
    --verify-with p1-heavy

# Corpus work (RO3/RO4). Runs where the corpus is — see `AGENTS.md` §1.9.
uv run python -m rlm_local.cli corpus index --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite     # paths only; reads no contents
uv run python -m rlm_local.cli corpus count --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli corpus classify --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite     # heads only, resumable
uv run python -m rlm_local.cli corpus search "…" --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli corpus search "…" --corpus-index ~/rlm-derived/corpus.sqlite \
    --count-only                                    # counts + coverage, no path, safe to paste
uv run python -m rlm_local.cli corpus sample --n 5 --seed 1234 \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite
                                                    # random passages + addresses, to devise
                                                    # questions from; output is corpus text
uv run python -m rlm_local.cli corpus reindex-encodings \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite
                                                    # re-index the 46,735 cp1252/latin-1
                                                    # files the old UTF-8 decode damaged
uv run python -m rlm_local.cli corpus counters --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli corpus counters --corpus-index ~/rlm-derived/corpus.sqlite \
    --refresh                                       # recompute the snapshot (~16 min; idle index)
uv run python -m rlm_local.cli mine plan --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli mine status --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli mine run --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite --for 2h   # a window, then stop
uv run python -m rlm_local.cli mine run --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite --for 25m --no-coverage-scan
                                                    # a window in a chain: skip the
                                                    # ~16 min scan; publish once at the end
uv run python -m rlm_local.cli mine pause     # stop after the item in flight
uv run python -m rlm_local.cli summarise --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite --limit 3 --cited-only
                                                    # RO6 by value: describe three cited
                                                    # documents with the configured model.
                                                    # Aggregates only (the log quotes nothing);
                                                    # --dry-run shows the set and its cost
                                                    # without calling a model
uv run python -m rlm_local.cli ask "…" --corpus-root /srv/corpus \
    --corpus-index ~/rlm-derived/corpus.sqlite     # corpus_find/read/search/… in a cell

# Read a run back as Markdown, where the corpus is (see the paragraph below)
uv run python -m rlm_local.cli trace render ~/rlm-derived --out-dir ~/rlm-derived/traces \
    --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite
uv run python -m rlm_local.cli trace summary ~/rlm-derived/live-ask-band1.jsonl

# The read-only proof. A marker scan CANNOT clear this corpus: it contains
# future-dated files, so "newer than the marker" is true forever. Compare two
# digests instead — one from the index, one from a fresh walk.
uv run python -m rlm_local.cli corpus digest --from-index \
    --corpus-index ~/rlm-derived/corpus.sqlite --out /tmp/before.json
uv run python -m rlm_local.cli corpus digest --corpus-root /srv/corpus \
    --compare /tmp/before.json
```

`rlm ask` with a corpus hands the model `corpus_find`, `corpus_list`,
`corpus_stat`, `corpus_read`, `corpus_count`, `corpus_search` and
`corpus_coverage`, and **refuses a submission from a run that called none of
them** — see `docs/20260914-2135-corpus-live-runs-and-the-unsearched-nudge.md`.
That guard is evidence-based (the parent serves every helper request), so a live
run whose answer looks ungrounded is checked against `corpus_calls`, not against
the model's prose. **The helpers' shapes are part of that contract**: the
enumeration verbs (`corpus_search`, `corpus_find`, `corpus_list`) return a *list*
and the rest return text, and a call with a parameter a helper does not take is
answered with the parameters it does take rather than a traceback — a worker-side
`_teaching` wrapper and `_as_hits`, with `WORKER_CORPUS_BAD_ARGUMENTS` in
`templates.py`. Both were learned the hard way: a model iterated a returned string
character by character, and another spent two of six turns on `limit=` where the
helper takes `k=` (`docs/20260919-2215-the-second-question-set.md`).

The same run's answer must cite its evidence — a final `Citations:` line of
addresses — and an answer that cites nothing and names no coverage is **refused
once** (`NUDGE_CORPUS_UNCITED`, both submission channels), with the escape hatch
that makes the refusal safe: "the corpus does not contain this, here is the
coverage" is accepted. **Since 2026-09-22 the citations may equally arrive on `answer['citations']`**
(a list, or a `;`/`,`-separated string): the scaffold hands the model a dict, and a citation
written there was discarded in the worker before the parent ever saw it, so the answer was
refused for being uncited. The correlation was exact — the three prose questions that used
that key were refused three times each, the three that did not were refused none —
`docs/20260922-2345-a-citation-on-the-dict-is-a-citation.md`. The order was measured first,
enforced second: the prompt
requirement alone produced 0 cited answers in 3 live runs of the 4B laptop model
(`docs/20260915-0655-corpus-citation-compliance-measured.md`). Each accepted
answer writes one `corpus_citation` guardrail event with
`answers_with_address=True|False`, and each refusal a `corpus_uncited` event —
read the counts with `grep -c` rather than reading the answers. **The escape hatch has now
fired live, once** (2026-09-19): the first attempt to test it died before the model could
submit, because the search counted the index it was searching (roadmap CL6); the first real
question set then produced a *voluntary*, uncited answer that named the coverage line and was
accepted for it — `docs/20260919-1318-owner-findings-first-question-set.md`.

**A citation must also answer the question, not merely have been served.** Every
search hit is labelled `covers n/m … (strong|partial|weak|none)`; the harness reads
that label back off the hit's own header line at submission, and an answer whose
cited addresses were **all** `weak`/`none` is refused once
(`NUDGE_CORPUS_WEAK_EVIDENCE`) unless it names coverage. One `strong`/`partial`
citation is enough, and an address with no label at all — one only `corpus_read`,
or any hit from a question with no content words — is `unknown` and refuses
nothing (the `AGENTS.md` §1.8 corollary again). Such refusals are their own event,
`corpus_weak_citation`, so they are never counted as `corpus_uncited`. Measured
2026-09-17: **implemented and unit-proved, never yet observed against a live
model** — `docs/20260917-0410-corpus-a-citation-must-answer-the-question.md`.

**A hit is a record with a short handle, and the model cites the handle.** Since
RO13 (2026-09-21) `corpus_search` returns a list of `HitRecord`s: `hit['address']`,
`hit['alias']`, `hit['band']`, `hit['covers']`, `hit['snippet']`, `hit['text']`.
`str(hit)` is exactly the line the model has always been shown, and `len(hits)` /
`hits[0]` / iteration still work, but `hit[0]` **raises** a message naming the
fields — an integer index used to return a character (`hits[0][0]` was `'S'`), and a
model that expected a structure got a letter and carried on, which is worse than an
error because nothing goes red. The five parts of the contract, each separately
guarded:

| part | where | the rule |
|---|---|---|
| the alias | `mnemonics.AliasTable` | `KQM7-3`: three letters, a digit, a check symbol, from alphabets without `L`, `0`, `1`, `5`, `8`. 93 750 codes, and **the width is a measured requirement, not a preference** |
| who owns it | `repl.REPLSandbox._alias_table` | the **parent**, beside `corpus_addresses_served`. The worker never learns the vocabulary — it hands an alias back untouched and the parent translates it — because the audit, the repair events and the served set all live in the parent |
| `corpus_read` | `repl._resolve_read_target` | takes an alias or a full address; an alias this session never minted is answered as an alias mistake (`WORKER_CORPUS_UNKNOWN_ALIAS`), never as "no such path" |
| the answer | `root_loop._finalize_answer` | the trajectory keeps the model's raw output; the **delivered** answer gets the true address substituted inline |
| the event | `root_loop._log_citation_repairs` | every repair writes `citation_repaired` — the number this feature exists to make countable |

Three properties make an alias safe to repair, and only the third is load-bearing:
the alphabet excludes one member of each confusable pair, so a slip cannot become a
*different valid* alias; the check symbol is a digest of the body, which is
corruption **detection** and is **not** injective (27 symbols cannot distinguish
93 750 codes — the design's original claim of injectivity is arithmetically
impossible); and `resolve` repairs only on a **unique winner**, refusing `ambiguous`
with both candidates named and never picking. The uniqueness rule is the guard, and
that was measured rather than reasoned: a variant that *additionally* required a
repair candidate's check symbol to match resolved **11 slips out of 3 110 to the
wrong passage**, while the unique-winner rule resolved none — narrowing the
candidate set can leave exactly one survivor where two readings exist.
`docs/20260921-0118-mnemonic-aliases-ownership-and-three-corrections.md`.

**A mnemonic layer that resolves ambiguously is worse than the addresses it
replaces**, because it points a citation at a passage the model never meant — the
one failure this project ranks below silence. The residual risk is therefore a slip
that lands *on* another live alias, and space is the whole defence: at the design's
original 3 750 codes a sampled session produced exactly that wrong resolution, and
at 93 750 it does not. Two live aliases one glyph apart would make such a slip
`ambiguous` rather than wrong, which is the safe direction; a fresh table also
refuses another session's aliases, so an old trace's handle cannot silently mean a
new passage.

**A member of a container is read through the container's extraction cache.** Since RO14
(2026-09-21) `corpus_read("arch.zip!member.txt")` resolves the container through the
indexed path column and serves its extracted text with a header naming the substitution —
the text is the *container's*, not the member's, and a reader who is not told cannot judge
it. A container with no cached extraction is answered with
`CORPUS_CONTAINER_NEEDS_MINING`, which names the operation that would mine it and says
"the corpus does not contain this" is an acceptable answer. **Two shapes, and the cheap-
looking one was the worse failure**: a *bare* member name never reached the chunk lookup at
all (no `#L` fragment, so the mount check answered `no such path` — unreadable rather than
slow), while the **address** form `…!member#L0-9` took `find_chunk`'s `display` fallback: a
scan of 29 015 791 rows, over 150 s against a 120 s cell limit, finding nothing. The guards
are therefore **trace-based** — a member read must issue no statement filtering
`text_chunks` on `display` — because the cost is invisible in the answer. A path that
legitimately contains `!` still reads as a file: the whole name being a stored path settles
it, and `pack.zip!notes.txt` beside a real `pack.zip` has its own test.
`docs/20260921-0550-a-container-member-reads-through-the-cache.md`.

**Read a run back as Markdown, and keep it where the corpus is.** `rlm trace
render` turns a trajectory into one page per run — question, outcome, citation
audit, turn-by-turn transcript, and the passage behind every cited or served
address — plus an `index.md`; `rlm trace summary` prints counts and writes
nothing. The pages contain corpus text, so `--out-dir` is refused inside the
corpus root and the files are written 0600 in a 0700 directory, and the command
prints only the output path and counts. Each page says when its audit is
**partial** (a run recorded before the `corpus_served` instrumentation, or a
trajectory torn by a kill) rather than presenting an unverifiable "no fabrication"
as a finding — `docs/20260917-0915-corpus-traces-a-human-can-audit.md`.

**Never count a big table on a cell's path.** `corpus_search` and
`corpus_coverage` quote a *published* coverage snapshot (`rlm corpus counters`,
recomputed by `--refresh` or at the end of every mining window); a search that
finds nothing and has no snapshot says **unknown**, never zero. The measurements
that forced this are in `docs/20260915-2305-corpus-search-cannot-count-its-own-index.md`:
`search` 0.2 s against `COUNT(*) FROM text_chunks` 972 s, and a 120 s cell limit.

**A harness limit is not a model failure, and must never read like one.** A cell
that dies on a time limit writes its own `cell_timeout` event naming the limit that
fired, the budget behind it, `activity=` (the gate's own input) and `last_helper=`
(the verb it was running); the model is told the budget it actually hit, rather than
a bug. There are **two limits, both per-invocation configurable** (RO16): a *soft*
one (`--cell-timeout`, `RLM_CELL_TIMEOUT`, else the profile's 60 s / 120 s) that
**signals**, extending a cell which has asked the harness for something to the
*hard* one (`--cell-timeout-hard`, `RLM_CELL_TIMEOUT_HARD`, default 3 600 s), which
stops it. A granted extension is announced on the operator's warning sink while the
cell runs and recorded as a `cell_extended` event; equal limits switch the second
stage off. Diagnose from the counts, not
from the answer: a cluster of `cell_timeout` on one helper is a harness problem (fix
the helper, or raise the budget for that workload), a scatter is the model asking for
too much, and a run whose cells died on the budget says nothing about the answer
until it is re-run with a bigger one. A cell that does not *compile* is the third
case: `syntax_retry` / `syntax_giveup` events, bounded by `max_syntax_retries`, and
it costs neither a turn nor the error budget — the loop's turn counter deliberately
stands still for it. `docs/20260917-1200-two-failures-that-were-not-the-models.md`,
`docs/20260918-0451-two-stage-cell-budget-landed.md`.

`-k "not slow and not load"` is **wrong**: `-k` matches a substring of the node
id, so it also drops ~20 tests that merely mention "load" in their name
(`test_ingest_loads_file`, the upload-cap tests) and reports them as "deselected".
Use `-m`. (Measured on 2026-09-11: `-m` 744 passed / 12 deselected vs `-k` 724 /
32. The suite has grown since: measured 2026-09-14, `-m "not slow and not load"`
over `tests/` is **1179 passed, 7 skipped, 12 deselected** in ~7 min.)

## 3. Environment traps (all of these have bitten this repo)

- **Never probe a process with `os.kill(pid, 0)`.** On POSIX that is the standard
  liveness check; on Windows Python's `os.kill` falls through to
  `TerminateProcess(handle, sig)`, so signal `0` **kills the process it asks
  about**. On 2026-09-14 it killed a pytest run, then the DSH harness hosting it,
  and — because the harness died mid-write — the owner's API key disappeared from
  the DSH configuration. Liveness is now decided by a heartbeat (the lock file's
  mtime, refreshed per item in `rlm_kernel/mine.py`), and
  `tests/rlm_kernel/test_mine.py` tokenises that module and fails if any
  process-signalling call comes back. Never "check" a process by signalling it.
- **Windows + sandbox temp dirs.** `tempfile.mkdtemp` creates `0o700`
  directories, which the sandbox cannot enter, which fails ~271 tests. The fix
  lives in `.venv/Lib/site-packages/sitecustomize.py` (0o700 → 0o750 on Windows)
  and is **not** in the repository — it is environment repair, documented in
  `20260910-0730-remediation-validation.md` §2, and removable.
- **Never use PowerShell text substitution on source files.** `Get-Content … |
  Set-Content -Encoding utf8` mangles non-ASCII (U+FFFD plus a BOM). Use the
  file-editing tools; the doc linter catches the damage after the fact.
- **Scratch roots** are `.tmp_*/`, gitignored. Do not leave scratch anywhere else.
  One scratch root from a permission probe (`rt_<pid>/`) cannot be deleted from
  inside this environment at all, so it is gitignored too; the removal
  instruction is written beside it in `.gitignore`. If `git status` reports an
  unreadable *directory* instead of an untracked file, that is this case.
- **`git push` needs an escalated sandbox** in this environment: MSYS `ssh`
  cannot create its signal pipe under the default sandbox (`WinError 5`). The
  denial is expected; retry the same command once with `sandbox_permissions`.
- **PowerShell** does not support `&&`, and multi-line commit messages with
  quotes break `-m`; write the message to a file and use `git commit -F`.
- **Never run `check_guard_nonvacuity.py` while editing source.** It rewrites the
  file, runs one test, and restores the *version it read* — an edit made in that
  window is silently lost. **An interrupted run does not restore anything**: the
  mutation it was applying when it was killed stays in the file. Measured
  2026-09-17: cancelling a full-table run left `src/rlm_web/app.py` with
  `require_same_origin` deleted from the `POST /jobs` dependencies — a security
  regression in the working tree, visible only to `git diff`. So after killing or
  losing a run, `git status --porcelain` must show no unexpected `src/` file, and a
  stray one is restored with `git checkout --` before anything else. **And do not
  `git add`/commit while it runs either.** Its mutations are real file contents, so a
  commit made mid-run captures one: measured 2026-09-22, `model_check.py` was committed
  with a probe's `_turn_after` guard deleted and had to be amended out. Wait for the
  table to finish and for `git status --porcelain` to come back clean.
- **`ssh` to `lunacode`** uses key auth with no persisted host key:
  `ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL lunacode …`.
  PowerShell re-adds CR to piped scripts; pipe remote scripts through
  `tr -d '\r' | bash -s`.
- **Never put `tr -d "\r"` in a PowerShell-built ssh one-liner.** The quotes do not
  survive the trip, `tr` receives the two characters `\r` as a *set* and, once bash
  has eaten the backslash, deletes **every letter `r`** in the file. On
  2026-09-16 it turned `cd /home/mizahnyx/Misc/rlm_for_local` into
  `cd /home/mizahnyx/Misc/lm_fo_local` and the script died before doing anything,
  which is at least a loud failure. The reliable transfer is base64 alone —
  `echo <b64> | base64 -d > file` — with LF line endings in the source file;
  verify with `grep -c` or `head` before running it. (The same shape as the other
  traps here: a quoting convention that works until it silently does not.)
- **Do not hand-pick a mix of `tests/` and `tests/rlm_kernel/` modules in one
  pytest invocation.** A combination like
  `pytest tests/rlm_kernel/test_index.py tests/test_cli.py tests/rlm_kernel/test_repl_bridge.py`
  can fail the *last* module with `fixture 'temp_vault' not found` even though
  every module passes — and every pair of those modules passes — in isolation.
  The documented invocations (`pytest tests/`, `pytest tests/rlm_kernel/`) are
  unaffected and are what the recorded suite numbers come from; select modules
  from one directory, or run the directory.

## 4. The local model server

- The harness talks to any OpenAI-compatible endpoint. On this LAN the model
  server is a llama.cpp **router** on `lunacode:9010` (HTTPS, self-signed,
  `role=router`, `models_autoload=true`), which loads models on demand.
- One export configures every model-facing command:
  `RLM_ENDPOINT`, `RLM_MODEL` (the CLI's `--endpoint` / `--model` do the same per
  invocation). `RLM_TEST_ENDPOINT` / `RLM_TEST_MODEL` gate the integration tests.
- Battery knobs: `RLM_CHECK_WEIGHTS` (`default` | `p1-heavy`) and
  `RLM_CHECK_P4_TRIALS` (1–3; unusable values fall back to 3, deliberately).
- **Host limits are real.** 15 GiB RAM, one model instance at a time in practice,
  ~6.6 tok/s prompt / ~3.0 tok/s decode. A quick battery on a 2–4B model takes
  30–60 min. Restart the router between models (`--before-each`) or the router
  keeps every model it has served resident and the box starts swapping.
- Model behaviour is **not** stable across router cache states: identical prompt,
  `temperature=0.0`, same server produced both voluntary submission and no
  submission minutes apart (`20260911-1359-p4-live-confirmation.md`).

## 5. Repository map

| Path | What it is |
|---|---|
| `src/rlm_local/` | The harness: root loop, parser, subprocess REPL, sub-call manager, context store, model backend, prompts/templates, CLI, model-check battery |
| `src/rlm_kernel/` | The evolvable layer: git-versioned vault, gate, SQLite FTS5 index, memory, GEPA optimizer, seed |
| `src/rlm_web/` | FastAPI console (Jinja2, SSE, session-cookie auth, origin-checked POSTs) |
| `docs/` | Living documents, dated records, `conformance/` history |
| `scripts/` | Guard-mutation table, doc linter, load-gate phases, router sweep, sweep re-scorer, doc self-test |
| `tests/` | Suite; `tests/load/` carries both `slow` and `load` markers |
| `logs/` | Run artifacts (gitignored: `*.jsonl`) |

## 6. The working loop for one item

1. Read the item in the roadmap ledger; if it changes behaviour, confirm it is
   not an owner call (or that the owner has called it).
2. Write the test that must fail without the change.
3. Implement the change.
4. Prove the guard non-vacuous: add a mutation entry, run it, see it red.
5. Update the living docs that describe the behaviour (manuals, operator guide,
   README) — a behaviour change with stale docs is an unfinished change.
6. If the change produced evidence worth keeping (a live run, a measurement, a
   verdict), write a dated record; if it closed a ledger item, update the
   roadmap's status.
7. Run the fast suite, the mutation table, and the doc lint (with `--self-test`).
8. Commit with the reasoning in the message (what was wrong, what changed, what
   was verified, what remains unverified), and push.

## 7. Security posture, in one paragraph

The defaults assume a single trusted user on one machine. The kernel gate is a
*quality* gate, not containment; the REPL is a process boundary, not a sandbox —
model code runs without `eval`/`exec`/`compile`/`globals`/`locals` and the worker
can be memory-bounded, but imports stay permitted and the worker's own names are
reachable through `globals()`, so a cell can still reach the filesystem (roadmap
DG10 covers closing that); TLS verification is off for the local self-signed
server (with a warning); the web console is loopback-only without
`RLM_WEB_TOKEN`, requires `RLM_WEB_SECRET` with one, and checks the origin of
every state-changing request. Run against content and models you trust. Details:
`README.md` §Security Posture and `docs/operator-guide.md` §6.
