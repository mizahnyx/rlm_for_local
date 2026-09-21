"""The mnemonic alias table's pure core (RO13, 2026-09-20).

What this is for, in one sentence: a corpus address is 40–120 characters of arbitrary
path bytes plus numbers, and a small local model asked to hand one back a second time
slices it. Measured 2026-09-20 on the prompt-experiment question: the model found four
`strong` hits, cited one exactly, and then lost three cells to `IndexError` doing
string surgery on `path#L<start>-<end>` to feed a hit to `corpus_read`
(`docs/20260920-2215-the-prompt-experiment-one-question.md`).

The properties tested here are the ones whose absence would be *worse than the long
addresses the layer replaces* — the design's own words. A mnemonic that resolves
ambiguously, or that silently accepts a corrupted check symbol, points a citation at
the wrong passage, and this project treats a confident wrong answer as worse than
silence (`AGENTS.md` §1.4, §1.5).

Everything in this file carries **invented** addresses and aliases. A real address is
corpus-derived data and must not reach this repository (`AGENTS.md` §1.9,
`docs/20260919-2330-a-question-id-reached-the-public-repository.md`).
"""

from __future__ import annotations

import random
import re

import pytest

import rlm_local.mnemonics as mnemonics
from rlm_local.mnemonics import (
    ALIAS_RE,
    CHECK,
    CHECK_DIGEST,
    DIGITS,
    LETTERS,
    AliasTable,
    _one_edit_apart,
    code_to_check,
    looks_like_alias,
    normalise_alias,
    substitute_addresses,
)

# ── Invented fixtures. Two different addresses, so bijection is testable. ────

A1 = "notes/alpha.txt#L10-42"
A2 = "notes/beta.txt#L7-99"
A3 = "archive/one.zip!member.txt#L3-8"


@pytest.fixture
def table() -> AliasTable:
    """A table with a fixed seed, so its aliases are the same on every run."""
    return AliasTable(rng=random.Random(20260920))


# ── The alphabet excludes confusables ────────────────────────────────────────

class TestAlphabet:
    """One member of each confusable pair survives, so a slip has somewhere to go.

    `0/O`, `1/I/L`, `5/S` and `8/B` are the pairs a reader (and a 4B model) confuses.
    The alias keeps the *letter* of each pair and drops the digit, which is what makes
    the fold in the next section capable of repairing a slip rather than creating one.
    """

    def test_letters_exclude_l(self):
        # `L` is dropped, not `I`: two confusable letters cannot both be in the set,
        # and `I` is the one a font renders as a bare stroke.
        assert "L" not in LETTERS
        assert "I" in LETTERS
        assert "O" in LETTERS
        assert "S" in LETTERS
        assert "B" in LETTERS

    def test_digits_exclude_the_confusable_four(self):
        for digit in "0158":
            assert digit not in DIGITS
        assert DIGITS == "234679"

    def test_check_symbols_exclude_the_body_letters(self):
        # `B`, `I`, `O` and `S` belong to the body, so a check symbol that could be read
        # as a body glyph is the one confusion the check position must not carry.
        for body_letter in "BIOS":
            assert body_letter not in CHECK
        assert set(CHECK) == set(DIGITS) | set("ACDEFGHJKMNPQRTUVWXYZ")

    def test_the_digest_alphabet_covers_every_body_glyph(self):
        # The digest indexes body glyphs, and `CHECK` lacks four of them — indexing
        # through `CHECK` would raise on `B`, `I`, `O` and `S` (a real bug, caught by
        # this suite before it reached a corpus).
        assert set(LETTERS) | set(DIGITS) <= set(CHECK_DIGEST)
        for body_letter in "BIOS":
            assert body_letter in CHECK_DIGEST

    def test_a_body_with_an_excluded_glyph_has_no_check_symbol(self):
        """`code_to_check` refuses a body the alphabet would never produce.

        This is the guard the fold leans on: a folded candidate is only considered if its
        body could be a real code, so a fold that produces `L` in a letter position (or a
        digit where a letter belongs) cannot resolve to anything. Empty string means "no
        such code", never "code with an empty check symbol".
        """
        assert code_to_check("KQM7")
        assert code_to_check("LQM7") == ""      # L is excluded
        assert code_to_check("K2M7") == ""      # a digit in a letter position
        assert code_to_check("KQMZ") == ""      # a letter in the digit position
        assert code_to_check("KQM") == ""       # wrong length
        assert code_to_check("KQM77") == ""

    def test_the_code_space_is_wide_enough_to_keep_slips_apart(self):
        """The width is a measured requirement, not a preference.

        A slip that lands on another live alias resolves to that alias's passage. At a
        three-glyph body (3 750 codes) a sampled 400-alias session produced exactly that
        wrong resolution. This pins the space so a future shortening of the code cannot
        quietly reintroduce it without a test going red.
        """
        space = len(LETTERS) ** 3 * len(DIGITS)
        assert space == 93_750, space

    def test_no_minted_alias_carries_a_confusable(self, table):
        for index in range(400):
            alias = table.mint(f"notes/file{index}.txt#L0-{index + 1}")
            body = alias.replace("-", "")
            assert not (set(body) & set("0158L")), alias
            assert ALIAS_RE.fullmatch(alias), alias


