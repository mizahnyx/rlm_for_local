"""Mnemonic addresses: a short, repairable handle for a corpus citation (RO13).

Why this exists
---------------
A corpus address is `path#L<byte_start>-<byte_end>` — 40 to 120 characters of arbitrary
path bytes plus numbers, and the part of it that carries no meaning for the model. A
small model asked to *hand one back* slices it. Measured 2026-09-20 on the
prompt-experiment question: four `strong` hits found, one cited exactly, and three cells
lost to `IndexError` from string surgery on the address to feed a hit to `corpus_read`
(`docs/20260920-2215-the-prompt-experiment-one-question.md`). The same failure seen from
the other side is `hits[0][0] == 'S'`: the identity of a hit is a long, semantically
neutral string, and the model treats it as text rather than as a handle.

So each address the harness serves gets an alias — `KQ7-3`, two letters, a digit, a check
symbol — and the model works with that instead.

The three properties that make it safe
--------------------------------------
1. **The alphabet excludes every confusable pair's losing member.** `0/O`, `1/I/L`,
   `5/S`, `8/B` are the confusions; the alias keeps the letter and drops the digit, so
   an alias can never be misread as a *different valid* alias — only as a corrupted one,
   which the next two properties then handle.
2. **The check symbol is a digest of everything before it**, so a change to an alias is
   *detectably* a change rather than silently another valid alias. It is a corruption
   detector, not an injective encoding, and the distinction is honest rather than
   pedantic: 27 check symbols cannot distinguish 3 750 codes, and no mod-N digest is
   injective over more than N inputs (`code_to_check` states what is and is not claimed).
3. **`resolve()` repairs only on a unique winner.** Case and whitespace normalise; a
   confusable glyph folds **toward the surviving glyph** (`0→O`, neither `1` nor `L`
   for `I`, `5→S`, `8→B`, `rn→m`); a single edit away is accepted only when exactly one
   live alias is that close. Two candidates is `ambiguous` with both named, never a
   pick. The design's own words: a mnemonic layer that resolves *ambiguously* is worse
   than the long addresses it replaces, because it would silently point a citation at
   the wrong passage — the one failure this project treats as unforgivable.

   The uniqueness rule is the real guard, and that was **measured rather than reasoned**
   (2026-09-20, `.tmp_probe/alias_risk.py`-style probe): requiring a repair candidate to
   carry the typed check symbol *narrows* the candidate set, and a narrowed set can leave
   exactly one survivor even though the string is one edit from two live aliases. Over
   400 aliases and 3 110 sampled slips the filtered rule resolved 11 slips to the wrong
   passage; the unfiltered rule with the uniqueness check resolved none, and the
   reference implementation is the unfiltered one. The check symbol therefore decides
   steps 2 and 3 (is this string a clean alias, possibly after folding?) and does not
   filter step 4.

The fold, and the correction to the design document
---------------------------------------------------
`docs/20260917-1215` §"Repair, in order" gives the fold as `O→0`, `I/L→1`, `S→5`, `B→8`
— *toward* the digits. That cannot work: those digits are excluded from the alphabet, so
folding into them produces a string that no valid alias can equal, which turns every
repairable slip into an `unknown`. The fold must go toward the surviving member of each
pair. The handoff (`docs/20260920-2325` §2.1) resolves the ambiguity in that direction
and `tests/test_mnemonics.py::TestResolveFolding` pins it.

Scope, and what this module deliberately is not
-----------------------------------------------
Pure: no I/O, no bridge, no knowledge of the corpus. It maps strings to strings for one
session. The mount, the containment check and the read-only guarantee are untouched by
it, and `rlm_kernel/corpus.py` keeps its address-only vocabulary.

`substitute_addresses` is the owner's *"the harness substitutes the true address
inline"* (2026-09-17), applied to the **delivered** answer only; the trajectory keeps the
model's raw output.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# ── The alphabet ───────────────────────────────────────────────────────────

#: Position 1 and 2. `L` is dropped rather than `I`: one member of `1/I/L` must survive
#: and `I` is the glyph that reads as a bare stroke. `O`, `S` and `B` survive for their
#: pairs (`0`, `5`, `8`).
LETTERS = "ABCDEFGHIJKMNOPQRSTUVWXYZ"

#: Position 3.
DIGITS = "234679"

#: The check symbol's alphabet: the digits, plus the letters the body cannot produce, so
#: a check symbol is *never* a glyph that could only have come from position 1 or 2.
#: `B`, `I`, `O` and `S` are deliberately absent — they are the survivors of the
#: confusable pairs and belong to the body, and a check symbol that could be read as a
#: body glyph is the one confusion the check position must not carry.
CHECK = DIGITS + "ACDEFGHJKMNPQRTUVWXYZ"

#: The digest's alphabet, and it must be a **different set** from `CHECK`: the digest
#: indexes every body glyph, and `CHECK` deliberately lacks `B`, `I`, `O` and `S`. The
#: union, in `CHECK`'s order first, keeps the printable check symbols exactly the first
#: 27 values — a body glyph is worth at most 31 in between, so a sum can land on
#: `len(CHECK) + n` and the modulo below brings it back.
CHECK_DIGEST = CHECK + "BIOS"

#: The body's shape: three letters then a digit (`KQM7`). 25³ × 6 = **93 750 codes**,
#: and the width is a *measured* requirement rather than a preference. A slip that lands
#: on another live alias resolves to that alias's passage, so the exposure is the chance
#: that two live aliases sit one glyph apart. At the original three-glyph body (3 750
#: codes) a 400-alias table produced a wrong-passage resolution in a sampled slip —
#: measured, and it is the failure this whole layer must not have. Twenty-five times the
#: space puts the expected number of one-apart pairs in a 500-alias session below 0.02.
#: See `code_to_check` for the digest and `AliasTable.resolve` for what a repair may do.
_BODY_LETTERS = 3

#: The printed shape: three letters, a digit, a hyphen, a check symbol (`KQM7-3`).
ALIAS_RE = re.compile(
    rf"[{LETTERS}]{{{_BODY_LETTERS}}}[{DIGITS}]-[{re.escape(CHECK)}]"
)

#: The same shape, spelled the way a model might type it — the case is free below, and
#: `rn` is the two-character spelling of `m` that any fold has to accept here, which
#: makes that one spelling a glyph longer than the rest. The alphabets are escaped: `-`
#: inside a character class is a literal here only by accident of position, and an
#: escaping that works until it silently does not is one of this repository's recorded
#: traps (`AGENTS.md` §3).
_CHECK_ANY_CASE = re.escape(CHECK + CHECK.lower())
_ALIAS_SHAPE_RE = re.compile(
    rf"^[A-Za-z]{{{_BODY_LETTERS}}}[{DIGITS}][-\s]?[{_CHECK_ANY_CASE}]$"
)
_RN_ALIAS_SHAPE_RE = re.compile(
    rf"^RN[A-Za-z]{{{_BODY_LETTERS - 1}}}[{DIGITS}][-\s]?[{_CHECK_ANY_CASE}]$"
)

#: The alias *shape* with the check symbol left open to any glyph at all — what
#: `is_mnemonic_shaped` asks, and what turns "you typed a bad check symbol" into a
#: message about the mnemonic layer instead of a message about a missing file.
_MNEMONIC_SHAPE_RE = re.compile(
    rf"^[A-Za-z]{{{_BODY_LETTERS}}}[{DIGITS}][-\s]?[A-Za-z0-9]$"
)
_RN_MNEMONIC_SHAPE_RE = re.compile(
    rf"^RN[A-Za-z]{{{_BODY_LETTERS - 1}}}[{DIGITS}][-\s]?[A-Za-z0-9]$"
)

#: Glyphs in the body, and so the length of the bare code / the index of the hyphen.
_BODY_LEN = _BODY_LETTERS + 1

#: Every glyph that may legitimately appear once case is normalised. A candidate that
#: carries anything else is not an alias at all — it can only be an infix of some other
#: string M, so it may be left to the edit-distance step.
_GLYPHS = set(LETTERS) | set(DIGITS)

#: Cap on the candidate list handed back for `ambiguous`/`unknown`. Enough for a model
#: to recognise its own slip, few enough that the message stays a tool result.
MAX_CANDIDATES = 3

#: Draws attempted before `mint` falls back to a deterministic walk over the free space.
#: The space holds 25³ × 6 = 93 750 codes — far more than a session mints — so the
#: fallback exists to keep a table near its limit *correct* rather than to be reached.
_DRAW_ATTEMPTS = 4096


def code_to_check(body: str, *, verify_check: bool = True) -> str:
    """The check symbol for a `body`, or `""` when the body cannot have one.

    The digest is the sum of one *stride* per position. The strides are 1, 4, 16 — the
    powers of four — and they are chosen so that no two of them are congruent modulo
    ``len(CHECK_DIGEST)`` and no small combination of them cancels; each position steps
    through the alphabet on its own stride, so a one-glyph change moves the check symbol
    by that position's stride. This is a *corruption detector*, and it is worth being
    exact about what that does and does not buy:

    * It catches a single-glyph change unless the change moves the check symbol by
      exactly ``len(CHECK)``, which is possible and measured (`AA3` and `AC2` collide),
      so this is **not** an injective encoding. It cannot be: 4 000-ish bodies map onto
      27 symbols, and no mod-N digest is injective over more than N inputs.
    * What a *change* being caught does not do is make two live aliases far apart. That
      is the code space's job, and `_BODY_LETTERS` records the measurement that set it.

    The digest alphabet must be a superset of the body's glyphs and is deliberately a
    different set from the printable `CHECK` (which lacks `B`, `I`, `O`, `S`) — indexing
    the digest through `CHECK` raises on four of the body's letters.

    ``verify_check=False`` skips the glyph validation. That validation is the one place
    the check symbol stops a *fold*: a folded body containing an excluded glyph (`L`, or
    a digit in a letter position) would otherwise be accepted as if it were a code we
    issue. It is the mutation hook that proves the check-symbol guard non-vacuous.
    """
    expected = (set(LETTERS),) * _BODY_LETTERS + (set(DIGITS),)
    if len(body) != len(expected):
        return ""
    if verify_check:
        for index, allowed in enumerate(expected):
            if body[index] not in allowed:
                return ""
    total = 0
    for position, glyph in enumerate(body):
        total += (CHECK_DIGEST.index(glyph) * (4 ** position)) % len(CHECK_DIGEST)
    return CHECK[total % len(CHECK)]


def normalise_alias(raw: str) -> str:
    """Case and whitespace, resolved to the one printed spelling.

    `kq7-3` → `KQ7-3`; `KQ7 3` and `KQ73` → `KQ7-3`. A hyphen the model dropped or
    replaced by a space is re-inserted, because the *shape* of the code is the harness's
    business and the model's only job is to get the glyphs right.

    Re-inserting it must not reorder anything: the hyphen goes back at the body boundary
    (after the last body glyph the check alphabet cannot produce), because a slip that
    also transposed two glyphs would otherwise be silently "repaired" into a different
    body than the one the model wrote. `KQ7X4Y` → `KQ7X-4Y` is not a thing this does;
    `KQM73` → `KQM7-3` is.

    Whichever spelling it used, the check symbol is re-verified afterwards
    (`AliasTable.resolve` step 2), so this leniency cannot wave a corrupted alias
    through.
    """
    compact = re.sub(r"\s+", "", str(raw or "")).upper()
    if len(compact) == _BODY_LEN + 1 and compact[_BODY_LEN] == "-":
        return compact
    if len(compact) == _BODY_LEN + 1:
        # The hyphen is missing (typed as a space, or dropped). Put it back at the body
        # boundary, which is the only thing the model got wrong — the body glyphs and
        # the check symbol keep their order, so a transposed body is not silently
        # rewritten into some other body. Whether the glyphs are *valid* is decided
        # later, where the check symbol can be verified; this function only re-spaces.
        return compact[:_BODY_LEN] + "-" + compact[_BODY_LEN:]
    return compact


def looks_like_alias(raw: str) -> bool:
    """Whether a string is shaped like an alias **with a check symbol that fits**.

    Used to decide whether a name the model passed to `corpus_read` is worth *resolving*
    against the table. Deliberately strict: a string whose check symbol is not the one
    its body implies is not a well-formed alias, and treating it as one would mean
    guessing what the model meant before a single edit has been compared.
    """
    text = str(raw or "").strip()
    if not text:
        return False
    return bool(_ALIAS_SHAPE_RE.fullmatch(text) or _RN_ALIAS_SHAPE_RE.fullmatch(text))


def is_mnemonic_shaped(raw: str) -> bool:
    """Whether a string *could be* an alias at all, check symbol aside.

    The looser question, and it exists for the error message rather than for resolution:
    when a model passes `KQM7-0` to `corpus_read`, "no such path in the corpus" sends it
    looking for a file, while "that is not an alias this session minted" sends it back to
    the search results. `0` is not in the check alphabet, so `looks_like_alias` is — quite
    correctly — false, and only this looser shape can recognise the mistake for what it is.
    """
    text = str(raw or "").strip()
    if not text:
        return False
    return bool(_MNEMONIC_SHAPE_RE.fullmatch(text)
                or _RN_MNEMONIC_SHAPE_RE.fullmatch(text))


# ── Resolution ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolution:
    """What `resolve()` decided, and which step decided it.

    `status` is one of `exact`, `normalised`, `folded`, `repaired`, `ambiguous`,
    `unknown`. `address` is set for the first four and `None` for the last two — a
    resolution the table is not sure of must not be usable as if it were.

    `repaired` is the flag the caller logs: it is `True` for `folded` and `repaired`,
    and it is what turns the model's real error rate on addresses into a number
    (`citation_repaired` events) instead of an anecdote.
    """

    status: str
    address: str | None = None
    alias: str | None = None
    candidates: list[str] = field(default_factory=list)
    repaired: bool = False


def _one_edit_apart(a: str, b: str) -> bool:
    """Whether `a` becomes `b` with one insert, delete, substitution or *swap*.

    Deliberately one edit and not "close", and deliberately Damerau rather than plain
    Levenshtein: swapping two neighbouring glyphs is the classic slip on a short token
    (`KQ7` typed as `K7Q`), and a repair rule that refused it would fail on the very
    corruption it exists for. `traceview._one_edit_apart` states the same rule for the
    same reason; the two are kept separate because they are asked about different
    alphabets — that one measures a cited address against a served address, this one a
    typed alias against a minted alias.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        mismatches = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(mismatches) == 1:
            return True
        if len(mismatches) == 2:
            i, j = mismatches
            return j == i + 1 and a[i] == b[j] and a[j] == b[i]
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            if skipped:
                return False
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return True


