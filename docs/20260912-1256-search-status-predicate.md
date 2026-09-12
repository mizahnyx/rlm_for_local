# Search stops returning retired pages

**Date:** 2026-09-12 12:56
**Enacts:** roadmap item 8, ledger CL1.
**Status:** enacted; two new mutations red as required (62 in the table); 290
kernel tests pass.

## 1. The defect

`Index.fts_search` queried the full-text table with **no status predicate**, so a
page the vault had explicitly retired kept answering queries exactly like an
active one — and a superseded page could outrank its own replacement. The kernel
manual documented the behaviour honestly rather than claiming otherwise, which is
how the defect stayed visible; it is now fixed rather than documented.

## 2. The change

- `fts_search(..., statuses=None)` defaults to `["active"]`, joined against
  `pages.status`. Passing `statuses` explicitly searches the history:
  `["active", "deprecated", "superseded", "pending"]` returns everything indexed.
- Results carry their `status`, so a caller that widened the search can tell a
  retired hit from a live one.
- `search_vault` passes the filter through (its `include_quarantine` argument
  remains the separate, explicit way to see quarantined proposals).
- The CLI exposes it: `rlm-kernel search "grep" --status active deprecated` and
  the same flag on `rlm search`. Retired hits are marked `[deprecated]` /
  `[superseded]` in the output, and an *empty* result says why it is empty
  ("Search returns active pages by default; --status … includes retired pages")
  instead of looking like a typo.

**One interaction found while testing:** `search._make_card` rebuilt the result
dict field by field and dropped `status`, so `--status deprecated` printed
identically to an active hit. It now carries the field, and a mutation pins it
(`CL1 a search card forgets the page's status`) — a filter that is applied but
then erased in the presentation layer is indistinguishable from no filter.

## 3. What this does not change

- **Demotion is still not deletion.** The page stays in the vault and in the
  index, `superseded_by` still points at its replacement, and asking for it
  explicitly returns it.
- **Helper injection already filtered by status**, and still does: only `active`
  helper pages reach the REPL namespace and the system prompt.
- **The index still holds retired pages.** This is a query-time filter, so
  rebuilding is not required for the change to take effect; a stale index simply
  has nothing new to filter.

## 4. Guards

| Mutation | Guard it proves |
|---|---|
| `CL1 search returns every status again` | The default predicate is applied. |
| `CL1 a search card forgets the page's status` | The status survives into the result. |

Tests: `TestSearchStatusPredicate` (a deprecated and a superseded page are
excluded by default, all three are returned on request, one status at a time,
results carry their status, and the kind filter composes with it) and
`TestSearchStatusFilter` at the CLI level (hidden from its own query by default;
`--status deprecated` finds it and marks it; an active hit has no marker; an empty
result explains the default).

## 5. Open

- **`list_paths(status=...)` and search now agree**, but `VaultStore.list()` still
  returns every status. That is deliberate — it is the walk used by the indexer
  and the gate — and it means "search hides it" and "the vault walk sees it" are
  both true, and documented as such in the kernel manual.
- **No live/end-to-end check beyond the suite.** The behaviour is entirely local
  (SQLite + CLI), and both layers are covered by tests driving the real entry
  points.