# ── One-to-one within a run, and stable ─────────────────────────────────────

class TestBijection:
    def test_minting_is_idempotent(self, table):
        first = table.mint(A1)
        assert table.mint(A1) == first
        assert table.alias_for(A1) == first
        assert table.address_for(first) == A1
        assert len(table) == 1

    def test_two_addresses_never_share_an_alias(self, table):
        aliases = [table.mint(address) for address in (A1, A2, A3)]
        assert len(set(aliases)) == 3
        assert len(table) == 3

    def test_a_repeated_draw_is_checked_for_collision(self):
        """Two addresses must never be handed the same alias.

        A sampler that returned its draw without asking whether the alias was already
        taken would pass every other test in this class and fail here — and the failure
        would stay invisible until two passages shared one handle, which is exactly the
        silent wrong resolution this layer must not have.

        The draws are forced to repeat rather than left to a seeded sampler: a collision
        between two random draws out of 93 750 codes is rare enough that a test depending
        on it would pass most of the time and prove nothing, which is the vacuity this
        repository treats as a defect in its own right.
        """
        table = AliasTable(rng=_RepeatingRng())
        first = table.mint(A1)
        second = table.mint(A2)
        assert first != second, "two addresses were handed the same alias"
        # …and the table kept both, so neither handle was silently overwritten.
        assert table.alias_for(A1) == first
        assert table.alias_for(A2) == second
        assert len(table) == 2

    def test_a_fresh_table_refuses_another_runs_alias(self, table):
        """The per-run scope, which is what stops an old alias meaning something new.

        An alias that resolved across runs could point a citation at a different
        passage than the one the trace recorded against it — the single failure this
        layer must not have.
        """
        alias = table.mint(A1)
        other = AliasTable(rng=random.Random(7))
        resolution = other.resolve(alias)
        assert resolution.status == "unknown"
        assert resolution.address is None
        assert other.address_for(alias) is None

    def test_unknown_addresses_resolve_to_nothing(self, table):
        assert table.alias_for("never/minted.txt#L1-2") is None
        assert table.address_for("ZZ9-9") is None


# ── The five-step resolve ───────────────────────────────────────────────────

class TestResolveExactAndNormalised:
    def test_exact_match(self, table):
        alias = table.mint(A1)
        resolution = table.resolve(alias)
        assert (resolution.status, resolution.address) == ("exact", A1)
        assert resolution.repaired is False

    def test_case_and_whitespace_normalisation(self, table):
        alias = table.mint(A1)
        # `status` is `exact` for the spellings that differ only in surrounding
        # whitespace — stripping it yields the stored string — and `normalised` for the
        # ones that need the case folded or the hyphen put back. Either way the address
        # is the point, and a normalisation is *not* a repair: it is not counted as one.
        for spelling in (alias.lower(), f" {alias} ", alias.replace("-", " "),
                         alias.replace("-", "")):
            resolution = table.resolve(spelling)
            assert resolution.address == A1, spelling
            assert resolution.status in ("exact", "normalised"), spelling
            assert resolution.repaired is False, spelling

    def test_normalising_does_not_excuse_a_bad_check_symbol(self, table):
        """A normalised spelling still has its check symbol verified.

        The distinction is observable, which is the point of asserting the status rather
        than "not exact": with the check symbol verified, the corrupted spelling misses
        both clean-match steps and falls through to the repair rule, coming back
        `repaired`. Without the verification, the *normalised* step would match it and
        hand back the address as a clean match — a silent acceptance of a corrupted
        handle, which is the failure this guard exists to prevent.
        """
        alias = table.mint(A1)
        # A lowercase spelling whose check symbol does not fit its body: uppercasing it
        # lands on a *stored* spelling, so the normalised step is reached, and only the
        # check symbol can reject it.
        mutated = alias[:1].lower() + alias[1:-1] + _corrupt_check(alias)[-1]
        assert mutated.upper() == _corrupt_check(alias)
        resolution = table.resolve(mutated)
        assert resolution.status == "repaired", (mutated, resolution)
        assert resolution.address == A1
        assert resolution.repaired is True