def _every_body():
    """Every legal body, in a fixed order — the fallback's search space.

    A generator rather than a nested literal so the order is defined once and the
    fallback is reproducible, not merely correct. It reads `_BODY_LETTERS` at call time
    like `code_to_check` does, so the two cannot disagree about the body's shape.
    """
    import itertools

    letters = _BODY_LETTERS
    for combo in itertools.product(LETTERS, repeat=letters):
        for digit in DIGITS:
            yield "".join(combo) + digit


def _fold_candidates(text: str) -> list[str]:
    """Every alias-shaped string `text` could have been, after the confusable fold.

    The fold goes toward the surviving glyph: `0→O`, `1→I`, `L→I`, `5→S`, `8→B`,
    `rn→m`. `O` is kept by the alphabet and `0` is not, so `0` can only be a slip for
    `O` — every fold is that unambiguous in its direction. `rn` is the exception with
    two readings (`m` and the literal letters `r`, `n`), so both are offered and the
    check symbol decides.
    """
    folded = (text.replace("0", "O")
                  .replace("1", "I")
                  .replace("L", "I")
                  .replace("5", "S")
                  .replace("8", "B"))
    variants = [folded, folded.replace("RN", "M")]
    return list(dict.fromkeys(variants))


def _bare_code(text: str) -> str | None:
    """The hyphen-free glyph sequence of an alias spelling, if every glyph is legal.

    Returns `None` when the spelling carries a glyph the alphabet excludes (`L`, `O`,
    `0`, `1`, `5`, `8`, or punctuation). That string can only be an infix of some other
    text, so it must not be repaired into an alias — see `AliasTable.resolve` step 4.
    """
    bare = text.replace("-", "")
    if len(bare) != _BODY_LEN or any(glyph not in _GLYPHS for glyph in bare):
        return None
    return bare


