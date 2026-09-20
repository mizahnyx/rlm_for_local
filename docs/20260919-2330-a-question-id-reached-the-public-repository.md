# A question id reached the public repository

**Created:** 2026-09-19 23:30
**Status:** point-in-time record of a privacy breach this work introduced, what was removed,
what was not, and the guard that now checks the rule.
**Supersedes nothing.** It *redacts* one earlier document — a privacy fix overrides the
"documents are dated and never rewritten" convention (`AGENTS.md` §1.4), whose purpose is to
preserve history rather than to preserve a leak.

## What happened

The owner asked, on reading the second question set: *"take care in not uploading to GitHub
anything containing personal data from the prose corpus."* An audit found that I had already
done exactly that, in three places:

1. **A dated record** — `docs/20260919-1952-the-encoding-fix-at-the-model-level.md` named
   the owner's question by its id, four times: in the opening sentence, in a page path, in an
   `--only` example, and inside a quoted error message. A question id is a
   `<person>-<place>`-shaped name taken from the prose the harness read.
2. **A test** — `tests/test_question_probe.py` used three of the owner's real question ids
   as test data for the `--only` filter, in a test written minutes after the leak the ids had
   come from.
3. **A commit message** — the body of `1603c59` quoted the `--only` invocation and the error
   message, both containing the id.

A wider scan of the pushed history found **earlier occurrences from previous sessions**:
commit bodies that quote corpus names, including what looks like search output — a name and
its match count on one line. Those predate this work; they are the same class.

## What was done

- **The record was redacted** at 23:05: the id is replaced by "question 1", the page path by
  its directory, and the error example by `<the id you asked for>`. The redaction is noted at
  the top of that document rather than silently applied.
- **The test now uses invented ids** (`Gamma-Topic`, `delta-topic`), with a comment saying
  why, so the next person does not "helpfully" put a real one back.
- **A check exists**: `scripts/check_privacy.py`. The *list of identifiers* cannot live in a
  public repository — that would be the leak — so the check takes `--tokens FILE`, a mode-0600
  list beside the corpus (`~/rlm-derived/private-tokens.txt`). `--history` additionally scans
  every commit message. Its failure message names the file, the line and the token's
  **position in the list**, never the token: a privacy check whose error quotes the
  identifier moves the leak to the terminal, the log and whatever pastes the log. Seven
  tests, one mutation entry, and the report-not-quoting property is itself tested.
- **`AGENTS.md` §1.9 says it explicitly now**: a question id is one of the names that stay on
  the laptop, and the check that enforces it is named there.

## What was not done, and why it is the owner's call

**The identifiers are still in the pushed history.** The current tree is clean; every commit
that contained them is not. Removing them from the refs needs a history rewrite
(`git filter-repo` or `filter-branch`) followed by a **force-push** to a public repository,
which:

- rewrites published history — anything already fetched elsewhere keeps its copy;
- breaks any existing clone (including `lunacode`'s, which would need re-cloning);
- **does not guarantee removal from GitHub's storage**: unreferenced objects can remain
  reachable by SHA for a time, and forks or caches keep whatever they took.

Three options, none of them mine to choose:

| option | effect | cost |
|---|---|---|
| **Rewrite and force-push** | the identifiers leave the refs and every future clone | published history changes; `lunacode` must re-clone; GitHub may still hold objects |
| **Make the repository private** | nobody new can read it, history included | the data was already public; visibility is a setting only the owner can change |
| **Fix forward only** (what has been done) | the tree is clean and the guard prevents recurrence | the history stays as it is |

My recommendation is the first two together if the names matter, and fix-forward if they do
not — but that judgement needs to know whose names they are, which is exactly the information
that does not belong in this session.

## The lesson, stated plainly

The rule was already written down and it was not enough. `AGENTS.md` §1.9 said "identifiers
may not travel — not in this conversation, not in the repository, not in a commit message" —
and the moment a *new kind* of identifier appeared (a question id, invented by the owner in
their own file, which felt like a label rather than a name out of the corpus), the rule did
not come to mind. That is the argument for a check rather than a convention, and it is the
same argument the mutation table makes for tests: a rule with nothing enforcing it is a rule
that holds until the first case it did not anticipate.
