# A token on the private list is in the public history

**Date:** 2026-09-26. **Status:** found by running the check that had not been run; the working
tree is clean and the exposure is in **published commit history**, which makes the remedy an
owner call. Discovered while verifying today's fix; it corrects the scope of one sentence in
`docs/20260926-1900-the-probe-printed-the-id-it-was-measuring.md`.

## What was run, and why it had not been

`scripts/check_privacy.py` has two forms. The tree form scans the working tree; the `--history`
form additionally scans every commit message. Today's verification of the id-printing fix ran
the **tree** form twice — at commit `6a04e44` and again at `a0b6f31` — and both said:

```
checked 8 token(s) against the working tree
no occurrence in the tree
```

That is true, and `docs/20260926-1900-…` recorded it. But the record's phrasing ("No sensitive
id reached the repository") claims more than the check measured, and the check that measures
the rest exists. Running it:

```
checked 8 token(s) against the working tree and every commit message
LEAK  commit 1603c59 message line 3 (token #1 of the list)
LEAK  commit 1603c59 message line 12 (token #1 of the list)
LEAK  commit d14796a message line 32 (token #7 of the list)
LEAK  commit 7712f49 message line 3 (token #7 of the list)
LEAK  commit 81af8fb message line 26 (token #7 of the list)
LEAK  commit 81af8fb message line 26 (token #8 of the list)

6 occurrence(s). Remove them from the tree; a token in *history* needs a rewrite and a
force-push, which is the owner's call.
```

Eight tokens were checked of the ten on `~/rlm-derived/private-tokens.txt` (two are presumably
too short or too common to match on); six occurrences were found, across four commits, naming
three different tokens. The tokens themselves are not repeated here — the checker is built to
name a commit, a line and a position instead, which is what makes its output quotable at all
(`AGENTS.md` §1.9).

## The dates, which shape the remedy

| commit | committed | on the remote? |
|---|---|---|
| `81af8fb` | 2026-09-14 17:38 | yes |
| `7712f49` | 2026-09-14 21:15 | yes |
| `d14796a` | 2026-09-17 04:44 | yes |
| `1603c59` | 2026-09-19 19:58 | yes |

**Every one predates the token list itself** (`~/rlm-derived/private-tokens.txt`, created
2026-09-20 09:25) and every one is an ancestor of `origin/main` — the local `HEAD` and
`origin/main` are both `a0b6f31`, and 105 commits have landed since the oldest of them. So this
is not a leak that the check failed to prevent at the time it was written; it is history that
was already public when the list was drawn up, and the list has been checked against the tree
ever since without anyone checking it against the log.

## What is *not* established, and it matters for the remedy

**Whether the six matches are genuine identifiers or ordinary words.** The tokens on the list
are letters-only words (9 of the 10; one carries a digit) and the checker matches substrings, so
a token that is also an ordinary noun or a place name would light up in a commit message that
has nothing to do with the corpus. Two observations cut both ways and neither settles it:

- One line (`81af8fb` line 26) carries **two** of the tokens at once, which is the shape of a
  *listing* rather than an incidental word.
- But the flagged lines are in subjects and bodies of commits about vaults, indexes and
  aliases, where a common word is entirely plausible.

Reading the four messages would settle it — and those messages are exactly what must not be
copied into a session transcript or a document, so the answer has to come from the machine that
holds the list. Nothing here claims which it is (the `AGENTS.md` §1.8 corollary: a check that
cannot see the truth says unknown).

## The remedy, and why it is an owner call

- **If the matches are ordinary words:** the honest fix is to make the token list precise
  (whole-word, length-bounded, or context-qualified) so the check stops reporting noise — a
  checker that cries wolf stops being read, which is the failure mode this project already
  recorded for the mutation table.
- **If they are genuine identifiers:** removing them means rewriting published history and
  force-pushing. That changes every commit hash from the oldest flagged commit forward, breaks
  every existing clone, and invalidates the hashes this project's records cite by design
  (`docs/` cites commits in dozens of places). It is a destructive, irreversible operation on a
  public repository, so it is the owner's call (`AGENTS.md` §1.6), and it is now in the
  roadmap's "Not decided yet (needs the owner)" list.

Two things are true whichever way that goes, and both are cheap:

1. **The `--history` form belongs in the routine.** `AGENTS.md` §1.9 now says so explicitly:
   the tree form before a commit, the `--history` form before a push.
2. **The listing was the leak mechanism, again.** Today's fix stopped the probe *printing* an id
   and left the artefacts named after it; this finding is the same shape one layer down — a
   name that travels because something with access to it enumerated a set. Neither is fixed by
   watching output more carefully; both are fixed by not naming things after identifiers.

## Unverified

- Whether each of the six occurrences is a genuine identifier (above).
- Whether the two tokens the checker skips are safe to skip. It reports 8 of 10 and does not say
  which two or why, and nothing here checked that.
