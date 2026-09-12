# The remaining ledger: dead code, worker messages, slot subsets, honest docs

**Date:** 2026-09-12 13:14
**Enacts:** roadmap items 9–12, ledger entries DG8, DG9, CL3, CL4, DG6, DG7.
**Status:** enacted; 69 guard mutations, all red as required; fast suite green
(`860 passed, 12 deselected`); doc lint 0 problems across 14 checked documents.

Four small items that had been carried in reports for weeks. Each one is the same
shape: something was documented honestly as *not done*, and the honest thing left
to do was either to do it or to say plainly why it stays undone.

## 1. `get_helper_summaries()` — dead API, duplicated rule (DG9)

The method had exactly one caller: a test. Meanwhile
`prompts.load_system_prompt_from_vault` built the same "Available helpers" section
itself — including an index-backed path (C1) and a 30-line progressive-disclosure
cap — so the rule for *which helpers the model sees* existed twice, and only one
copy was exercised.

It is now the single source: the bridge does the index-backed listing (falling back
to a vault walk when no index exists), applies the cap, and filters to `active`;
the prompt assembly calls it and keeps its own walk only for the no-bridge case.
The section's text is unchanged, which matters because it is prompt text.

While wiring it, one thing was worth pinning: the index-backed path was asserted
by *making the vault walk fail* and requiring the summaries to still come back.
A test that only compares output cannot tell the two paths apart.

## 2. `Config.max_depth` — removed (DG8)

Declared, defaulted, never read. R16's precedent for inert configuration is to
remove it rather than leave a capacity claim the model never sees, so it is gone
rather than wired to nothing. Recursive sub-calls remain unimplemented; that is
now the absence of an attribute instead of a field that looks like a feature.

## 3. The worker's messages come from `templates.py` (CL3)

The sandboxed worker program carried its own copies of four harness messages
(`Error: invalid regex: …`, `Error: no response from harness`,
`Error: propose failed`, and the `(no results)` that the harness side of the same
protocol also emits). They are now defined once in `templates.py` and injected
into the worker as a literal `_MSG` dict at the top of the generated program.

Two consequences worth stating:

- **The worker and the bridge can no longer disagree** about `(no results)`:
  `repl_bridge` imports the same constant, and a bridge test compares against it
  rather than against a literal.
- **`context_store`'s invalid-regex message was a third copy** — different wording
  (`invalid regex pattern:` vs `invalid regex:`) for the same failure, depending on
  whether the context was spilled to disk. Both now use one template.

The template-discipline guard needed refining rather than relaxing: it looks for a
constant's *name* in shipping code, and the worker sees dict keys and text, not
Python names. It now also accepts a constant whose **value** is carried inside
`WORKER_MESSAGES`, which counts as emitting it — a worker that inlined its own
copies again would fail both that test and the inline-string scan.

## 4. Slot-subset validation at the gate (CL4)

An evolved contract page that invented `{new_slot}` promoted happily, then raised
`KeyError` inside prompt assembly, where a broad `except` swallowed it and used
the packaged prompt instead. The vault kept a page that could never take effect.

Now: for a page that would land on a **formatted contract path**, every slot must
be one the runtime fills, or validation fails. The scoping is the interesting
part, and it came from reading the data rather than the code — a survey of the
seeded vault found:

| Page | Slots | Filled by `prompt_vars`? |
|---|---|---|
| `contract/repl-contract.md` | `{repl_cap}`, `{sub_budget}` | yes — and this page *is* `.format()`-ed |
| `contract/how-to-work.md` | `{max_turns}`, `{example_chunking_idiom}` | not formatted; appended verbatim |
| `contract/templates/*.md` (6 pages) | `{turn}`, `{used}`, `{budget}`, `{context_len}` … | no — those are `templates.py` slots |

A global subset check would therefore have rejected the pages the vault ships
with. The check is scoped to `prompts.FORMATTED_CONTRACT_PATHS`, a new named
constant that the assembly code itself now uses — so the list is a rule with two
consumers, not documentation that can drift.

Guards include the negative direction: a template page carrying `{turn}` must
still validate, and **every seeded contract/template page must still validate**.

## 5. Two documentation gaps (DG6, DG7)

- **DG7 was already done.** The extensibility guide §8.5 states plainly that there
  is no `call_api` binding and no `KernelBridge.handle_api_call`, and names the
  four RPC verbs that do exist. Verified while working this item; the ledger says
  so instead of pretending it needed fixing.
- **DG6 is now stated where it matters.** The kernel manual's GEPA target list
  (kernel manual §9.3 and §11.7) says that only `how-to-work` and `helper-docs` can change
  behaviour today: `prologue`, `nudges` and `fewshots` are read by the runtime from
  `rlm_local.templates`, so optimizing them produces a better-written page and no
  behavioural change. Wiring them remains a feature project, recorded as DG6 — but
  a run against one of them is now visibly a measurement of nothing rather than a
  surprise.

## 6. Guards added

| Mutation | Guard it proves |
|---|---|
| `DG9 the helper summaries ignore their cap` | Progressive disclosure is enforced. |
| `DG9 the helper summaries always walk the vault` | The index-backed path is taken when an index exists. |
| `DG9 the prompt builds the helper section itself again` | One source of truth for the helper section. |
| `CL3 the worker carries its own copies of the harness messages` | The injection, and the template discipline that notices if it goes. |
| `CL3 the bridge stops using the shared no-results message` | The two sides of the protocol agree. |
| `CL4 unknown slots are accepted again` | The gate rejects a slot the runtime cannot fill. |
| `CL4 every page is slot-checked, including the shipped templates` | The scoping that keeps the check from crying wolf. |

The mutation runner also gained a check it should have had: an **ambiguous**
target (a literal appearing more than once in a file) is now reported as a problem
instead of silently mutating the first occurrence — which had made one of this
batch's mutations look vacuous when the guard was in fact fine.

## 7. Open after this batch

- **DG10** — cells run in the worker's own globals, so the harness's names (`os`,
  `socket`, `_send`) are reachable from model code. That is the substantive
  remainder of the design's §5.3 work and belongs in its own change.
- **VD4/VD5/OD1–OD5** — the load-gate `git` measurement (host-bound), the two
  models never battery-tested, and the owner calls, unchanged.
- **No live re-measurement is needed for any item here**: all four are local
  behaviour, exercised through the real entry points (the CLI, a real worker
  subprocess, a real vault).