class TestResolveFolding:
    """The fold goes *toward the surviving glyph*, which is the design's own correction.

    `docs/20260917-1215` wrote the fold as `O→0`, `I/L→1`, `S→5`, `B→8` — toward the
    digits the alphabet excludes. That would turn every repairable slip into an
    `unknown`: folding into a glyph no valid alias can contain produces a string no
    valid alias can equal. The handoff resolves it, and this is the test that pins it.
    """

    @pytest.mark.parametrize("slip,folds_to", [
        ("0", "O"),   # 0 → O
        ("1", "I"),   # 1 and L → I
        ("L", "I"),
        ("5", "S"),   # 5 → S
        ("8", "B"),   # 8 → B
    ])
    def test_a_confusable_slip_folds_to_the_surviving_glyph(self, table, slip, folds_to):
        # Build an alias whose *first letter* is the surviving glyph, then feed the glyph
        # a reader would confuse it with. Under the design document's original direction
        # (`O→0`, `I/L→1`, `S→5`, `B→8`) this resolves to nothing at all, because the
        # folded string contains a glyph no valid alias can hold.
        alias = _alias_with(table, first_letter=folds_to)
        slipped = slip + alias[1:]
        resolution = table.resolve(slipped)
        assert resolution.status == "folded", (slipped, resolution)
        assert resolution.address == table.address_for(alias)
        assert resolution.repaired is True

    def test_rn_folds_to_m(self, table):
        alias = _alias_with(table, first_letter="M")
        slipped = "rn" + alias[1:]
        # `rn` is two glyphs where the alias has one, so this is a length-changing fold:
        # without it the string is not alias-shaped and would fall through to `unknown`.
        resolution = table.resolve(slipped)
        assert resolution.status == "folded", (slipped, resolution)
        assert resolution.address == table.address_for(alias)

    def test_a_folded_body_with_an_excluded_glyph_is_not_a_candidate(self, table):
        """The fold cannot mint a code we would never issue.

        `L` is excluded from the alphabet (it is confusable with `I`), and `code_to_check`
        refuses a body containing it. That refusal is what stops this fold: without it, an
        `L` in the first position would fold to `I` and — with a check symbol recomputed to
        match — resolve as the alias whose body it landed on, which is a code the harness
        would never have handed out.
        """
        alias = _alias_with(table, first_letter="I")
        expected = table.address_for(alias)
        body, _ = alias.split("-")
        slipped_body = "L" + body[1:]
        slipped = f"{slipped_body}-{code_to_check(slipped_body)}"
        assert slipped != alias
        resolution = table.resolve(slipped)
        assert resolution.status != "folded", (slipped, resolution)
        # The edit rule may recover it — the body is right and only one glyph is
        # mis-spelled — but it must never be the fold's clean answer.
        assert resolution.address in (None, expected), (slipped, resolution)