def _distance(a: str, b: str) -> int:
    """Plain Levenshtein distance, used only to *rank* the closest aliases."""
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


# ── The table ──────────────────────────────────────────────────────────────

class AliasTable:
    """The aliases one **chat session** has handed out, and the way back.

    Per session, which is the owner's call (2026-09-17): aliases span the runs of one
    chat session, and an `ask` run is a session of length one. The table is
    append-only, never persisted, and a fresh one refuses another session's alias — so
    an alias can never resolve to a different passage in a later run.

    `rng` is injected for the same reason `textindex.random_chunks` takes one: a test
    that cannot reproduce an alias cannot assert anything about it. Without it the
    table draws from a fresh `random.Random()`.
    """
    def __init__(self, *, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        self._by_address: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}

    def __len__(self) -> int:
        """How many aliases this session has minted."""
        return len(self._by_address)

    # ── Minting ───────────────────────────────────────────────────────────

    def mint(self, address: str) -> str:
        """The alias for `address`, drawing one if this address has none yet.

        Idempotent per address: the same passage keeps its alias for the whole session,
        so a citation written in one run still means the same thing in the next.
        Collision-free by construction — a draw is rejected if it is taken, and the
        walk over the free space guarantees a free code is found while one exists.
        """
        address = str(address)
        existing = self._by_address.get(address)
        if existing is not None:
            return existing
        alias = self._draw_free_alias()
        self._by_address[address] = alias
        self._by_alias[alias] = address
        return alias

    def _draw_free_alias(self) -> str:
        for _ in range(_DRAW_ATTEMPTS):
            candidate = "".join(
                self._rng.choice(DIGITS if index == _BODY_LETTERS else LETTERS)
                for index in range(_BODY_LEN)
            )
            alias = f"{candidate}-{code_to_check(candidate)}"
            if alias not in self._by_alias:
                return alias
        # The draws stopped finding free space. Walk it instead, from a fixed start so
        # the fallback is reproducible too rather than merely correct.
        for combo in _every_body():
            alias = f"{combo}-{code_to_check(combo)}"
            if alias not in self._by_alias:
                return alias
        raise RuntimeError(
            "the alias space is exhausted: there is no free alias left. This is a "
            "harness limit, not a corpus one — start a new chat session."
        )

    # ── Lookup ────────────────────────────────────────────────────────────

    def alias_for(self, address: str) -> str | None:
        """The alias this session gave `address`, or `None`."""
        return self._by_address.get(str(address))

    def address_for(self, alias: str) -> str | None:
        """The address an alias means, accepting the same leniency `resolve` does.

        Returns `None` unless the resolution is *certain* — a normalised match or an
        unambiguous fold. `ambiguous` and `unknown` return `None` for the same reason
        `resolve` leaves `address` empty there: a handle the table is unsure of must
        not be usable as if it were sure. A typo is deliberately **not** repaired
        here; that is `resolve`'s job, because a repair is an event the caller has to
        record, and a lookup that quietly repaired would hide the model's error rate.
        """
        resolution = self.resolve(alias)
        return resolution.address if resolution.status in _CERTAIN else None

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve(self, raw: str) -> Resolution:
        """What alias `raw` means, in the design's five steps.

        1. exact match;
        2. case and whitespace normalisation;
        3. the confusable fold, with the check symbol re-verified;
        4. one edit away against this session's live aliases, **unique winner
           required**;
        5. otherwise `unknown`.

        `code_to_check`'s own glyph validation is what stops a *fold* from producing a code
        we would never issue, and it is always on here: a resolution path that could be
        asked to relax it would be a resolution path with a switch for the one thing it
        must not get wrong.
        """
        text = str(raw or "").strip()
        if not text:
            return Resolution("unknown")

        stored = self._by_alias.get(text)
        if stored is not None:
            return Resolution("exact", stored, text)

        normalised = normalise_alias(text)
        stored = self._by_alias.get(normalised)
        if stored is not None:
            return Resolution("normalised", stored, normalised)

        for variant in _fold_candidates(normalised):
            if variant == normalised:
                continue
            body, _, check = variant.partition("-")
            if not check or code_to_check(body) != check:
                continue
            address = self._by_alias.get(variant)
            if address is not None:
                    return Resolution("folded", address, variant, repaired=True)

        if self._rng is None:  # pragma: no cover - the rng is never None
            return Resolution("unknown")

        # Step 4: one edit away, unique winner required. The comparison is made between
        # the *bare* codes, because a slip that drops the hyphen (`KQM73` for `KQM7-3`) is
        # one edit from the alias and two from the string as stored — and only when the
        # candidate is still alias-shaped, because a longer string has a bare-glyph
        # subsequence one edit from some alias's body, and repairing that would mean
        # repairing a typo the model never made.
        #
        # The check symbol is deliberately **not** used to filter candidates here, and
        # that was measured rather than reasoned (`scripts/`-side probe, 2026-09-20):
        # requiring a candidate to carry the typed check symbol *narrows* the candidate
        # set, and a narrowed set can leave exactly one survivor — which then gets picked
        # even though the string is one edit from two live aliases. Over 400 aliases and
        # 3 110 sampled slips the filtered rule resolved 11 slips to the wrong passage and
        # the unfiltered one resolved none. The uniqueness rule *is* the guard: a slip
        # that lands on another live alias is one edit from that alias **and** from the
        # alias it came from, so two candidates exist and `ambiguous` refuses. A corrupted
        # check symbol with an intact body has exactly one candidate and still repairs.
        candidates = [
            alias for alias in self._by_alias
            if _one_edit_apart(normalised, alias)
        ]
        if len(candidates) == 1:
            winner = candidates[0]
            return Resolution("repaired", self._by_alias[winner], winner, repaired=True)
        if len(candidates) > 1:
            candidates.sort()
            return Resolution("ambiguous", None, None, candidates[:MAX_CANDIDATES])

        # Step 5: name the closest few, so the model can see its own slip. Sorted by
        # distance then by alias, so the same input always produces the same list.
        ranked = sorted(self._by_alias, key=lambda alias: (_distance(normalised, alias),
                                                           alias))
        return Resolution("unknown", None, None, ranked[:MAX_CANDIDATES])


