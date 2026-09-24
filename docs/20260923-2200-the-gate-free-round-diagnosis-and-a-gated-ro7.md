# The gate-free round: what the diagnosis found, and why RO7 is gated

**Date:** 2026-09-23. **Commits:** `72c9f23` (`rlm summarise` honours `mine pause`). Related:
`docs/20260923-2100-ro6-the-cold-cost-and-a-call-in-flight.md` (the cost work that preceded this).

The owner's instruction for this stretch: *"only the work that needs no decision at all … leave
every open question untouched"*. This records what that round produced, and — because "no
decision" cannot be verified by doing nothing — what it deliberately did **not** do.

## Landed: the pause flag

`rlm summarise` takes the mining lock but did not pass the pause file, so `rlm mine pause` stopped
the queue worker and silently left a summarise run running. A stop signal that works for one
command and not the other is worse than none, because it is trusted. The command now takes all
three paths from the same `_mine_paths` helper `mine run` uses, and accepts `--pause-file`.

Test written first: it pauses *before* the run and asserts the model is never called and the item
is left queued. The mutation that drops `pause_file` goes red individually. 10 CLI tests pass.

## Diagnosis: six of the thirteen cited documents cannot be read

The enrichment plan's cited set has 13 documents. Seven read through the mount; **six do not**.
They are all one kind — the mount **cannot stat** them — and none contains `!`, so the first
hypothesis (that they were container-member addresses, which RO14 taught `corpus_read` to serve)
is **wrong**. The classification store independently has no row for the same six, so two harness
artefacts built by different passes agree that these entries are not readable regular files.

**The cause is not established, and this document does not guess it.** The mount resolves a path
before it `lstat`s it — a deliberate containment property, not an oversight — so it cannot report
whether the raw entry is a symlink whose target is absent from this backup, or an address recorded
from an earlier state of the tree. Telling those apart needs either a policy change (let the mount
`lstat` the unresolved entry, purely for classification) or an out-of-harness check, which
`AGENTS.md` §1.9 forbids. Both are calls, so neither was taken.

What matters for planning, and is established: **about half the cited set names something that is
not there to describe.** Anyone weighing hours of inference on the value set should know that
before spending them — and it would otherwise have looked like "the summariser skips documents".

## RO7 was investigated and not built, because it is gated

RO7 ("wiki synthesis: cluster by topic, generate source/topic pages, suggest links, all through
the gate — with the link suggestion non-destructive to page bodies") reads like an item that needs
no decision. It does not, for two separate reasons, both traceable to documents rather than to
taste:

1. **The build approach is an open owner question.** The roadmap's own *"Not decided yet (needs
   the owner)"* list contains *"whether the wiki service is built per its spec or reached
   harness-first"*, and the section that specifies RO7 says it *assumes* harness-first with the
   service as the destination. Clustering and page generation are the same work under either
   answer but not the same *code*: one is a kernel module, the other a service.
2. **The one property that could be isolated touches the vault format.** "Link suggestion
   non-destructive to page bodies" has to write its suggestions somewhere — a sidecar, or
   frontmatter the vault schema does not have today. `AGENTS.md` §1.6 reserves vault-format
   changes for the owner, so implementing it would take exactly the decision this round was told
   not to take.

So RO7 has no gate-free slice that this round could identify, and **nothing was built**: no
clustering module, no page schema, no link storage. The finding is the deliverable.

## The full mutation table: 281 guards, 8 problems

Re-run in full, with no source edited while it ran (the rule: an edit in that window is silently
lost). It checked **281 guards and reported 8 problems** — entries whose mutation target no longer
exists, or now matches twice. That is the table working as designed: an entry that cannot be
applied proves nothing, and this project has shipped vacuous tests three times.

**Three of the eight were broken by this session's own edits**, and each is repaired by moving the
mutation to the guard's current text, then re-run to red:

| entry | why it went stale |
|---|---|
| `R9 extraction goes back to raw indexing` | the call moved inside `ChatResult(...)` when `chat_detailed` was added |
| `RO6 a summary already paid for is paid for again` | the cache-hit branch grew a body — it now also ensures the description is indexed |
| `RO6 the metrics record carries a snippet of the document` | `measure`'s return dict gained `kind`, `server` and the residual |

**The other five are repaired too**, each by locating its guard in the code that exists today and
moving the mutation there — and each re-run to red on its own:

| entry | why it went stale, where the mutation moved |
|---|---|
| `RO3 mining: text files get queued for extraction they do not need` | the extraction claim now uses `extract_like`/`extract_patterns` (`.rar` is claimed for both tasks), not `doc_like`/`patterns` |
| `RO3 text: vendored matches stop being counted` | a comment was inserted between `if not include_vendored:` and the bounded count query, so the two-line target no longer sat together |
| `RO10 the band stops travelling with the address` | the served payload gained a `provenance` key, so the three-line target became four lines |
| `the sample stops being reproducible from its seed` | the target line now appears in two samplers; the mutation carries the following comment as context so it addresses one of them |
| `RO15 content routing is skipped for an unknown extension` | routing gained `rar` → `libarchive`, so the routing dict in the target was a key out of date |

That is eight of eight, and every repaired entry was run alone and went red. **The full table is
green: 289 guards checked, 0 problems** — the count rose from 281 because this session added eight
entries of its own. The order matters and is deliberate: the run that found the problems predates
the repairs, so each repair was verified on its own first and the whole table re-run afterwards,
rather than the table being assumed to have fixed itself by having been edited.

## Not done, and why

Nothing irreversible, and no default was taken on any open question: no scoring change, no vault
format, no sandbox boundary, no load-test scope, no default mining window.