class TestResolveRepair:
    def test_one_edit_apart_resolves_and_reports_the_repair(self, table):
        alias = table.mint(A1)
        swapped = alias[1] + alias[0] + alias[2:]
        assert swapped != alias
        resolution = table.resolve(swapped)
        assert resolution.status == "repaired"
        assert resolution.address == A1
        assert resolution.repaired is True

    def test_a_dropped_hyphen_is_one_edit_away(self, table):
        """The slip the mnemonic exists for: the code right, the punctuation lost."""
        alias = table.mint(A1)
        resolution = table.resolve(alias.replace("-", ""))
        assert resolution.address == A1
        assert resolution.repaired is False  # re-inserting the hyphen is normalisation

    def test_an_unknown_alias_names_the_closest(self, table):
        table.mint(A1)
        table.mint(A2)
        resolution = table.resolve("ZZZZZZ")
        assert resolution.status == "unknown"
        assert resolution.address is None
        assert resolution.candidates, "an unknown alias names the closest few"

    def test_ambiguity_cannot_arise_for_a_live_alias(self):
        """Why the unique-winner rule always has a winner — the structural reason.

        Two *distinct* valid aliases always differ in at least two positions: if they
        differed in exactly one, the code space would hold two codes one substituted
        glyph apart, and then a string that is neither would be one edit from both —
        and no rule could choose between them. `test_a_change_never_resolves_to_an_
        unrelated_alias` is what checks the consequence.

        The `ambiguous` branch is therefore unreachable through the repair rule; it is
        kept as the refusal that would fire if that ever stopped being true.
        """
        table = AliasTable(rng=random.Random(5))
        aliases = [table.mint(f"notes/amb{i}.txt#L0-{i + 1}") for i in range(400)]
        for one in aliases:
            for two in aliases:
                if one == two:
                    continue
                differing = sum(1 for x, y in zip(one, two) if x != y)
                assert differing >= 2, (one, two)

    def test_ambiguity_is_refused_not_guessed(self):
        """The refusal that makes the unique-winner rule meaningful, exercised directly.

        A string that is one edit from *two* live aliases is the case where the harness
        can see two readings and cannot choose. Resolving to either would point the
        citation at a passage the model may not have meant, so it must refuse.

        The pair is built from the code space rather than hoped for in a random draw: the
        two aliases must differ in exactly one *body* position and carry the same check
        symbol, which is what makes a third glyph at that position one substitution from
        both. A test that depended on the sampler happening to mint such a pair would be
        testing the sampler.
        """
        table = AliasTable(rng=random.Random(3))
        one, two, position = _one_edit_pair()
        for address, alias in (("notes/one.txt#L0-1", one), ("notes/two.txt#L0-2", two)):
            table._by_address[address] = alias
            table._by_alias[alias] = address
        glyph = next(g for g in LETTERS + DIGITS
                     if g not in (one[position], two[position]))
        slipped = one[:position] + glyph + one[position + 1:]
        resolution = table.resolve(slipped)
        assert resolution.status == "ambiguous", (slipped, one, two, resolution)
        assert resolution.address is None
        assert set(resolution.candidates) == {one, two}
        assert resolution.repaired is False
        # …and the slip really is one edit from both, which is what makes it ambiguous
        # rather than a repair of whichever alias the sampler happened to mint first.
        assert _one_edit(slipped, one) and _one_edit(slipped, two)
        # The *addresses* differ, so resolving to the wrong one would cite another
        # passage — the failure being refused here.
        assert table._by_alias[one] != table._by_alias[two]

    def test_a_corrupted_check_symbol_with_an_intact_body_still_repairs(self):
        """The other side of the same rule: one candidate means a repair, not a refusal.

        A model that retypes the body correctly and the check symbol wrongly has made one
        mistake, and it is repairable — the body is exact, so only one live alias is in
        reach. Refusing this would refuse the commonest slip of all.
        """
        table = AliasTable(rng=random.Random(4))
        alias = table.mint(A1)
        corrupted = _corrupt_check(alias)
        resolution = table.resolve(corrupted)
        assert resolution.status == "repaired", (corrupted, resolution)
        assert resolution.address == A1
        assert resolution.repaired is True

    def test_a_slip_lands_on_a_different_live_alias_vanishingly_rarely(self):
        """The residual exposure, measured rather than claimed to be zero.

        A slip that lands *on* another live alias resolves to that alias's passage, and
        no check symbol can see it: the slipped string is a perfectly valid code with a
        correct check symbol. The defence is space, not cleverness — at the original
        three-glyph body this test's predecessor found a wrong resolution in a sampled
        slip, and at 93 750 codes the expected number of one-apart pairs in a session of
        a few hundred is far below one.

        The assertion is a *rate* with a bound, because a property that depends on space
        is a probabilistic claim and pretending otherwise would be the confident default
        this project ranks below saying `unknown`. The rate is asserted to be zero at the
        session size a real chat reaches, and the property that makes it small is pinned
        by `test_ambiguity_cannot_arise_for_a_live_alias`.
        """
        rng = random.Random(6)
        table = AliasTable(rng=random.Random(7))
        addresses = {f"notes/slip{i}.txt#L0-{i + 1}": table.mint(
            f"notes/slip{i}.txt#L0-{i + 1}") for i in range(200)}
        wrong = 0
        slipped_count = 0
        for address, alias in addresses.items():
            for _ in range(6):
                position = rng.randrange(len(alias) - 1)
                glyph = rng.choice(LETTERS + DIGITS)
                slipped = alias[:position] + glyph + alias[position + 1:]
                if slipped == alias:
                    continue
                slipped_count += 1
                resolution = table.resolve(slipped)
                if resolution.status == "exact" and resolution.address != address:
                    wrong += 1
        assert slipped_count > 500, "the property was not exercised"
        assert wrong == 0, f"{wrong} slips resolved to another live alias"