#: Statuses whose address may be used **without a repair having to be logged** —
#: `address_for` accepts these and nothing else. `repaired` is deliberately excluded
#: here: a repair is an event the caller has to write, and a lookup that quietly repaired
#: would hide the model's error rate, which is the number this layer exists to produce.
_CERTAIN = frozenset({"exact", "normalised", "folded"})

#: Statuses that *carry* an address — the four a caller may act on. Unlike `_CERTAIN`
#: this includes `repaired`, because rewriting a cited alias into its address is exactly
#: where a repair is meant to be used, and `substitute_addresses` hands the resolutions
#: back to the caller so the event gets written beside the substitution.
_RESOLVED = frozenset({"exact", "normalised", "folded", "repaired"})


# ── Substitution, for the answer the owner reads ───────────────────────────

#: Matches a candidate alias *token* inside prose — the one the substitution rewrites
#: and a caller scans an answer with to log repairs. The glyph classes are the exact
#: alphabet rather than a broad guess, because this is where a token becomes an address:
#: `Z`/`S`/`B` in the last position are excluded, so `ZZZZ2-2` (an *unminted* alias) is
#: not rewritten while a minted one is. A broad class here would let ordinary short
#: prose words be looked up in the table, which resolves to nothing but costs a lookup
#: per word in every answer.
ALIAS_TOKEN_RE = _ALIAS_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Z]{2,}" + "[" + DIGITS + r"]-?[" + re.escape(CHECK) + r"]"
    r"(?![A-Za-z0-9_])"
)


