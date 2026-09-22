# The member read is verified, and two of my own probes leaked

**2026-09-22.** RO23's last unverified link is now measured. The same session also produced
two probe artefacts and one **disclosure of a corpus identifier into a session transcript**,
which is recorded here because the project's rule is to write down what went wrong rather
than to quietly fix it.

## The member read, end to end

68 `.rar` containers were extracted by the live window and have listed members. Asking the
real entry point — `CorpusBridge.handle_read`, not a copy of its logic — for a member of a
sample of them, in both shapes RO14 distinguishes:

| shape | served | headered | needs mining | no such path |
|---|---|---|---|---|
| bare name (`container!member`) | 6 of 6 | 6 | 0 | 0 |
| address (`container!member#L0-9`) | 6 of 6 | 6 | 0 | 0 |

- Served lengths: 332 … 20 249 characters — real text, not a header alone.
- **No read filtered `text_chunks` on `display`** (0 of 12, asserted with a trace callback):
  the `>150 s` fallback RO14 exists to avoid is not being taken, on the real index, for a
  container family that did not exist when that guard was written.

So a `.rar` member is readable, citable and re-readable through the same path as any other
container — which is what "list + extract" was for. RO23 is complete.

## The two "unreadable" `.rar` files are not regular files

The earlier probe recorded two paths as `unreadable:ReadOnlyViolation`. They are refused by
the mount with **`not a regular file`**, which is the containment layer working as designed:
both sit inside a WebLogic server's internal cache directory, where the entries are not
plain files. The recorded outcome was right and its cause was vague; the correct
description is "the mount refuses it, and it should".

## Two probe artefacts, and a disclosure

**Artefact 1 — absolute paths.** Two of the recorded `.rar` records hold **absolute** paths,
while the mount API takes paths *relative to the corpus root*; `is_contained` answers `False`
and everything downstream refuses. My probe read that refusal as a property of the files.

**Artefact 2 — printing an exception.** Diagnosing the two above, a probe let a traceback
reach the terminal, and **`ReadOnlyViolation`'s message quotes the path it refused.** That
put one corpus-derived identifier — a full path, with directory and file names — into this
session's transcript, which is sent to a model provider. It did not reach the repository and
it is not in this document.

The vector is worth naming precisely, because this change applied the project's existing rule
to one place and not another: `bsdtar`'s stderr is **classified, never echoed**
(`_libarchive_class`), for exactly this reason. Python's own exceptions were left alone, and
a probe that prints a traceback therefore carries an identifier out through a channel that
looks like a stack trace rather than a diagnostic. Two consequences, one of them an owner
call:

- **Practice, no decision needed**: probes classify exceptions rather than print them. Every
  probe written today after that point did this, which is why the other four reported
  classes and counts.
- **Owner call**: whether `ReadOnlyViolation` — and the mount's other messages — should quote
  the path at all. Quoting it is what makes an operator's failure legible; it is also what
  makes any traceback a disclosure. A message that carried a length and a hash instead would
  be safe and less useful, and that trade is the owner's to make.

## What is still open

- The identifier that reached the transcript cannot be recalled. What can be done is what
  this document does: state it, name the vector, and change the practice that produced it.
- `mine status` still costs ~118 s on the `mine_queue` GROUP BY, and `find_members` still
  scans the member table — both recorded earlier and neither touched here.
