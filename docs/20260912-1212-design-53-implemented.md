# Design §5.3 implemented — and the one requirement that was retired

**Date:** 2026-09-12 12:12
**Enacts:** roadmap item 6, ledger entries DG1–DG4 (and adds DG10).
**Status:** enacted; three new mutations red as required, with one guard
explicitly exempted and the reason recorded below.

## 1. What the design's §5.3 asked for, and what happened

The design (`20260721-1034-rlm-harness-small-models-design.md` §5.3) specified four
things for the REPL worker. Three were never implemented — the manual said so
plainly rather than describing the design as if it were the code — and this change
closes three of the four:

| Requirement | Outcome |
|---|---|
| Restricted builtins (`input/eval/exec/compile/globals/locals`) | **Implemented.** Model cells run with those removed, plus `breakpoint` — a worker with no stdin would sit there until the cell timeout, which is a worse failure than an error message. `RLM_REPL_ALLOW_DYNAMIC=1` restores the full set for an operator whose cells need it. |
| Memory bound on the worker | **Implemented where the OS allows.** `RLM_REPL_MEMORY_MB` sets `RLIMIT_AS` at worker start. POSIX only: Windows has no `resource` module, so the cell timeout remains the only bound there. A bogus value is ignored, never fatal. |
| Scaffold names restored after every cell | **Implemented.** See §2 — the semantics matter as much as the mechanism. |
| `open` jailed to the task directory | **Retired.** See §3. |

The restriction is applied *only* to the model's cell: the worker's own post-cell
work (reading the submission, repairing the scaffold, sending the result) runs
with the interpreter's real builtins, so the harness cannot be starved by its own
restriction.

## 2. "Restored" means usable, not reset

A cell is free to set `answer['content']`, and that state **must** survive into the
next cell — resetting `answer` every turn would make the submission protocol
impossible. What the worker repairs is a name that no longer holds something the
next cell can use:

- `answer` rebound to a non-dict → restored to `{"content": "", "ready": False}`;
- `context` deleted or set to `None` → restored from the snapshot taken at init;
- a helper (`grep`, `peek`, `chunk`, `map_query`, `show_vars`, `llm_query*`,
  `search`, `propose`, and any vault-injected helper) replaced by a non-callable
  or deleted → the injected/original definition is put back.

A model that deliberately replaces `grep` with a *different callable* is left
alone: restoring is about usability, not about freezing the namespace. The repaired
names are reported in the result and recorded in the trajectory
(`repl_result.scaffold_repaired`), so a run where the harness had to intervene is
visible afterwards instead of silent.

**Ordering, found while implementing:** the cell-end state has to be read
*before* the repair. Repairing first would hide a rebound `answer` from the
diagnostic that exists to report it (VD2), turning "the model rebound `answer`"
into "the line was not reached". A mutation pins the order
(`DG4 the cell-end state is read after the scaffold is repaired`).

## 3. The `open` jail is retired, not pending

Keeping "`open` is jailed to the task directory" on the list would buy a claim
rather than a boundary:

- imports stay permitted, so `import os` reaches `os.open`, and `pathlib` reaches
  the same syscalls through a different door;
- the worker's own `_FileContext` must read the spill file, which lives in the
  harness's temp directory and not in any "task dir";
- the worker already holds a socket to the harness and inherits the parent's
  environment.

Retiring it is the honest disposition, and the manual now says *retired* rather
than *never implemented*, because those are different states: one is a plan, the
other is a decision with a reason.

## 4. What is still reachable — DG10

The worker's own module names (`os`, `socket`, `sys`, and the protocol functions
`_send`/`_recv`) are in the same globals a cell runs in, so model code can reach
past the restricted builtins by accident, not only by `import os`. Executing cells
in a dedicated namespace that holds just the documented scaffold — with state
carried across cells — is the real fix, and it is recorded as roadmap **DG10**
rather than implied to be done. Until then the manual, README, extensibility guide
and `AGENTS.md` all state the same thing: a process boundary with a timeout, a
memory bound and a hygiene list, **not** a sandbox.

## 5. Guards

| Mutation | Guard it proves |
|---|---|
| `DG1 model code gets the full builtins again` | The restricted-builtin set is applied to model code. |
| `DG4 the scaffold is never restored after a cell` | The repair happens at all. |
| `DG4 the cell-end state is read after the scaffold is repaired` | The ordering that keeps VD2's diagnosis honest. |

Tests: `tests/test_repl.py::TestDesignSection53Scaffold` — nine of them, each a
real worker subprocess: the blocked names each raise; ordinary code (imports,
`len`, `json`) still works; the escape hatch restores full builtins; a rebound
`answer` is repaired and the *next* cell can submit; a helper overwritten by an
int is repaired; a deliberate callable override is respected; deleting `context`
is repaired; a memory limit is honoured where the OS allows; a bogus limit does not
stop the worker.

**One guard has no mutation entry, deliberately:** `_apply_memory_limit` uses
`resource.setrlimit`, which does not exist on Windows, so a mutation for it would
be reported as VACUOUS on this development host — a false alarm about the code
rather than about the test. The gap is written into
`scripts/check_guard_nonvacuity.py` as a comment next to the table, where the next
person will look.

## 6. Open

- **DG10 is the substantive remainder** (§4). It is a bigger change than the three
  made here and deserves its own item rather than being smuggled in.
- **Not verified on a live model.** These are harness behaviours, exercised by real
  worker subprocesses; no router model was run against them, and none needs to be.
- **The design document itself still describes the `open` jail** as the default.
  It is a point-in-time record and is not rewritten; this document and the local
  manual's §6.1 are where the current behaviour lives.