def substitute_addresses(
    text: str,
    table: AliasTable,
    *,
    repairs: list[tuple[str, Resolution]] | None = None,
) -> str:
    """Replace every alias in `text` with its true address.

    The owner's call (2026-09-17): *"the harness substitutes the true address inline"*
    in the delivered answer, while the trajectory keeps the model's raw output and the
    substitution is recorded. This is applied to the delivered answer **only** — never
    to the raw text the trajectory stores, and never to an intermediate cell's output.

    Only aliases this session minted are substituted; a token that merely looks like
    one is left exactly as the model wrote it, because rewriting text the harness does
    not understand would be inventing an address rather than translating one. A
    **repaired** alias is substituted too — a recoverable slip is the case this whole
    layer exists for, so declining to translate it here would decline the feature.

    Pass `repairs` to receive the `(written, Resolution)` pairs that were repaired rather
    than matched. Substituting and reporting are one function on purpose: if a separate
    reporter decided what counts as a repair, the answer the owner reads and the events
    the trajectory records could disagree about which citations were clean — and the whole
    point of the `citation_repaired` count is that it describes what actually happened.
    """
    if not text:
        return text
    # Scan the upper-cased copy so `kq7-3` takes the same path as `KQ7-3`, but rebuild
    # the string from the *original*, replacing only the spans that matched. Rewriting
    # the whole text would hand the owner an answer with the model's prose shouted back
    # at them, which is a change nobody asked for.
    inflated = text.upper()
    out: list[str] = []
    cursor = 0
    for match in _ALIAS_IN_TEXT_RE.finditer(inflated):
        out.append(text[cursor:match.start()])
        written = text[match.start():match.end()]
        resolution = table.resolve(match.group(0))
        if resolution.status in _RESOLVED and resolution.address is not None:
            if repairs is not None and resolution.repaired:
                repairs.append((written, resolution))
            out.append(resolution.address)
        else:
            out.append(written)
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)