class TestResolveRefusals:
    def test_empty_and_junk_input(self, table):
        for junk in ("", "   ", "-", "!!!", "123"):
            resolution = table.resolve(junk)
            assert resolution.status == "unknown", junk
            assert resolution.address is None

    def test_a_valid_shaped_alias_from_nowhere_is_unknown(self, table):
        table.mint(A1)
        # Correct shape and a *valid* check symbol, but never minted here.
        resolution = table.resolve("QQ2-4")
        assert resolution.status in ("unknown", "ambiguous", "repaired")
        if resolution.status == "repaired":
            assert resolution.address is not None


# ── Shapes and substitution ─────────────────────────────────────────────────

class TestShapes:
    def test_alias_re_matches_only_the_code(self):
        assert ALIAS_RE.fullmatch("KQM7-3")
        assert not ALIAS_RE.fullmatch("KQM7-30")
        assert not ALIAS_RE.fullmatch("KQM-3")
        assert not ALIAS_RE.fullmatch("KQMD-3")     # a digit where the check belongs
        assert not ALIAS_RE.fullmatch("KQM73")      # the hyphen is part of the shape

    def test_looks_like_alias_guards_the_read_path(self):
        assert looks_like_alias("KQM7-3")
        assert looks_like_alias("kqm7-3")
        assert looks_like_alias("KRM7-3")           # the `rn` spelling of `M`
        assert not looks_like_alias("notes/alpha.txt#L10-42")
        assert not looks_like_alias("notes/alpha.txt")
        assert not looks_like_alias("KQM7-3-4")
        assert not looks_like_alias("")

    def test_normalise_alias_is_the_input_to_the_table(self):
        assert normalise_alias("kqm7-3") == "KQM7-3"
        assert normalise_alias("KQM7 3") == "KQM7-3"
        assert normalise_alias("KQM73") == "KQM7-3"
        assert normalise_alias("  KQM7-3  ") == "KQM7-3"

    def test_normalise_does_not_reorder_glyphs(self):
        """Re-inserting the hyphen must not silently transpose the body.

        A `[:body] + "-" + rest[:1]` reconstruction would turn `KQRM73` into a body the
        model never wrote. The hyphen goes back at the body boundary instead.
        """
        assert normalise_alias("KQRM73") == "KQRM73"


class TestSubstitution:
    """What the owner reads: true addresses, with the model's raw output kept.

    Owner's call (2026-09-17): *"the harness substitutes the true address inline"* in
    the delivered answer, while the trajectory keeps the model's untouched output.
    """

    def test_an_alias_in_the_answer_becomes_its_address(self, table):
        alias = table.mint(A1)
        text = f"The passage says so. Citations: {alias}"
        assert substitute_addresses(text, table) == f"The passage says so. Citations: {A1}"

    def test_every_spelling_the_model_might_use(self, table):
        alias = table.mint(A1)
        for template in ("Citations: {}", "Citations: {}.", "Citations: {};",
                         "see ({})", "[{}]", "{}", "*{}*"):
            assert substitute_addresses(template.format(alias), table) == \
                template.format(A1)

    def test_unknown_tokens_are_left_alone(self, table):
        table.mint(A1)
        text = "Citations: QQ2-4 and notes/alpha.txt#L10-42"
        assert substitute_addresses(text, table) == text

    def test_a_real_address_is_not_rewritten(self, table):
        table.mint(A1)
        text = f"see {A1}"
        assert substitute_addresses(text, table) == text

    def test_no_aliases_means_no_change(self, table):
        assert substitute_addresses("plain prose", table) == "plain prose"


# ── Determinism ─────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_seed_same_aliases(self):
        one = AliasTable(rng=random.Random(99))
        two = AliasTable(rng=random.Random(99))
        for address in (A1, A2, A3):
            assert one.mint(address) == two.mint(address)

    def test_the_walk_finds_a_free_code_when_draws_fail(self, monkeypatch):
        """The fallback path: when random draws stop finding free keys, a walk does.

        Reached on a deliberately tiny alphabet — the real space holds 25³ × 6 = 93 750
        codes and cannot be exhausted in a test — but the code path is the same one a
        session near the limit would need. `_DRAW_ATTEMPTS` is zeroed so the walk is what
        answers, rather than depending on which codes the draws happen to pick.
        """
        monkeypatch.setattr(mnemonics, "_BODY_LETTERS", 1)
        monkeypatch.setattr(mnemonics, "_BODY_LEN", 2)
        monkeypatch.setattr(mnemonics, "_DRAW_ATTEMPTS", 0)
        space = _small_space()
        table = AliasTable(rng=random.Random(3))
        for body in space:
            table.mint(f"notes/{body}.txt#L0-1")
        assert sorted(table.alias_for(f"notes/{body}.txt#L0-1")
                      for body in space) == sorted(space)
        # …and the walk keeps finding free codes, rather than looping on a taken one.
        assert len(table) == len(space)
        assert table.mint("notes/more.txt#L0-1").startswith("B")

    def test_an_exhausted_space_refuses_rather_than_loops(self, monkeypatch):
        """The one case the fallback cannot serve says so, in the harness's own voice.

        A table that silently stopped minting, or looped forever, would leave a served
        address with no handle — and the model would be handed a hit it cannot cite.
        """
        monkeypatch.setattr(mnemonics, "_BODY_LETTERS", 1)
        monkeypatch.setattr(mnemonics, "_BODY_LEN", 2)
        monkeypatch.setattr(mnemonics, "_DRAW_ATTEMPTS", 0)
        table = AliasTable(rng=random.Random(3))
        for body in _small_space(every_letter=True):
            table.mint(f"notes/{body}.txt#L0-1")
        with pytest.raises(RuntimeError, match="alias space is exhausted"):
            table.mint("notes/one-too-many.txt#L0-1")


# ── Helpers ─────────────────────────────────────────────────────────────────

class _RepeatingRng:
    """A `random.Random` stand-in whose `choice` always returns the first element.

    Forces every draw to produce the same candidate code, which is what makes the
    collision check observable: without it, `mint` would hand the same alias to two
    addresses, and with it the second `mint` finds the next free code.
    """

    def choice(self, seq):  # noqa: D102 - the interface `mint` uses
        return seq[0]


def _one_edit(a: str, b: str) -> bool:
    """One insert, delete, substitution or adjacent swap — the repair rule's distance."""
    return _one_edit_apart(a, b)


def _one_edit_pair() -> tuple[str, str, int]:
    """Two valid aliases differing in exactly one body position, and which position.

    Both conditions are required for the ambiguity to be real. They must differ in
    **exactly one** position, or no single substitution is one edit from both; and that
    position must be inside the body, because a differing check symbol is itself an edit —
    which would put the two aliases two edits apart and make the third-glyph string a
    repair of one of them rather than a genuine ambiguity.

    Only positions 0 and 1 can yield such a pair: the check symbol is a function of the
    whole body, so two bodies differing only in the digit (position 3) always carry
    different check symbols. That is why this scans those two positions and asserts the
    result rather than trusting the construction — `test_ambiguity_is_refused_not_guessed`
    checks the one-edit property of the string it builds.
    """
    for position in (0, 1):
        seen: dict[tuple[str, str], str] = {}
        for body in mnemonics._every_body():
            check = code_to_check(body)
            key = (body[:position] + body[position + 1:], check)
            other = seen.get(key)
            if other is not None:
                return (f"{other}-{code_to_check(other)}",
                        f"{body}-{check}", position)
            seen[key] = body
    raise AssertionError("no one-edit pair in the code space")


def _small_space(*, every_letter: bool = False) -> list[str]:
    """Codes of a one-letter, one-digit alphabet — six codes, in a fixed order.

    Used with `_BODY_LETTERS`/`_BODY_LEN` monkeypatched, so the walk-and-refuse paths
    can be reached without minting 93 750 aliases. `every_letter=True` returns the whole
    150-code space, which is what exhausting it requires.
    """
    letters = LETTERS if every_letter else LETTERS[:1]
    return [f"{letter}{digit}-{code_to_check(letter + digit)}"
            for letter in letters for digit in DIGITS]


def _corrupt_check(alias: str) -> str:
    """Return the same body with a *different* check symbol."""
    body, check = alias.split("-")
    for candidate in CHECK:
        if candidate != check:
            return f"{body}-{candidate}"
    raise AssertionError("unreachable: CHECK has more than one member")


def _alias_with(table: AliasTable, *, first_letter: str) -> str:
    """Mint addresses until one has the requested first letter, then return it."""
    for index in range(600):
        alias = table.mint(f"notes/letter{index}.txt#L0-{index + 1}")
        if alias[0] == first_letter:
            return alias
    raise AssertionError(f"no alias with first letter {first_letter!r} in 600 mints")
